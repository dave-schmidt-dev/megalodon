"""Tests for .fleet/<lane>.session.txt persistence (Task 3.4 — CV-5).

Plan §6.5: after the session-id discovery returns the spawned-harness id,
write the id as a single line to ``<mission>/.fleet/<lane>.session.txt``
mode 0644. Single-line, no JSON, no metadata — durable companion to the
in-memory ``LaneSession.session_id``, read on a fresh server start so
``build_followup_argv`` can compose ``--resume <id>`` without rediscovery.

Adapters whose ``session_log_dir()`` returns None skip the write entirely.
"""

from __future__ import annotations

import stat
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
async def test_session_txt_written_after_discovery(tmp_path: Path):
    """Single-line ``<id>`` written at ``<mission>/.fleet/<lane>.session.txt``."""
    mission_dir = tmp_path / "mission"
    fleet_dir = mission_dir / ".fleet"
    fleet_dir.mkdir(parents=True)

    session_dir = tmp_path / "claude-projects"
    session_dir.mkdir()
    (session_dir / "preexisting.jsonl").write_text("{}")

    config = _make_config(["A"])
    adapter = _adapter_with_session_dir(session_dir)
    spawner = FleetSpawner(mission_dir, config, MagicMock(return_value=adapter), SOCKET)

    async def _new_session_writes(**_):
        (session_dir / "discovered-id-xyz.jsonl").write_text("{}")
        return 0

    with (
        patch("megalodon_ui.spawn.tmux.list_sessions", new=AsyncMock(return_value=[])),
        patch("megalodon_ui.spawn.tmux.new_session", side_effect=_new_session_writes),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
    ):
        await spawner.start_all()

    txt = fleet_dir / "A.session.txt"
    assert txt.exists()
    assert txt.read_text().strip() == "discovered-id-xyz"
    # File mode 0644 per plan.
    assert stat.S_IMODE(txt.stat().st_mode) == 0o644


@pytest.mark.asyncio
async def test_session_txt_not_written_when_adapter_returns_none(tmp_path: Path):
    """``session_log_dir() == None`` → no discovery → no session.txt at all."""
    mission_dir = tmp_path / "mission"
    fleet_dir = mission_dir / ".fleet"
    fleet_dir.mkdir(parents=True)

    config = _make_config(["A"])
    adapter = _adapter_with_session_dir(None)
    spawner = FleetSpawner(mission_dir, config, MagicMock(return_value=adapter), SOCKET)

    with (
        patch("megalodon_ui.spawn.tmux.list_sessions", new=AsyncMock(return_value=[])),
        patch("megalodon_ui.spawn.tmux.new_session", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
    ):
        await spawner.start_all()

    assert not (fleet_dir / "A.session.txt").exists()


@pytest.mark.asyncio
async def test_session_txt_not_written_when_discovery_ambiguous(tmp_path: Path):
    """2+ new entries → session_id None → no file."""
    mission_dir = tmp_path / "mission"
    fleet_dir = mission_dir / ".fleet"
    fleet_dir.mkdir(parents=True)
    session_dir = tmp_path / "shared-claude-projects"
    session_dir.mkdir()

    config = _make_config(["A"])
    adapter = _adapter_with_session_dir(session_dir)
    spawner = FleetSpawner(mission_dir, config, MagicMock(return_value=adapter), SOCKET)

    async def _new_session_writes_two(**_):
        (session_dir / "a.jsonl").write_text("{}")
        (session_dir / "b.jsonl").write_text("{}")
        return 0

    with (
        patch("megalodon_ui.spawn.tmux.list_sessions", new=AsyncMock(return_value=[])),
        patch("megalodon_ui.spawn.tmux.new_session", side_effect=_new_session_writes_two),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn._SESSION_DISCOVERY_TIMEOUT", 0.2),
        patch("megalodon_ui.spawn._SESSION_DISCOVERY_INTERVAL", 0.05),
    ):
        await spawner.start_all()

    assert not (fleet_dir / "A.session.txt").exists()


@pytest.mark.asyncio
async def test_session_txt_overwrites_on_respawn(tmp_path: Path):
    """Open-on-write semantics: a second discovery overwrites the file."""
    mission_dir = tmp_path / "mission"
    fleet_dir = mission_dir / ".fleet"
    fleet_dir.mkdir(parents=True)
    (fleet_dir / "A.session.txt").write_text("stale-id\n")

    session_dir = tmp_path / "claude-projects"
    session_dir.mkdir()

    config = _make_config(["A"])
    adapter = _adapter_with_session_dir(session_dir)
    spawner = FleetSpawner(mission_dir, config, MagicMock(return_value=adapter), SOCKET)

    async def _new_session_writes(**_):
        (session_dir / "fresh-id.jsonl").write_text("{}")
        return 0

    with (
        patch("megalodon_ui.spawn.tmux.list_sessions", new=AsyncMock(return_value=[])),
        patch("megalodon_ui.spawn.tmux.new_session", side_effect=_new_session_writes),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
    ):
        await spawner.start_all()

    assert (fleet_dir / "A.session.txt").read_text().strip() == "fresh-id"
