"""Unit tests for pipe-pane wiring in FleetSpawner (Task 3.1).

Plan §6.5 + §7 P3.1:
- After ``tmux.new_session`` succeeds for a lane, call
  ``tmux.pipe_pane(socket, name, stream_log)`` so PTY bytes accumulate in
  ``<mission>/.fleet/<short>.stream.log``.
- Reattach branch: query ``#{pane_pipe}`` first via
  ``display_message_pane_pipe``; only call ``pipe_pane`` if the existing
  pane has no active pipe (idempotency, so a stop+restart doesn't toggle
  the existing pipe off).

These tests mock the tmux module so they run on every platform; the real-
tmux byte-delivery integration tests (``test_pipe_pane_writes_bytes``,
``test_pipe_pane_line_delivery_under_500ms``) live separately and are
guarded by ``skipif(tmux not on PATH)`` plus the
``@pytest.mark.isolated`` marker per §7 P3.1.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from megalodon_ui.mission_config.schema import MissionConfig
from megalodon_ui.spawn import FleetSpawner


@pytest.fixture
def socket_path(tmp_path):
    return tmp_path / ".fleet" / "tmux.sock"


@pytest.fixture
def mission_dir(tmp_path):
    return tmp_path / "mission"


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
            # Orchestration test with a stub mission dir (no scripts/ symlink) and
            # mock adapters; the governor preflight is out of scope, so disable it
            # (Task 2.2 — governor wiring covered by test_governor_wiring.py).
            "governor_enabled": False,
        }
    )


def _make_resolver() -> MagicMock:
    adapter = MagicMock()
    adapter.build_argv = MagicMock(return_value=(["stub", "arg"], {}))
    adapter.session_log_dir = MagicMock(return_value=None)
    return MagicMock(return_value=adapter)


@pytest.mark.asyncio
async def test_start_all_calls_pipe_pane_for_each_freshly_spawned_lane(
    socket_path, mission_dir
):
    """For every newly-spawned lane, pipe_pane must be invoked exactly once with
    the lane's ``.fleet/<short>.stream.log`` path."""
    config = _make_config(["A", "B"])
    spawner = FleetSpawner(mission_dir, config, _make_resolver(), socket_path)

    with (
        patch("megalodon_ui.spawn.tmux.list_sessions", new=AsyncMock(return_value=[])),
        patch("megalodon_ui.spawn.tmux.new_session", new=AsyncMock(return_value=0)),
        patch(
            "megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)
        ) as mock_pipe,
    ):
        await spawner.start_all()

    assert mock_pipe.call_count == 2
    pipe_dests = {
        (call.args + tuple(call.kwargs.values()))[-1]
        for call in mock_pipe.call_args_list
    }
    assert mission_dir / ".fleet" / "A.stream.log" in pipe_dests
    assert mission_dir / ".fleet" / "B.stream.log" in pipe_dests


@pytest.mark.asyncio
async def test_pipe_pane_called_after_new_session(socket_path, mission_dir):
    """Ordering invariant — pipe_pane must NOT fire before new_session returns 0."""
    call_order: list[str] = []
    config = _make_config(["A"])
    spawner = FleetSpawner(mission_dir, config, _make_resolver(), socket_path)

    async def _record_new(**kwargs):
        call_order.append(f"new_session:{kwargs['name']}")
        return 0

    async def _record_pipe(*args, **kwargs):
        # First positional is socket, second is name, third is dest.
        call_order.append(f"pipe_pane:{args[1]}")
        return 0

    with (
        patch("megalodon_ui.spawn.tmux.list_sessions", new=AsyncMock(return_value=[])),
        patch("megalodon_ui.spawn.tmux.new_session", side_effect=_record_new),
        patch("megalodon_ui.spawn.tmux.pipe_pane", side_effect=_record_pipe),
    ):
        await spawner.start_all()

    assert call_order == ["new_session:lane-A", "pipe_pane:lane-A"]


@pytest.mark.asyncio
async def test_reattach_skips_pipe_pane_when_pipe_already_active(
    socket_path, mission_dir
):
    """Idempotency: a reattach of a session whose pane already has an active
    pipe-pane must not call ``tmux.pipe_pane`` again."""
    config = _make_config(["A"])
    spawner = FleetSpawner(mission_dir, config, _make_resolver(), socket_path)

    with (
        # Existing session for lane-A discovered at startup.
        patch(
            "megalodon_ui.spawn.tmux.list_sessions",
            new=AsyncMock(return_value=["lane-A"]),
        ),
        patch.object(spawner, "_is_fleet_owned", new=AsyncMock(return_value=True)),
        # Pipe is already active.
        patch(
            "megalodon_ui.spawn.tmux.display_message_pane_pipe",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "megalodon_ui.spawn.tmux.new_session", new=AsyncMock(return_value=0)
        ) as mock_new,
        patch(
            "megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)
        ) as mock_pipe,
    ):
        await spawner.start_all()

    assert mock_new.call_count == 0, "reattach must not spawn a fresh session"
    assert mock_pipe.call_count == 0, "active pipe must not be re-wired"


@pytest.mark.asyncio
async def test_reattach_wires_pipe_pane_when_pipe_inactive(socket_path, mission_dir):
    """If the existing session's pane has NO active pipe (e.g., the bytes file
    was deleted), reattach must re-wire pipe-pane so the SSE backend keeps
    receiving bytes."""
    config = _make_config(["A"])
    spawner = FleetSpawner(mission_dir, config, _make_resolver(), socket_path)

    with (
        patch(
            "megalodon_ui.spawn.tmux.list_sessions",
            new=AsyncMock(return_value=["lane-A"]),
        ),
        patch.object(spawner, "_is_fleet_owned", new=AsyncMock(return_value=True)),
        # Pipe is NOT active on the existing pane.
        patch(
            "megalodon_ui.spawn.tmux.display_message_pane_pipe",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "megalodon_ui.spawn.tmux.new_session", new=AsyncMock(return_value=0)
        ) as mock_new,
        patch(
            "megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)
        ) as mock_pipe,
    ):
        await spawner.start_all()

    assert mock_new.call_count == 0
    assert mock_pipe.call_count == 1
    # Verify the pipe destination matches the stream log path.
    args = mock_pipe.call_args.args
    assert args[-1] == mission_dir / ".fleet" / "A.stream.log"


@pytest.mark.asyncio
async def test_stream_log_path_uses_lane_short_code(socket_path, mission_dir):
    """Stream log filename is ``<short>.stream.log`` (not ``<name>.stream.log``)."""
    config = _make_config(["X", "Y"])
    spawner = FleetSpawner(mission_dir, config, _make_resolver(), socket_path)

    with (
        patch("megalodon_ui.spawn.tmux.list_sessions", new=AsyncMock(return_value=[])),
        patch("megalodon_ui.spawn.tmux.new_session", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
    ):
        await spawner.start_all()

    assert spawner.sessions["X"].stream_log.name == "X.stream.log"
    assert spawner.sessions["Y"].stream_log.name == "Y.stream.log"
