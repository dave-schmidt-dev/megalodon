"""Tests for FleetSpawner.subscribe / unsubscribe (Task 4.1 — SSE plumbing).

Plan §6.2 / plan §6.4: each LaneSession owns a ``subscribers`` list of
``asyncio.Queue[bytes]``. ``FleetSpawner.subscribe(lane)`` returns a fresh
queue bound to that lane; ``unsubscribe(lane, q)`` removes it. The list
mutation and the producer fan-out iteration MUST both hold
``LaneSession.subscribers_lock`` (SR-3 from pre-mortem) — these tests cover
the list-mutation half; ``test_spawn_tail_fanout.py`` covers iteration.

``SSE_MAX_SUBSCRIBERS_PER_LANE`` is enforced at subscribe time; the 11th
caller raises ``TooManySubscribersError`` (or equivalent). The SSE endpoint
turns that into a 503 in Task 4.2.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from megalodon_ui.mission_config.schema import MissionConfig
from megalodon_ui.spawn import FleetSpawner, TooManySubscribersError


SOCKET = Path("/tmp/test-fleet-subs.sock")


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


def _spawner(mission_dir: Path, shorts: list[str]) -> FleetSpawner:
    adapter = MagicMock()
    adapter.build_argv = MagicMock(return_value=(["stub"], {}))
    adapter.session_log_dir = MagicMock(return_value=None)
    return FleetSpawner(mission_dir, _make_config(shorts), MagicMock(return_value=adapter), SOCKET)


async def _start(spawner: FleetSpawner) -> None:
    """Start FleetSpawner with tmux fully mocked (no real subprocess)."""
    with (
        patch("megalodon_ui.spawn.tmux.list_sessions", new=AsyncMock(return_value=[])),
        patch("megalodon_ui.spawn.tmux.new_session", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
        # Block tail-task launch so these subscribe/unsubscribe unit tests
        # don't depend on a real tail subprocess.
        patch("megalodon_ui.spawn.FleetSpawner._start_tail_task", new=AsyncMock()),
    ):
        await spawner.start_all()


@pytest.mark.asyncio
async def test_subscribe_returns_queue_appended_to_lane_list(tmp_path: Path) -> None:
    """subscribe(lane) returns a fresh queue and appends it to the lane's subscribers."""
    mission_dir = tmp_path / "mission"
    (mission_dir / ".fleet").mkdir(parents=True)
    spawner = _spawner(mission_dir, ["A"])
    await _start(spawner)

    q = await spawner.subscribe("A")

    assert isinstance(q, asyncio.Queue)
    assert q in spawner.get("A").subscribers


@pytest.mark.asyncio
async def test_unsubscribe_removes_queue_from_list(tmp_path: Path) -> None:
    """unsubscribe(lane, q) drops the queue from subscribers without affecting peers."""
    mission_dir = tmp_path / "mission"
    (mission_dir / ".fleet").mkdir(parents=True)
    spawner = _spawner(mission_dir, ["A"])
    await _start(spawner)

    q1 = await spawner.subscribe("A")
    q2 = await spawner.subscribe("A")
    await spawner.unsubscribe("A", q1)

    subs = spawner.get("A").subscribers
    assert q1 not in subs
    assert q2 in subs


@pytest.mark.asyncio
async def test_subscribe_enforces_max_subscribers(tmp_path: Path, monkeypatch) -> None:
    """The (max+1)th subscribe raises TooManySubscribersError; existing subscribers untouched."""
    mission_dir = tmp_path / "mission"
    (mission_dir / ".fleet").mkdir(parents=True)
    # Patch to 3 so the test is quick.
    monkeypatch.setattr("megalodon_ui.spawn.SSE_MAX_SUBSCRIBERS_PER_LANE", 3)
    spawner = _spawner(mission_dir, ["A"])
    await _start(spawner)

    qs = [await spawner.subscribe("A") for _ in range(3)]
    with pytest.raises(TooManySubscribersError):
        await spawner.subscribe("A")

    # The three existing subscribers are still registered.
    assert all(q in spawner.get("A").subscribers for q in qs)


@pytest.mark.asyncio
async def test_subscribe_unknown_lane_raises_keyerror(tmp_path: Path) -> None:
    """Subscribing to a non-existent lane raises KeyError (matches FleetSpawner.get)."""
    mission_dir = tmp_path / "mission"
    (mission_dir / ".fleet").mkdir(parents=True)
    spawner = _spawner(mission_dir, ["A"])
    await _start(spawner)

    with pytest.raises(KeyError):
        await spawner.subscribe("ZZZ")


@pytest.mark.asyncio
async def test_subscribe_blocks_while_subscribers_lock_held(tmp_path: Path) -> None:
    """If the producer holds subscribers_lock, subscribe waits until release (SR-3)."""
    mission_dir = tmp_path / "mission"
    (mission_dir / ".fleet").mkdir(parents=True)
    spawner = _spawner(mission_dir, ["A"])
    await _start(spawner)

    lane = spawner.get("A")
    # Externally acquire the producer-side lock (simulates fan-out iteration).
    await lane.subscribers_lock.acquire()
    try:
        # subscribe must wait — assert it times out while we hold the lock.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(spawner.subscribe("A"), timeout=0.2)
    finally:
        lane.subscribers_lock.release()

    # Once released, subscribe completes.
    q = await asyncio.wait_for(spawner.subscribe("A"), timeout=1.0)
    assert q in lane.subscribers
