"""Activity wall: ring buffer + fan-in task + subscriber fanout.

The activity wall aggregates events from 7 sources into a shared in-memory
ring buffer (deque, maxlen=500) and fans them out to per-connection asyncio
queues for SSE clients.

The seven sources are: findings, signals, status_notes, history, queue_applier,
inject_log, and governor_log (the PreToolUse governor's audit log — see Phase 3
governor hook). The governor-log source is what makes a governed lane's
allow/deny activity visible on the board.

SCHISM FIX — live signals for ALL THREE comms channels. A signal can arrive via
three channels and ALL THREE now emit a live ``type:"signal"`` event:
  * ``source:"file"``        — a new ``signals/*.md`` file (``_source_signals``).
  * ``source:"finding"``     — a new SIGNAL-class finding (``signal-type`` in YAML
                               frontmatter) ALSO emits a signal event alongside
                               its ``type:"finding"`` event (``_source_findings``).
  * ``source:"status-note"`` — a new ``[SIG ...]`` token in STATUS.md notes, via
                               the ``_source_status_notes`` watcher (sender bound
                               to the owning STATUS row, anti-spoof).
Signal event payload shape (all three channels):
``{filename, from_lane, to_lane, topic, utc, source, excerpt}``.

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

from .signal_grammar import parse_signal_filename

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants / tunables (module-level so tests can monkey-patch)
# ---------------------------------------------------------------------------

RING_BUFFER_MAXLEN: int = 500
SUBSCRIBER_QUEUE_MAXLEN: int = 100

# Regex to parse a finding / signal filename.
# Pattern: agent-XXXX-L-PPHASE-topic-...-UTC.md (with optional .scratch)
_FINDING_STEM_RE = re.compile(r"^agent-[A-Za-z0-9]+-([A-Z])(?:-|$)")

# Canonical signal filename grammar now lives in the shared leaf module
# ``signal_grammar`` (imported above as ``parse_signal_filename``). It carries
# no server/activity_wall imports, so importing it here does not create the
# cycle that previously forced a local regex copy.

# Applier log timestamp: "2026-05-16T22:00:00Z | INFO | ..."
_APPLIER_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z")
# Lane field in applier log lines
_APPLIER_LANE_RE = re.compile(r"(?<!\w)lane=(\S+)")


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# Small pure helpers, mirrored from server.py so SIGNAL-class finding events and
# STATUS-note signal events share the same lane-label / topic normalization the
# unified ``parse_signals`` uses — WITHOUT importing server.py (which lazily
# imports this module, so a top-level back-import would risk a cycle).


def _slugify(text: str, default: str = "note") -> str:
    """Lowercase ``[a-z0-9-]+`` slug, or *default* if empty (matches server.py)."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return slug or default


def _normalize_lane_label(raw: str, default: str) -> str:
    """Normalize a lane label to canonical ``LANE-<X>`` (matches server.py)."""
    short = (raw or "").strip().upper()
    if not short:
        return default
    if short.startswith("LANE-"):
        return short
    if short == "ORCHESTRATOR":
        short = "ORCH"
    if re.fullmatch(r"[A-Z0-9]+", short):
        return f"LANE-{short}"
    return short


# `[SIG from=X to=Y text="..." cite=...]` token embedded in STATUS.md notes.
# Mirrors server.py ``_SIG_TOKEN_RE`` (shared grammar; no import cycle).
_SIG_TOKEN_RE = re.compile(
    r'\[SIG\s+from=(\S+)\s+to=(\S+)\s+text="([^"]*)"\s*(?:cite=(\S+))?\s*\]'
)

# A STATUS.md table row → owning lane (first cell) + remaining row text.
# Mirrors server.py ``_STATUS_ROW_LANE_RE``.
_STATUS_ROW_LANE_RE = re.compile(
    r"^\|\s*(?P<lane>[A-Z][A-Z0-9\- ]*?)\s*\|(?P<rest>.*)\|\s*$",
    re.MULTILINE,
)

