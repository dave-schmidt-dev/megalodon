"""Megalodon protocol primitives (pure functions, stdlib-only).

This module exposes the protocol-level operations the unit-test suite in
`ui/tests/unit/test_protocol_primitives.py` imports. No FastAPI / uvicorn /
sse_starlette imports here — this module must be importable in a pure-stdlib
Python environment (per BACKEND P2.5-C plan-v2 Δ1.1).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
import re
import shutil

from .mission_config.default_v9_0_shape import synthesize as _synthesize_default_config
from .mission_config.regex_builder import build_lane_short_charclass
from .mission_config.schema import MissionConfig

# Build default config once at module load from the v9.0 back-compat shape.
# This is always filesystem-safe: synthesize() falls back to datetime.now()
# when neither MISSION.md nor .mission-events is present.
_DEFAULT_CONFIG = _synthesize_default_config(Path.cwd())

# LANE_LONG_TO_SHORT maps long lane names to their single/double-letter short
# codes as defined by the v9.0 back-compat config (e.g. AUDIT→A, ARCHITECT→B).
# Using the dict resolves the v8 first-letter ambiguity (AUDIT[0]==ARCHITECT[0]=='A').
LANE_LONG_TO_SHORT: dict[str, str] = {l.name: l.short for l in _DEFAULT_CONFIG.lanes}

# ---------------------------------------------------------------------------
# Canonical task-id regex (v8 Edit 3 — ASCII only; see P2.5-C plan-v2 Δ2.2).
# Exported so FRONTEND can mirror client-side validation per FE P2-D-to-C C4.
# Single source of truth: the loose shape pattern from MissionConfig's default
# factory (schema.py line 65) rather than a copy-pasted literal here.
# ---------------------------------------------------------------------------

_DEFAULT_LOOSE_PATTERN = MissionConfig.model_fields["task_id_patterns"].default_factory().patterns[0]
CANONICAL_TASK_ID_RE = re.compile(_DEFAULT_LOOSE_PATTERN)
"""Server-side canonical task-id pattern. ASCII-only per v8 Edit 3."""


def canonicalize_task_id(s: str) -> str:
    """Collapse Unicode `→` / `->` / ` to ` into the canonical `-to-` form.

    Per v8 Edit 3 (4-LANE BLOCKING quorum on run-1 file-collision defect).
    """
    out = s.strip()
    out = out.replace("→", "-to-")
    out = out.replace("->", "-to-")
    return out


# ---------------------------------------------------------------------------
# RULE 1 — heartbeat staleness
# ---------------------------------------------------------------------------

STALE_THRESHOLD_SECONDS = 15 * 60


def _utc_now() -> datetime:
    """Return current UTC as a tz-aware datetime."""
    return datetime.now(timezone.utc)


def _parse_utc(s: str) -> datetime:
    """Parse 'YYYY-MM-DDTHH:MMZ' (minute resolution, Z suffix) → aware UTC."""
    s = s.strip()
    # Accept the minute-resolution canonical form; fall back to fromisoformat
    # for second-resolution variants found in .mission-events.
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
    except ValueError:
        # second-resolution: 2026-05-16T19:01:00Z
        if s.endswith("Z"):
            s2 = s[:-1] + "+00:00"
        else:
            s2 = s
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt


def is_stale(last_utc: str, *, now: datetime | None = None) -> bool:
    """True iff (now - parse(last_utc)) > STALE_THRESHOLD_SECONDS."""
    if now is None:
        now = _utc_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    parsed = _parse_utc(last_utc)
    age = (now - parsed).total_seconds()
    return age > STALE_THRESHOLD_SECONDS


# ---------------------------------------------------------------------------
# RULE 2 — atomic mkdir claim
# ---------------------------------------------------------------------------


def try_claim(claims_dir: Path, task_id: str) -> bool:
    """Attempt atomic claim via mkdir.

    Returns True if this caller acquired the lock; False if it already existed.
    Normalizes `task_id` via canonicalize_task_id (v8 Edit 3 ASCII-only).
    """
    canonical = canonicalize_task_id(task_id)
    target = Path(claims_dir) / canonical
    try:
        target.mkdir(parents=False, exist_ok=False)
        return True
    except FileExistsError:
        return False


# ---------------------------------------------------------------------------
# RULE 4 — SIGNAL evidence validation
# ---------------------------------------------------------------------------


def validate_signal(payload: dict) -> None:
    """Raise ValueError if signal payload lacks evidence citation (RULE 4).

    Accepts both schemas the protocol uses: `{cite: "path:line"}` (test fixture)
    and `{evidence: "path:line"}` (server endpoint).
    """
    cite = payload.get("cite") or payload.get("evidence") or ""
    if not str(cite).strip():
        raise ValueError(
            "signal missing evidence — RULE 4 requires cite='path:line' or "
            "'path:section'"
        )


# ---------------------------------------------------------------------------
# Severity-quorum math (TIER 2)
# ---------------------------------------------------------------------------

_SEVERITY_ORDER = {"NIT": 0, "DELTA": 1, "MINOR": 2, "MAJOR": 3, "BLOCKING": 4}


def compute_effective_severity(findings: list[dict]) -> str:
    """Apply README TIER 2 quorum rules to a list of finding dicts.

    Each dict has at least `severity`, `artifact`, `pass`; optionally `lane`,
    `agent`, `type` (e.g., "ACK-VERIFIED").

    Rules:
      - MINOR → MAJOR with 1 peer Pass-1 finding on same artifact.
      - MAJOR → BLOCKING with 2+ INDEPENDENT lanes' Pass-1 findings.
      - ACK-VERIFIED entries do NOT count toward quorum.
    """
    if not findings:
        return "NIT"

    # Quorum considers only Pass-1, non-ACK-VERIFIED findings.
    quorum_set = [
        f for f in findings
        if f.get("pass") == 1 and f.get("type") != "ACK-VERIFIED"
    ]

    # Group by artifact for quorum math.
    by_artifact: dict[str, list[dict]] = {}
    for f in quorum_set:
        by_artifact.setdefault(f.get("artifact", ""), []).append(f)

    effective = "NIT"
    for art, group in by_artifact.items():
        # Distinct lanes among this artifact's Pass-1 findings.
        lanes = {f.get("lane") for f in group if f.get("lane")}
        max_sev = max((_SEVERITY_ORDER.get(f.get("severity", "NIT"), 0) for f in group), default=0)

        # MAJOR → BLOCKING: 2+ INDEPENDENT lanes' Pass-1 findings on same artifact.
        if max_sev == _SEVERITY_ORDER["MAJOR"] and len(lanes) >= 2:
            promoted = "BLOCKING"
        # MINOR → MAJOR: 1 peer's Pass-1 on same artifact (i.e., 2+ findings).
        elif max_sev == _SEVERITY_ORDER["MINOR"] and len(group) >= 2:
            promoted = "MAJOR"
        else:
            # No promotion — use highest raw severity from this artifact's group.
            for name, level in _SEVERITY_ORDER.items():
                if level == max_sev:
                    promoted = name
                    break
            else:
                promoted = "NIT"

        if _SEVERITY_ORDER.get(promoted, 0) > _SEVERITY_ORDER.get(effective, 0):
            effective = promoted

    return effective


# ---------------------------------------------------------------------------
# RULE 6 — stale-row reclamation / retroactive recovery
# ---------------------------------------------------------------------------


def _finding_exists_for_task(root: Path, task_id: str) -> Path | None:
    """Return path to the first finding whose filename references task_id."""
    findings_dir = Path(root) / "findings"
    if not findings_dir.is_dir():
        return None
    canonical = canonicalize_task_id(task_id)
    # Filename convention: agent-<id>-<lane>-<task>-<utc>.md
    for p in findings_dir.iterdir():
        if not p.is_file():
            continue
        name = p.name
        if canonical in name and name.endswith(".md"):
            return p
    return None


def reclaim_or_recover(root: Path, task_id: str, agent: str) -> None:
    """Reclaim a stale claim or retroactively complete it.

    If a matching finding exists → retroactive recovery (touch done).
    Else → STALE-RECLAIMED (rm -rf the claim directory).
    """
    root = Path(root)
    canonical = canonicalize_task_id(task_id)
    claim_dir = root / "claims" / canonical
    matching = _finding_exists_for_task(root, canonical)
    if matching is not None:
        # Retroactive recovery: just touch done; leave claim dir intact.
        claim_dir.mkdir(parents=True, exist_ok=True)
        (claim_dir / "done").touch()
    else:
        # No finding → STALE-RECLAIMED: rm -rf the claim dir.
        if claim_dir.exists():
            shutil.rmtree(claim_dir, ignore_errors=False)


# ---------------------------------------------------------------------------
# RULE 10 — atomic four-step completion
# ---------------------------------------------------------------------------


def _utc_now_str_minute() -> str:
    return _utc_now().strftime("%Y-%m-%dT%H:%MZ")


def mark_complete(
    root: Path,
    *,
    task_id: str,
    agent: str,
    lane: str,
    finding: str,
    severity: str,
) -> None:
    """Execute all four RULE-10 steps in a single call.

    1. touch claims/<task_id>/done
    2. Mark TASKS bracket [done: <agent> @ <utc>]
    3. Append HISTORY line in canonical format
    4. Update STATUS row to idle
    """
    root = Path(root)
    canonical = canonicalize_task_id(task_id)
    utc = _utc_now_str_minute()

    # Step 1: touch done
    claim_dir = root / "claims" / canonical
    claim_dir.mkdir(parents=True, exist_ok=True)
    (claim_dir / "done").touch()

    # Step 2: TASKS bracket — replace first occurrence of "[ ]" or
    # "[claimed: ...]" for this task with "[done: <agent> @ <utc>]".
    tasks_path = root / "TASKS.md"
    if tasks_path.exists():
        text = tasks_path.read_text()
        # Match either `[ ] [LANE-X] \`<task>\`` or `[claimed: ...] [LANE-X] \`<task>\``
        # _LANE_SHORT_CLASS is config-driven (e.g. "[A-F]" for the 6-lane default).
        # Wrap alternation form as non-capturing so group indices don't shift.
        _LANE_SHORT_CLASS = build_lane_short_charclass(_DEFAULT_CONFIG)
        _lane_match = (
            _LANE_SHORT_CLASS
            if _LANE_SHORT_CLASS.startswith("[")
            else f"(?:{_LANE_SHORT_CLASS.strip('()')})"
        )
        pattern = re.compile(
            r"\[(?:\s|claimed:[^\]]*)\]"
            r"(\s\[LANE-" + _lane_match + r"\]\s`" + re.escape(canonical) + r"`)"
        )
        new_text, n = pattern.subn(
            f"[done: {agent} @ {utc}]\\1", text, count=1
        )
        if n == 0:
            # Already done or task absent — no-op (idempotent).
            new_text = text
        tasks_path.write_text(new_text)

    # Step 3: HISTORY append (canonical line per test_protocol_primitives.py:198).
    # LANE_LONG_TO_SHORT comes from the v9.0 back-compat config and resolves the
    # v8 ambiguity where first-letter derivation collided (AUDIT[0]==ARCHITECT[0]).
    lane_canonical = lane if lane.startswith("LANE-") else f"LANE-{LANE_LONG_TO_SHORT[lane]}"
    history_line = (
        f"{utc} | {agent} | {lane_canonical} | {canonical} | {finding} | {severity}\n"
    )
    history_path = root / "HISTORY.md"
    with open(history_path, "a") as f:
        f.write(history_line)

    # Step 4: STATUS row → idle. Best-effort regex replacement.
    status_path = root / "STATUS.md"
    if status_path.exists():
        status_text = status_path.read_text()
        # Match any row containing this agent and replace the State + Last UTC
        # cells. Conservative: only update rows currently in "working:" state.
        # Note: the test fixture's STATUS uses a simple 3-column row form;
        # we accept either canonical "| Lane | Agent | State | Last UTC | Notes |"
        # or the test's "| LANE-A | agent-X | working: T1 | ... |" simplified form.
        agent_pat = re.escape(agent)
        # Try canonical 6-column form first.
        row_pat = re.compile(
            r"(\|\s*[A-Z\- ]+\s*\|\s*" + agent_pat + r"\s*\|\s*)"
            r"(?:working:[^|]*|initialized|idle)"
            r"(\s*\|\s*)[^|]*(\s*\|[^|\n]*\|?)"
        )
        new_status, n = row_pat.subn(
            r"\1idle\g<2>" + utc + r"\3",
            status_text,
            count=1,
        )
        if n == 0:
            # Append a synthetic note for tests that don't pre-seed the row.
            new_status = status_text
            if "idle" not in new_status:
                new_status = new_status.rstrip("\n") + (
                    f"\n| {lane_canonical} | {agent} | idle | {utc} | {canonical} done |\n"
                )
        status_path.write_text(new_status)


# ---------------------------------------------------------------------------
# RULE 11 — distributed atomic phase-flip (+ Edit 14 stuck-flip recovery)
# ---------------------------------------------------------------------------


def _phase_flip_lock_dir(root: Path, from_phase: str, to_phase: str) -> Path:
    return Path(root) / ".phase-flip-locks" / f"{from_phase}-to-{to_phase}"


def _is_holder_fresh(
    root: Path, agent_id: str, *, now: datetime, stuck_after_seconds: int
) -> bool:
    """Read STATUS.md, locate agent_id's row, return True if their Last UTC
    age (relative to `now`) is ≤ stuck_after_seconds. False if row missing
    or stale.
    """
    status_path = Path(root) / "STATUS.md"
    if not status_path.exists():
        return False
    try:
        text = status_path.read_text()
    except OSError:
        return False
    # Match a table row containing this agent_id; extract the 4th cell (Last UTC).
    # Row form: | Lane | Agent | State | Last UTC | Notes |
    pat = re.compile(
        r"^\|[^|\n]*\|\s*" + re.escape(agent_id) +
        r"\s*\|[^|\n]*\|\s*([0-9TZ:\-+\.]+)\s*\|",
        re.MULTILINE,
    )
    m = pat.search(text)
    if not m:
        return False
    last_utc_str = m.group(1)
    try:
        last_utc = _parse_utc(last_utc_str)
    except ValueError:
        return False
    age = (now - last_utc).total_seconds()
    return age <= stuck_after_seconds


def try_phase_flip(
    root: Path, from_phase: str, to_phase: str, agent: str
) -> bool:
    """Attempt the distributed atomic phase-flip per RULE 11.

    On win: mkdir lock, write owner.txt (v8.1 Edit-14 fix), append flip event,
    update README "Mission status". Returns True.

    On loss: returns False; no filesystem mutations.
    """
    root = Path(root)
    locks_parent = root / ".phase-flip-locks"
    locks_parent.mkdir(parents=True, exist_ok=True)
    lock_dir = _phase_flip_lock_dir(root, from_phase, to_phase)
    try:
        lock_dir.mkdir(parents=False, exist_ok=False)
    except FileExistsError:
        return False

    utc = _utc_now_str_minute()

    # v8.1-candidate Edit-14 fix: write owner.txt inside lock at acquire.
    (lock_dir / "owner.txt").write_text(f"{agent}\n{utc}\n")

    # Append flip event to .mission-events
    events_path = root / ".mission-events"
    events_line = (
        f"{utc} {from_phase}->{to_phase} by {agent} "
        f"-- RULE 11 distributed-atomic flip won via mkdir + owner.txt\n"
    )
    with open(events_path, "a") as f:
        f.write(events_line)

    # Update README "Mission status" section (best-effort).
    readme_path = root / "README.md"
    if readme_path.exists():
        readme_text = readme_path.read_text()
        # Replace "Current: <something>" with the new phase.
        new_readme = re.sub(
            r"\*\*Current:\s*[^*]+\*\*",
            f"**Current: {to_phase}**",
            readme_text,
            count=1,
        )
        if new_readme == readme_text:
            # Test fixture uses "Current: <phase>" without bold; fall back.
            new_readme = re.sub(
                r"Current:\s*[^\n]+",
                f"Current: {to_phase}",
                readme_text,
                count=1,
            )
        readme_path.write_text(new_readme)

    return True


def detect_and_recover_stuck_flips(
    root: Path, *, now: datetime, stuck_after_seconds: int
) -> None:
    """v8 Edit 14 (step 4a) — recover from stuck phase-flip locks.

    A flip is "stuck" when ALL of:
      (a) `.phase-flip-locks/<from>-to-<to>/` exists AND age > stuck_after_seconds
      (b) `.mission-events` last line does NOT contain the matching `<from>-to-<to>`
      (c) (v8.1) owner-id readable from `<lock>/owner.txt` AND that agent's
          STATUS Last UTC also > stuck_after_seconds (else: do not recover)

    If conditions hold (and v8.1 (c) cannot disprove staleness): rm lock,
    re-mkdir, append a synthetic RECOVERY flip event to .mission-events.
    """
    root = Path(root)
    locks_parent = root / ".phase-flip-locks"
    if not locks_parent.is_dir():
        return
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    events_path = root / ".mission-events"
    last_event = ""
    if events_path.exists():
        for line in events_path.read_text().splitlines():
            if line.strip():
                last_event = line

    # Last event's timestamp is the canonical staleness reference. The
    # filesystem mtime of the lock dir is unreliable when callers pass a
    # simulated `now` from the past (e.g., test fixtures).
    last_event_utc: datetime | None = None
    if last_event:
        # Last event format: "<UTC> <FROM>-><TO> by <agent> -- <reason>"
        first_field = last_event.split(" ", 1)[0]
        try:
            last_event_utc = _parse_utc(first_field)
        except (ValueError, IndexError):
            last_event_utc = None

    for lock_dir in locks_parent.iterdir():
        if not lock_dir.is_dir():
            continue
        name = lock_dir.name  # "<from>-to-<to>"
        if "-to-" not in name:
            continue
        from_phase, _, to_phase = name.partition("-to-")

        # (a) age check via .mission-events last-event time (preferred) or
        # filesystem mtime (fallback).
        if last_event_utc is not None:
            age = (now - last_event_utc).total_seconds()
        else:
            mtime = datetime.fromtimestamp(lock_dir.stat().st_mtime, tz=timezone.utc)
            age = (now - mtime).total_seconds()
        if age <= stuck_after_seconds:
            continue

        # (b) event-not-yet-appended check
        if name in last_event or f"{from_phase}->{to_phase}" in last_event:
            continue

        # (c) v8.1: owner.txt + holder STATUS-freshness check. If owner.txt is
        # present AND that agent's STATUS Last UTC is fresh (age ≤ threshold),
        # the holder is making progress — DO NOT recover. This closes the
        # SIG-AUDIT-1 false-positive defect (4-LANE BLOCKING quorum).
        owner_path = lock_dir / "owner.txt"
        owner_id = ""
        if owner_path.exists():
            txt = owner_path.read_text().strip()
            if txt:
                # First whitespace-separated token of the first non-empty line.
                first_line = txt.splitlines()[0]
                owner_id = first_line.split()[0] if first_line.split() else ""

            if owner_id:
                # Holder is "fresh" if their STATUS Last UTC is within RULE-1's
                # stale threshold (15 min) — NOT the lock's stuck_after_seconds.
                # A 60s stuck-lock with a 2-min-quiet holder is still recoverable
                # only if the holder has gone fully stale per RULE 1.
                holder_fresh = _is_holder_fresh(
                    root, owner_id, now=now,
                    stuck_after_seconds=STALE_THRESHOLD_SECONDS,
                )
                if holder_fresh:
                    # Holder is mid-flight; preserve their work.
                    continue

        # Recovery: rm lock + remkdir + append synthetic event with RECOVERY marker.
        shutil.rmtree(lock_dir)
        lock_dir.mkdir()
        (lock_dir / "owner.txt").write_text("recovery-agent\n" + _utc_now_str_minute() + "\n")

        utc_str = _utc_now_str_minute()
        suffix = f" (RECOVERY, previous holder: {owner_id})" if owner_id else " (RECOVERY)"
        recovery_line = (
            f"{utc_str} {from_phase}->{to_phase} by recovery-agent "
            f"-- v8 Edit 14 step 4a stuck-flip recovery; "
            f"lock age {int(age)}s exceeded {stuck_after_seconds}s threshold{suffix}\n"
        )
        with open(events_path, "a") as f:
            f.write(recovery_line)
