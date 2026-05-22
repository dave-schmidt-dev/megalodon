"""v9.3 — surface Claude Code REPL permission prompts to the dashboard.

When a lane is spawned in ``live_repl`` mode with a tight ``--allowedTools``
allowlist, the agent will hit Claude Code's permission prompt for any
operation outside that allowlist (e.g. ``python3 -c ...``, ``uv run ...``).
The prompt renders inside the agent's tmux pane and blocks the agent until
something answers it.

This module tails each lane's pipe-pane stream log, detects the prompt
marker in the latest TUI output, extracts the command preview, and exposes
the pending prompts to the FastAPI server so the dashboard can render them
in a unified banner. The server then calls :func:`respond` to tmux
send-keys the operator's choice (``1`` for approve, ``3`` for deny).

Detection contract (verified against ``claude`` v2.1.138 REPL output):

* Prompt marker: ``"Do you want to proceed?"`` — appears as plain text after
  ANSI-stripping the tail of the stream log.
* Command preview: extracted from the text between the most-recent
  ``"Bash command"`` (or ``"Edit file"``, ``"Read file"``, etc.) marker and
  the ``"Do you want to proceed?"`` marker.
* Resolution: after the operator approves/denies, Claude Code clears the
  prompt block from the TUI via CSI cursor-up + erase-line sequences, so
  the marker no longer appears in the tail. We poll, and the absence of
  the marker on the next scan clears the pending state.

The watcher is a coroutine task spawned at server startup and cancelled at
shutdown. It is intentionally simple: scan every ``POLL_INTERVAL`` seconds,
read the last ``TAIL_BYTES`` from each lane's stream log, ANSI-strip, look
for the marker. No SSE, no event sourcing — the dashboard polls
``/api/v1/permission_prompts`` on the same cadence as the existing state
endpoint.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tuning knobs (module-level so tests can monkey-patch)
# ---------------------------------------------------------------------------

POLL_INTERVAL_SECONDS: float = 1.0
# v9.3.5: bumped from 4096 → 32768. A Sonnet 4.6 agent doing high-effort
# thinking can fill the previous 4KB window with thinking-dots + scope-context
# lines in ~30s, scrolling the actual permission prompt OUT of the tail and
# making it invisible to the dashboard forever (operator can't approve what
# they can't see). LANE-E was blocked for 195 min on a prompt the watcher
# couldn't see because of this. 32KB covers ~8 minutes of dense thinking.
TAIL_BYTES: int = 32768
# The Claude REPL TUI renders "Do you want to proceed?" using per-character
# cursor positioning, so after ANSI-stripping the spaces between letters are
# gone. We match against the no-space form with a regex that also tolerates
# any whitespace between letters (handles both single-shot prints and the
# fragmented TUI render).
_PROMPT_MARKER_RE = re.compile(r"Do\s*you\s*want\s*to\s*proceed\??", re.IGNORECASE)
# Back-compat constant for tests that imported the old string. Kept as the
# regex-compatible no-space form so direct substring searches in test data
# also work.
PROMPT_MARKER: str = "Do you want to proceed?"

# Tool-call header markers that precede the command summary inside a Claude
# REPL approval prompt. Order matters only insofar as we want the most-
# specific match; the watcher uses ``rfind`` to locate the LATEST occurrence
# before the prompt marker.
_TOOL_HEADERS: tuple[str, ...] = (
    "Bash command",
    "Edit file",
    "Write file",
    "Read file",
    "WebFetch",
    "WebSearch",
    "Monitor",
    "Glob",
    "Grep",
    "TodoWrite",
    "NotebookEdit",
    "Task",
)

# ANSI strip patterns — handles CSI (most escapes), OSC (titlesetting), and
# the short two-byte sequences ``\x1bN``, ``\x1bO``, ``\x1bP``, ``\x1b\\``,
# ``\x1b=``, ``\x1b>``.
_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
_OSC_RE = re.compile(r"\x1b\][^\x07]*\x07")
_SHORT_RE = re.compile(r"\x1b[NOP\\=>]")


def _strip_ansi(text: str) -> str:
    text = _CSI_RE.sub("", text)
    text = _OSC_RE.sub("", text)
    text = _SHORT_RE.sub("", text)
    return text.replace("\r", "\n")


def _extract_command_preview(stripped: str, prompt_idx: int) -> str:
    """Pull the most-recent tool-call header + summary out of the tail.

    Strategy:
      1. Try to find one of the known tool-header markers (Bash command,
         Monitor, etc.) and excerpt from there.
      2. If no known marker, fall back to "last 400 chars before prompt"
         with horizontal-rule garbage trimmed. Better to show something
         than ``<unknown command>``.
    """
    before = stripped[:prompt_idx]
    best_idx = -1
    best_header = ""
    for header in _TOOL_HEADERS:
        i = before.rfind(header)
        if i > best_idx:
            best_idx = i
            best_header = header
    if best_idx >= 0:
        excerpt = before[best_idx : best_idx + 280]
        excerpt = re.sub(r"\s+", " ", excerpt).strip()
        excerpt = excerpt.split("Do you want")[0].strip()
        return f"[{best_header}] {excerpt[len(best_header) :].strip()}"

    # Fallback: take the last ~400 chars before the prompt and clean them.
    tail = before[-400:]
    # Strip long runs of horizontal-rule chars + collapse whitespace.
    tail = re.sub(r"[─━═]{4,}", " ", tail)
    tail = re.sub(r"\s+", " ", tail).strip()
    if not tail:
        return "<no context available>"
    return f"[unknown tool] {tail[-260:]}"


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class PromptInfo:
    """One pending permission prompt for a single lane."""

    lane_short: str
    lane_name: str
    command_preview: str
    detected_at_utc: str
    # Stable hash of the command_preview so the FE can dedupe repeated polls
    # and so the watcher knows when the prompt content has CHANGED (vs the
    # same prompt still standing).
    fingerprint: str = field(default="")

    def to_json(self) -> dict:
        return {
            "lane": self.lane_short,
            "lane_name": self.lane_name,
            "command": self.command_preview,
            "detected_at": self.detected_at_utc,
            "fingerprint": self.fingerprint,
        }


# ---------------------------------------------------------------------------
# PermissionWatcher
# ---------------------------------------------------------------------------


class PermissionWatcher:
    """Tails every lane's stream log; surfaces active permission prompts.

    Parameters
    ----------
    mission_dir:
        Absolute path to the mission directory (contains ``.fleet/``).
    lanes:
        List of ``(short, name)`` tuples for every configured lane.
    """

    def __init__(
        self,
        mission_dir: Path,
        lanes: list[tuple[str, str]],
        on_change: Callable[[str, PromptInfo | None, str | None], None] | None = None,
        capture_fn: Callable[[str], str | None] | None = None,
    ) -> None:
        self.mission_dir = mission_dir
        self.lanes = lanes
        # Live-screen confirmation. The stream log is append-only, so a prompt's
        # marker text persists in the tail long after the operator answered it
        # (the REPL erases it from the *screen* via CSI sequences, but those
        # bytes stay in the captured log). After the v9.3.5 TAIL_BYTES 4K→32K
        # bump a resolved marker lingers ~8 min, so the dashboard surfaced
        # phantom prompts and "approve" sent "1" to the REPL's main input.
        # We confirm a detected marker is ALSO on the live tmux pane before
        # surfacing it. ``capture_fn(short) -> pane text | None`` is injectable
        # for tests; the default captures ``lane-<short>`` from the mission's
        # tmux socket. Confirmation FAILS OPEN (returns the prompt) on any error
        # or when no live socket exists — never hide a real prompt.
        self._capture_fn = capture_fn
        # Optional callback fired on every state transition:
        #   on_change(lane_short, new_info, action)
        # • pending-add: on_change(lane, PromptInfo, None)
        # • clear:       on_change(lane, None, action)  — action is the respond
        #                action string ("approve"/"approve_remember"/"deny") or
        #                None when cleared without a specific action.
        # A misbehaving callback must NOT crash the watcher — all calls are
        # wrapped in try/except inside the firing helpers.
        self._on_change = on_change
        # Lane short → current pending prompt, or None.
        self._pending: dict[str, PromptInfo | None] = {s: None for s, _ in lanes}
        self._task: asyncio.Task | None = None
        # Re-flash suppression. After clear_lane(), record (fingerprint, expires_at)
        # so the next ~5s of scans ignore re-matches with the same fingerprint.
        # Without this, the prompt re-appears in the tail (Claude REPL takes 1-2s
        # to scroll past the prompt text) before the watcher's next scan sees it
        # gone — UX is "click approve → prompt disappears → flashes back for ~1s
        # → gone for good." The fingerprint check means a NEW prompt with different
        # content still surfaces immediately; only re-detections of the cleared
        # prompt are suppressed.
        self._suppressed: dict[str, tuple[str, float]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Launch the background polling task."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Cancel the polling task."""
        if self._task is None or self._task.done():
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # Public read API
    # ------------------------------------------------------------------

    def pending(self) -> list[PromptInfo]:
        """Snapshot of currently-pending prompts across all lanes."""
        return [p for p in self._pending.values() if p is not None]

    def pending_for_lane(self, lane_short: str) -> PromptInfo | None:
        return self._pending.get(lane_short)

    # Window (seconds) after clear_lane during which a same-fingerprint re-match
    # is ignored. Tuned to cover the Claude REPL's redraw latency (~1-2s observed)
    # plus margin. Exposed at module level so tests can monkey-patch it tight.
    CLEAR_SUPPRESSION_SECONDS: float = 5.0

    def clear_lane(self, lane_short: str, action: str | None = None) -> None:
        """Mark a lane's prompt resolved (called after send-keys response).

        Parameters
        ----------
        lane_short:
            Short lane identifier (e.g. ``"A"``).
        action:
            The operator action that resolved the prompt — one of
            ``"approve"``, ``"approve_remember"``, ``"deny"``, or ``None``
            when cleared without a specific action (backward-compat callers
            that omit the parameter). Forwarded to the ``on_change`` callback.

        Also arms a per-lane suppression window: any same-fingerprint re-match
        within ``CLEAR_SUPPRESSION_SECONDS`` is ignored, so the prompt cannot
        re-flash in the dashboard while the REPL finishes redrawing.
        """
        existing = self._pending.get(lane_short)
        if existing is not None:
            self._suppressed[lane_short] = (
                existing.fingerprint,
                time.monotonic() + self.CLEAR_SUPPRESSION_SECONDS,
            )
        if lane_short in self._pending:
            self._pending[lane_short] = None
        self._fire_change(lane_short, None, action)

    def _fire_change(
        self,
        lane_short: str,
        info: PromptInfo | None,
        action: str | None,
    ) -> None:
        """Invoke ``self._on_change`` if set, swallowing any exception."""
        if self._on_change is None:
            return
        try:
            self._on_change(lane_short, info, action)
        except Exception:
            _log.exception("permission_watcher on_change callback raised — ignoring")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        """Poll loop. Resilient to file-missing / decode errors per scan."""
        while True:
            try:
                self._scan_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.exception("permission_watcher scan error")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    def _scan_once(self) -> None:
        for short, name in self.lanes:
            # Prefer the LIVE pane (authoritative current screen) over the
            # append-only stream log. The log is wrong in BOTH directions:
            #   • it retains an answered prompt's marker (the REPL erases the
            #     screen, not the byte stream) → phantom prompts (FM-2); and
            #   • under heavy repaint (e.g. a thinking spinner ticking for
            #     minutes while blocked) a LIVE marker scrolls out of the
            #     TAIL_BYTES window → the prompt goes invisible (FM-1, the
            #     195-min incident).
            # A blocking prompt is always on the live screen and an answered
            # one is always gone, so capture-pane is correct for both. Fall
            # back to the stream-log tail only when no live capture is
            # available (unit tests / no running fleet).
            live = self._capture_pane(short)
            if live is not None:
                stripped = _strip_ansi(live)
            else:
                path = self.mission_dir / ".fleet" / f"{short}.stream.log"
                try:
                    stripped = self._read_tail_stripped(path)
                except FileNotFoundError:
                    continue
            # Use the regex so we tolerate both contiguous and fragmented
            # renders of the prompt marker. Take the LAST match (most
            # recent prompt in the tail).
            matches = list(_PROMPT_MARKER_RE.finditer(stripped))
            if not matches:
                self._pending[short] = None
                continue
            idx = matches[-1].start()
            preview = _extract_command_preview(stripped, idx)
            fingerprint = f"{hash(preview):x}"
            # Re-flash suppression: if we recently cleared this same fingerprint
            # and the suppression window hasn't expired, ignore the re-match.
            # The REPL's redraw just hasn't caught up yet.
            suppressed = self._suppressed.get(short)
            if suppressed is not None:
                sup_fp, sup_until = suppressed
                if time.monotonic() < sup_until:
                    if sup_fp == fingerprint:
                        continue
                else:
                    # Window expired — drop the entry so a NEW prompt with the
                    # same fingerprint (unlikely but possible) is not blocked.
                    self._suppressed.pop(short, None)
            existing = self._pending[short]
            if existing is None or existing.fingerprint != fingerprint:
                new_info = PromptInfo(
                    lane_short=short,
                    lane_name=name,
                    command_preview=preview,
                    detected_at_utc=_now_utc_iso(),
                    fingerprint=fingerprint,
                )
                self._pending[short] = new_info
                self._fire_change(short, new_info, None)

    def _capture_pane(self, short: str) -> str | None:
        """Capture lane ``short``'s visible tmux pane as text, or None.

        Uses the injected ``capture_fn`` when set (tests); otherwise shells out
        to ``tmux capture-pane`` against the mission's socket. Returns None when
        the socket is absent (no live fleet) or capture fails.
        """
        if self._capture_fn is not None:
            return self._capture_fn(short)
        socket = self.mission_dir / ".fleet" / "tmux.sock"
        if not socket.exists():
            return None
        try:
            proc = subprocess.run(
                [
                    "tmux",
                    "-S",
                    str(socket),
                    "capture-pane",
                    "-p",
                    "-t",
                    f"lane-{short}",
                ],
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout

    @staticmethod
    def _read_tail_stripped(path: Path) -> str:
        """Read the last ``TAIL_BYTES`` from ``path`` and ANSI-strip it."""
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            data = f.read()
        return _strip_ansi(data.decode("utf-8", errors="replace"))
