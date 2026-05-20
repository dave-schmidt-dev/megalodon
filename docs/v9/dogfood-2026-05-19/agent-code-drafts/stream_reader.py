"""megalodon_ui.stream_reader — server-owned async tail of per-lane stream logs (CV-9).

LaneStreamReader tails `.fleet/<lane>.stream.log`, parses each line through
the lane's HarnessAdapter, and delivers typed Event objects to a caller-
supplied callback. It is designed to run as a long-lived asyncio task inside
the server lifespan and exits cleanly when `stop_event` is set.

Design constraints from the P1-C plan (2026-05-19T20-10-00Z):
  - 100ms EOF-retry poll (low CPU, sub-second latency for dashboard "last activity")
  - 200ms file-existence poll, 5s timeout (gives tmux pipe-pane time to initialise)
  - asyncio.to_thread() for all file I/O (readline blocks; don't block the event loop)
  - File-rotation detection via tell() > stat().st_size (re-seeks to 0 on shrinkage)
  - Best-effort: file-never-appears → WARNING logged, reader exits, mission continues
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable

from megalodon_ui.harnesses.base import Event, HarnessAdapter

_log = logging.getLogger(__name__)

FILE_EXIST_TIMEOUT_S: float = 5.0
FILE_EXIST_POLL_S: float = 0.2
EOF_POLL_S: float = 0.1


class LaneStreamReader:
    """Async tail-follower for a single lane's pipe-pane stream log.

    Usage::

        reader = LaneStreamReader(session.stream_log, adapter, lane="C")
        stop = asyncio.Event()
        asyncio.create_task(reader.run(on_event_callback, stop_event=stop))
        # ... later:
        stop.set()
    """

    def __init__(
        self,
        stream_log: Path,
        adapter: HarnessAdapter,
        lane: str,
    ) -> None:
        self._log_path = stream_log
        self._adapter = adapter
        self._lane = lane
        self._logger = logging.getLogger(f"{__name__}.{lane}")

    async def run(
        self,
        on_event: Callable[[Event], Awaitable[None]],
        *,
        stop_event: asyncio.Event,
    ) -> None:
        """Tail stream_log, parse lines via adapter, deliver Events.

        Returns when stop_event is set or the file never appears (degraded mode).
        Never raises; all errors are logged as WARNING.
        """
        if not await self._wait_for_file(stop_event):
            self._logger.warning(
                "stream log %s never appeared within %.0fs; "
                "stream reading disabled for lane %s",
                self._log_path,
                FILE_EXIST_TIMEOUT_S,
                self._lane,
            )
            return
        await self._tail_and_parse(on_event, stop_event=stop_event)

    async def _wait_for_file(self, stop_event: asyncio.Event) -> bool:
        """Wait up to FILE_EXIST_TIMEOUT_S for _log_path to exist.

        Returns True if the file appeared; False if timed out or stop was set.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + FILE_EXIST_TIMEOUT_S
        while loop.time() < deadline:
            if stop_event.is_set():
                return False
            if self._log_path.exists():
                return True
            await asyncio.sleep(FILE_EXIST_POLL_S)
        return self._log_path.exists()

    async def _tail_and_parse(
        self,
        on_event: Callable[[Event], Awaitable[None]],
        *,
        stop_event: asyncio.Event,
    ) -> None:
        """Open _log_path and tail it until stop_event is set."""
        try:
            fh = await asyncio.to_thread(
                lambda: open(self._log_path, "r", encoding="utf-8", errors="replace")
            )
        except OSError as exc:
            self._logger.warning("cannot open stream log %s: %s", self._log_path, exc)
            return

        try:
            while not stop_event.is_set():
                line: str = await asyncio.to_thread(fh.readline)
                if not line:
                    # EOF — check for log rotation before sleeping.
                    try:
                        pos = await asyncio.to_thread(fh.tell)
                        size = self._log_path.stat().st_size
                        if pos > size:
                            await asyncio.to_thread(lambda: fh.seek(0))
                    except OSError:
                        pass
                    await asyncio.sleep(EOF_POLL_S)
                    continue
                try:
                    event = self._adapter.parse_stream_line(line)
                except Exception as exc:
                    self._logger.debug(
                        "parse_stream_line raised for lane %s: %s", self._lane, exc
                    )
                    continue
                if event is not None:
                    try:
                        await on_event(event)
                    except Exception as exc:
                        self._logger.warning(
                            "on_event callback raised for lane %s: %s", self._lane, exc
                        )
        finally:
            await asyncio.to_thread(fh.close)
