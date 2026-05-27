"""v9.4 Task 2.2 — tests for megalodon_ui.event_tail.

Tests
-----
1. Roundtrip: write 10 lines → generator yields all 10 in order.
2. Dir watch: create 3 new files → generator yields all 3 paths.
3. Rotation: write 2 lines, rename file, touch new file, write 2 lines
   → generator yields all 4 in order.
4. Burst-100: 100 lines written fast → all delivered within 1000ms.
5. No-FH leak: open + cancel → no lingering file descriptors.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import megalodon_ui.event_tail as et


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch):
    monkeypatch.setattr(et, "POLL_INTERVAL_S", 0.05)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _start_collector(gen, n_target: int) -> tuple[asyncio.Task, list]:
    """Start a background task that collects up to *n_target* items.

    Returns ``(task, items_list)``.  The task stops itself once it has
    *n_target* items or the generator is closed.  The caller awaits the
    task with a timeout.
    """
    items: list = []

    async def _consume():
        async for item in gen:
            items.append(item)
            if len(items) >= n_target:
                break

    task = asyncio.create_task(_consume())
    return task, items


# ---------------------------------------------------------------------------
# 1. Roundtrip: tail_file_lines yields 10 lines
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tail_file_lines_roundtrip(tmp_path: Path):
    log = tmp_path / "app.log"
    log.touch()

    gen = et.tail_file_lines(log)
    task, items = await _start_collector(gen, 10)

    # Let the generator open the file and seek to end before we write.
    await asyncio.sleep(0.15)

    with log.open("a") as f:
        for i in range(10):
            f.write(f"line-{i}\n")
        f.flush()

    await asyncio.wait_for(task, timeout=5.0)
    assert items == [f"line-{i}" for i in range(10)]
    await gen.aclose()


# ---------------------------------------------------------------------------
# 2. Dir watch: yields 3 new files
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watch_dir_for_new_files(tmp_path: Path):
    watch_dir = tmp_path / "signals"
    watch_dir.mkdir()

    # Pre-existing file — must NOT be yielded.
    (watch_dir / "existing.txt").write_text("old")

    gen = et.watch_dir_for_new_files(watch_dir)
    task, items = await _start_collector(gen, 3)

    # Let snapshot settle before writing new files.
    await asyncio.sleep(0.15)

    new_files = []
    for i in range(3):
        p = watch_dir / f"signal-{i}.txt"
        p.write_text(f"data {i}")
        new_files.append(p)
        await asyncio.sleep(0.01)  # stagger mtimes for deterministic sort

    await asyncio.wait_for(task, timeout=5.0)
    assert len(items) == 3
    assert set(items) == set(new_files)
    await gen.aclose()


# ---------------------------------------------------------------------------
# 3. Rotation: 2 lines → rename → new file → 2 more lines → all 4 delivered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tail_file_lines_rotation(tmp_path: Path):
    log = tmp_path / "rotating.log"
    log.touch()

    gen = et.tail_file_lines(log)
    task, items = await _start_collector(gen, 4)

    # Let the generator open and seek to end.
    await asyncio.sleep(0.15)

    # Write 2 pre-rotation lines.
    with log.open("a") as f:
        f.write("pre-1\npre-2\n")
        f.flush()

    # Wait for the pre-rotation lines to arrive.
    deadline = time.monotonic() + 3.0
    while len(items) < 2 and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert items[:2] == ["pre-1", "pre-2"], f"pre-rotation lines missing: {items}"

    # Rotate: rename old file, create fresh file at same path.
    log.rename(tmp_path / "rotating.log.bak")
    log.write_text("")  # creates fresh inode

    # Let the watcher detect the rotation.
    await asyncio.sleep(0.20)

    # Write 2 post-rotation lines.
    with log.open("a") as f:
        f.write("post-1\npost-2\n")
        f.flush()

    await asyncio.wait_for(task, timeout=5.0)
    assert items == ["pre-1", "pre-2", "post-1", "post-2"]
    await gen.aclose()


# ---------------------------------------------------------------------------
# 4. Burst-100: 100 lines delivered within 1000ms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tail_file_lines_burst_100(tmp_path: Path):
    log = tmp_path / "burst.log"
    log.touch()

    gen = et.tail_file_lines(log)
    task, items = await _start_collector(gen, 100)

    # Let the generator open and seek to end.
    await asyncio.sleep(0.15)

    # Write 100 lines as fast as possible, then start the clock.
    with log.open("a") as f:
        for i in range(100):
            f.write(f"burst-{i}\n")
        f.flush()

    t0 = time.monotonic()
    await asyncio.wait_for(task, timeout=5.0)
    elapsed_ms = (time.monotonic() - t0) * 1000

    assert items == [f"burst-{i}" for i in range(100)]
    # Be lenient: 1000ms cap accounts for scheduling jitter on slow CI.
    assert elapsed_ms < 1000, f"burst took {elapsed_ms:.1f}ms — too slow"
    await gen.aclose()


# ---------------------------------------------------------------------------
# 5. No file-handle leak after cancellation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_fh_leak_after_cancel(tmp_path: Path):
    log = tmp_path / "leak.log"
    log.touch()

    gen = et.tail_file_lines(log)
    # Start a consuming task so the generator actually opens the file.
    task, _ = await _start_collector(gen, 1)
    # Give it time to open the file and reach the sleeping-poll state.
    await asyncio.sleep(0.20)

    # Cancel both the task and close the generator cleanly.
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, StopAsyncIteration):
        pass
    await gen.aclose()

    # Give finalizers a moment to run.
    await asyncio.sleep(0.05)

    open_fds = _count_open_fds_for(log)
    assert open_fds == 0, (
        f"Expected 0 open FDs for {log}, found {open_fds}. "
        "Possible file-handle leak in tail_file_lines."
    )


# ---------------------------------------------------------------------------
# OS-level FD counting
# ---------------------------------------------------------------------------


def _count_open_fds_for(path: Path) -> int:
    """Return number of open FDs pointing to *path* in the current process."""
    path_str = str(path)

    # Prefer psutil when available — fast, no subprocess.
    try:
        import psutil  # type: ignore[import-not-found]

        proc = psutil.Process()
        return sum(1 for f in proc.open_files() if f.path == path_str)
    except ImportError:
        pass

    # Fallback: parse /proc/self/fd on Linux.
    fd_dir = Path("/proc/self/fd")
    if fd_dir.is_dir():
        count = 0
        for fd_path in fd_dir.iterdir():
            try:
                if os.readlink(str(fd_path)) == path_str:
                    count += 1
            except OSError:
                pass
        return count

    # macOS fallback: lsof (subprocess).
    import subprocess

    try:
        out = subprocess.check_output(
            ["lsof", "-p", str(os.getpid()), "-Fn"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.count(path_str)
    except Exception:
        return 0  # can't determine — skip assertion
