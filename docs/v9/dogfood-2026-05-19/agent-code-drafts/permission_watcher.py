"""megalodon_ui.permission_watcher — scan lane stream logs for Claude Code permission prompts.

Detection: reads the last _STREAM_TAIL_BYTES of each lane's .fleet/<LANE>.stream.log,
strips ANSI escape sequences, and matches the Claude Code permission-banner pattern.

Suppression window: after clear_lane() is called (e.g. operator approval keypress sent),
re-detection is suppressed for SUPPRESSION_WINDOW_SECONDS. This prevents the watcher
from re-adding the same prompt while the TUI clears the permission block — the Claude
REPL renders the prompt for ~1s after the keystroke is received before the block disappears.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

_ANSI_ESC_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

# Claude Code renders permission prompts containing at least one of these markers.
_PERM_RE = re.compile(
    r"Tool use requires permission"
    r"|Do you want to allow"
    r"|\[1\]\s*Allow",
    re.IGNORECASE,
)

_STREAM_TAIL_BYTES = 10_000


class PermissionWatcher:
    """Watches per-lane pipe-pane stream logs for Claude Code permission prompts.

    Suppression window: after clear_lane() is called, re-detection is suppressed
    for SUPPRESSION_WINDOW_SECONDS. This prevents false re-detection while the
    Claude REPL TUI is still rendering the permission block after approval.
    """

    SUPPRESSION_WINDOW_SECONDS: float = 5.0

    def __init__(self, mission_dir: Path, stream_tail_bytes: int = _STREAM_TAIL_BYTES):
        self._mission_dir = Path(mission_dir)
        self._stream_tail_bytes = stream_tail_bytes
        self._suppressed_until: dict[str, float] = {}

    def _is_suppressed(self, lane: str) -> bool:
        return time.monotonic() < self._suppressed_until.get(lane, 0.0)

    def scan_lane(self, lane_short: str) -> dict | None:
        """Scan one lane's stream log for a permission prompt.

        Returns {lane, detected_text} when a prompt is detected and the lane
        is not within its suppression window; None otherwise.
        """
        lane = lane_short.upper()
        if self._is_suppressed(lane):
            return None
        log_path = self._mission_dir / ".fleet" / f"{lane}.stream.log"
        try:
            st = log_path.stat()
            with log_path.open("rb") as fh:
                fh.seek(max(0, st.st_size - self._stream_tail_bytes))
                raw = fh.read().decode("utf-8", errors="replace")
        except OSError:
            return None
        clean = _ANSI_ESC_RE.sub("", raw)
        m = _PERM_RE.search(clean)
        if m:
            return {"lane": lane, "detected_text": m.group(0)}
        return None

    def scan_all(self, lane_shorts: list[str]) -> dict[str, dict | None]:
        """Scan all given lanes. Returns {LANE: prompt_dict_or_None}."""
        return {s.upper(): self.scan_lane(s) for s in lane_shorts}

    def clear_lane(self, lane_short: str) -> None:
        """Record operator approval for a lane; start suppression window.

        Suppresses re-detection for SUPPRESSION_WINDOW_SECONDS so the watcher
        does not re-add the prompt while the TUI clears its permission block.
        """
        lane = lane_short.upper()
        self._suppressed_until[lane] = time.monotonic() + self.SUPPRESSION_WINDOW_SECONDS
