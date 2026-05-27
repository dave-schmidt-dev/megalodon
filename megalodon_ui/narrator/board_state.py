"""Deterministic per-lane board-state assembly for the narrator summary board.

Splits into a PURE assembler (no I/O — fully unit-testable) and a thin ASYNC
wrapper that handles transcript I/O. The narrator phrase is NOT produced here.

The caller flow is:
  1. ``build_lane_rows`` (async) — reads each Claude lane's transcript via
     ``asyncio.to_thread``, then delegates to the pure assembler.
  2. ``assemble_lane_rows`` (pure) — constructs one ``LaneRow`` per lane from
     already-read digests + task records.

The scheduler owns flipping ``narrator_ok`` to True after a successful narrate
call; board_state always emits ``narrator_ok=False``.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from megalodon_ui.narrator.digest import (
    SessionDigest,
    parse_session,
    render_for_prompt,
)

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LaneRow dataclass
# ---------------------------------------------------------------------------


@dataclass
class LaneRow:
    """Board state for one lane, as consumed by the scheduler and endpoints.

    ``digest_text`` is internal (NOT included in ``to_dict()``): the scheduler
    passes it to narrate() to produce the phrase.

    Args:
        lane: Short lane code (e.g. ``"A"``).
        lane_name: Long lane name (e.g. ``"AUDIT"``).
        state: Lane state string (e.g. ``"claimed"``).
        last: Latest DONE task as ``{"task_id": ..., "desc": ..., "phrase": None}``,
            or None.  ``phrase`` is always None here; the scheduler fills it with
            an advisory "just-completed" narrative (OQ1) — the deterministic
            ``desc`` remains the fallback.
        now: Latest CLAIMED task as ``{"task_id": ..., "desc": ..., "phrase": None}``,
            or None.  ``phrase`` is always None here; the scheduler fills it.
        goal: Human-readable goal: now.desc, else last.desc, else the lane role.
        tokens: Total tokens from the session digest, or None when no usable
            transcript is available (non-claude harness, missing session_id, etc.).
        narrator_ok: Always False from board_state; the scheduler sets it True
            iff narrate() succeeds.
        digest_text: Internal only — the rendered transcript text passed to
            narrate().  Omitted from ``to_dict()``.
    """

    lane: str
    lane_name: str
    state: str
    last: dict[str, Any] | None
    now: dict[str, Any] | None
    goal: str
    tokens: int | None
    narrator_ok: bool
    # Governance PROVENANCE of the lane's live process (Task 2.5). True only when
    # the running process was spawned/respawned UNDER the governor (per the
    # spawner's spawn-time marker). False == ``ungoverned`` — a reattached
    # pre-governor process that must be respawned to come under governance. This
    # is provenance, NOT the P3.2 deny-loop ``governor-blocked`` alarm.
    # board.js renders this as the UNGOVERNED indicator (§3.3) — an amber chip
    # shown alongside (orthogonal to) the state pill, but only for a RUNNING lane
    # whose ``governed`` is strictly False.
    # Defaults False (fail toward ungoverned): an absent/non-running lane was NOT
    # spawned under the governor, so it must not claim governance — consistent
    # with ``LaneSession.governed``'s own False default. (The board's running-state
    # guard prevents this default from false-flagging idle lanes.)
    governed: bool = False
    # LIVENESS of the lane's live process (Wave-3 CRITICAL fix). Derived from the
    # live ``LaneSession`` (``running`` / ``exited_rc``) by ``_derive_liveness``:
    #   "running" — session.running True and exited_rc is None (alive).
    #   "exited"  — exited_rc == 0 (clean exit).
    #   "dead"    — exited_rc not in (None, 0) (crashed / nonzero exit).
    #   "unknown" — no live session for the lane / attributes unreadable.
    # Distinct from ``governed`` (provenance) and from the deny-loop ``governor-
    # blocked`` alarm. Defaults "unknown" (fail toward not-asserting-health): a
    # lane with no supplied session has no positive proof it is alive. The async
    # wrapper supplies real per-lane values from the live sessions; the board's
    # DEAD pill renders only on the strict "dead" value.
    liveness: str = "unknown"
    digest_text: str | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        """Return the public JSON-serializable payload (excludes ``digest_text``).

        Returns:
            Dict with keys: lane, lane_name, state, last, now, goal, tokens,
            narrator_ok, governed, liveness.
        """
        return {
            "lane": self.lane,
            "lane_name": self.lane_name,
            "state": self.state,
            "last": self.last,
            "now": self.now,
            "goal": self.goal,
            "tokens": self.tokens,
            "narrator_ok": self.narrator_ok,
            "governed": self.governed,
            "liveness": self.liveness,
        }


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _parse_utc(raw: str | None) -> datetime | None:
    """Parse a UTC timestamp string to an aware datetime, or return None.

    Accepts ISO-8601 with trailing Z (e.g. ``"2026-05-01T10:00:00Z"``) or
    any format ``datetime.fromisoformat`` handles with a UTC offset.
    Returns None on any parse error or for None input.

    Args:
        raw: Raw timestamp string from the task record, or None.

    Returns:
        An aware ``datetime`` or None if unparseable.
    """
    if raw is None:
        return None
    try:
        # Replace trailing Z with +00:00 for fromisoformat compatibility.
        normalized = raw.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        # Only return if timezone-aware; tz-naive datetimes cannot be compared
        # with tz-aware ones (would raise TypeError at comparison time).
        return dt if dt.tzinfo is not None else None
    except (ValueError, AttributeError):
        return None


def _derive_liveness(session: Any) -> str:
    """Map a live ``LaneSession`` (or None) to a board liveness string.

    Reads ``session.running`` / ``session.exited_rc`` DEFENSIVELY — a fake or
    partial session object in tests may lack either attribute, in which case we
    fail toward ``"unknown"`` rather than asserting a health we cannot back.

    Mapping (mirrors ``LaneRow.liveness`` doc):
      * ``"running"`` — ``running`` truthy AND ``exited_rc`` is None.
      * ``"exited"``  — ``exited_rc == 0`` (clean exit).
      * ``"dead"``    — ``exited_rc`` not in ``(None, 0)`` (crashed / nonzero).
      * ``"unknown"`` — no session, or attributes are missing / unreadable.

    Args:
        session: The lane's live ``LaneSession`` or None.

    Returns:
        One of ``"running" | "exited" | "dead" | "unknown"``.
    """
    if session is None:
        return "unknown"
    # Use a sentinel so a *present* exited_rc=None is distinguished from a lane
    # whose session object simply lacks the attribute (→ unknown).
    _MISSING = object()
    rc = getattr(session, "exited_rc", _MISSING)
    running = getattr(session, "running", _MISSING)
    if rc is _MISSING and running is _MISSING:
        return "unknown"
    if rc is not _MISSING and rc is not None:
        try:
            return "exited" if int(rc) == 0 else "dead"
        except (TypeError, ValueError):
            return "unknown"
    # rc is None (or missing); fall back to the running flag.
    if running is _MISSING:
        return "unknown"
    return "running" if running else "unknown"


def _pick_latest(
    tasks: list[dict[str, Any]],
    doc_order: dict[str, int],
) -> dict[str, Any] | None:
    """Return the "latest" task from a list, deterministically.

    Primary sort key: ``claim_utc`` parsed to an aware datetime (descending).
    Fallback (tied, unparseable, or tz-naive ``claim_utc``): document-order
    index from ``doc_order`` (higher index = later = wins).

    ``_parse_utc`` normalizes every value to either a tz-aware ``datetime`` or
    None (tz-naive and malformed strings both become None). The sort key's
    ``has_ts`` flag therefore guarantees the ``datetime`` element is only ever
    compared against another tz-aware ``datetime``, so the comparison can never
    raise — no try/except is needed. A task with a parseable timestamp always
    ranks above one without; within each group, ``doc_order`` breaks ties.

    If two tasks are equal on all three key components (both unparseable
    ``claim_utc`` AND both absent from ``doc_order``), the first in input order
    wins — ``max`` returns the first maximal element, so the result is
    deterministic within a call.

    Args:
        tasks: List of task records (each has at least ``task_id``).
        doc_order: Mapping of task_id to 0-based document position.

    Returns:
        The selected task record, or None if ``tasks`` is empty.
    """
    if not tasks:
        return None
    if len(tasks) == 1:
        return tasks[0]

    def _sort_key(task: dict[str, Any]) -> tuple[int, datetime, int]:
        """Return (has_ts, ts_or_min, doc_idx) — higher key = more recent/preferred; used with max()."""
        ts = _parse_utc(task.get("claim_utc"))
        idx = doc_order.get(task.get("task_id", ""), 0)
        # When ts is None (has_ts=0) the placeholder datetime is never compared
        # against a real one: the has_ts flag separates the two groups first.
        return (
            1 if ts is not None else 0,
            ts or datetime.min.replace(tzinfo=timezone.utc),
            idx,
        )

    return max(tasks, key=_sort_key)


# ---------------------------------------------------------------------------
# STATUS.md fallback helpers
# ---------------------------------------------------------------------------


def _status_to_board_state(status_state: str) -> tuple[str, str | None]:
    """Map a STATUS.md lane-state string to a board state + optional task id.

    The board pill only understands a fixed vocabulary (``blocked`` / ``claimed``
    drive non-IDLE treatment; ``open`` is IDLE). STATUS.md lane states are
    richer (``"working: P1-B"``, ``"initialized"``, ``"unclaimed"``, ...). This
    collapses them so a lane reporting live activity in STATUS.md is not shown
    as IDLE when TASKS.md has no row backing it (the INIT/pre-PLAN case).

    Returns ``(board_state, task_id)`` where ``board_state`` is one of
    ``"blocked" | "claimed" | "open"`` and ``task_id`` is parsed from a
    ``"working: <id>"`` form when present (else None).
    """
    s = (status_state or "").strip().lower()
    if s.startswith("blocked"):
        return ("blocked", None)
    if s.startswith("working"):
        # "working: P1-B" / "working:P1-C" → task id after the colon.
        task_id = status_state.split(":", 1)[1].strip() if ":" in status_state else None
        return ("claimed", task_id or None)
    if s == "initialized":
        return ("claimed", None)
    # unclaimed, idle, awaiting OPERATOR-ACK, unknown → leave IDLE.
    return ("open", None)


def _index_status_rows(
    status_rows: list[dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    """Index STATUS.md rows by a lowercased lane key for O(1) lookup.

    The ``lane`` column may be a long name (``AUDIT``) or a ``LANE-A`` form
    depending on the mission's status table; callers resolve against both.
    """
    index: dict[str, dict[str, Any]] = {}
    for row in status_rows or []:
        key = str(row.get("lane", "")).strip().lower()
        if key:
            index[key] = row
    return index


def _lookup_status_row(
    cfg: Any, status_index: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """Find the STATUS.md row for a lane config, trying name / short / LANE-<short>."""
    for cand in (getattr(cfg, "name", None), getattr(cfg, "short", None)):
        if cand:
            row = status_index.get(str(cand).strip().lower())
            if row is not None:
                return row
    short = getattr(cfg, "short", None)
    if short:
        return status_index.get(f"lane-{str(short).strip().lower()}")
    return None


# Coordination-signal notes routed between lanes look like ``[SIG ...]`` or
# ``SIG-FROM-LANE-X: ...``. They are routing chatter, NOT a description of what
# the lane is working ON — surfacing them as the Goal/Now line (I3) is wrong.
_SIGNAL_NOTE_RE = re.compile(r"(?:^\s*\[?\s*SIG\b|\bSIG-FROM-LANE-)", re.IGNORECASE)


def _is_signal_note(note: str) -> bool:
    """True when a STATUS.md note is a coordination-signal routing string (I3).

    These (``[SIG ...]`` / ``SIG-FROM-LANE-D: ...``) are inter-lane routing
    chatter, not a human-readable description of the lane's current work, so
    they must never become the Goal line.
    """
    return bool(_SIGNAL_NOTE_RE.search(note or ""))


def _resolve_task_desc(
    task_id: str | None, all_tasks: list[dict[str, Any]]
) -> str | None:
    """Return the TASKS.md description for ``task_id``, or None if not found.

    Used to turn a STATUS.md ``working:<task_id>`` marker into a clean,
    human-readable Goal/Now line sourced from the canonical task record rather
    than the raw STATUS note (which may be a coordination signal — I3).
    """
    if not task_id:
        return None
    for t in all_tasks:
        if t.get("task_id") == task_id:
            desc = str(t.get("description", "")).strip()
            return desc or None
    return None


# ---------------------------------------------------------------------------
# Pure assembler
# ---------------------------------------------------------------------------


def assemble_lane_rows(
    tasks_fe: dict[str, Any],
    lane_cfgs: list[Any],
    digests: dict[str, SessionDigest | None],
    doc_order: dict[str, int],
    status_rows: list[dict[str, Any]] | None = None,
    governed_by_lane: dict[str, bool] | None = None,
    liveness_by_lane: dict[str, str] | None = None,
) -> dict[str, LaneRow]:
    """Assemble one ``LaneRow`` per lane — pure, no I/O.

    Lane state is derived entirely from task ``claim_state`` — this assembler
    does not touch session objects (the async wrapper resolves transcripts and
    passes the read digests via ``digests``).

    Args:
        tasks_fe: Output of ``parse_tasks_fe_shape()``: ``{"phases": {...}, "cross": [...]}``.
        lane_cfgs: List of ``LaneConfig`` objects from the mission config.
        digests: Mapping of lane short code to already-read ``SessionDigest`` or
            None (when no usable transcript).
        doc_order: Mapping of task_id to document position (0-based); used as
            tiebreaker in ``_pick_latest``.

    Returns:
        Dict mapping lane short code to its assembled ``LaneRow``.
    """
    # Flatten all tasks across phases + cross into a single list.
    all_tasks: list[dict[str, Any]] = []
    for phase_tasks in tasks_fe.get("phases", {}).values():
        all_tasks.extend(phase_tasks)
    all_tasks.extend(tasks_fe.get("cross", []))

    rows: dict[str, LaneRow] = {}

    # STATUS.md fallback index (empty when no status_rows supplied).
    status_index = _index_status_rows(status_rows)

    # Governance provenance per lane (Task 2.5). Absent ⇒ default False (fail
    # toward ungoverned): a lane with no supplied provenance has no positive
    # proof it was spawned under the governor. The async wrapper supplies real
    # per-lane values from the live sessions.
    governed_map = governed_by_lane or {}

    # Liveness per lane (Wave-3 CRITICAL fix). Absent ⇒ default "unknown" (fail
    # toward not-asserting-health): a lane with no supplied liveness has no
    # positive proof it is alive. The async wrapper supplies real per-lane values
    # derived from the live sessions via ``_derive_liveness``.
    liveness_map = liveness_by_lane or {}

    for cfg in lane_cfgs:
        short = cfg.short
        if not short:
            continue

        # Filter tasks that belong to this lane (keyed by long name in tasks_fe).
        lane_tasks = [t for t in all_tasks if t.get("lane") == cfg.name]
        done_tasks = [t for t in lane_tasks if t.get("claim_state") == "done"]
        claimed_tasks = [t for t in lane_tasks if t.get("claim_state") == "claimed"]
        blocked_tasks = [t for t in lane_tasks if t.get("claim_state") == "blocked"]

        latest_done = _pick_latest(done_tasks, doc_order)
        latest_claimed = _pick_latest(claimed_tasks, doc_order)

        last: dict[str, Any] | None = None
        if latest_done is not None:
            last = {
                "task_id": latest_done["task_id"],
                "desc": latest_done.get("description", ""),
                "phrase": None,  # scheduler fills this (OQ1); desc is the fallback
            }

        now: dict[str, Any] | None = None
        if latest_claimed is not None:
            now = {
                "task_id": latest_claimed["task_id"],
                "desc": latest_claimed.get("description", ""),
                "phrase": None,
            }

        # Default state before any STATUS or task-derived override.  Every path
        # below either assigns state explicitly or relies on this safe baseline.
        state = "open"

        # ------------------------------------------------------------------
        # CURRENT-ACTIVITY PRECEDENCE (B1/B2/I3 fix).
        #
        # STATUS.md ``working:<task_id>`` is the AUTHORITATIVE source of what a
        # lane is doing RIGHT NOW. A prior DONE task must never displace it.
        # Precedence for state/now/goal: live STATUS working: > claimed task >
        # last done > role. The live ``working:`` marker fires whenever STATUS
        # reports it — independent of any done/claimed task rows (B1: a lane
        # with a prior done P3-A but STATUS ``working: P4-A`` must reflect P4-A).
        #
        # The Now/Goal text comes from the TASKS.md DESCRIPTION resolved by
        # task_id (B2: basic progress must not require a live LLM narrator). The
        # raw STATUS note is used only when the task_id is unknown, and a
        # coordination-signal note (``[SIG ...]``) is never used for the Goal
        # line (I3 — that text is routing chatter, not a work description).
        # ------------------------------------------------------------------
        # STATUS lifecycle flags.
        #
        # ``status_working_fired``  — STATUS ``working:<id>`` is authoritative
        #   for CURRENT activity; overrides any prior done/claimed task rows.
        # ``status_idle_fired``     — STATUS explicitly says ``idle`` (agent
        #   consciously paused).  Authoritative over a stale TASKS.md ``claimed``
        #   row: the lane must render IDLE.  Does NOT fire for ``unclaimed`` /
        #   ``awaiting-OPERATOR-ACK`` / unknown — those mean "no agent yet" and
        #   TASKS.md still wins for state/now.  (HIGH fix.)
        # ``status_gap_fill_fired`` — Non-working, non-idle STATUS state with
        #   no task rows (INIT / pre-PLAN / BLOCKED before any task record);
        #   used to carry the STATUS note into the goal (MEDIUM fix).
        # ------------------------------------------------------------------
        status_working_fired = False
        status_idle_fired = False
        status_gap_fill_fired = False
        status_gap_fill_note = ""
        if status_index:
            status_row = _lookup_status_row(cfg, status_index)
            if status_row is not None:
                status_state = str(status_row.get("state", ""))
                board_state, task_id = _status_to_board_state(status_state)
                raw_note = str(status_row.get("notes", "")).strip()
                # Prefer the canonical task description; fall back to a CLEAN
                # status note (never a signal-routing note — I3).
                resolved_desc = _resolve_task_desc(task_id, all_tasks)
                clean_note = "" if _is_signal_note(raw_note) else raw_note
                is_working = status_state.strip().lower().startswith("working")
                if is_working and (task_id or resolved_desc or clean_note):
                    # LIVE working: wins for current activity (B1) — it overrides
                    # any prior done/claimed-derived now, regardless of task rows.
                    status_working_fired = True
                    desc = resolved_desc or clean_note or status_state.strip()
                    now = {"task_id": task_id, "desc": desc, "phrase": None}
                    # "claimed" → RUNNING pill — UNLESS a blocked task row out-
                    # ranks it (blocked is a higher-severity alarm that the live
                    # working marker must not mask). The now/goal still reflect
                    # the live work; only the pill defers to blocked.
                    state = "blocked" if blocked_tasks else board_state
                elif board_state == "open" and status_state.strip().lower() == "idle":
                    # STATUS explicitly says ``idle`` — the agent is assigned and
                    # has consciously returned to an idle state.  This is
                    # authoritative over any stale TASKS.md ``claimed`` row: the
                    # agent stopped working on it, so the lane must render IDLE.
                    # NOTE: ``unclaimed`` / ``awaiting-OPERATOR-ACK`` / unknown all
                    # also map to board_state "open" but are NOT authoritative over
                    # a genuine claimed task — they mean "no agent assigned yet"
                    # rather than "agent explicitly paused", so TASKS.md still wins.
                    # (HIGH fix: STATUS idle beats stale claimed.)
                    status_idle_fired = True
                elif (
                    board_state != "open"
                    and not done_tasks
                    and not claimed_tasks
                    and not blocked_tasks
                ):
                    # INIT / pre-PLAN gap-fill (initialized, no task rows): reflect
                    # live activity rather than IDLE.  Record the clean note so the
                    # goal block below can surface it (MEDIUM fix for BLOCKED lanes).
                    status_gap_fill_fired = True
                    status_gap_fill_note = clean_note or cfg.role or ""
                    state = board_state
                    desc = resolved_desc or clean_note or status_state.strip()
                    now = {"task_id": task_id, "desc": desc, "phrase": None}

        # When STATUS explicitly says idle, it is authoritative: clear the now
        # that was speculatively set from claimed tasks above so the FE sees
        # now=None (IDLE signal) rather than a stale claimed task as the current
        # activity.  (HIGH fix — part 2.)
        if status_idle_fired:
            now = None

        # Goal: live working desc > claimed/now desc > last done desc > role.
        # When the live STATUS working: marker fired, its now.desc IS the goal —
        # resolved from the task description (I3: not the raw signal note).
        #
        # Fix MEDIUM: when the gap-fill fired (e.g. a BLOCKED lane with no task
        # rows), use the STATUS note (or role) — not last.desc — as the goal so
        # the BLOCKED lane always has a meaningful, non-empty goal string.
        #
        # Fix LOW/HIGH: when STATUS explicitly said idle, stale claimed/done
        # task descriptions must not become the goal — use the lane role so the
        # goal is still informative but not misleadingly stale.
        if status_working_fired and now is not None:
            goal = now["desc"]
        elif status_gap_fill_fired:
            goal = status_gap_fill_note
        elif status_idle_fired:
            # STATUS idle is authoritative: role is the goal (not stale task desc).
            goal = cfg.role or ""
        elif latest_claimed is not None:
            goal = latest_claimed.get("description", "") or (cfg.role or "")
        elif last is not None:
            goal = last["desc"]
        else:
            goal = cfg.role or ""

        # Derive state label from the highest-priority active task — UNLESS the
        # live STATUS working: marker already set RUNNING (which wins for the
        # "what is it doing now" question per the precedence above).
        # Precedence: blocked > claimed > done > open.
        # When STATUS explicitly said idle, it is authoritative over a stale
        # TASKS.md claimed row — suppress claimed promotion (HIGH fix).
        if not status_working_fired:
            if blocked_tasks:
                state = "blocked"
            elif claimed_tasks and not status_idle_fired:
                state = "claimed"
            elif done_tasks:
                state = "done"
            # else: state remains "open" (baseline set above), which is correct for
            # lanes with no task rows, no gap-fill, and no idle override.

        # Digest + tokens.
        digest = digests.get(short)
        tokens: int | None = digest.total_tokens if digest is not None else None

        rows[short] = LaneRow(
            lane=short,
            lane_name=cfg.name,
            state=state,
            last=last,
            now=now,
            goal=goal,
            tokens=tokens,
            narrator_ok=False,
            governed=governed_map.get(short, False),
            liveness=liveness_map.get(short, "unknown"),
            digest_text=None,  # set by build_lane_rows after this call
        )

    return rows


# ---------------------------------------------------------------------------
# Async wrapper
# ---------------------------------------------------------------------------


def _capture_doc_order(mission_dir: Path) -> dict[str, int]:
    """Read TASKS.md and return a mapping of task_id to line position.

    Scans for lines matching the task pattern and records the first occurrence
    position (0-based) of each task_id.  Lines that don't look like task
    records are skipped.  Returns an empty dict if the file is absent.

    Args:
        mission_dir: Path to the mission directory containing TASKS.md.

    Returns:
        Dict mapping task_id strings to 0-based document position.
    """
    path = mission_dir / "TASKS.md"
    if not path.is_file():
        return {}
    # Defensive read (mirrors digest.parse_session): a non-UTF8 byte or a
    # transient OSError must NOT crash the narrator tick → scheduler loop, which
    # would take the narrator permanently dark. Degrade to "no doc order".
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    order: dict[str, int] = {}
    idx = 0
    for line in text.splitlines():
        # Look for backtick-wrapped task IDs: `TASK-ID`
        stripped = line.strip()
        if stripped.startswith("-") and "`" in stripped:
            start = stripped.find("`")
            end = stripped.find("`", start + 1)
            if start != -1 and end != -1 and end > start:
                task_id = stripped[start + 1 : end]
                if task_id and task_id not in order:
                    order[task_id] = idx
                    idx += 1
    return order


# ---------------------------------------------------------------------------
# Session-id self-heal (agent-id correlation)
#
# The spawner's time-window session-id discovery fails for live_repl lanes: the
# Claude transcript is created only after the initial prompt is delivered (well
# after the discovery poll window), and all lanes share one ``~/.claude/projects``
# dir, so the "exactly one new file" heuristic is ambiguous. The result is
# session_id=None for every lane → no transcript digest → the narrator never
# narrates. We recover each lane's session_id by matching its STATUS.md agent-id
# (baked uniquely into each lane's launch prompt) to the transcript that opens
# with that identity.
# ---------------------------------------------------------------------------

_AGENT_ID_RE = re.compile(rb"agent-[0-9a-f]{4}")


def _owning_agent_id(jsonl_path: Path, *, max_lines: int = 400) -> str | None:
    """Return the FIRST ``agent-XXXX`` id appearing in a transcript.

    A lane's own launch identity is the first agent-id to appear (it is baked
    into the launch prompt that opens the session); cross-references to other
    lanes only appear in later turns. Reads at most ``max_lines`` lines, stopping
    at the first match, so it stays cheap on large transcripts. Returns None on
    any read error or when no id appears in the scanned window.
    """
    try:
        with jsonl_path.open("rb") as fh:
            for _ in range(max_lines):
                line = fh.readline()
                if not line:
                    break
                m = _AGENT_ID_RE.search(line)
                if m:
                    return m.group(0).decode("ascii")
    except OSError:
        return None
    return None


def _resolve_session_ids_by_agent(
    log_dir: Path, agent_by_lane: dict[str, str]
) -> dict[str, str]:
    """Map lane short code → transcript stem (== session_id) via agent-id.

    Scans ``*.jsonl`` in ``log_dir`` newest-mtime-first; the first (newest)
    transcript owned by each wanted agent wins. Pure synchronous I/O — call via
    ``asyncio.to_thread``. Returns a possibly-partial ``{short: stem}`` (lanes
    with no matching transcript are omitted). Never raises: a missing/unreadable
    dir yields ``{}``.
    """
    try:
        if not log_dir.is_dir():
            return {}
        transcripts = sorted(
            log_dir.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return {}

    wanted = set(agent_by_lane.values())
    stem_by_agent: dict[str, str] = {}
    for p in transcripts:
        if wanted.issubset(stem_by_agent.keys()):
            break  # every wanted agent resolved (newest already won)
        aid = _owning_agent_id(p)
        if aid and aid in wanted and aid not in stem_by_agent:
            stem_by_agent[aid] = p.stem

    return {
        short: stem_by_agent[aid]
        for short, aid in agent_by_lane.items()
        if aid in stem_by_agent
    }


def _persist_session_id(mission_dir: Path, lane_short: str, session_id: str) -> None:
    """Persist a recovered session_id to ``.fleet/<lane>.session.txt`` (CV-5).

    Mirrors the spawner's write so a later server start can ``--resume`` without
    re-discovery. Best-effort: any OSError is swallowed (the in-memory id is what
    the current process needs; persistence is an optimisation).
    """
    try:
        txt = mission_dir / ".fleet" / f"{lane_short}.session.txt"
        txt.parent.mkdir(parents=True, exist_ok=True)
        txt.write_text(session_id + "\n", encoding="utf-8")
        txt.chmod(0o644)
    except OSError:
        _log.debug("board_state: could not persist session id for lane %s", lane_short)


async def _recover_missing_session_ids(
    mission_dir: Path,
    sessions: dict[str, Any],
    adapter_resolver: Callable[[str], Any],
    lane_cfgs: list[Any],
    status_rows: list[dict[str, Any]] | None,
) -> None:
    """Fill in session_id for claude lanes the spawner left unresolved.

    Mutates ``sessions[short].session_id`` in place (and persists) for any lane
    recovered via agent-id correlation. No-op when nothing is recoverable
    (no status rows, every lane already resolved, or unclaimed agents).
    """
    status_index = _index_status_rows(status_rows)
    if not status_index:
        return

    # Group eligible lanes by transcript dir. Lanes share a cwd in practice, but
    # resolving per-dir stays correct if that ever changes.
    by_dir: dict[Path, dict[str, str]] = {}
    for cfg in lane_cfgs:
        short = getattr(cfg, "short", None)
        if not short or getattr(cfg.harness, "cli", None) != "claude":
            continue
        session = sessions.get(short)
        if session is None or session.session_id is not None:
            continue
        status_row = _lookup_status_row(cfg, status_index)
        agent_id = str(status_row.get("agent", "")).strip() if status_row else ""
        if not agent_id or agent_id.lower() == "unclaimed":
            continue
        try:
            log_dir = adapter_resolver("claude").session_log_dir(session.cwd)
        except Exception:
            log_dir = None
        if log_dir is None:
            continue
        by_dir.setdefault(Path(log_dir), {})[short] = agent_id

    for log_dir, agent_by_lane in by_dir.items():
        resolved = await asyncio.to_thread(
            _resolve_session_ids_by_agent, log_dir, agent_by_lane
        )
        for short, sid in resolved.items():
            session = sessions.get(short)
            if session is not None:
                session.session_id = sid
                _persist_session_id(mission_dir, short, sid)
                _log.info(
                    "board_state: recovered session_id %s for lane %s via agent-id %s",
                    sid,
                    short,
                    agent_by_lane[short],
                )


async def build_lane_rows(
    mission_dir: Path,
    tasks_fe: dict[str, Any],
    sessions: dict[str, Any],
    adapter_resolver: Callable[[str], Any],
    lane_cfgs: list[Any],
    status_rows: list[dict[str, Any]] | None = None,
) -> dict[str, LaneRow]:
    """Async wrapper: read transcripts off-loop, then assemble board rows.

    For each lane whose harness is ``"claude"`` AND whose session has a non-None
    ``session_id``, the transcript is read via ``asyncio.to_thread(parse_session,
    path)`` so a large file cannot stall the event loop. All other lanes receive
    a deterministic-only row (tokens=None, digest_text=None, narrator_ok=False).

    Args:
        mission_dir: Absolute path to the mission directory.
        tasks_fe: Output of ``parse_tasks_fe_shape()``.
        sessions: Mapping of lane short code to ``LaneSession`` (has
            ``session_id: str | None`` and ``cwd: Path``).
        adapter_resolver: Callable that maps a harness CLI name to its adapter
            instance (e.g. ``FleetSpawner.adapter_resolver``).
        lane_cfgs: List of ``LaneConfig`` objects from the mission config.
        status_rows: Optional STATUS.md rows (``server.parse_status()`` shape).
            When supplied, a lane with no TASKS.md row falls back to its
            STATUS.md state so live activity is not rendered as IDLE.

    Returns:
        Dict mapping lane short code to its assembled ``LaneRow``.
    """
    # Capture document order from TASKS.md once (pure synchronous read).
    doc_order = _capture_doc_order(mission_dir)

    # Self-heal session ids the spawner failed to discover (live_repl + shared
    # projects dir) so the lanes below become narratable without a respawn.
    await _recover_missing_session_ids(
        mission_dir, sessions, adapter_resolver, lane_cfgs, status_rows
    )

    # Resolve digests per lane — off-loop reads for Claude lanes with sessions.
    digests: dict[str, SessionDigest | None] = {}

    for cfg in lane_cfgs:
        short = cfg.short
        if not short:
            continue

        cli = cfg.harness.cli
        session = sessions.get(short)
        session_id = session.session_id if session is not None else None

        # CR-5/CR-6: only claude + non-None session_id warrant a transcript read.
        if cli != "claude" or session_id is None:
            digests[short] = None
            continue

        try:
            adapter = adapter_resolver(cli)
            # session is guaranteed non-None here: a None session yields
            # session_id=None, which already `continue`d above.
            path = adapter.session_log_path(session.cwd, session_id)
            if path is None:
                digests[short] = None
                continue
            # CR-7: off-loop read so a large transcript can't stall the event loop.
            digest = await asyncio.to_thread(parse_session, path)
            digests[short] = digest
        except Exception:
            _log.exception(
                "board_state: failed to read transcript for lane %s (session %s)",
                short,
                session_id,
            )
            digests[short] = None

    # Governance provenance per lane (Task 2.5): surface each live session's
    # `governed` flag so an ungoverned (pre-governor) reattached lane is visibly
    # distinct in the board data. Lanes with no live process / no positive proof
    # of governance default to False (fail toward ungoverned — never affirm
    # governance we cannot back). Read defensively — a fake/partial session
    # object in tests may lack the attribute.
    governed_by_lane = {
        cfg.short: bool(getattr(sessions.get(cfg.short), "governed", False))
        for cfg in lane_cfgs
        if getattr(cfg, "short", None)
    }

    # Liveness per lane (Wave-3 CRITICAL fix): surface each live session's
    # running/exited state so a dead (crashed) lane is visible immediately rather
    # than waiting ~15 min for the STATUS-stale heuristic. Mirrors how
    # ``governed`` is plumbed — read defensively via ``_derive_liveness`` so a
    # fake/partial session in tests degrades to "unknown" instead of raising.
    liveness_by_lane = {
        cfg.short: _derive_liveness(sessions.get(cfg.short))
        for cfg in lane_cfgs
        if getattr(cfg, "short", None)
    }

    rows = assemble_lane_rows(
        tasks_fe,
        lane_cfgs,
        digests,
        doc_order,
        status_rows=status_rows,
        governed_by_lane=governed_by_lane,
        liveness_by_lane=liveness_by_lane,
    )

    # Attach digest_text to rows that have a digest (internal, for the scheduler).
    for cfg in lane_cfgs:
        short = cfg.short
        if not short or short not in rows:
            continue
        digest = digests.get(short)
        if digest is not None:
            rows[short].digest_text = render_for_prompt(digest)

    return rows
