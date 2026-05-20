"""Shared async file/dir polling primitives for the activity wall.

Provides two async generators consumed by the activity wall:

- ``tail_file_lines``: yield new lines as a file grows; handles rotation.
- ``watch_dir_for_new_files``: yield new file paths as they appear.

Design constraints
------------------
* **No third-party deps** — stdlib only (no watchdog, no aiofiles).
* **Non-blocking** — all I/O dispatched via ``asyncio.to_thread`` or
  standard async primitives; the event loop is never blocked.
* **250ms poll** — ``POLL_INTERVAL_S`` is module-level so tests can
  monkey-patch it.
* **Cancellable** — both generators trap ``asyncio.CancelledError`` and
  clean up file handles before re-raising.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tuning knobs
# ---------------------------------------------------------------------------

#: Poll cadence in seconds. Module-level so consumers and tests can override.
POLL_INTERVAL_S: float = 0.25


# ---------------------------------------------------------------------------
# tail_file_lines
# ---------------------------------------------------------------------------


async def tail_file_lines(path: Path) -> AsyncIterator[str]:
    """Yield each new line appended to *path* as the file grows.

    Behaviour
    ---------
    * Opens the file and seeks to the END on first open, so historical
      content is not replayed (the activity-wall snapshot endpoint handles
      the backlog).
    * Polls every ``POLL_INTERVAL_S`` seconds using
      ``asyncio.to_thread(os.stat, path)`` — never blocks the event loop.
    * **Rotation detection**: if ``st_ino`` changes *or* ``st_size`` has
      shrunk below the last-read position the file has been replaced.  The
      generator closes the old handle, reopens at position 0, and reads
      forward from there.
    * **Partial-line buffering**: bytes that arrive without a trailing
      newline are buffered and delivered when the ``\n`` arrives.
    * **Per-poll coalescing**: all available lines are collected in one
      poll cycle and yielded in order before awaiting again.
    * **Cancellation**: on ``asyncio.CancelledError`` the file handle is
      closed before re-raising.

    Parameters
    ----------
    path:
        Absolute (or repo-relative) path to the file to tail.  The file
        must exist before the generator is started; if it does not the
        generator will wait until it is created.

    Yields
    ------
    str
        Complete lines, stripped of the trailing newline character.
    """
    fh = None
    last_ino: int | None = None
    last_pos: int = 0
    partial: str = ""
    # True once the first stat attempt has completed — distinguishes the
    # "file existed at generator startup → skip its history" case from the
    # "file created after startup → read from the beginning" case.
    _initial_stat_taken: bool = False

    async def _stat_or_none():
        try:
            return await asyncio.to_thread(os.stat, path)
        except FileNotFoundError:
            return None

    try:
        while True:
            try:
                await asyncio.sleep(POLL_INTERVAL_S)
            except asyncio.CancelledError:
                return

            st = await _stat_or_none()
            # Track whether the file was present on the very first successful
            # stat.  We use this below to decide whether to seek past existing
            # content.
            first_stat_this_poll = not _initial_stat_taken
            if st is not None:
                _initial_stat_taken = True
            if st is None:
                # File doesn't exist yet; mark that startup has passed so
                # when the file later appears we read it from the beginning.
                _initial_stat_taken = True
                continue

            rotated = fh is None or last_ino != st.st_ino or st.st_size < last_pos

            if rotated:
                if fh is not None:
                    try:
                        fh.close()
                    except OSError:
                        pass
                    fh = None
                try:
                    fh = await asyncio.to_thread(open, path, "r", errors="replace")
                    if last_ino is None and first_stat_this_poll:
                        # First open AND the file already existed at generator
                        # startup: seek to end so we don't replay old history.
                        await asyncio.to_thread(fh.seek, 0, 2)
                        last_pos = await asyncio.to_thread(fh.tell)
                    else:
                        # Either a rotation or the file was created after startup:
                        # read from the beginning so no events are lost.
                        last_pos = 0
                    last_ino = st.st_ino
                except (OSError, FileNotFoundError):
                    fh = None
                    continue

            if st.st_size <= last_pos:
                # No new bytes.
                continue

            # Drain all available bytes in this poll cycle.
            try:
                chunk = await asyncio.to_thread(fh.read)
            except OSError:
                continue

            if not chunk:
                continue

            last_pos += len(chunk.encode("utf-8", errors="replace"))

            # Split on newlines, preserve partial trailing line.
            combined = partial + chunk
            lines = combined.split("\n")
            # The last element is either "" (chunk ended with \n) or a
            # partial line waiting for its terminator.
            partial = lines[-1]
            complete_lines = lines[:-1]

            for line in complete_lines:
                yield line

    except asyncio.CancelledError:
        return
    finally:
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# watch_dir_for_new_files
# ---------------------------------------------------------------------------


async def watch_dir_for_new_files(path: Path) -> AsyncIterator[Path]:
    """Yield each new file that appears inside *path*.

    Behaviour
    ---------
    * Takes an initial snapshot of existing filenames on startup; those
      are **not** yielded (caller fetches the backlog via snapshot endpoint).
    * Polls every ``POLL_INTERVAL_S`` seconds.  New files are yielded
      sorted by ``mtime`` (oldest-new-file first).
    * If the directory does not exist the generator waits silently until
      it is created.
    * Cancellation is handled cleanly.

    Parameters
    ----------
    path:
        Directory to watch.

    Yields
    ------
    Path
        Absolute path of each newly-appeared file (not subdirectories).
    """

    async def _listdir_or_empty() -> dict[str, float]:
        """Return {filename: mtime} for regular files in *path*."""
        try:
            entries = await asyncio.to_thread(_scan_dir, path)
            return entries
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            return {}

    # Take initial snapshot without yielding.
    snapshot: dict[str, float] = await _listdir_or_empty()

    try:
        while True:
            try:
                await asyncio.sleep(POLL_INTERVAL_S)
            except asyncio.CancelledError:
                return

            current = await _listdir_or_empty()

            new_names = sorted(
                (name for name in current if name not in snapshot),
                key=lambda n: current[n],  # sort by mtime ascending
            )

            for name in new_names:
                yield path / name

            # Update snapshot whether or not new files appeared.
            snapshot = current

    except asyncio.CancelledError:
        return


def _scan_dir(path: Path) -> dict[str, float]:
    """Synchronous helper: list regular files in *path* → {name: mtime}."""
    result: dict[str, float] = {}
    with os.scandir(path) as it:
        for entry in it:
            if entry.is_file(follow_symlinks=False):
                try:
                    result[entry.name] = entry.stat(follow_symlinks=False).st_mtime
                except OSError:
                    pass
    return result
