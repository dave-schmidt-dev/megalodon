"""Tests for session-id discovery in FleetSpawner (Task 3.3 — PM-6).

Plan §6.5 contract: before ``tmux.new_session``, snapshot
``adapter.session_log_dir(cwd)`` for existing entries. After spawn settles
(poll up to 5 s with 100 ms backoff), diff. Exactly one new entry →
``LaneSession.session_id = entry.stem``. Zero or 2+ → log a WARNING and
leave ``session_id = None`` (follow-up degrades to no-resume).

The PM-6 concurrent-spawn variant uses distinct ``cwd`` per lane so each
lane's ``session_log_dir`` is independent — demonstrates the discovery
algorithm works under ``asyncio.gather`` for the supported topology
(lanes with separate session-log dirs).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from megalodon_ui.mission_config.schema import MissionConfig
from megalodon_ui.spawn import FleetSpawner


SOCKET = Path("/tmp/test-fleet.sock")


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


def _adapter_with_session_dir(session_log_dir: Path | None) -> MagicMock:
    adapter = MagicMock()
    adapter.build_argv = MagicMock(return_value=(["stub", "arg"], {}))
    adapter.session_log_dir = MagicMock(return_value=session_log_dir)
    return adapter


@pytest.mark.asyncio
async def test_session_id_discovered_when_single_new_jsonl_appears(tmp_path: Path):
    """Claude-style: one .jsonl shows up post-spawn → stem becomes session_id."""
    mission_dir = tmp_path / "mission"
    mission_dir.mkdir()
    session_dir = tmp_path / "claude-projects"
    session_dir.mkdir()
    (session_dir / "preexisting.jsonl").write_text("{}")

    config = _make_config(["A"])
    adapter = _adapter_with_session_dir(session_dir)
    spawner = FleetSpawner(mission_dir, config, MagicMock(return_value=adapter), SOCKET)

    async def _new_session_writes_log(**kwargs):
        # Simulate Claude writing its session log shortly after spawn.
        (session_dir / "fresh-session-abc.jsonl").write_text("{}")
        return 0

    with (
        patch("megalodon_ui.spawn.tmux.list_sessions", new=AsyncMock(return_value=[])),
        patch(
            "megalodon_ui.spawn.tmux.new_session", side_effect=_new_session_writes_log
        ),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
    ):
        await spawner.start_all()

    assert spawner.sessions["A"].session_id == "fresh-session-abc"


@pytest.mark.asyncio
async def test_session_id_none_when_adapter_returns_no_dir(tmp_path: Path):
    """Adapters with ``session_log_dir() == None`` skip discovery entirely."""
    mission_dir = tmp_path / "mission"
    mission_dir.mkdir()

    config = _make_config(["A"])
    adapter = _adapter_with_session_dir(None)
    spawner = FleetSpawner(mission_dir, config, MagicMock(return_value=adapter), SOCKET)

    with (
        patch("megalodon_ui.spawn.tmux.list_sessions", new=AsyncMock(return_value=[])),
        patch("megalodon_ui.spawn.tmux.new_session", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
    ):
        await spawner.start_all()

    assert spawner.sessions["A"].session_id is None


@pytest.mark.asyncio
async def test_session_id_none_when_zero_new_entries_appear(tmp_path: Path):
    """If the harness writes no new entry within timeout, id stays None.
    The test uses a tight timeout via patching so we don't wait 5 s."""
    mission_dir = tmp_path / "mission"
    mission_dir.mkdir()
    session_dir = tmp_path / "empty-claude-projects"
    session_dir.mkdir()

    config = _make_config(["A"])
    adapter = _adapter_with_session_dir(session_dir)
    spawner = FleetSpawner(mission_dir, config, MagicMock(return_value=adapter), SOCKET)

    # No new file is written, but the test should not hang for 5s. Patch the
    # discovery timeout via constants reference (one short window is enough).
    with (
        patch("megalodon_ui.spawn.tmux.list_sessions", new=AsyncMock(return_value=[])),
        patch("megalodon_ui.spawn.tmux.new_session", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn._SESSION_DISCOVERY_TIMEOUT", 0.2),
        patch("megalodon_ui.spawn._SESSION_DISCOVERY_INTERVAL", 0.05),
    ):
        await spawner.start_all()

    assert spawner.sessions["A"].session_id is None


@pytest.mark.asyncio
async def test_session_id_none_on_ambiguous_diff_two_plus_entries(tmp_path: Path):
    """If two new entries land in the shared dir, neither lane gets resolved."""
    mission_dir = tmp_path / "mission"
    mission_dir.mkdir()
    session_dir = tmp_path / "shared-claude-projects"
    session_dir.mkdir()

    config = _make_config(["A"])
    adapter = _adapter_with_session_dir(session_dir)
    spawner = FleetSpawner(mission_dir, config, MagicMock(return_value=adapter), SOCKET)

    async def _new_session_writes_two(**kwargs):
        # Race-replica: simulate two files appearing during this lane's spawn
        # window (e.g., a concurrent lane sharing the same Claude project dir).
        (session_dir / "a.jsonl").write_text("{}")
        (session_dir / "b.jsonl").write_text("{}")
        return 0

    with (
        patch("megalodon_ui.spawn.tmux.list_sessions", new=AsyncMock(return_value=[])),
        patch(
            "megalodon_ui.spawn.tmux.new_session", side_effect=_new_session_writes_two
        ),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn._SESSION_DISCOVERY_TIMEOUT", 0.2),
        patch("megalodon_ui.spawn._SESSION_DISCOVERY_INTERVAL", 0.05),
    ):
        await spawner.start_all()

    assert spawner.sessions["A"].session_id is None


@pytest.mark.asyncio
async def test_concurrent_spawn_each_lane_discovers_own_id_when_dirs_distinct(
    tmp_path: Path,
):
    """PM-6: ``asyncio.gather`` spawn of two lanes whose adapters return
    distinct session_log_dirs — each lane discovers its own new entry."""
    mission_dir = tmp_path / "mission"
    mission_dir.mkdir()
    dir_a = tmp_path / "claude-A"
    dir_b = tmp_path / "claude-B"
    dir_a.mkdir()
    dir_b.mkdir()

    config = _make_config(["A", "B"])

    # Per-lane adapter; resolver picks one per call. We use harness.cli to
    # distinguish — both lanes use "claude" in the schema, so the resolver
    # below routes by call ordering instead.
    adapter_a = _adapter_with_session_dir(dir_a)
    adapter_b = _adapter_with_session_dir(dir_b)
    adapters = iter([adapter_a, adapter_b])
    resolver = MagicMock(side_effect=lambda *_: next(adapters))

    spawner = FleetSpawner(mission_dir, config, resolver, SOCKET)

    async def _new_session_writes_per_lane(**kwargs):
        if kwargs["name"] == "lane-A":
            (dir_a / "session-A.jsonl").write_text("{}")
        else:
            (dir_b / "session-B.jsonl").write_text("{}")
        return 0

    with (
        patch("megalodon_ui.spawn.tmux.list_sessions", new=AsyncMock(return_value=[])),
        patch(
            "megalodon_ui.spawn.tmux.new_session",
            side_effect=_new_session_writes_per_lane,
        ),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
    ):
        await asyncio.wait_for(spawner.start_all(), timeout=5.0)

    assert spawner.sessions["A"].session_id == "session-A"
    assert spawner.sessions["B"].session_id == "session-B"
