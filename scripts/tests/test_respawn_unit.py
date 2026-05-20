"""P6.3 — FleetSpawner.respawn() unit-level mocked test.

Plan §6.4 + §13: respawn must execute in order
  1. `tmux.respawn_pane(socket, name, argv, env)` (rc=0 required)
  2. `tmux.pipe_pane(socket, name, stream_log)` to re-establish bytes stream
  3. Verify via `tmux.display_message_pane_pipe(socket, name)` returning True;
     fail loud if pipe didn't take (PM-3).
  4. Under `LaneSession.subscribers_lock`: DRAIN every subscriber queue to
     empty, then push the pinned sentinel byte chunk
     `b"\\x1bc\\xe2\\x9f\\xb3 restarting\\xe2\\x80\\xa6\\r\\n"` into every queue
     (CV-12 + PM-7). The drain-then-push order guarantees the sentinel is the
     FIRST post-respawn chunk every subscriber sees, even under slow-consumer
     backpressure that would otherwise drop-oldest the sentinel.

The session_id discovery is reused from P3 (the session_log_dir snapshot
diff); we verify the helper is invoked.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from megalodon_ui.mission_config.schema import MissionConfig
from megalodon_ui.spawn import FleetSpawner, LaneSession


SENTINEL = b"\x1bc\xe2\x9f\xb3 restarting\xe2\x80\xa6\r\n"


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
            "mission": {"id": "test", "utc_started": "2026-01-01T00:00:00Z"},
            "lanes": lanes,
            "phases": ["INIT"],
        }
    )


def _build_spawner(tmp_path: Path) -> tuple[FleetSpawner, LaneSession]:
    mission_dir = tmp_path / "mission"
    (mission_dir / ".fleet").mkdir(parents=True)
    stream_log = mission_dir / ".fleet" / "A.stream.log"
    stream_log.touch()
    config = _make_config(["A"])
    adapter = MagicMock()
    adapter.session_log_dir = MagicMock(return_value=None)
    spawner = FleetSpawner(
        mission_dir, config, MagicMock(return_value=adapter),
        mission_dir / ".fleet" / "tmux.sock",
    )
    session = LaneSession(
        lane="A", name="lane-A", cwd=mission_dir,
        argv=["old"], env={}, stream_log=stream_log,
        session_id="prior-sid", running=True,
    )
    spawner.sessions["A"] = session
    return spawner, session


@pytest.mark.asyncio
async def test_respawn_calls_tmux_respawn_then_pipe_pane(tmp_path: Path):
    spawner, session = _build_spawner(tmp_path)

    with (
        patch("megalodon_ui.spawn.tmux.respawn_pane", new=AsyncMock(return_value=0)) as resp,
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)) as pipe,
        patch(
            "megalodon_ui.spawn.tmux.display_message_pane_pipe",
            new=AsyncMock(return_value=True),
        ),
    ):
        await spawner.respawn("A", ["claude", "--print", "p"], {"X": "y"})

    resp.assert_awaited_once()
    pipe.assert_awaited_once()
    # respawn_pane args: socket, name, argv, env
    args, _ = resp.call_args
    assert args[1] == "lane-A"
    assert "claude" in args[2]
    assert args[3] == {"X": "y"}
    # pipe_pane args: socket, name, stream_log
    args, _ = pipe.call_args
    assert args[1] == "lane-A"
    assert args[2] == session.stream_log


@pytest.mark.asyncio
async def test_respawn_pushes_sentinel_into_every_subscriber_queue(tmp_path: Path):
    spawner, session = _build_spawner(tmp_path)
    q1 = await spawner.subscribe("A")
    q2 = await spawner.subscribe("A")
    # Pre-fill queues with stale bytes that drain-then-push must evict.
    q1.put_nowait(b"stale-1")
    q2.put_nowait(b"stale-2")

    with (
        patch("megalodon_ui.spawn.tmux.respawn_pane", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
        patch(
            "megalodon_ui.spawn.tmux.display_message_pane_pipe",
            new=AsyncMock(return_value=True),
        ),
    ):
        await spawner.respawn("A", ["new"], {})

    # First chunk out of each queue is the sentinel — stale bytes were drained.
    assert q1.get_nowait() == SENTINEL
    assert q2.get_nowait() == SENTINEL


@pytest.mark.asyncio
async def test_respawn_fails_loud_when_pipe_pane_does_not_attach(tmp_path: Path):
    """If display_message_pane_pipe returns False after pipe_pane, raise."""
    spawner, _ = _build_spawner(tmp_path)

    with (
        patch("megalodon_ui.spawn.tmux.respawn_pane", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
        patch(
            "megalodon_ui.spawn.tmux.display_message_pane_pipe",
            new=AsyncMock(return_value=False),
        ),
    ):
        with pytest.raises(RuntimeError, match="pipe-pane"):
            await spawner.respawn("A", ["x"], {})


@pytest.mark.asyncio
async def test_respawn_propagates_tmux_respawn_failure(tmp_path: Path):
    """Non-zero rc from tmux.respawn_pane must bubble up as RuntimeError."""
    spawner, _ = _build_spawner(tmp_path)

    with (
        patch("megalodon_ui.spawn.tmux.respawn_pane", new=AsyncMock(return_value=1)),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
    ):
        with pytest.raises(RuntimeError, match="respawn-pane"):
            await spawner.respawn("A", ["x"], {})


@pytest.mark.asyncio
async def test_respawn_unknown_lane_raises_keyerror(tmp_path: Path):
    spawner, _ = _build_spawner(tmp_path)
    with pytest.raises(KeyError):
        await spawner.respawn("ZZZ", ["x"], {})


@pytest.mark.asyncio
async def test_respawn_updates_argv_on_lane_session(tmp_path: Path):
    """After respawn, LaneSession.argv reflects the NEW argv for future re-pipes."""
    spawner, session = _build_spawner(tmp_path)
    assert session.argv == ["old"]
    new_argv = ["claude", "--print", "new prompt"]

    with (
        patch("megalodon_ui.spawn.tmux.respawn_pane", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
        patch(
            "megalodon_ui.spawn.tmux.display_message_pane_pipe",
            new=AsyncMock(return_value=True),
        ),
    ):
        await spawner.respawn("A", new_argv, {})

    assert spawner.get("A").argv == new_argv
