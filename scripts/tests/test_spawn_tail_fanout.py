"""Tests for FleetSpawner tail subprocess + fan-out (Task 4.1).

Plan §6.2 / plan §6.4: each lane runs ``tail -c +1 -F <stream_log>`` in an
asyncio subprocess. The producer reads 8 KiB chunks and fans them out to
every queue in ``LaneSession.subscribers`` via ``put_nowait``; on
``QueueFull``, drop the oldest entry then push.

Tests use a custom subprocess-spawn helper to inject a controlled stdout
stream — this isolates queue mechanics from real-``tail`` timing. The
ANSI-byte-transparency check against real ``tail`` lives in
``test_spawn_tail_realfile.py``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from megalodon_ui.mission_config.schema import MissionConfig
from megalodon_ui.spawn import FleetSpawner


SOCKET = Path("/tmp/test-fleet-tail.sock")


def _make_config(shorts: list[str]) -> MissionConfig:
    lanes = [
        {
            "name": f"LANE{s}",
            "short": s,
            "role": f"role-{s.lower()}",
            "harness": {"cli": "claude", "model": "sonnet"},
            "cadence_seconds": 300,
            "tick_offset_seconds": 0,
        }
        for s in shorts
    ]
    return MissionConfig.model_validate(
        {
            "mission": {"id": "test-mission", "utc_started": "2026-01-01T00:00:00Z"},
            "lanes": lanes,
            "phases": ["INIT"],
            # Tail-fanout test: stub MISSION_DIR has no scripts/ symlink and the
            # adapter is mocked, so disable the governor preflight (Task 2.2).
            "governor_enabled": False,
        }
    )


class _FakeStdout:
    """Async-readable stand-in for ``proc.stdout``; chunks supplied via a queue.

    The producer (test) calls ``feed(bytes)`` to push a chunk; the consumer
    (``_tail_lane``) sees one chunk per ``read()`` call. ``feed(None)`` is
    treated as EOF.
    """

    def __init__(self) -> None:
        self._q: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def feed(self, data: bytes | None) -> None:
        await self._q.put(data)

    async def read(self, _n: int) -> bytes:
        chunk = await self._q.get()
        if chunk is None:
            return b""  # EOF
        return chunk


class _FakeProc:
    """Stand-in for asyncio.subprocess.Process with read-side stdout."""

    def __init__(self) -> None:
        self.stdout = _FakeStdout()
        self.returncode: int | None = None

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def _patch_tail_spawn(fake_proc: _FakeProc) -> Callable[..., Awaitable]:
    """Return an async stand-in for ``_spawn_tail_subprocess`` that hands back fake_proc."""

    async def _fake(_path: Path) -> _FakeProc:
        return fake_proc

    return _fake


def _spawner(mission_dir: Path, shorts: list[str]) -> FleetSpawner:
    adapter = MagicMock()
    adapter.build_argv = MagicMock(return_value=(["stub"], {}))
    adapter.session_log_dir = MagicMock(return_value=None)
    return FleetSpawner(
        mission_dir, _make_config(shorts), MagicMock(return_value=adapter), SOCKET
    )


async def _start_with_fake_tail(spawner: FleetSpawner, fake_proc: _FakeProc) -> None:
    """Start FleetSpawner with tmux mocked and tail-spawn injected."""
    with (
        patch("megalodon_ui.spawn.tmux.list_sessions", new=AsyncMock(return_value=[])),
        patch("megalodon_ui.spawn.tmux.new_session", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
        patch(
            "megalodon_ui.spawn._spawn_tail_subprocess",
            new=_patch_tail_spawn(fake_proc),
        ),
    ):
        await spawner.start_all()


@pytest.mark.asyncio
async def test_tail_task_started_after_spawn(tmp_path: Path) -> None:
    """``start_all`` launches a tail task per lane; the task is alive after start."""
    mission_dir = tmp_path / "mission"
    (mission_dir / ".fleet").mkdir(parents=True)
    spawner = _spawner(mission_dir, ["A"])
    fake_proc = _FakeProc()
    await _start_with_fake_tail(spawner, fake_proc)

    try:
        lane = spawner.get("A")
        assert lane.tail_task is not None
        assert not lane.tail_task.done()
    finally:
        # EOF unblocks the read loop so the task exits cleanly.
        await fake_proc.stdout.feed(None)
        await spawner.stop_all()


@pytest.mark.asyncio
async def test_fanout_delivers_to_all_subscribers(tmp_path: Path) -> None:
    """Each chunk emitted by tail is delivered to every subscriber queue."""
    mission_dir = tmp_path / "mission"
    (mission_dir / ".fleet").mkdir(parents=True)
    spawner = _spawner(mission_dir, ["A"])
    fake_proc = _FakeProc()
    await _start_with_fake_tail(spawner, fake_proc)

    try:
        q1 = await spawner.subscribe("A")
        q2 = await spawner.subscribe("A")

        await fake_proc.stdout.feed(b"hello")
        await fake_proc.stdout.feed(b"world")

        # Both queues see both chunks (in order).
        got1 = [await asyncio.wait_for(q1.get(), timeout=1.0) for _ in range(2)]
        got2 = [await asyncio.wait_for(q2.get(), timeout=1.0) for _ in range(2)]
        assert got1 == [b"hello", b"world"]
        assert got2 == [b"hello", b"world"]

        # last_bytes_offset tracks total bytes seen.
        assert spawner.get("A").last_bytes_offset == 10
    finally:
        await fake_proc.stdout.feed(None)
        await spawner.stop_all()


@pytest.mark.asyncio
async def test_drop_oldest_when_subscriber_slow(tmp_path: Path, monkeypatch) -> None:
    """A slow consumer's queue caps at maxsize; producer doesn't stall."""
    mission_dir = tmp_path / "mission"
    (mission_dir / ".fleet").mkdir(parents=True)
    # Tiny queue so we can exercise drop-oldest quickly.
    monkeypatch.setattr("megalodon_ui.spawn.SSE_PER_SUBSCRIBER_QUEUE_MAXSIZE", 2)

    spawner = _spawner(mission_dir, ["A"])
    fake_proc = _FakeProc()
    await _start_with_fake_tail(spawner, fake_proc)

    try:
        q = await spawner.subscribe("A")
        # Push 5 chunks; q has maxsize=2 — last 2 should win.
        for i in range(5):
            await fake_proc.stdout.feed(f"chunk-{i}".encode())

        # Let the producer drain its read loop.
        # Wait until last_bytes_offset reflects all 5 chunks have been processed.
        deadline = asyncio.get_event_loop().time() + 2.0
        target = sum(len(f"chunk-{i}".encode()) for i in range(5))
        while asyncio.get_event_loop().time() < deadline:
            if spawner.get("A").last_bytes_offset >= target:
                break
            await asyncio.sleep(0.01)

        assert spawner.get("A").last_bytes_offset == target
        # Queue holds the last two chunks; the older three were dropped.
        assert q.qsize() == 2
        seen = [q.get_nowait(), q.get_nowait()]
        # Drop-oldest semantics: the queue retains the two MOST RECENT chunks.
        assert seen == [b"chunk-3", b"chunk-4"]
    finally:
        await fake_proc.stdout.feed(None)
        await spawner.stop_all()


@pytest.mark.asyncio
async def test_tail_task_cancelled_by_stop_all(tmp_path: Path) -> None:
    """``stop_all`` cancels the per-lane tail task and the subprocess is reaped."""
    mission_dir = tmp_path / "mission"
    (mission_dir / ".fleet").mkdir(parents=True)
    spawner = _spawner(mission_dir, ["A"])
    fake_proc = _FakeProc()
    await _start_with_fake_tail(spawner, fake_proc)

    lane = spawner.get("A")
    assert lane.tail_task is not None and not lane.tail_task.done()

    # No EOF — stop_all should cancel the still-running tail task.
    await spawner.stop_all()
    assert lane.tail_task.done()
    # The subprocess was terminated as part of cleanup.
    assert fake_proc.returncode is not None


@pytest.mark.asyncio
async def test_unsubscribed_queue_stops_receiving(tmp_path: Path) -> None:
    """After unsubscribe, the queue receives no further chunks."""
    mission_dir = tmp_path / "mission"
    (mission_dir / ".fleet").mkdir(parents=True)
    spawner = _spawner(mission_dir, ["A"])
    fake_proc = _FakeProc()
    await _start_with_fake_tail(spawner, fake_proc)

    try:
        q = await spawner.subscribe("A")
        await fake_proc.stdout.feed(b"before-unsub")
        assert await asyncio.wait_for(q.get(), timeout=1.0) == b"before-unsub"

        await spawner.unsubscribe("A", q)
        await fake_proc.stdout.feed(b"after-unsub")
        # Give the producer a tick to process the chunk.
        await asyncio.sleep(0.1)

        assert q.empty()
    finally:
        await fake_proc.stdout.feed(None)
        await spawner.stop_all()
