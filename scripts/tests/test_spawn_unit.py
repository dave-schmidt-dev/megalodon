"""Unit tests for megalodon_ui.spawn — all tmux calls are mocked."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from megalodon_ui.mission_config.schema import MissionConfig
from megalodon_ui.spawn import FleetSpawner, LaneSession


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def socket_path(tmp_path):
    return tmp_path / ".fleet" / "tmux.sock"


@pytest.fixture
def mission_dir(tmp_path):
    return tmp_path / "mission"


def _make_config(lane_shorts: list[str] | None = None) -> MissionConfig:
    """Build a minimal MissionConfig with 3 claude lanes (A, B, C)."""
    shorts = lane_shorts or ["A", "B", "C"]
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
            "mission": {
                "id": "test-mission",
                "utc_started": "2026-01-01T00:00:00Z",
            },
            "lanes": lanes,
            "phases": ["INIT"],
            # Orchestration tests use a stub MISSION_DIR with no scripts/ symlink
            # and mock adapters; the governor preflight is out of scope here, so
            # disable it (Task 2.2). Governor wiring is covered by
            # test_governor_wiring.py + test_harness_claude.py.
            "governor_enabled": False,
        }
    )


def _make_adapter() -> MagicMock:
    """Return a mock HarnessAdapter whose build_argv returns a fixed argv."""
    adapter = MagicMock()
    adapter.build_argv = MagicMock(return_value=(["stub", "arg"], {}))
    # session_log_dir → None short-circuits _discover_session_id's real-FS poll.
    # Without this the spawner polls a MagicMock "log dir" until the discovery
    # timeout (~5s) on every spawned lane — pure test-harness stall, not behavior
    # under test. None models "no resume log dir", which the spawner handles.
    adapter.session_log_dir = MagicMock(return_value=None)
    return adapter


def _make_resolver(adapter: MagicMock | None = None) -> MagicMock:
    if adapter is None:
        adapter = _make_adapter()
    resolver = MagicMock(return_value=adapter)
    return resolver


# ---------------------------------------------------------------------------
# test_start_all_calls_new_session_once_per_lane
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_all_calls_new_session_once_per_lane(socket_path, mission_dir):
    """start_all must call tmux.new_session exactly once per configured lane."""
    config = _make_config(["A", "B", "C"])
    adapter = _make_adapter()
    resolver = _make_resolver(adapter)
    spawner = FleetSpawner(mission_dir, config, resolver, socket_path)

    with (
        patch("megalodon_ui.spawn.tmux.list_sessions", new=AsyncMock(return_value=[])),
        patch(
            "megalodon_ui.spawn.tmux.new_session", new=AsyncMock(return_value=0)
        ) as mock_new,
        patch("megalodon_ui.spawn.tmux.kill_session", new=AsyncMock(return_value=0)),
    ):
        await spawner.start_all()

    assert mock_new.call_count == 3
    called_names = {call.kwargs["name"] for call in mock_new.call_args_list}
    assert called_names == {"lane-A", "lane-B", "lane-C"}

    for call in mock_new.call_args_list:
        assert call.kwargs["argv"] == ["stub", "arg"]
        assert call.kwargs["cwd"] == mission_dir
        assert call.kwargs["cols"] == 200
        assert call.kwargs["rows"] == 50

    # build_argv must be CALLED once per lane with the real prompt/model/cwd —
    # assert on the recorded call args, NOT the stub's constant return value.
    # With no prompt_override and governor disabled, the spawner passes the
    # lane's role as the prompt, the lane's harness model, and the mission dir
    # as cwd (no governor_settings / live_repl kwargs for these lanes).
    assert adapter.build_argv.call_count == 3
    seen = {call.args[0]: call.kwargs for call in adapter.build_argv.call_args_list}
    assert set(seen) == {"role-a", "role-b", "role-c"}, "prompt = lane role"
    for prompt, kwargs in seen.items():
        assert kwargs["model"] == "sonnet"
        assert kwargs["cwd"] == mission_dir
        assert "governor_settings" not in kwargs
        assert "live_repl" not in kwargs


# ---------------------------------------------------------------------------
# test_lane_session_carries_v92_fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lane_session_carries_v92_fields(socket_path, mission_dir):
    """LaneSession must have exited_rc, pane_dead_checked_at, and subscribers_lock defaults."""
    config = _make_config(["A"])
    resolver = _make_resolver()
    spawner = FleetSpawner(mission_dir, config, resolver, socket_path)

    with (
        patch("megalodon_ui.spawn.tmux.list_sessions", new=AsyncMock(return_value=[])),
        patch("megalodon_ui.spawn.tmux.new_session", new=AsyncMock(return_value=0)),
    ):
        await spawner.start_all()

    ls = spawner.sessions["A"]
    assert isinstance(ls, LaneSession)
    assert ls.exited_rc is None
    assert ls.pane_dead_checked_at == 0.0
    assert isinstance(ls.subscribers_lock, asyncio.Lock)
    assert ls.session_id is None
    assert ls.running is True


# ---------------------------------------------------------------------------
# test_cancellation_cleans_up_spawned_sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancellation_cleans_up_spawned_sessions(socket_path, mission_dir):
    """On cancellation, kill sessions that spawned successfully; skip those that did not."""
    config = _make_config(["A", "B"])
    resolver = _make_resolver()
    spawner = FleetSpawner(mission_dir, config, resolver, socket_path)

    # lane-A spawns instantly (rc=0); lane-B blocks indefinitely until cancelled.
    async def new_session_side_effect(**kwargs):
        if kwargs["name"] == "lane-A":
            return 0
        # lane-B
        await asyncio.sleep(3600)
        return 0

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
        task = asyncio.create_task(spawner.start_all())
        # Give lane-A time to complete and lane-B time to start blocking.
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    # lane-A spawned successfully -> must be killed in cleanup.
    assert "lane-A" in kill_calls
    # lane-B never finished spawning -> must NOT be killed.
    assert "lane-B" not in kill_calls


# ---------------------------------------------------------------------------
# test_orphan_purge_only_kills_marker_tagged_sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orphan_purge_only_kills_marker_tagged_sessions(socket_path, mission_dir):
    """Orphan purge must kill MEGALODON_FLEET_OWNED=1 sessions not in config;
    sessions without the marker are left alone."""
    config = _make_config(["X"])  # only lane-X is configured
    resolver = _make_resolver()
    spawner = FleetSpawner(mission_dir, config, resolver, socket_path)

    # Existing sessions: lane-A (fleet-owned orphan), lane-MANUAL (no marker)
    existing_sessions = ["lane-A", "lane-MANUAL"]

    # show-environment simulation
    async def fake_is_fleet_owned_proc(**kwargs):
        # We patch _is_fleet_owned directly instead
        pass

    async def fake_is_fleet_owned(self_or_name, session_name=None):
        # Called as instance method: first arg is self, second is session_name
        # but since we patch the bound method, arg is just the session name
        name = self_or_name if session_name is None else session_name
        return name == "lane-A"

    kill_calls: list[str] = []

    async def kill_side_effect(socket, name):
        kill_calls.append(name)
        return 0

    new_session_calls: list[str] = []

    async def new_session_side_effect(**kwargs):
        new_session_calls.append(kwargs["name"])
        return 0

    with (
        patch(
            "megalodon_ui.spawn.tmux.list_sessions",
            new=AsyncMock(return_value=existing_sessions),
        ),
        patch.object(
            FleetSpawner,
            "_is_fleet_owned",
            new=AsyncMock(side_effect=lambda n: n == "lane-A"),
        ),
        patch(
            "megalodon_ui.spawn.tmux.kill_session",
            new=AsyncMock(side_effect=kill_side_effect),
        ),
        patch(
            "megalodon_ui.spawn.tmux.new_session",
            new=AsyncMock(side_effect=new_session_side_effect),
        ),
    ):
        await spawner.start_all()

    # lane-A is fleet-owned but NOT in config (only X is) -> must be killed
    assert "lane-A" in kill_calls
    # lane-MANUAL has no marker -> must NOT be killed
    assert "lane-MANUAL" not in kill_calls


# ---------------------------------------------------------------------------
# test_reattach_branch_preserves_existing_marker_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reattach_branch_preserves_existing_marker_session(
    socket_path, mission_dir
):
    """If a configured lane already has a fleet-owned session, do NOT spawn it again."""
    config = _make_config(["A", "B"])
    resolver = _make_resolver()
    spawner = FleetSpawner(mission_dir, config, resolver, socket_path)

    # lane-A already exists and is fleet-owned; lane-B does not exist
    existing_sessions = ["lane-A"]

    new_session_calls: list[str] = []

    async def new_session_side_effect(**kwargs):
        new_session_calls.append(kwargs["name"])
        return 0

    with (
        patch(
            "megalodon_ui.spawn.tmux.list_sessions",
            new=AsyncMock(return_value=existing_sessions),
        ),
        patch.object(
            FleetSpawner,
            "_is_fleet_owned",
            new=AsyncMock(side_effect=lambda n: n == "lane-A"),
        ),
        patch(
            "megalodon_ui.spawn.tmux.new_session",
            new=AsyncMock(side_effect=new_session_side_effect),
        ),
        patch("megalodon_ui.spawn.tmux.kill_session", new=AsyncMock(return_value=0)),
        patch(
            "megalodon_ui.spawn.tmux.display_message_pane_pipe",
            new=AsyncMock(return_value=True),
        ),
    ):
        await spawner.start_all()

    # lane-A already existed -> NOT spawned
    assert "lane-A" not in new_session_calls
    # lane-B did not exist -> spawned
    assert "lane-B" in new_session_calls
    # lane-A is recorded as running via reattach
    assert spawner.sessions["A"].running is True


# ---------------------------------------------------------------------------
# B1: watchdog pid files written on spawn, removed on cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_writes_pid_file_then_stop_removes_it(
    tmp_path, socket_path, mission_dir
):
    """A fresh spawn writes ``<PID_DIR>/<lane_name>.pid``; stop_all removes it."""
    config = _make_config(["A"])
    resolver = _make_resolver()
    spawner = FleetSpawner(mission_dir, config, resolver, socket_path)

    pid_dir = tmp_path / "pids"

    with (
        patch("megalodon_ui.spawn.PID_DIR", pid_dir),
        patch("megalodon_ui.spawn.tmux.list_sessions", new=AsyncMock(return_value=[])),
        patch("megalodon_ui.spawn.tmux.new_session", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn.tmux.kill_session", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
        patch(
            "megalodon_ui.spawn.tmux.display_message_pane_pid",
            new=AsyncMock(return_value=4242),
        ),
        patch(
            "megalodon_ui.spawn._discover_session_id",
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            FleetSpawner, "_start_tail_task", new=AsyncMock(return_value=None)
        ),
    ):
        await spawner.start_all()

        # The pid file is keyed by the LONG lane name ("LANEA") so the watchdog's
        # _read_pid(lane.name) finds it; it contains the tmux pane pid.
        pid_file = pid_dir / "LANEA.pid"
        assert pid_file.is_file()
        assert pid_file.read_text().strip() == "4242"

        await spawner.stop_all()
        assert not pid_file.exists()


@pytest.mark.asyncio
async def test_spawn_skips_pid_file_when_pane_pid_unavailable(
    tmp_path, socket_path, mission_dir
):
    """A None pane pid (query failed) writes no file — watchdog falls back."""
    config = _make_config(["A"])
    resolver = _make_resolver()
    spawner = FleetSpawner(mission_dir, config, resolver, socket_path)

    pid_dir = tmp_path / "pids"

    with (
        patch("megalodon_ui.spawn.PID_DIR", pid_dir),
        patch("megalodon_ui.spawn.tmux.list_sessions", new=AsyncMock(return_value=[])),
        patch("megalodon_ui.spawn.tmux.new_session", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn.tmux.kill_session", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
        patch(
            "megalodon_ui.spawn.tmux.display_message_pane_pid",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "megalodon_ui.spawn._discover_session_id",
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            FleetSpawner, "_start_tail_task", new=AsyncMock(return_value=None)
        ),
    ):
        await spawner.start_all()
        assert not (pid_dir / "LANEA.pid").exists()
