"""P1 coverage: SpawnError propagation when tmux new-session returns non-zero.

Covers megalodon_ui/spawn.py:_spawn_one (lines 203-204):
    if rc != 0:
        raise SpawnError(f"new-session failed for {session.lane}: rc={rc}")

Existing tests only cover rc=0 (success) and CancelledError cleanup.
This file adds two missing branches:

1. A single lane failing with rc != 0 raises SpawnError out of start_all.
2. When one lane succeeds and the second fails, the first lane's session is
   killed in the cancellation-cleanup block (OW-3, server.py:162-170) — this
   is the same cleanup path exercised by CancelledError, but now reached via
   a regular SpawnError.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from megalodon_ui.mission_config.schema import MissionConfig
from megalodon_ui.spawn import FleetSpawner, SpawnError


@pytest.fixture
def socket_path(tmp_path):
    return tmp_path / ".fleet" / "tmux.sock"


@pytest.fixture
def mission_dir(tmp_path):
    return tmp_path / "mission"


def _make_config(lane_shorts: list[str]) -> MissionConfig:
    lanes = [
        {
            "name": f"LANE{s}",
            "short": s,
            "role": f"role-{s.lower()}",
            "harness": {"cli": "claude", "model": "sonnet"},
            "cadence_seconds": 300,
            "tick_offset_seconds": 0,
        }
        for s in lane_shorts
    ]
    return MissionConfig.model_validate(
        {
            "mission": {"id": "test-err", "utc_started": "2026-01-01T00:00:00Z"},
            "lanes": lanes,
            "phases": ["INIT"],
            # Orchestration test with a stub mission dir (no scripts/ symlink) and
            # mock adapters; the governor preflight is out of scope, so disable it
            # (Task 2.2 — governor wiring covered by test_governor_wiring.py).
            "governor_enabled": False,
        }
    )


def _make_resolver() -> MagicMock:
    adapter = MagicMock()
    adapter.build_argv = MagicMock(return_value=(["stub"], {}))
    return MagicMock(return_value=adapter)


# ---------------------------------------------------------------------------
# Test 1: single lane rc != 0 -> SpawnError propagates out of start_all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_error_on_nonzero_rc(socket_path, mission_dir):
    """start_all raises SpawnError when new_session returns rc != 0."""
    config = _make_config(["A"])
    spawner = FleetSpawner(mission_dir, config, _make_resolver(), socket_path)

    with (
        patch("megalodon_ui.spawn.tmux.list_sessions", new=AsyncMock(return_value=[])),
        patch("megalodon_ui.spawn.tmux.new_session", new=AsyncMock(return_value=1)),
        patch("megalodon_ui.spawn.tmux.kill_session", new=AsyncMock(return_value=0)),
    ):
        with pytest.raises(SpawnError, match="new-session failed for A: rc=1"):
            await spawner.start_all()


# ---------------------------------------------------------------------------
# Test 2: first lane succeeds, second fails -> first lane is killed (OW-3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_error_kills_already_spawned_sessions(socket_path, mission_dir):
    """When one lane's new-session fails, previously spawned lanes are killed (OW-3).

    lane-A spawns successfully (rc=0); lane-B fails (rc=2). The OW-3 cleanup
    block must kill lane-A (it was appended to `spawned` before lane-B failed).
    """
    config = _make_config(["A", "B"])
    spawner = FleetSpawner(mission_dir, config, _make_resolver(), socket_path)

    call_count = {"n": 0}

    async def new_session_side_effect(**kwargs):
        call_count["n"] += 1
        # lane-A (first call) succeeds; lane-B (second call) fails.
        if kwargs["name"] == "lane-A":
            return 0
        return 2  # lane-B

    kill_calls: list[str] = []

    async def kill_side_effect(socket, name):
        kill_calls.append(name)
        return 0

    with (
        patch("megalodon_ui.spawn.tmux.list_sessions", new=AsyncMock(return_value=[])),
        patch(
            "megalodon_ui.spawn.tmux.new_session",
            new=AsyncMock(side_effect=new_session_side_effect),
        ),
        patch(
            "megalodon_ui.spawn.tmux.kill_session",
            new=AsyncMock(side_effect=kill_side_effect),
        ),
    ):
        with pytest.raises(SpawnError):
            await spawner.start_all()

    # lane-A spawned successfully and must be killed in OW-3 cleanup.
    assert "lane-A" in kill_calls, (
        f"OW-3: lane-A should have been killed after lane-B SpawnError; "
        f"kill_calls={kill_calls}"
    )
    # lane-B never appended to `spawned` (it raised before append) -> not killed.
    assert "lane-B" not in kill_calls, (
        f"OW-3: lane-B was never successfully spawned; should not be killed; "
        f"kill_calls={kill_calls}"
    )
