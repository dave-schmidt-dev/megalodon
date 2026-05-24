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
        last: Latest DONE task as ``{"task_id": ..., "desc": ...}``, or None.
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
    digest_text: str | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        """Return the public JSON-serializable payload (excludes ``digest_text``).

        Returns:
            Dict with keys: lane, lane_name, state, last, now, goal, tokens,
            narrator_ok.
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
# Pure assembler
# ---------------------------------------------------------------------------


def assemble_lane_rows(
    tasks_fe: dict[str, Any],
    lane_cfgs: list[Any],
    digests: dict[str, SessionDigest | None],
    doc_order: dict[str, int],
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
            }

        now: dict[str, Any] | None = None
        if latest_claimed is not None:
            now = {
                "task_id": latest_claimed["task_id"],
                "desc": latest_claimed.get("description", ""),
                "phrase": None,
            }

        # Goal: now.desc > last.desc > lane role.
        if now is not None:
            goal = now["desc"]
        elif last is not None:
            goal = last["desc"]
        else:
            goal = cfg.role or ""

        # Derive state label from the highest-priority active task.
        # Precedence: blocked > claimed > done > open.
        if blocked_tasks:
            state = "blocked"
        elif claimed_tasks:
            state = "claimed"
        elif done_tasks:
            state = "done"
        else:
            state = "open"

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
    order: dict[str, int] = {}
    idx = 0
    for line in path.read_text(encoding="utf-8").splitlines():
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


async def build_lane_rows(
    mission_dir: Path,
    tasks_fe: dict[str, Any],
    sessions: dict[str, Any],
    adapter_resolver: Callable[[str], Any],
    lane_cfgs: list[Any],
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

    Returns:
        Dict mapping lane short code to its assembled ``LaneRow``.
    """
    # Capture document order from TASKS.md once (pure synchronous read).
    doc_order = _capture_doc_order(mission_dir)

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

    rows = assemble_lane_rows(tasks_fe, lane_cfgs, digests, doc_order)

    # Attach digest_text to rows that have a digest (internal, for the scheduler).
    for cfg in lane_cfgs:
        short = cfg.short
        if not short or short not in rows:
            continue
        digest = digests.get(short)
        if digest is not None:
            rows[short].digest_text = render_for_prompt(digest)

    return rows
