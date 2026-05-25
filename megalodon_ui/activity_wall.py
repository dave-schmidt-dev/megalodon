"""Activity wall: ring buffer + fan-in task + subscriber fanout.

The activity wall aggregates events from 6 sources into a shared in-memory
ring buffer (deque, maxlen=500) and fans them out to per-connection asyncio
queues for SSE clients.

The six sources are: findings, signals, history, queue_applier, inject_log,
and governor_log (the PreToolUse governor's audit log — see Phase 3 governor
hook). The governor-log source is what makes a governed lane's allow/deny
activity visible on the board.

Usage (from server.py lifespan)
--------------------------------
    wall = ActivityWall(mission_dir)
    await wall.start()
    app.state.activity_wall = wall
    # ...at shutdown:
    await wall.stop()

Event shape (all sources)
--------------------------
    {
      "type": str,           # "finding" | "signal" | "history" | "queue"
                             #   | "inject" | "restart-loop" | "governor"
      "lane": str | None,
      "ts": str,             # ISO-8601 UTC
      "summary": str,        # ≤200 chars
      "payload": dict,       # source-specific fields
    }

``"governor"`` events carry ``payload`` keys: ``permission`` ("allow"/"deny"),
``category``, ``reason``, ``tool``, ``input_sha256``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants / tunables (module-level so tests can monkey-patch)
# ---------------------------------------------------------------------------

RING_BUFFER_MAXLEN: int = 500
SUBSCRIBER_QUEUE_MAXLEN: int = 100

# Regex to parse a finding / signal filename.
# Pattern: agent-XXXX-L-PPHASE-topic-...-UTC.md (with optional .scratch)
_FINDING_STEM_RE = re.compile(r"^agent-[A-Za-z0-9]+-([A-Z])(?:-|$)")

# Applier log timestamp: "2026-05-16T22:00:00Z | INFO | ..."
_APPLIER_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z")
# Lane field in applier log lines
_APPLIER_LANE_RE = re.compile(r"(?<!\w)lane=(\S+)")


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_lane_from_filename(name: str) -> str | None:
    """Extract single-letter lane short from a finding/signal filename stem.

    Pattern: agent-XXXX-L-... where L is a single uppercase letter.
    Returns the letter or None if not matched.
    """
    stem = name
    for suffix in (".md", ".scratch.md", ".scratch"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    m = _FINDING_STEM_RE.match(stem)
    if m:
        return m.group(1)
    return None


# ---------------------------------------------------------------------------
# ActivityWall
# ---------------------------------------------------------------------------


class ActivityWall:
    """Central fan-in hub for the 6 activity-wall event sources.

    Sources: findings, signals, history, queue_applier, inject_log,
    governor_log.

    Parameters
    ----------
    mission_dir:
        Root of the mission directory (same as ``MissionContext.mission_dir``).

    Memory
    ------
    * Ring buffer: deque(maxlen=500). All events since startup (up to cap).
    * Per-subscriber asyncio.Queue(maxsize=100). Overflow drops the oldest
      item with a WARNING log (fast publisher, slow consumer) — see
      ``_fan_out``.

    Fan-in task
    -----------
    ``start()`` launches a single asyncio.Task (``_fan_in_task``) that drives
    all 6 source coroutines via ``asyncio.gather``. Each source is its own
    inner coroutine that loops on its generator and calls ``_emit``.
    ``stop()`` cancels the task and awaits it.
    """

    def __init__(self, mission_dir: Path) -> None:
        self._mission_dir = mission_dir
        self._ring: deque[dict] = deque(maxlen=RING_BUFFER_MAXLEN)
        self._subscribers: list[asyncio.Queue] = []
        self._task: asyncio.Task | None = None
        # Counter exposed for tests (GC/cleanup assertions)
        self.subscriber_count: int = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Launch the fan-in background task."""
        if self._task is not None:
            return  # already running
        self._task = asyncio.create_task(self._fan_in(), name="activity-wall-fan-in")

    async def stop(self) -> None:
        """Cancel and await the fan-in task."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            pass
        self._task = None

    # ------------------------------------------------------------------
    # Public API used by endpoints
    # ------------------------------------------------------------------

    def snapshot(self, limit: int = 100) -> list[dict]:
        """Return the most-recent *limit* events, newest-first.

        ``limit`` must be in [1, 500]; callers must clamp before calling.
        """
        buf = list(self._ring)
        # buf is oldest-first; return newest-first slice
        return buf[-limit:][::-1]

    def subscribe(self) -> asyncio.Queue:
        """Register a new SSE subscriber; return its per-connection queue."""
        q: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_MAXLEN)
        self._subscribers.append(q)
        self.subscriber_count += 1
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """Remove a subscriber's queue (called on client disconnect)."""
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass
        self.subscriber_count -= 1

    # ------------------------------------------------------------------
    # Internal: emit + fanout
    # ------------------------------------------------------------------

    def _emit(self, event: dict) -> None:
        """Add *event* to the ring buffer and fan out to all subscribers."""
        self._ring.append(event)
        self._fan_out(event)

    def _fan_out(self, event: dict) -> None:
        """Push *event* to every subscriber queue, dropping oldest if full."""
        for q in list(self._subscribers):
            if q.full():
                try:
                    q.get_nowait()  # drop oldest
                except asyncio.QueueEmpty:
                    pass
                _log.warning(
                    "activity-wall subscriber queue full — dropped oldest event"
                )
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Race between the get_nowait above and another coroutine;
                # just drop silently.
                pass

    # ------------------------------------------------------------------
    # Fan-in task
    # ------------------------------------------------------------------

    async def _fan_in(self) -> None:
        """Run all 6 source coroutines concurrently; restart on unexpected exit."""
        try:
            await asyncio.gather(
                self._source_findings(),
                self._source_signals(),
                self._source_history(),
                self._source_queue_applier(),
                self._source_inject_log(),
                self._source_governor_log(),
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("activity-wall fan-in crashed")

    # ------------------------------------------------------------------
    # Source 1: findings/
    # ------------------------------------------------------------------

    async def _source_findings(self) -> None:
        from .event_tail import watch_dir_for_new_files

        findings_dir = self._mission_dir / "findings"
        try:
            async for path in watch_dir_for_new_files(findings_dir):
                try:
                    event = self._build_file_event("finding", path)
                    if event:
                        self._emit(event)
                except Exception:
                    _log.exception("activity-wall: error processing finding %s", path)
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("activity-wall: findings source crashed")

    # ------------------------------------------------------------------
    # Source 2: signals/
    # ------------------------------------------------------------------

    async def _source_signals(self) -> None:
        from .event_tail import watch_dir_for_new_files

        signals_dir = self._mission_dir / "signals"
        try:
            async for path in watch_dir_for_new_files(signals_dir):
                try:
                    event = self._build_file_event("signal", path)
                    if event:
                        self._emit(event)
                except Exception:
                    _log.exception("activity-wall: error processing signal %s", path)
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("activity-wall: signals source crashed")

    def _build_file_event(self, event_type: str, path: Path) -> dict | None:
        """Build an event dict for a findings/ or signals/ file."""
        try:
            mtime = path.stat().st_mtime
            ts = (
                datetime.fromtimestamp(mtime, tz=timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
        except OSError:
            ts = _now_utc_iso()
        lane = _parse_lane_from_filename(path.name)
        return {
            "type": event_type,
            "lane": lane,
            "ts": ts,
            "summary": path.stem[:200],
            "payload": {
                "filename": path.name,
                "path": str(path),
            },
        }

    # ------------------------------------------------------------------
    # Source 3: HISTORY.md tail
    # ------------------------------------------------------------------

    async def _source_history(self) -> None:
        from .event_tail import tail_file_lines

        history_path = self._mission_dir / "HISTORY.md"
        try:
            async for line in tail_file_lines(history_path):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                ts = _parse_history_ts(stripped) or _now_utc_iso()
                event = {
                    "type": "history",
                    "lane": None,
                    "ts": ts,
                    "summary": stripped[:200],
                    "payload": {"line": stripped},
                }
                self._emit(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("activity-wall: history source crashed")

    # ------------------------------------------------------------------
    # Source 4: .fleet/queue-applier.log tail
    # ------------------------------------------------------------------

    async def _source_queue_applier(self) -> None:
        from .event_tail import tail_file_lines

        log_path = self._mission_dir / ".fleet" / "queue-applier.log"
        try:
            async for line in tail_file_lines(log_path):
                stripped = line.strip()
                if not stripped:
                    continue
                ts_m = _APPLIER_TS_RE.match(stripped)
                ts = (ts_m.group(1) + "Z") if ts_m else _now_utc_iso()
                lane_m = _APPLIER_LANE_RE.search(stripped)
                lane = lane_m.group(1) if lane_m else None
                event = {
                    "type": "queue",
                    "lane": lane,
                    "ts": ts,
                    "summary": stripped[:200],
                    "payload": {"raw": stripped},
                }
                self._emit(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("activity-wall: queue-applier source crashed")

    # ------------------------------------------------------------------
    # Source 5: .fleet/inject-log-YYYY-MM-DD.jsonl (daily rotation)
    # ------------------------------------------------------------------

    async def _source_inject_log(self) -> None:
        """Tail today's inject-log file; restart the inner tail on date rollover.

        Uses a separate drainer task that feeds lines into a local queue.
        This avoids ``asyncio.wait_for(__anext__, ...)`` which has cancellation
        hazards in Python 3.12+ when the outer task is cancelled while the
        inner coroutine is suspended inside ``asyncio.to_thread``.
        """
        from .event_tail import tail_file_lines

        fleet_dir = self._mission_dir / ".fleet"
        current_date: str = ""
        drainer_task: asyncio.Task | None = None
        line_queue: asyncio.Queue = asyncio.Queue(maxsize=256)

        async def _drain_into_queue(path: Path, q: asyncio.Queue) -> None:
            try:
                async for line in tail_file_lines(path):
                    await q.put(line)
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.exception("activity-wall: inject-log drainer crashed for %s", path)

        try:
            while True:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if today != current_date:
                    # Date rolled over (or first start): restart the drainer.
                    if drainer_task is not None and not drainer_task.done():
                        drainer_task.cancel()
                        try:
                            await drainer_task
                        except (asyncio.CancelledError, Exception):
                            pass
                    current_date = today
                    log_path = fleet_dir / f"inject-log-{today}.jsonl"
                    drainer_task = asyncio.create_task(
                        _drain_into_queue(log_path, line_queue),
                        name="inject-log-drainer",
                    )

                # Read lines from the queue; poll with short timeout so we can
                # detect date rollover once per second.
                try:
                    line = await asyncio.wait_for(line_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    entry = json.loads(stripped)
                except (json.JSONDecodeError, ValueError):
                    continue

                ts = entry.get("ts") or _now_utc_iso()
                lane = entry.get("lane")
                byte_count = entry.get("byte_count", 0)
                enter = entry.get("enter", False)
                sha256 = entry.get("text_sha256", "")
                source = entry.get("source")

                event_type = "restart-loop" if source else "inject"
                event = {
                    "type": event_type,
                    "lane": lane,
                    "ts": ts,
                    "summary": f"{byte_count} bytes",
                    "payload": {
                        "sha256": sha256,
                        "byte_count": byte_count,
                        "enter": enter,
                        "source": source,
                    },
                }
                self._emit(event)

        except asyncio.CancelledError:
            if drainer_task is not None and not drainer_task.done():
                drainer_task.cancel()
                try:
                    await drainer_task
                except (asyncio.CancelledError, Exception):
                    pass
            raise
        except Exception:
            _log.exception("activity-wall: inject-log source crashed")

    # ------------------------------------------------------------------
    # Source 6: .fleet/governor-log-YYYY-MM-DD.jsonl (daily rotation)
    # ------------------------------------------------------------------

    async def _source_governor_log(self) -> None:
        """Tail today's governor-log file; restart the inner tail on date rollover.

        The governor (PreToolUse hook) writes one JSON line per tool decision:
        ``{ts, lane, tool, permission, category, reason, input_sha256}`` where
        ``permission`` is ``"allow"`` or ``"deny"``. Surfacing these is what
        makes a governed lane's activity visible on the board (without this, a
        deny-looping or governed-but-quiet lane reads as IDLE).

        Mirrors ``_source_inject_log``: a separate drainer task feeds lines into
        a local queue, polled once per second so date rollover is detected. This
        avoids ``asyncio.wait_for(__anext__, ...)`` cancellation hazards.
        """
        from .event_tail import tail_file_lines

        fleet_dir = self._mission_dir / ".fleet"
        current_date: str = ""
        drainer_task: asyncio.Task | None = None
        line_queue: asyncio.Queue = asyncio.Queue(maxsize=256)

        async def _drain_into_queue(path: Path, q: asyncio.Queue) -> None:
            try:
                async for line in tail_file_lines(path):
                    await q.put(line)
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.exception(
                    "activity-wall: governor-log drainer crashed for %s", path
                )

        try:
            while True:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if today != current_date:
                    # Date rolled over (or first start): restart the drainer.
                    if drainer_task is not None and not drainer_task.done():
                        drainer_task.cancel()
                        try:
                            await drainer_task
                        except (asyncio.CancelledError, Exception):
                            pass
                    current_date = today
                    log_path = fleet_dir / f"governor-log-{today}.jsonl"
                    drainer_task = asyncio.create_task(
                        _drain_into_queue(log_path, line_queue),
                        name="governor-log-drainer",
                    )

                # Read lines from the queue; poll with short timeout so we can
                # detect date rollover once per second.
                try:
                    line = await asyncio.wait_for(line_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    entry = json.loads(stripped)
                except (json.JSONDecodeError, ValueError):
                    continue

                event = {
                    "type": "governor",
                    "lane": entry.get("lane"),
                    "ts": entry.get("ts") or _now_utc_iso(),
                    "summary": f"{entry.get('permission', '?')} "
                    f"{entry.get('category', '')}".strip(),
                    "payload": {
                        "permission": entry.get("permission"),
                        "category": entry.get("category"),
                        "reason": entry.get("reason"),
                        "tool": entry.get("tool"),
                        "input_sha256": entry.get("input_sha256"),
                    },
                }
                self._emit(event)

        except asyncio.CancelledError:
            if drainer_task is not None and not drainer_task.done():
                drainer_task.cancel()
                try:
                    await drainer_task
                except (asyncio.CancelledError, Exception):
                    pass
            raise
        except Exception:
            _log.exception("activity-wall: governor-log source crashed")


# ---------------------------------------------------------------------------
# History line timestamp parser
# ---------------------------------------------------------------------------

_HISTORY_TS_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?)"
)


def _parse_history_ts(line: str) -> str | None:
    """Extract an ISO-8601 timestamp from the start of a HISTORY.md line.

    Returns the raw match string (with possible trailing 'Z' or offset), or
    None if no timestamp found.
    """
    m = _HISTORY_TS_RE.match(line)
    if not m:
        return None
    raw = m.group(1)
    # Normalize: if no Z/offset, append Z (assume UTC).
    if not (raw.endswith("Z") or "+" in raw[10:] or raw[10:].count("-") >= 1):
        raw += "Z"
    return raw
