"""
Megalodon orchestrator-console — stub API server.

Phase-3 BUILD tick 1 deliverable. Implements all read endpoints with live-file
reads from <PROJECT_ROOT>/, all mutation endpoints as validation-only stubs
(no filesystem writes yet — that's tick 2-3), an SSE event stream driven by a
simple 2s polling loop, plus static-file serving for ui/static/.

API contract is documented in ui/api-contract.md. Plan basis:
  findings/agent-8318-C-P1-backend-plan-2026-05-16T15-33Z.md  (P1-C)
  findings/agent-8318-C-P2.5-backend-plan-v2-2026-05-16T15-46Z.md  (P2.5-C)

Run:
  uv run python ui/server.py
  # then visit http://127.0.0.1:8080
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Literal, Optional

try:
    import yaml
    from fastapi import FastAPI, HTTPException, Request, Response
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse
    from fastapi.staticfiles import StaticFiles
    from sse_starlette.sse import EventSourceResponse
    import uvicorn
except ImportError as exc:
    missing = str(exc).split("'")[1] if "'" in str(exc) else "dependency"
    sys.stderr.write(
        f"ERROR: missing {missing}. Install with: uv pip install fastapi 'uvicorn[standard]' sse-starlette pyyaml\n"
    )
    sys.exit(1)

from mutations import (
    append_history_line,
    append_phase_event,
    cas_modify,
    file_locks,
    format_canonical_signal,
    mutator_mission_status,
    mutator_status_reclaim,
    mutator_status_signal,
    mutator_tasks_inject,
    mutator_tasks_reset,
    try_acquire_phase_flip_lock,
    utc_now_iso,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(
    os.environ.get("MEGALODON_MISSION_DIR")
    or Path(__file__).resolve().parent.parent
).resolve()
HOST = "127.0.0.1"
PORT = int(os.environ.get("MEGALODON_UI_PORT", "8080"))
HEARTBEAT_INTERVAL_SECONDS = 15
POLL_INTERVAL_SECONDS = 2
SSE_QUEUE_CAPACITY = 100
FILE_WATCH_DEBOUNCE_MS = 100
STALE_THRESHOLD_SECONDS = 900  # 15 min, per RULE 6

CSRF_TOKEN = secrets.token_hex(16)  # rotates per process restart

log = logging.getLogger("megalodon.ui")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)


# ---------------------------------------------------------------------------
# Domain shapes (see ui/api-contract.md for canonical reference)
# ---------------------------------------------------------------------------

LANES = ("AUDIT", "ARCHITECT", "BACKEND", "FRONTEND", "TEST", "META")
PHASES = (
    "INIT",
    "PHASE-PLAN",
    "PHASE-CHALLENGE",
    "PHASE-BUILD",
    "PHASE-VERIFY",
    "DRAINING",
    "COMPLETE",
)


@dataclass
class LaneRow:
    lane: str
    agent: Optional[str]
    state: str
    last_utc: Optional[str]
    notes: str
    staleness_seconds: Optional[int] = None
    is_stale: bool = False
    working_task_id: Optional[str] = None


@dataclass
class Task:
    id: str
    phase: str
    lane_code: Optional[str]
    description: str
    state: Literal["open", "claimed", "done"]
    claimer_agent: Optional[str] = None
    claim_utc: Optional[str] = None
    done_utc: Optional[str] = None
    expected_output: Optional[str] = None
    has_lock_dir: bool = False
    has_done_marker: bool = False


@dataclass
class Finding:
    filename: str
    lane: Optional[str]
    agent: Optional[str]
    task: Optional[str]
    severity: Optional[str]
    utc: Optional[str]
    artifact: Optional[str]
    title: Optional[str]
    frontmatter: dict[str, Any] = field(default_factory=dict)
    body_md: Optional[str] = None


@dataclass
class HistoryEntry:
    utc: str
    agent: str
    lane: str
    task: str
    finding_filename: str
    severity: str


@dataclass
class PhaseEvent:
    utc: str
    from_phase: str
    to_phase: str
    by_agent: str
    reason: str


@dataclass
class Signal:
    utc: str
    from_lane: str
    from_agent: str
    to: str
    kind: str  # SIGNAL | ACK-VERIFIED | DISSENT | DEFER
    claim: str
    evidence: dict[str, Any]
    source_artifact: str  # status-notes | finding | history
    source_ref: str
    finding_ref: Optional[str] = None
    confidence: str = "high"


@dataclass
class Claim:
    task_id: str
    agent: Optional[str]
    claimed_utc: Optional[str]
    done: bool
    done_utc: Optional[str] = None


# ---------------------------------------------------------------------------
# Parsers — STATUS.md / TASKS.md / HISTORY.md / .mission-events / findings/
# ---------------------------------------------------------------------------

UTC_NOW = lambda: datetime.now(timezone.utc)


def parse_utc(s: str) -> Optional[datetime]:
    """Parse ISO-8601 UTC with trailing Z (tz-aware). Returns None on failure."""
    if not s:
        return None
    s = s.strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def staleness_seconds(last_utc_str: Optional[str]) -> Optional[int]:
    if not last_utc_str:
        return None
    dt = parse_utc(last_utc_str)
    if dt is None:
        return None
    return int((UTC_NOW() - dt).total_seconds())


_TABLE_ROW_RE = re.compile(r"^\|(.+)\|$")
_LANE_ROW_NAMES = {name.upper(): name for name in LANES}


def parse_status_md() -> list[LaneRow]:
    """Parse the STATUS.md pipe-table into LaneRow records."""
    rows: list[LaneRow] = []
    path = PROJECT_ROOT / "STATUS.md"
    if not path.exists():
        return rows
    in_table = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if not in_table and line.startswith("|") and "Lane" in line and "Agent" in line:
            in_table = True
            continue
        if in_table and line.startswith("|---"):
            continue
        if not in_table:
            continue
        if not line.startswith("|"):
            in_table = False
            continue
        # parse cells (strip leading/trailing pipe)
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4:
            continue
        lane_cell = cells[0].upper()
        if lane_cell not in _LANE_ROW_NAMES:
            continue
        agent_cell = cells[1]
        agent = None if agent_cell in ("unclaimed", "—") else agent_cell
        state = cells[2]
        last_utc = cells[3]
        if last_utc in ("—", ""):
            last_utc = None
        notes = " | ".join(cells[4:]) if len(cells) > 4 else ""
        working_task = None
        if state.startswith("working:"):
            working_task = state.split(":", 1)[1].strip()
        s_secs = staleness_seconds(last_utc)
        is_stale = (
            s_secs is not None
            and s_secs > STALE_THRESHOLD_SECONDS
            and state not in ("idle", "PEER-REVIEWER")
        )
        rows.append(
            LaneRow(
                lane=_LANE_ROW_NAMES[lane_cell],
                agent=agent,
                state=state,
                last_utc=last_utc,
                notes=notes,
                staleness_seconds=s_secs,
                is_stale=is_stale,
                working_task_id=working_task,
            )
        )
    return rows


_TASK_BULLET_RE = re.compile(
    r"^- \[(?P<bracket>[^\]]+)\] \[(?P<lane_tag>[^\]]+)\] `(?P<task_id>[^`]+)` — (?P<desc>.+)$"
)
_PHASE_HEADER_RE = re.compile(r"^## (?P<phase>PHASE [^\n]+)")


def parse_tasks_md() -> list[Task]:
    """Parse the TASKS.md bullet list into Task records."""
    tasks: list[Task] = []
    path = PROJECT_ROOT / "TASKS.md"
    if not path.exists():
        return tasks
    current_phase = "UNKNOWN"
    for line in path.read_text(encoding="utf-8").splitlines():
        m_phase = _PHASE_HEADER_RE.match(line.strip())
        if m_phase:
            current_phase = m_phase.group("phase").strip()
            continue
        m = _TASK_BULLET_RE.match(line.strip())
        if not m:
            continue
        bracket = m.group("bracket").strip()
        lane_tag = m.group("lane_tag").strip()
        task_id = m.group("task_id").strip()
        desc = m.group("desc").strip()
        # Decode bracket → state
        state: Literal["open", "claimed", "done"] = "open"
        claimer = None
        claim_utc = None
        done_utc = None
        if bracket == " " or bracket == "":
            state = "open"
        elif bracket.startswith("claimed:"):
            state = "claimed"
            parts = bracket[len("claimed:") :].split("@", 1)
            if len(parts) == 2:
                claimer = parts[0].strip()
                claim_utc = parts[1].strip()
        elif bracket.startswith("done:"):
            state = "done"
            parts = bracket[len("done:") :].split("@", 1)
            if len(parts) == 2:
                claimer = parts[0].strip()
                done_utc = parts[1].strip()
        # Filesystem cross-check
        normalized_ids = _normalize_task_id(task_id)
        has_lock = any((PROJECT_ROOT / "claims" / nid).is_dir() for nid in normalized_ids)
        has_done = any(
            (PROJECT_ROOT / "claims" / nid / "done").is_file() for nid in normalized_ids
        )
        lane_code = None
        m_lane = re.match(r"LANE-([A-F])", lane_tag)
        if m_lane:
            lane_code = m_lane.group(1)
        tasks.append(
            Task(
                id=task_id,
                phase=current_phase,
                lane_code=lane_code,
                description=desc,
                state=state,
                claimer_agent=claimer,
                claim_utc=claim_utc,
                done_utc=done_utc,
                has_lock_dir=has_lock,
                has_done_marker=has_done,
            )
        )
    return tasks


def _normalize_task_id(task_id: str) -> list[str]:
    """Return all on-disk variants for a logical task ID.

    Workers have used both 'P2-C→B' (Unicode arrow) and 'P2-C-to-B' (ASCII)
    and even truncated 'P2-C' forms. UI must surface inconsistency, but for
    has_lock / has_done detection we check all common variants.
    """
    variants = [task_id]
    if "→" in task_id:
        variants.append(task_id.replace("→", "-to-"))
        variants.append(task_id.replace("→", "-"))  # e.g. P2-C→B → P2-C-B
        variants.append(task_id.split("→")[0])  # source-lane only e.g. P2-C
    return variants


_HISTORY_ENTRY_RE = re.compile(
    r"^(?P<utc>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z) \| (?P<agent>[^|]+) \| (?P<lane>[^|]+) \| (?P<task>[^|]+) \| (?P<filename>[^|]+) \| (?P<severity>.+)$"
)


def parse_history_md() -> list[HistoryEntry]:
    entries: list[HistoryEntry] = []
    path = PROJECT_ROOT / "HISTORY.md"
    if not path.exists():
        return entries
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _HISTORY_ENTRY_RE.match(line.strip())
        if not m:
            continue
        entries.append(
            HistoryEntry(
                utc=m.group("utc").strip(),
                agent=m.group("agent").strip(),
                lane=m.group("lane").strip(),
                task=m.group("task").strip(),
                finding_filename=m.group("filename").strip(),
                severity=m.group("severity").strip(),
            )
        )
    return entries


_MISSION_EVENT_RE = re.compile(
    r"^(?P<utc>[\d\-T:]+Z) (?P<from>[A-Z\-]+)->(?P<to>[A-Z\-]+) by (?P<by>[^ ]+) — (?P<reason>.+)$"
)


def parse_mission_events() -> list[PhaseEvent]:
    events: list[PhaseEvent] = []
    path = PROJECT_ROOT / ".mission-events"
    if not path.exists():
        return events
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _MISSION_EVENT_RE.match(line.strip())
        if not m:
            continue
        events.append(
            PhaseEvent(
                utc=m.group("utc"),
                from_phase=m.group("from"),
                to_phase=m.group("to"),
                by_agent=m.group("by"),
                reason=m.group("reason"),
            )
        )
    return events


def current_phase() -> str:
    events = parse_mission_events()
    if not events:
        return "INIT"
    return events[-1].to_phase


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


def parse_finding(path: Path) -> Finding:
    """Read a finding file, split YAML frontmatter from body."""
    text = path.read_text(encoding="utf-8")
    frontmatter: dict[str, Any] = {}
    body = text
    m = _FRONTMATTER_RE.match(text)
    if m:
        try:
            frontmatter = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            frontmatter = {}
        body = m.group(2)
    title = None
    for line in body.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    return Finding(
        filename=path.name,
        lane=frontmatter.get("lane"),
        agent=frontmatter.get("agent"),
        task=str(frontmatter.get("task")) if frontmatter.get("task") else None,
        severity=frontmatter.get("severity"),
        utc=str(frontmatter.get("utc")) if frontmatter.get("utc") else None,
        artifact=frontmatter.get("artifact"),
        title=title,
        frontmatter=frontmatter,
        body_md=body,
    )


def list_findings(metadata_only: bool = True) -> list[Finding]:
    findings_dir = PROJECT_ROOT / "findings"
    if not findings_dir.is_dir():
        return []
    out: list[Finding] = []
    for p in sorted(findings_dir.glob("*.md")):
        f = parse_finding(p)
        if metadata_only:
            f.body_md = None
        out.append(f)
    return out


def list_claims() -> dict[str, Claim]:
    claims_dir = PROJECT_ROOT / "claims"
    if not claims_dir.is_dir():
        return {}
    out: dict[str, Claim] = {}
    for d in sorted(claims_dir.iterdir()):
        if not d.is_dir():
            continue
        task_id = d.name
        done = (d / "done").is_file()
        claimed_utc = (
            datetime.fromtimestamp(d.stat().st_mtime, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        done_utc = None
        if done:
            done_utc = (
                datetime.fromtimestamp((d / "done").stat().st_mtime, tz=timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
        out[task_id] = Claim(
            task_id=task_id,
            agent=None,  # filled in from TASKS.md if possible
            claimed_utc=claimed_utc,
            done=done,
            done_utc=done_utc,
        )
    # Cross-reference with TASKS.md for claimer agent
    for t in parse_tasks_md():
        for variant in _normalize_task_id(t.id):
            if variant in out and t.claimer_agent:
                out[variant].agent = t.claimer_agent
    return out


# ---------------------------------------------------------------------------
# Signal extraction (Δ1 + Δ2 from P2.5-C)
# Lenient parser: accepts canonical <SIG>...</SIG> tokens AND legacy prose.
# ---------------------------------------------------------------------------

_SIG_CANONICAL_RE = re.compile(
    r'<SIG\s+kind="(?P<kind>[^"]+)"\s+from="(?P<from>[^"]+)"\s+to="(?P<to>[^"]+)"\s+utc="(?P<utc>[^"]+)"\s+evidence="(?P<ev>[^"]+)">\s*(?P<claim>.*?)\s*</SIG>',
    re.DOTALL,
)
_SIG_PROSE_ACK_RE = re.compile(
    r"ACK-VERIFIED\s+(?P<sender>[A-Z]+)[:.]?\s+(?P<rest>.+)$"
)
_SIG_PROSE_DISSENT_RE = re.compile(
    r"DISSENT\s+(?P<sender>[A-Z]+)[:.]?\s+(?P<rest>.+)$"
)
_SIG_PROSE_DEFER_RE = re.compile(
    r"DEFER\s+(?P<sender>[A-Z]+)[:.]?\s+(?P<rest>.+)$"
)
_SIG_PROSE_SIGNAL_RE = re.compile(
    r"SIG(NAL)?-?[A-Z]*[:.]?\s+(?P<rest>.+)$"
)


def extract_signals_from_status() -> list[Signal]:
    """Extract Signal records from STATUS.md Notes column (lenient parser)."""
    out: list[Signal] = []
    rows = parse_status_md()
    for r in rows:
        notes = r.notes
        for m in _SIG_CANONICAL_RE.finditer(notes):
            out.append(
                Signal(
                    utc=m.group("utc"),
                    from_lane=m.group("from"),
                    from_agent=r.agent or "unknown",
                    to=m.group("to"),
                    kind=m.group("kind"),
                    claim=m.group("claim").strip(),
                    evidence={"path": m.group("ev")},
                    source_artifact="status-notes",
                    source_ref=f"STATUS.md#{r.lane}",
                    confidence="high",
                )
            )
        # Prose-style: ACK-VERIFIED, DISSENT, DEFER inline
        for token, pattern, kind in [
            ("ACK-VERIFIED", _SIG_PROSE_ACK_RE, "ACK-VERIFIED"),
            ("DISSENT", _SIG_PROSE_DISSENT_RE, "DISSENT"),
            ("DEFER", _SIG_PROSE_DEFER_RE, "DEFER"),
        ]:
            if token in notes:
                m = pattern.search(notes)
                if m:
                    out.append(
                        Signal(
                            utc=r.last_utc or "",
                            from_lane=r.lane,
                            from_agent=r.agent or "unknown",
                            to=m.group("sender"),
                            kind=kind,
                            claim=m.group("rest").strip(),
                            evidence={"path": "STATUS.md", "section": r.lane},
                            source_artifact="status-notes",
                            source_ref=f"STATUS.md#{r.lane}",
                            confidence="medium",
                        )
                    )
    return out


# ---------------------------------------------------------------------------
# Application state — in-memory cache + SSE broadcast registry
# ---------------------------------------------------------------------------


@dataclass
class MissionState:
    lanes: list[LaneRow]
    tasks: list[Task]
    findings: list[Finding]
    history: list[HistoryEntry]
    phase: str
    phase_events: list[PhaseEvent]
    claims: dict[str, Claim]
    signals: list[Signal]
    mission_status: str
    fingerprint: str
    utc: str


def snapshot_state() -> MissionState:
    lanes = parse_status_md()
    tasks = parse_tasks_md()
    findings = list_findings(metadata_only=True)
    history = parse_history_md()
    events = parse_mission_events()
    phase = events[-1].to_phase if events else "INIT"
    claims = list_claims()
    signals = extract_signals_from_status()
    mission_status = parse_mission_status_section()
    payload = {
        "lanes": [asdict(l) for l in lanes],
        "phase": phase,
        "claims_keys": sorted(claims.keys()),
        "history_count": len(history),
        "findings_count": len(findings),
    }
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return MissionState(
        lanes=lanes,
        tasks=tasks,
        findings=findings,
        history=history,
        phase=phase,
        phase_events=events,
        claims=claims,
        signals=signals,
        mission_status=mission_status,
        fingerprint=fingerprint,
        utc=UTC_NOW().isoformat().replace("+00:00", "Z"),
    )


_MISSION_STATUS_RE = re.compile(
    r"\*\*Current:\s*([A-Z\-]+)\s*\(", re.MULTILINE
)


def parse_mission_status_section() -> str:
    path = PROJECT_ROOT / "README.md"
    if not path.exists():
        return "UNKNOWN"
    text = path.read_text(encoding="utf-8")
    m = _MISSION_STATUS_RE.search(text)
    return m.group(1) if m else "UNKNOWN"


# ---------------------------------------------------------------------------
# SSE broadcast bus — per-client queue with bounded capacity
# ---------------------------------------------------------------------------


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()
        self._sequence = 0

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=SSE_QUEUE_CAPACITY)
        async with self._lock:
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers.discard(q)

    def next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    async def broadcast(self, event: str, data: dict[str, Any]) -> None:
        seq = self.next_sequence()
        payload = json.dumps({"seq": seq, **data})
        msg = {"event": event, "data": payload, "id": str(seq)}
        async with self._lock:
            stale: list[asyncio.Queue] = []
            for q in self._subscribers:
                try:
                    q.put_nowait(msg)
                except asyncio.QueueFull:
                    stale.append(q)
            for q in stale:
                # Drain + send lagging signal
                while not q.empty():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                try:
                    q.put_nowait(
                        {
                            "event": "lagging",
                            "data": json.dumps(
                                {
                                    "reason": "buffer overflow",
                                    "resync_urls": [
                                        "/api/v1/state",
                                        "/api/v1/status",
                                        "/api/v1/tasks",
                                    ],
                                    "since_utc": UTC_NOW()
                                    .isoformat()
                                    .replace("+00:00", "Z"),
                                }
                            ),
                            "id": str(seq),
                        }
                    )
                except asyncio.QueueFull:
                    pass  # client truly hopeless; disconnect on next yield


event_bus = EventBus()


# ---------------------------------------------------------------------------
# Background polling loop — detect file changes, emit SSE events
# ---------------------------------------------------------------------------


class PollingWatcher:
    """2s polling watcher.

    Detects changes by computing a fingerprint and diffing tracked collections
    (findings, phase events, history entries, tasks, claims, signals). Emits
    matching SSE events on the broadcast bus.

    Tick 3 may replace inner-loop with watchfiles + retain this as fallback
    per C5 (macOS FSEvents coalescing).
    """

    def __init__(self) -> None:
        self._last_fingerprint: Optional[str] = None
        self._last_seen_findings: set[str] = set()
        self._last_seen_phase_event_count: int = 0
        self._last_seen_history_count: int = 0
        self._last_tasks_state: dict[str, str] = {}
        self._last_claims_keys: set[str] = set()
        self._last_claims_done: set[str] = set()
        self._last_signal_keys: set[str] = set()
        self._last_lane_rows: dict[str, tuple] = {}  # lane → (state, agent, last_utc, notes_hash)
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while self._running:
            try:
                state = await asyncio.get_event_loop().run_in_executor(None, snapshot_state)
                # V2 fix (RR-1): emit canonical {lane, row, utc} per changed lane
                # rather than a single {fingerprint, utc} that the FE can't consume.
                current_lane_rows: dict[str, tuple] = {}
                for row in state.lanes:
                    key = (row.state, row.agent or "", row.last_utc or "", hash(row.notes))
                    current_lane_rows[row.lane] = key
                    prev = self._last_lane_rows.get(row.lane)
                    if self._last_lane_rows and prev != key:
                        await event_bus.broadcast(
                            "status-change",
                            {"lane": row.lane, "row": asdict(row), "utc": state.utc},
                        )
                self._last_lane_rows = current_lane_rows
                # finding-new events
                current_filenames = {f.filename for f in state.findings}
                new_filenames = current_filenames - self._last_seen_findings
                for fn in sorted(new_filenames):
                    finding = next(f for f in state.findings if f.filename == fn)
                    await event_bus.broadcast(
                        "finding-new",
                        {
                            "filename": fn,
                            "frontmatter": finding.frontmatter,
                            "utc": state.utc,
                        },
                    )
                self._last_seen_findings = current_filenames
                # phase-flip events
                if len(state.phase_events) > self._last_seen_phase_event_count:
                    for ev in state.phase_events[self._last_seen_phase_event_count :]:
                        await event_bus.broadcast(
                            "phase-flip",
                            {
                                "from": ev.from_phase,
                                "to": ev.to_phase,
                                "by": ev.by_agent,
                                "reason": ev.reason,
                                "utc": ev.utc,
                            },
                        )
                self._last_seen_phase_event_count = len(state.phase_events)
                # history-append events
                if len(state.history) > self._last_seen_history_count:
                    for entry in state.history[self._last_seen_history_count :]:
                        await event_bus.broadcast(
                            "history-append", {"entry": asdict(entry), "utc": state.utc}
                        )
                self._last_seen_history_count = len(state.history)
                # task-change events (diff against last poll)
                current_tasks_state = {t.id: t.state for t in state.tasks}
                if self._last_tasks_state:
                    for tid, new_state in current_tasks_state.items():
                        old_state = self._last_tasks_state.get(tid)
                        if old_state is not None and old_state != new_state:
                            task = next((t for t in state.tasks if t.id == tid), None)
                            await event_bus.broadcast(
                                "task-change",
                                {
                                    "task_id": tid,
                                    "old_state": old_state,
                                    "new_state": new_state,
                                    "agent": task.claimer_agent if task else None,
                                    "utc": state.utc,
                                },
                            )
                self._last_tasks_state = current_tasks_state
                # claim-create and claim-done events
                current_claim_keys = set(state.claims.keys())
                new_claims = current_claim_keys - self._last_claims_keys
                for tid in sorted(new_claims):
                    await event_bus.broadcast(
                        "claim-create", {"task_id": tid, "utc": state.utc}
                    )
                current_done = {tid for tid, c in state.claims.items() if c.done}
                new_done = current_done - self._last_claims_done
                for tid in sorted(new_done):
                    await event_bus.broadcast(
                        "claim-done", {"task_id": tid, "utc": state.utc}
                    )
                self._last_claims_keys = current_claim_keys
                self._last_claims_done = current_done
                # signal-new events (diff by unique signal key)
                current_signal_keys = {
                    f"{s.from_agent}|{s.to}|{s.kind}|{s.utc}|{s.claim[:64]}"
                    for s in state.signals
                }
                new_signals = current_signal_keys - self._last_signal_keys
                for sig in state.signals:
                    key = f"{sig.from_agent}|{sig.to}|{sig.kind}|{sig.utc}|{sig.claim[:64]}"
                    if key in new_signals and self._last_signal_keys:  # skip first-tick replay
                        await event_bus.broadcast("signal-new", asdict(sig))
                self._last_signal_keys = current_signal_keys
                self._last_fingerprint = state.fingerprint
            except Exception as exc:
                log.exception("polling loop error: %s", exc)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)


watcher = PollingWatcher()


# ---------------------------------------------------------------------------
# FastAPI app + endpoints
# ---------------------------------------------------------------------------


app = FastAPI(title="Megalodon UI", version="0.1.0-stub")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[f"http://{HOST}:{PORT}", f"http://localhost:{PORT}"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.middleware("http")
async def origin_and_csrf_check(request: Request, call_next):
    """Δ8 — Origin check on POST; CSRF token defense-in-depth."""
    if request.method == "POST":
        origin = request.headers.get("origin", "")
        allowed = (f"http://{HOST}:{PORT}", f"http://localhost:{PORT}")
        if origin not in allowed:
            return JSONResponse(
                status_code=403,
                content={
                    "ok": False,
                    "error": f"Origin {origin!r} not allowed",
                    "code": "ORIGIN_REJECTED",
                    "recoverable": False,
                },
            )
        csrf = request.headers.get("x-csrf-token", "")
        if csrf != CSRF_TOKEN:
            return JSONResponse(
                status_code=403,
                content={
                    "ok": False,
                    "error": "CSRF token mismatch",
                    "code": "CSRF_FAILED",
                    "recoverable": False,
                },
            )
    return await call_next(request)


# --- Read endpoints ---------------------------------------------------------


@app.get("/api/v1/state")
def get_state():
    s = snapshot_state()
    return _asdict_with_string_severity(s)


@app.get("/api/v1/status")
def get_status():
    return {"lanes": [asdict(l) for l in parse_status_md()]}


@app.get("/api/v1/tasks")
def get_tasks(phase: Optional[str] = None, lane: Optional[str] = None, state: Optional[str] = None):
    tasks = parse_tasks_md()
    if phase:
        tasks = [t for t in tasks if t.phase.startswith(phase)]
    if lane:
        tasks = [t for t in tasks if t.lane_code == lane]
    if state:
        tasks = [t for t in tasks if t.state == state]
    return {"tasks": [asdict(t) for t in tasks]}


@app.get("/api/v1/phase")
def get_phase():
    events = parse_mission_events()
    if not events:
        return {"current": "INIT", "last_event": None}
    last = events[-1]
    return {"current": last.to_phase, "last_event": asdict(last)}


@app.get("/api/v1/mission-events")
def get_mission_events(since: Optional[str] = None):
    events = parse_mission_events()
    if since:
        since_dt = parse_utc(since)
        if since_dt:
            events = [e for e in events if (parse_utc(e.utc) or since_dt) > since_dt]
    return {"events": [asdict(e) for e in events]}


@app.get("/api/v1/findings")
def get_findings(
    lane: Optional[str] = None,
    severity: Optional[str] = None,
    task: Optional[str] = None,
):
    fs = list_findings(metadata_only=True)
    if lane:
        fs = [f for f in fs if (f.lane or "").upper() == lane.upper()]
    if severity:
        fs = [f for f in fs if (f.severity or "").upper() == severity.upper()]
    if task:
        fs = [f for f in fs if f.task == task]
    return {"findings": [asdict(f) for f in fs]}


@app.get("/api/v1/findings/{filename}")
def get_finding(filename: str):
    safe = re.sub(r"[^a-zA-Z0-9._\-]", "", filename)
    if safe != filename:
        raise HTTPException(status_code=400, detail="invalid filename")
    path = PROJECT_ROOT / "findings" / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="finding not found")
    f = parse_finding(path)
    return asdict(f)


@app.get("/api/v1/history")
def get_history(limit: Optional[int] = None):
    entries = parse_history_md()
    if limit:
        entries = entries[-limit:]
    return {"history": [asdict(e) for e in entries]}


@app.get("/api/v1/claims")
def get_claims():
    return {"claims": {k: asdict(v) for k, v in list_claims().items()}}


@app.get("/api/v1/signals")
def get_signals(since: Optional[str] = None):
    sigs = extract_signals_from_status()
    if since:
        since_dt = parse_utc(since)
        if since_dt:
            sigs = [s for s in sigs if (parse_utc(s.utc) or since_dt) >= since_dt]
    return {"signals": [asdict(s) for s in sigs]}


@app.get("/api/v1/lanes/{lane}")
def get_lane(lane: str):
    lane_upper = lane.upper()
    rows = parse_status_md()
    row = next((r for r in rows if r.lane.upper() == lane_upper), None)
    if not row:
        raise HTTPException(status_code=404, detail=f"lane {lane!r} not found")
    findings = [f for f in list_findings() if (f.lane or "").upper() == lane_upper]
    history = [h for h in parse_history_md() if lane_upper in h.lane.upper()]
    return {
        "row": asdict(row),
        "findings": [asdict(f) for f in findings],
        "recent_history": [asdict(h) for h in history[-10:]],
    }


@app.get("/api/v1/config")
def get_config():
    return {
        "heartbeat_interval_seconds": HEARTBEAT_INTERVAL_SECONDS,
        "file_watch_debounce_ms": FILE_WATCH_DEBOUNCE_MS,
        "poll_interval_seconds": POLL_INTERVAL_SECONDS,
        "sse_queue_capacity": SSE_QUEUE_CAPACITY,
        "max_findings_per_page": 100,
        "stale_threshold_seconds": STALE_THRESHOLD_SECONDS,
        "csrf_token": CSRF_TOKEN,  # served only to localhost — see middleware
    }


# --- SSE stream -------------------------------------------------------------


@app.get("/api/v1/events")
async def sse_events(request: Request):
    queue = await event_bus.subscribe()

    async def gen() -> AsyncIterator[dict[str, Any]]:
        try:
            # initial sync hint
            yield {
                "event": "sync",
                "data": json.dumps({"utc": UTC_NOW().isoformat().replace("+00:00", "Z")}),
            }
            heartbeat_task = asyncio.create_task(_heartbeat(queue))
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield msg
                except asyncio.TimeoutError:
                    continue
        finally:
            try:
                heartbeat_task.cancel()
            except Exception:
                pass
            await event_bus.unsubscribe(queue)

    return EventSourceResponse(gen())


async def _heartbeat(queue: asyncio.Queue) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
        try:
            queue.put_nowait(
                {
                    "event": "heartbeat",
                    "data": json.dumps(
                        {"utc": UTC_NOW().isoformat().replace("+00:00", "Z")}
                    ),
                }
            )
        except asyncio.QueueFull:
            pass


# --- Mutation endpoints (STUB — return {ok: true} without writing) ---------


@app.post("/api/v1/signal")
async def post_signal(req: Request):
    body = await _safe_json(req)
    for required in ("to_lane", "claim", "evidence"):
        if required not in body:
            raise HTTPException(status_code=422, detail=f"missing field: {required}")
    target_lane = body["to_lane"].upper()
    if target_lane not in LANES + ("ALL", "ORCH"):
        raise HTTPException(status_code=422, detail="invalid to_lane")
    if not str(body.get("evidence", "")).strip():
        raise HTTPException(
            status_code=422, detail="evidence required (RULE 4)"
        )
    # Find target lane row to attach signal to (defaults to first row matching)
    rows = parse_status_md()
    target_row = next((r for r in rows if r.lane.upper() == target_lane), None)
    if not target_row and target_lane not in ("ALL", "ORCH"):
        raise HTTPException(status_code=404, detail=f"lane {target_lane!r} not in STATUS.md")
    signal_token = format_canonical_signal(
        kind="SIGNAL",
        from_agent="orchestrator",
        to=target_lane,
        claim=str(body["claim"]).strip(),
        evidence_path=str(body["evidence"]).strip(),
    )
    if target_lane in ("ALL", "ORCH"):
        # No row to attach to; record in HISTORY instead
        utc = utc_now_iso()
        append_history_line(
            PROJECT_ROOT,
            f"{utc} | orchestrator | UI | SIGNAL | — | INFO {signal_token}",
        )
        await event_bus.broadcast(
            "signal-new", {"to": target_lane, "claim": body["claim"], "utc": utc}
        )
        return {"ok": True, "utc": utc, "destination": "HISTORY.md"}
    # Lane-targeted: CAS append to STATUS.md row Notes column
    mutator = mutator_status_signal(target_row.lane, signal_token)
    outcome = await cas_modify(PROJECT_ROOT / "STATUS.md", mutator)
    if not outcome.ok:
        return JSONResponse(
            status_code=409 if outcome.recoverable else 500,
            content={
                "ok": False,
                "error": outcome.error,
                "code": outcome.code,
                "recoverable": outcome.recoverable,
                "attempts": outcome.attempts,
            },
        )
    utc = utc_now_iso()
    await event_bus.broadcast(
        "signal-new",
        {"to": target_lane, "claim": body["claim"], "utc": utc, "token": signal_token},
    )
    return {"ok": True, "utc": utc, "attempts": outcome.attempts}


@app.post("/api/v1/reclaim")
async def post_reclaim(req: Request):
    body = await _safe_json(req)
    if "lane" not in body:
        raise HTTPException(status_code=422, detail="missing field: lane")
    target_lane = body["lane"].upper()
    if target_lane not in LANES:
        raise HTTPException(status_code=422, detail="invalid lane")
    rows = parse_status_md()
    target = next((r for r in rows if r.lane == target_lane), None)
    if not target:
        raise HTTPException(status_code=404, detail="lane not found in STATUS.md")
    if not body.get("force") and (target.staleness_seconds or 0) < STALE_THRESHOLD_SECONDS:
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "error": "lane is fresh (< 15 min); pass force=true to override",
                "code": "LANE_FRESH",
                "recoverable": False,
                "staleness_seconds": target.staleness_seconds,
            },
        )
    utc = utc_now_iso()
    # Determine retroactive-recovery candidate: working_task_id with done marker present
    action = "stale-reclaim"
    working_task = target.working_task_id
    if working_task and (PROJECT_ROOT / "claims" / working_task / "done").is_file():
        action = "retroactive-recovery"
    # Acquire locks on the files we touch (alphabetical order via file_locks)
    status_path = PROJECT_ROOT / "STATUS.md"
    tasks_path = PROJECT_ROOT / "TASKS.md"
    async with file_locks(status_path, tasks_path):
        outcome = await cas_modify(
            status_path,
            mutator_status_reclaim(target.lane, target.agent, utc),
        )
        if not outcome.ok:
            return JSONResponse(
                status_code=409 if outcome.recoverable else 500,
                content={
                    "ok": False, "error": outcome.error, "code": outcome.code,
                    "recoverable": outcome.recoverable, "attempts": outcome.attempts,
                },
            )
        if action == "stale-reclaim" and working_task:
            # Reset TASKS bracket back to [ ] for the working task
            tasks_outcome = await cas_modify(
                tasks_path, mutator_tasks_reset(working_task)
            )
            if not tasks_outcome.ok:
                # STATUS already mutated; surface partial-failure to operator
                return JSONResponse(
                    status_code=500,
                    content={
                        "ok": False,
                        "error": f"STATUS reclaimed but TASKS reset failed: {tasks_outcome.error}",
                        "code": "PARTIAL_FAILURE",
                        "recoverable": False,
                        "partial_state": {"status_reclaimed": True, "tasks_reset": False},
                    },
                )
            # Remove the claim directory (no done marker present)
            claim_dir = PROJECT_ROOT / "claims" / working_task
            if claim_dir.is_dir():
                try:
                    import shutil
                    shutil.rmtree(claim_dir)
                except OSError as exc:
                    log.warning("failed to remove %s: %s", claim_dir, exc)
        elif action == "retroactive-recovery":
            # Touch HISTORY.md note for the recovery
            append_history_line(
                PROJECT_ROOT,
                f"{utc} | orchestrator | UI | RECOVERY | {working_task} | recovered done-marker for {target.agent}",
            )
    # V2 fix (RR-1): emit canonical {lane, row, utc} after re-reading STATUS.md
    updated_rows = parse_status_md()
    updated_row = next((r for r in updated_rows if r.lane == target_lane), None)
    if updated_row:
        await event_bus.broadcast(
            "status-change",
            {"lane": target_lane, "row": asdict(updated_row), "utc": utc},
        )
    return {"ok": True, "action": action, "utc": utc, "working_task": working_task}


@app.post("/api/v1/challenge")
async def post_challenge(req: Request):
    body = await _safe_json(req)
    if "finding_filename" not in body:
        raise HTTPException(status_code=422, detail="missing field: finding_filename")
    finding_filename = body["finding_filename"]
    path = PROJECT_ROOT / "findings" / finding_filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="finding not found")
    finding_id = finding_filename.replace(".md", "")
    task_id = f"CHALLENGE-{finding_id}"
    description = body.get(
        "description", f"Construct the strongest argument that the consensus on {finding_filename} is wrong."
    )
    task_text = f"- [ ] [CHALLENGE] `{task_id}` — {description}"
    outcome = await cas_modify(
        PROJECT_ROOT / "TASKS.md",
        mutator_tasks_inject("CHALLENGE TASKS", task_text),
    )
    if not outcome.ok:
        return JSONResponse(
            status_code=409 if outcome.recoverable else 500,
            content={
                "ok": False, "error": outcome.error, "code": outcome.code,
                "recoverable": outcome.recoverable, "attempts": outcome.attempts,
            },
        )
    utc = utc_now_iso()
    await event_bus.broadcast(
        "task-change", {"task_id": task_id, "new_state": "open", "agent": None, "utc": utc}
    )
    return {"ok": True, "task_id": task_id, "utc": utc, "attempts": outcome.attempts}


@app.post("/api/v1/phase-flip")
async def post_phase_flip(req: Request):
    body = await _safe_json(req)
    for required in ("from", "to", "reason"):
        if required not in body:
            raise HTTPException(status_code=422, detail=f"missing field: {required}")
    from_phase = body["from"]
    to_phase = body["to"]
    if from_phase not in PHASES or to_phase not in PHASES:
        raise HTTPException(status_code=422, detail="invalid phase")
    current = current_phase()
    if from_phase != current and not body.get("force"):
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "error": f"phase mismatch: current={current}, requested_from={from_phase}",
                "code": "PHASE_MISMATCH",
                "recoverable": True,
                "current_phase": current,
            },
        )
    # Acquire phase-flip lock via mkdir (RULE 11)
    if not try_acquire_phase_flip_lock(PROJECT_ROOT, from_phase, to_phase):
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "error": "phase-flip lock already held; another worker may be flipping",
                "code": "CONCURRENT_FLIP",
                "recoverable": True,
                "lock_path": f".phase-flip-locks/{from_phase}-to-{to_phase}",
            },
        )
    # Append .mission-events (O_APPEND atomic)
    reason = str(body["reason"]).strip()
    event_line = append_phase_event(
        PROJECT_ROOT, from_phase, to_phase, "orchestrator-ui", reason
    )
    # CAS README.md Mission status section to reflect new phase
    readme_outcome = await cas_modify(
        PROJECT_ROOT / "README.md",
        mutator_mission_status(to_phase),
    )
    utc = utc_now_iso()
    if not readme_outcome.ok:
        log.warning(
            "phase-flip: .mission-events appended OK but README.md update failed: %s",
            readme_outcome.error,
        )
    await event_bus.broadcast(
        "phase-flip",
        {"from": from_phase, "to": to_phase, "by": "orchestrator-ui", "reason": reason, "utc": utc},
    )
    return {
        "ok": True,
        "event_line": event_line.rstrip("\n"),
        "readme_updated": readme_outcome.ok,
        "utc": utc,
    }


@app.post("/api/v1/mission-status")
async def post_mission_status(req: Request):
    body = await _safe_json(req)
    if "status" not in body:
        raise HTTPException(status_code=422, detail="missing field: status")
    new_status = body["status"]
    if new_status not in ("ACTIVE", "DRAINING", "COMPLETE", "IDLE"):
        raise HTTPException(status_code=422, detail="invalid status")
    outcome = await cas_modify(
        PROJECT_ROOT / "README.md",
        mutator_mission_status(new_status),
    )
    if not outcome.ok:
        return JSONResponse(
            status_code=409 if outcome.recoverable else 500,
            content={
                "ok": False, "error": outcome.error, "code": outcome.code,
                "recoverable": outcome.recoverable, "attempts": outcome.attempts,
            },
        )
    utc = utc_now_iso()
    # V2 fix (RR-1): mission-status changes use the mission-status SSE event,
    # NOT status-change (which is per-lane). FE EVENT_TYPES already lists this.
    await event_bus.broadcast(
        "mission-status", {"status": new_status, "utc": utc}
    )
    return {"ok": True, "utc": utc, "attempts": outcome.attempts}


@app.post("/api/v1/inject-task")
async def post_inject_task(req: Request):
    body = await _safe_json(req)
    for required in ("task_text", "section"):
        if required not in body:
            raise HTTPException(status_code=422, detail=f"missing field: {required}")
    task_text = str(body["task_text"]).rstrip("\n")
    section = str(body["section"])
    # Δ10/C10 — validate task_text bracket prefix format
    if not re.match(
        r"^- \[ \] \[[A-Z\-\d]+\] `[A-Za-z0-9\-→\.]+` — .+$", task_text
    ):
        raise HTTPException(
            status_code=422,
            detail="task_text must match: '- [ ] [LANE-X] `<task-id>` — <description>'",
        )
    # Soft-warn for non-ASCII task IDs (CH-2 5-source BLOCKING quorum)
    if "→" in task_text:
        log.warning("inject-task: task_text contains '→' (CH-2 — ASCII '-to-' preferred): %s", task_text)
    outcome = await cas_modify(
        PROJECT_ROOT / "TASKS.md",
        mutator_tasks_inject(section, task_text),
    )
    if not outcome.ok:
        return JSONResponse(
            status_code=409 if outcome.recoverable else 500,
            content={
                "ok": False, "error": outcome.error, "code": outcome.code,
                "recoverable": outcome.recoverable, "attempts": outcome.attempts,
            },
        )
    utc = utc_now_iso()
    # Extract task_id for the event
    m = re.search(r"`([^`]+)`", task_text)
    task_id = m.group(1) if m else None
    await event_bus.broadcast(
        "task-change", {"task_id": task_id, "new_state": "open", "utc": utc}
    )
    return {"ok": True, "utc": utc, "task_id": task_id, "attempts": outcome.attempts}


# --- Helpers ---------------------------------------------------------------


async def _safe_json(req: Request) -> dict[str, Any]:
    try:
        return await req.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"invalid JSON: {exc}")


def _asdict_with_string_severity(state: MissionState) -> dict[str, Any]:
    return {
        "lanes": [asdict(l) for l in state.lanes],
        "tasks": [asdict(t) for t in state.tasks],
        "findings": [asdict(f) for f in state.findings],
        "history": [asdict(h) for h in state.history],
        "phase": state.phase,
        "phase_events": [asdict(e) for e in state.phase_events],
        "claims": {k: asdict(v) for k, v in state.claims.items()},
        "signals": [asdict(s) for s in state.signals],
        "mission_status": state.mission_status,
        "fingerprint": state.fingerprint,
        "utc": state.utc,
    }


# --- Static file mount (FE assets) -----------------------------------------
# Static files come from the server's own ui/static/ directory (hardcoded),
# NOT from the mission directory. This separates server code from mission state.

SERVER_ROOT = Path(__file__).resolve().parent.parent  # one above ui/
static_dir = SERVER_ROOT / "ui" / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir), html=True), name="static")


# --- Lifecycle -------------------------------------------------------------


@app.on_event("startup")
async def on_startup() -> None:
    log.info("Megalodon UI starting on http://%s:%d", HOST, PORT)
    log.info("Project root: %s", PROJECT_ROOT)
    log.info("CSRF token (rotates per restart): %s", CSRF_TOKEN)
    await watcher.start()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await watcher.stop()


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Megalodon orchestrator-console server")
    parser.add_argument(
        "--mission-dir",
        default=os.environ.get("MEGALODON_MISSION_DIR"),
        help="Path to the Megalodon project root (mission directory). Defaults to the parent of ui/.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MEGALODON_UI_PORT", "8080")),
        help="Port to bind (default 8080)",
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Host to bind (default 127.0.0.1)"
    )
    args = parser.parse_args()
    global PROJECT_ROOT, PORT, HOST
    if args.mission_dir:
        PROJECT_ROOT = Path(args.mission_dir).resolve()
    PORT = args.port
    HOST = args.host
    log.info("Megalodon UI bind: http://%s:%d", HOST, PORT)
    log.info("Mission dir: %s", PROJECT_ROOT)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