#: Orchestrator-origin sender labels (server-written tokens; trusted).
_ORCH_SENDER_LABELS = frozenset({"ORCHESTRATOR", "ORCH", "LANE-ORCH"})

# A STATUS table LINE's leading lane cell: ``| <lane> | ...`` — NOT anchored on
# the row's closing pipe. Mirrors server.py ``_STATUS_LINE_LANE_RE`` so a forged
# ``[SIG ...]`` token appended AFTER the row's closing ``|`` still binds to the
# owning lane (anti-spoof, trailing-pipe bypass).
_STATUS_LINE_LANE_RE = re.compile(r"^\|\s*(?P<lane>[A-Z][A-Z0-9\- ]*?)\s*\|")


def _owning_lane_on_line(text: str, pos: int) -> str | None:
    """Owning lane of the STATUS table LINE that offset *pos* sits on.

    Mirrors server.py ``_owning_lane_on_line``: binds a token to the lane named
    in the first cell of its physical line (a line starting with ``|``), so an
    attacker appending a forged ``[SIG ...]`` token AFTER the row's closing
    ``|`` cannot escape sender binding. ``None`` if the line is not a table line
    or names no lane (header row).
    """
    line_start = text.rfind("\n", 0, pos) + 1
    line_end = text.find("\n", pos)
    if line_end == -1:
        line_end = len(text)
    line = text[line_start:line_end]
    m = _STATUS_LINE_LANE_RE.match(line)
    if not m:
        return None
    lane_label = (m.group("lane") or "").strip()
    if lane_label.lower() == "lane":  # header row
        return None
    return lane_label


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
    """Central fan-in hub for the 7 activity-wall event sources.

    Sources: findings, signals, status_notes, history, queue_applier,
    inject_log, governor_log.

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
    all 7 source coroutines via ``asyncio.gather``. Each source is its own
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
        """Backfill the ring from existing state, then launch the fan-in task.

        The file-tail sources seek to EOF on attach, so on a server restart the
        wall would otherwise be empty until brand-new writes arrive. We replay a
        bounded slice of the current on-disk state (HISTORY.md tail, findings/,
        signals/) into the ring *before* the live watchers start, so the
        snapshot is populated immediately after a restart.
        """
        if self._task is not None:
            return  # already running
        try:
            self._backfill()
        except Exception:
            _log.exception("activity-wall: backfill on start failed")
        self._task = asyncio.create_task(self._fan_in(), name="activity-wall-fan-in")

    # ------------------------------------------------------------------
    # History replay on restart
    # ------------------------------------------------------------------

    #: Bound on how much existing state to replay on start.
    BACKFILL_HISTORY_LINES: int = 200
    BACKFILL_MAX_FILES: int = 200

    def _backfill(self) -> None:
        """Emit synthetic events for existing state (bounded, ordered by ts).

        Reads the tail of HISTORY.md, existing findings/*.md, and existing
        signals/*.md, builds events, sorts them oldest-first by ts, and emits
        them so they land in the ring (capped to the ring size). Every read is
        best-effort and never raises out of this method.
        """
        events: list[dict] = []

        # HISTORY.md tail
        history_path = self._mission_dir / "HISTORY.md"
        try:
            if history_path.is_file():
                lines = history_path.read_text(errors="replace").splitlines()
                for raw in lines[-self.BACKFILL_HISTORY_LINES :]:
                    stripped = raw.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    ts = _parse_history_ts(stripped) or _now_utc_iso()
                    events.append(
                        {
                            "type": "history",
                            "lane": None,
                            "ts": ts,
                            "summary": stripped[:200],
                            "payload": {"line": stripped},
                        }
                    )
        except OSError:
            pass

        # findings/ and signals/ existing files
        for sub, etype in (("findings", "finding"), ("signals", "signal")):
            d = self._mission_dir / sub
            try:
                if not d.is_dir():
                    continue
                paths = [
                    p for p in d.iterdir() if p.is_file() and p.name.endswith(".md")
                ]
            except OSError:
                continue
            # Newest files first, capped, so a huge dir doesn't blow the ring.
            try:
                paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            except OSError:
                pass
            for p in paths[: self.BACKFILL_MAX_FILES]:
                try:
                    ev = self._build_file_event(etype, p)
                    if ev:
                        events.append(ev)
                except Exception:
                    _log.exception("activity-wall: backfill error on %s", p)

        # Oldest-first so the ring's natural newest-at-tail ordering holds; cap
        # to the ring maxlen so we never emit more than the buffer can hold.
        events.sort(key=lambda e: e.get("ts") or "")
        for ev in events[-RING_BUFFER_MAXLEN:]:
            self._emit(ev)

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

        Events are sorted by ``ts`` descending. Insertion order is *not* a
        reliable proxy for chronology: findings/signals use file mtime, history
        uses the parsed line timestamp, and ``_backfill`` replays existing state
        before the live watchers attach — so a strict ts sort is required for a
        correct newest-first view. Events with an unparseable/empty ``ts`` sort
        last (treated as oldest).
        """
        buf = list(self._ring)
        buf.sort(key=lambda e: e.get("ts") or "", reverse=True)
        return buf[:limit]

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
        """Run all 7 source coroutines concurrently; restart on unexpected exit."""
        try:
            await asyncio.gather(
                self._source_findings(),
                self._source_signals(),
                self._source_status_notes(),
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
                    # SCHISM FIX (a): a SIGNAL-class finding (``signal-type`` in
                    # YAML frontmatter) ALSO emits a ``type:"signal"`` event so
                    # the signals page updates live for the finding channel — not
                    # just for signals/*.md files. The finding event above is
                    # kept unchanged for the findings page.
                    sig_event = self._build_finding_signal_event(path)
                    if sig_event:
                        self._emit(sig_event)
                except Exception:
                    _log.exception("activity-wall: error processing finding %s", path)
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("activity-wall: findings source crashed")

    def _build_finding_signal_event(self, path: Path) -> dict | None:
        """Emit a ``type:"signal"`` event for a SIGNAL-class finding, else None.

        A finding is SIGNAL-class iff its YAML frontmatter carries a
        ``signal-type`` key (per ``signal_parser.parse_signal``). The emitted
        signal payload mirrors the file/status-note contract
        (``from_lane``/``to_lane``/``topic``/``utc``/``source``/``excerpt``)
        with ``source:"finding"`` and the finding filename.
        """
        from . import signal_parser

        try:
            fm = signal_parser.parse_signal(path)
        except Exception:
            fm = None
        if not fm:
            return None

        from_raw = str(
            fm.get("from-lane")
            or fm.get("from_lane")
            or fm.get("agent")
            or fm.get("lane")
            or ""
        ).strip()
        to_raw = str(
            fm.get("to-lane") or fm.get("to_lane") or fm.get("addressed-to") or ""
        ).strip()
        from_lane = _normalize_lane_label(from_raw, "LANE-UNKNOWN")
        to_lane = _normalize_lane_label(to_raw, "LANE-ALL")
        topic = _slugify(str(fm.get("signal-type", "note")))
        utc = str(fm.get("utc", "")).strip()

        try:
            mtime = path.stat().st_mtime
            ts = (
                datetime.fromtimestamp(mtime, tz=timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
        except OSError:
            ts = _now_utc_iso()

        excerpt = ""
        try:
            raw = path.read_text(errors="replace")
            if raw.startswith("---"):
                end = raw.find("\n---", 3)
                if end >= 0:
                    raw = raw[end + 4 :].lstrip("\n")
            excerpt = raw[:200]
        except OSError:
            pass

        return {
            "type": "signal",
            "lane": from_lane,
            "ts": ts,
            "summary": f"{from_lane}→{to_lane}: {topic}"[:200],
            "payload": {
                "filename": path.name,
                "path": str(path),
                "from_lane": from_lane,
                "to_lane": to_lane,
                "topic": topic,
                "utc": utc,
                "source": "finding",
                "excerpt": excerpt,
            },
        }

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

    # ------------------------------------------------------------------
    # Source 2b: STATUS.md `[SIG ...]` notes (the previously-dark channel)
    # ------------------------------------------------------------------

    async def _source_status_notes(self) -> None:
        """Watch STATUS.md and emit ``type:"signal"`` events for NEW SIG tokens.

        SCHISM FIX (b): the third comms channel — ``[SIG from=X to=Y text=...]``
        tokens embedded in STATUS.md notes — had NO live source, so the signals
        page never updated for it. STATUS.md is rewritten in place (not appended),
        so we poll its mtime and re-parse on change, emitting a signal event only
        for tokens not seen before (tracked by a stable identity key). The sender
        is bound to the OWNING STATUS row (anti-spoof; orchestrator-origin tokens
        stay trusted) — mirroring server.py ``_parse_status_note_signals``.
        """
        from .event_tail import POLL_INTERVAL_S

        status_path = self._mission_dir / "STATUS.md"
        seen: set[str] = set()
        last_mtime: float = -1.0

        # Prime: existing tokens at startup are part of the snapshot/backfill, not
        # live deltas — record them as seen WITHOUT emitting so the first real
        # change doesn't replay the whole file.
        try:
            for ev in self._parse_status_note_events(status_path):
                seen.add(ev["_identity"])
            last_mtime = self._safe_mtime(status_path)
        except Exception:
            _log.exception("activity-wall: status-note prime failed")

        try:
            while True:
                try:
                    await asyncio.sleep(POLL_INTERVAL_S)
                except asyncio.CancelledError:
                    return
                mtime = self._safe_mtime(status_path)
                if mtime == last_mtime:
                    continue
                last_mtime = mtime
                try:
                    for ev in self._parse_status_note_events(status_path):
                        ident = ev.pop("_identity")
                        if ident in seen:
                            continue
                        seen.add(ident)
                        self._emit(ev)
                except Exception:
                    _log.exception("activity-wall: status-note parse failed")
        except asyncio.CancelledError:
            return
        except Exception:
            _log.exception("activity-wall: status-notes source crashed")

    @staticmethod
    def _safe_mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return -1.0

    def _parse_status_note_events(self, status_path: Path) -> list[dict]:
        """Parse STATUS.md SIG tokens → signal events (owning-row-bound sender).

        Returns events carrying a private ``_identity`` key the caller uses for
        new-token dedupe (popped before emit). Sender binding mirrors server.py.
        """
        try:
            if not status_path.is_file():
                return []
            text = status_path.read_text(errors="replace")
        except OSError:
            return []

        try:
            ts = (
                datetime.fromtimestamp(status_path.stat().st_mtime, tz=timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
        except OSError:
            ts = _now_utc_iso()

        # Span → owning lane, from the table rows.
        spans: list[tuple[int, int, str]] = []
        for rm in _STATUS_ROW_LANE_RE.finditer(text):
            lane_label = (rm.group("lane") or "").strip()
            if lane_label.lower() == "lane":
                continue
            spans.append((rm.start(), rm.end(), lane_label))

        def _owning_lane_for(pos: int) -> str | None:
            for start, end, lane in spans:
                if start <= pos < end:
                    return lane
            return None

        events: list[dict] = []
        for idx, m in enumerate(_SIG_TOKEN_RE.finditer(text)):
            from_raw = (m.group(1) or "").strip()
            to_raw = (m.group(2) or "").strip()
            sig_text = (m.group(3) or "").strip()
            claimed_from = _normalize_lane_label(from_raw, "LANE-UNKNOWN")
            to_lane = _normalize_lane_label(to_raw, "LANE-ALL")
            # Prefer the precise closing-pipe-anchored span; fall back to the
            # lane named on the token's physical line so a forged token appended
            # AFTER the row's closing ``|`` (which breaks the span anchor) still
            # binds (anti-spoof, trailing-pipe bypass). Mirrors server.py.
            owning_label = _owning_lane_for(m.start())
            if owning_label is None:
                owning_label = _owning_lane_on_line(text, m.start())
            owning_lane = (
                _normalize_lane_label(owning_label, "LANE-UNKNOWN")
                if owning_label is not None
                else None
            )
            from_unverified = False
            if from_raw.upper() in _ORCH_SENDER_LABELS:
                from_lane = "LANE-ORCH"
            elif owning_lane is None:
                # Token on NO recognizable STATUS line — claimed sender is
                # attacker-controllable; fail closed, never trust it.
                from_lane = "LANE-UNKNOWN"
                from_unverified = True
            else:
                from_lane = owning_lane
                if claimed_from != owning_lane:
                    from_unverified = True

            topic = _slugify(" ".join(sig_text.split()[:5]))
            # Identity is the authoritative sender + target + text, so re-saving
            # STATUS.md with the same tokens does not re-emit, but a genuinely new
            # token (different text/target) does.
            identity = f"{from_lane}|{to_lane}|{sig_text}"
            # Unique per-token id so concurrent status-note signals don't collide
            # on the FE (which keys live signals on ``filename || id``). The
            # ordinal index keeps it stable across re-polls of the same file yet
            # distinct across different tokens. ``filename`` carries the same
            # unique value (server.py contract: ``status-note-<idx>``).
            note_id = f"status-note-{idx}"
            events.append(
                {
                    "type": "signal",
                    "id": note_id,
                    "lane": from_lane,
                    "ts": ts,
                    "summary": f"{from_lane}→{to_lane}: {topic}"[:200],
                    "payload": {
                        "id": note_id,
                        "filename": note_id,
                        "from_lane": from_lane,
                        "claimed_from": claimed_from,
                        "from_unverified": from_unverified,
                        "to_lane": to_lane,
                        "topic": topic,
                        "utc": "",
                        "source": "status-note",
                        "excerpt": sig_text[:200],
                    },
                    "_identity": identity,
                }
            )
        return events

    def _build_file_event(self, event_type: str, path: Path) -> dict | None:
        """Build an event dict for a findings/ or signals/ file.

        For ``event_type == "signal"`` the canonical filename grammar
        (``LANE-<FROM>-to-LANE-<TO>-<topic>-<UTC>.md``) is parsed so the event
        carries ``from_lane``/``to_lane``/``topic``/``utc`` and a directional
        summary. Non-canonical / legacy signal files fall back to the plain
        filename-only payload (and still emit) so nothing is dropped.
        """
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

        if event_type == "signal":
            return self._build_signal_event(path, ts, lane)

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

    def _build_signal_event(self, path: Path, ts: str, lane: str | None) -> dict | None:
        """Build an enriched signal event from a signals/ file.

        Parses the canonical grammar to extract from/to/topic/utc and a body
        excerpt. Falls back gracefully when the filename is non-canonical.
        """
        stem = path.name[:-3] if path.name.endswith(".md") else path.name
        from_lane = to_lane = topic = utc = ""
        parsed = parse_signal_filename(path.name)
        if parsed is not None:
            from_lane = parsed["from_lane"]
            to_lane = parsed["to_lane"]
            topic = parsed["topic"]
            utc = parsed["utc"]

        excerpt = ""
        try:
            excerpt = path.read_text(errors="replace")[:200]
        except OSError:
            pass

        # ``lane`` from the agent-NNNN-L- filename grammar is None for canonical
        # cross-lane signal files (``LANE-X-to-LANE-Y-...``). Fall back to the
        # parsed ``from_lane`` so the signal row is attributed to its sender
        # instead of rendering ``lane: null``.
        if lane is None and from_lane:
            lane = from_lane

        if from_lane and to_lane:
            summary = f"{from_lane}→{to_lane}: {topic}"[:200]
        else:
            summary = stem[:200]

        return {
            "type": "signal",
            "lane": lane,
            "ts": ts,
            "summary": summary,
            "payload": {
                "filename": path.name,
                "path": str(path),
                "from_lane": from_lane,
                "to_lane": to_lane,
                "topic": topic,
                "utc": utc,
                "source": "file",
                "excerpt": excerpt,
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
