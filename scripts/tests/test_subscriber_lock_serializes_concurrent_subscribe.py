"""Stress test: concurrent subscribe / unsubscribe during active fan-out (SR-3).

Plan §6.2: subscribe / unsubscribe AND the producer's fan-out iteration all
hold ``LaneSession.subscribers_lock``. Without the lock, a fast
subscribe/unsubscribe burst during spawn could race with iteration —
manifesting as missed deliveries, dropped subscribers, or (in extreme
cases) a runtime error.

This test runs a busy producer and a worker that adds/removes subscribers in
a tight loop. It asserts:
  - No exceptions propagate to either side.
  - Surviving subscribers continue receiving bytes after the churn.

Marked ``@pytest.mark.isolated`` because timing-sensitive interleaving makes
it sensitive to shared event-loop state from prior tests.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from megalodon_ui.mission_config.schema import MissionConfig
from megalodon_ui.spawn import FleetSpawner


pytestmark = [pytest.mark.isolated]


SOCKET = Path("/tmp/test-fleet-subs-stress.sock")


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
        }
    )


class _FakeStdout:
    def __init__(self) -> None:
        self._q: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def feed(self, data: bytes | None) -> None:
        await self._q.put(data)

    async def read(self, _n: int) -> bytes:
        chunk = await self._q.get()
        if chunk is None:
            return b""
        return chunk


class _FakeProc:
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


@pytest.mark.asyncio
async def test_burst_subscribe_unsubscribe_during_fanout(
    tmp_path: Path, monkeypatch
) -> None:
    """Hammer subscribe / unsubscribe while the producer is fanning out chunks."""
    mission_dir = tmp_path / "mission"
    (mission_dir / ".fleet").mkdir(parents=True)
    # Slightly larger cap so the churn can park multiple temporary subscribers.
    monkeypatch.setattr("megalodon_ui.spawn.SSE_MAX_SUBSCRIBERS_PER_LANE", 8)

    adapter = MagicMock()
    adapter.build_argv = MagicMock(return_value=(["stub"], {}))
    adapter.session_log_dir = MagicMock(return_value=None)
    spawner = FleetSpawner(
        mission_dir, _make_config(["A"]), MagicMock(return_value=adapter), SOCKET
    )

    fake_proc = _FakeProc()

    async def _fake_spawn(_path: Path) -> _FakeProc:
        return fake_proc

    with (
        patch("megalodon_ui.spawn.tmux.list_sessions", new=AsyncMock(return_value=[])),
        patch("megalodon_ui.spawn.tmux.new_session", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn._spawn_tail_subprocess", new=_fake_spawn),
    ):
        await spawner.start_all()

    try:
        # A long-lived subscriber that must keep receiving across churn.
        stable_q = await spawner.subscribe("A")

        # Concurrent producer: pump chunks for ~1 s.
        async def producer() -> None:
            for i in range(200):
                await fake_proc.stdout.feed(f"c{i}".encode())
                # Give the event loop a tick so reads interleave with subs.
                await asyncio.sleep(0)

        # Concurrent churn: rapidly subscribe + unsubscribe.
        async def churn() -> None:
            for _ in range(100):
                q = await spawner.subscribe("A")
                await asyncio.sleep(0)
                await spawner.unsubscribe("A", q)

        await asyncio.gather(producer(), churn())

        # Drain stable_q and check we received at least one chunk and no exceptions.
        # (Exact count is non-deterministic because of drop-oldest under backpressure.)
        assert stable_q.qsize() > 0
        # No subscribers leaked from the churn.
        # Drop-oldest may have evicted some chunks from stable_q, but stable_q is still in subscribers.
        assert stable_q in spawner.get("A").subscribers
        # Only the stable subscriber should remain.
        assert len(spawner.get("A").subscribers) == 1
    finally:
        await fake_proc.stdout.feed(None)
        await spawner.stop_all()
