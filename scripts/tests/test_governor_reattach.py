"""Reattach governance-provenance tests (Task 2.5 / PM-6 / CR-2 / CV-6).

When the server restarts, ``FleetSpawner.start_all`` REATTACHES to already-running
tmux lane sessions instead of respawning them (to preserve in-flight work). The
reattach branch rebuilds the lane's ``argv`` WITH ``--settings`` (Task 2.2), but
the LIVE process is the old one — it started under whatever regime it was born
with. So the rebuilt argv FALSELY advertises governance.

These tests pin the fix: a reattached lane is marked ``governed`` ONLY when a
per-lane marker file written AT SPAWN TIME (under the governor) is present AND its
fingerprint still matches the current governor settings. Detection keys off SPAWN
IDENTITY (the marker), never the lying rebuilt argv. Fail TOWARD ``ungoverned``.

This ``ungoverned`` concept is the PROVENANCE of the live process — distinct from
the P3.2 deny-loop ``governor-blocked`` alarm (which is about a running governed
process hitting repeated denies). Keep them separate.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.governor.wiring import (
    governed_marker_path,
    governor_fingerprint,
    governor_settings_path,
    read_governed_marker_is_valid,
    remove_governed_marker,
    write_governed_marker,
)
from megalodon_ui.mission_config.schema import MissionConfig
from megalodon_ui.spawn import FleetSpawner, LaneSession

@pytest.fixture
def socket_path(tmp_path):
    return tmp_path / ".fleet" / "tmux.sock"


# ---------------------------------------------------------------------------
# Config / adapter fakes (mirror test_spawn_unit.py)
# ---------------------------------------------------------------------------


def _make_config(
    lane_shorts: list[str] | None = None,
    *,
    governor_enabled: bool = True,
    cli: str = "claude",
) -> MissionConfig:
    shorts = lane_shorts or ["A", "B"]
    lanes = [
        {
            "name": f"LANE{s}",
            "short": s,
            "role": f"role-{s.lower()}",
            "harness": {"cli": cli, "model": "sonnet"},
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
            "governor_enabled": governor_enabled,
        }
    )


def _make_adapter(argv: list[str] | None = None) -> MagicMock:
    adapter = MagicMock()
    adapter.build_argv = MagicMock(return_value=(argv or ["stub", "arg"], {}))
    return adapter


def _governed_argv(mission_dir: Path) -> list[str]:
    """An argv that carries ``--settings <governor settings>`` (governed shape)."""
    return ["claude", "--model", "sonnet", "--settings", str(governor_settings_path())]


# ---------------------------------------------------------------------------
# Marker helpers — fingerprint, write/read/remove, atomicity
# ---------------------------------------------------------------------------


def test_governor_fingerprint_is_path_plus_content_hash():
    """Fingerprint binds the absolute settings path + a hash of its content."""
    settings = governor_settings_path()
    fp = governor_fingerprint(settings)
    assert fp["settings_path"] == str(settings)
    expected = hashlib.sha256(settings.read_bytes()).hexdigest()
    assert fp["settings_sha256"] == expected


def test_write_then_read_marker_valid(tmp_path):
    """A freshly written marker validates against the current settings."""
    write_governed_marker(tmp_path, "A")
    assert governed_marker_path(tmp_path, "A").exists()
    assert read_governed_marker_is_valid(tmp_path, "A") is True


def test_read_marker_absent_is_invalid(tmp_path):
    """No marker → not governed."""
    assert read_governed_marker_is_valid(tmp_path, "A") is False


def test_read_marker_stale_is_invalid(tmp_path):
    """A marker whose fingerprint no longer matches current settings → invalid."""
    write_governed_marker(tmp_path, "A")
    # Corrupt the stored fingerprint to simulate a settings change.
    marker = governed_marker_path(tmp_path, "A")
    data = json.loads(marker.read_text())
    data["settings_sha256"] = "deadbeef" * 8
    marker.write_text(json.dumps(data))
    assert read_governed_marker_is_valid(tmp_path, "A") is False


def test_read_marker_corrupt_is_invalid(tmp_path):
    """A non-JSON / truncated marker fails TOWARD ungoverned."""
    marker = governed_marker_path(tmp_path, "A")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("{ this is not json")
    assert read_governed_marker_is_valid(tmp_path, "A") is False


def test_remove_marker_is_idempotent(tmp_path):
    """remove is a no-op when absent, and clears a present marker."""
    remove_governed_marker(tmp_path, "A")  # absent → no raise
    write_governed_marker(tmp_path, "A")
    remove_governed_marker(tmp_path, "A")
    assert not governed_marker_path(tmp_path, "A").exists()


# ---------------------------------------------------------------------------
# Fresh-spawn marker behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fresh_governed_spawn_writes_marker_and_sets_governed(tmp_path, socket_path):
    """Governor on + claude lane → marker written, LaneSession.governed True."""
    config = _make_config(["A"], governor_enabled=True)
    adapter = _make_adapter(_governed_argv(tmp_path))
    resolver = MagicMock(return_value=adapter)
    spawner = FleetSpawner(tmp_path, config, resolver, socket_path)

    with (
        patch("megalodon_ui.spawn.tmux.list_sessions", new=AsyncMock(return_value=[])),
        patch("megalodon_ui.spawn.tmux.new_session", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn.preflight_governor"),
        patch("megalodon_ui.spawn.governor_canary_selftest"),
        patch.object(FleetSpawner, "_start_tail_task", new=AsyncMock()),
    ):
        await spawner.start_all()

    assert spawner.sessions["A"].governed is True
    assert read_governed_marker_is_valid(tmp_path, "A") is True


@pytest.mark.asyncio
async def test_fresh_ungoverned_spawn_removes_stale_marker(tmp_path, socket_path):
    """Governor off → no marker (and a pre-existing stale one is removed)."""
    # Pre-seed a stale marker as if an earlier governed run wrote it.
    write_governed_marker(tmp_path, "A")
    assert governed_marker_path(tmp_path, "A").exists()

    config = _make_config(["A"], governor_enabled=False)
    adapter = _make_adapter(["stub", "arg"])
    resolver = MagicMock(return_value=adapter)
    spawner = FleetSpawner(tmp_path, config, resolver, socket_path)

    with (
        patch("megalodon_ui.spawn.tmux.list_sessions", new=AsyncMock(return_value=[])),
        patch("megalodon_ui.spawn.tmux.new_session", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
        patch.object(FleetSpawner, "_start_tail_task", new=AsyncMock()),
    ):
        await spawner.start_all()

    assert spawner.sessions["A"].governed is False
    assert not governed_marker_path(tmp_path, "A").exists()


# ---------------------------------------------------------------------------
# Reattach branch — governance derived from marker, NOT the rebuilt argv
# ---------------------------------------------------------------------------


async def _run_reattach(tmp_path, config, socket_path) -> FleetSpawner:
    """Reattach lane-A (already running, fleet-owned); never spawn it."""
    adapter = _make_adapter(_governed_argv(tmp_path))  # rebuilt argv LIES: governed
    resolver = MagicMock(return_value=adapter)
    spawner = FleetSpawner(tmp_path, config, resolver, socket_path)

    with (
        patch(
            "megalodon_ui.spawn.tmux.list_sessions",
            new=AsyncMock(return_value=["lane-A"]),
        ),
        patch.object(
            FleetSpawner,
            "_is_fleet_owned",
            new=AsyncMock(side_effect=lambda n: n == "lane-A"),
        ),
        patch(
            "megalodon_ui.spawn.tmux.new_session", new=AsyncMock(return_value=0)
        ) as mock_new,
        patch(
            "megalodon_ui.spawn.tmux.kill_session", new=AsyncMock(return_value=0)
        ) as mock_kill,
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
        patch(
            "megalodon_ui.spawn.tmux.display_message_pane_pipe",
            new=AsyncMock(return_value=True),
        ),
        patch("megalodon_ui.spawn.preflight_governor"),
        patch("megalodon_ui.spawn.governor_canary_selftest"),
        patch.object(FleetSpawner, "_start_tail_task", new=AsyncMock()),
    ):
        await spawner.start_all()

    spawner._mock_new = mock_new  # type: ignore[attr-defined]
    spawner._mock_kill = mock_kill  # type: ignore[attr-defined]
    return spawner


@pytest.mark.asyncio
async def test_reattach_valid_marker_is_governed_and_not_respawned(tmp_path, socket_path):
    """Valid governed marker → governed True; lane is NOT killed/respawned."""
    write_governed_marker(tmp_path, "A")  # the live process WAS born governed
    config = _make_config(["A"], governor_enabled=True)
    spawner = await _run_reattach(tmp_path, config, socket_path)

    assert spawner.sessions["A"].governed is True
    # In-flight work preserved: never killed, never re-newed.
    assert "lane-A" not in [
        c.kwargs.get("name") for c in spawner._mock_new.call_args_list
    ]
    assert "lane-A" not in [c.args[1] for c in spawner._mock_kill.call_args_list]


@pytest.mark.asyncio
async def test_reattach_no_marker_is_ungoverned_and_not_respawned(tmp_path, socket_path):
    """No marker → governed False (ungoverned); lane is NOT killed/respawned."""
    config = _make_config(["A"], governor_enabled=True)
    spawner = await _run_reattach(tmp_path, config, socket_path)

    assert spawner.sessions["A"].governed is False
    assert "lane-A" not in [
        c.kwargs.get("name") for c in spawner._mock_new.call_args_list
    ]
    assert "lane-A" not in [c.args[1] for c in spawner._mock_kill.call_args_list]


@pytest.mark.asyncio
async def test_reattach_stale_marker_is_ungoverned(tmp_path, socket_path):
    """Stale marker (fingerprint mismatch) → governed False; not respawned."""
    write_governed_marker(tmp_path, "A")
    marker = governed_marker_path(tmp_path, "A")
    data = json.loads(marker.read_text())
    data["settings_sha256"] = "0" * 64  # simulate settings changed since spawn
    marker.write_text(json.dumps(data))

    config = _make_config(["A"], governor_enabled=True)
    spawner = await _run_reattach(tmp_path, config, socket_path)

    assert spawner.sessions["A"].governed is False
    assert "lane-A" not in [c.args[1] for c in spawner._mock_kill.call_args_list]


@pytest.mark.asyncio
async def test_reattach_governed_ignores_lying_argv(tmp_path, socket_path):
    """Even though the rebuilt argv carries --settings, NO marker → ungoverned.

    This is the core invariant: the rebuilt argv lies; only the marker decides.
    """
    config = _make_config(["A"], governor_enabled=True)
    spawner = await _run_reattach(tmp_path, config, socket_path)

    # The stored argv is the would-be-governed template (correct for a future
    # respawn) — assert it DOES carry --settings...
    assert "--settings" in spawner.sessions["A"].argv
    # ...yet governance is False because no marker proves the LIVE process is governed.
    assert spawner.sessions["A"].governed is False


# ---------------------------------------------------------------------------
# Respawn governs (operator-initiated)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_respawn_with_governed_argv_writes_marker(tmp_path, socket_path):
    """An operator respawn under the governor writes the marker + sets governed."""
    config = _make_config(["A"], governor_enabled=True)
    adapter = _make_adapter()
    resolver = MagicMock(return_value=adapter)
    spawner = FleetSpawner(tmp_path, config, resolver, socket_path)
    spawner.sessions["A"] = LaneSession(
        lane="A",
        name="lane-A",
        cwd=tmp_path,
        argv=["old"],
        env={},
        stream_log=tmp_path / ".fleet" / "A.stream.log",
        running=True,
        governed=False,  # was ungoverned (pre-governor lane)
    )

    gov_argv = _governed_argv(tmp_path)
    with (
        patch("megalodon_ui.spawn.tmux.respawn_pane", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
        patch(
            "megalodon_ui.spawn.tmux.display_message_pane_pipe",
            new=AsyncMock(return_value=True),
        ),
    ):
        await spawner.respawn("A", gov_argv, {})

    assert spawner.sessions["A"].governed is True
    assert read_governed_marker_is_valid(tmp_path, "A") is True


@pytest.mark.asyncio
async def test_respawn_with_ungoverned_argv_clears_marker(tmp_path, socket_path):
    """A respawn whose argv lacks --settings clears any stale marker → ungoverned."""
    write_governed_marker(tmp_path, "A")
    config = _make_config(["A"], governor_enabled=True)
    resolver = MagicMock(return_value=_make_adapter())
    spawner = FleetSpawner(tmp_path, config, resolver, socket_path)
    spawner.sessions["A"] = LaneSession(
        lane="A",
        name="lane-A",
        cwd=tmp_path,
        argv=["old"],
        env={},
        stream_log=tmp_path / ".fleet" / "A.stream.log",
        running=True,
        governed=True,
    )

    with (
        patch("megalodon_ui.spawn.tmux.respawn_pane", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
        patch(
            "megalodon_ui.spawn.tmux.display_message_pane_pipe",
            new=AsyncMock(return_value=True),
        ),
    ):
        await spawner.respawn("A", ["claude", "--print", "hi"], {})

    assert spawner.sessions["A"].governed is False
    assert not governed_marker_path(tmp_path, "A").exists()


# ---------------------------------------------------------------------------
# Board surfacing
# ---------------------------------------------------------------------------


def test_board_lane_row_surfaces_governed_field():
    """LaneRow.to_dict() exposes a `governed` flag so ungoverned is visible."""
    from megalodon_ui.narrator.board_state import LaneRow

    row = LaneRow(
        lane="A",
        lane_name="LANEA",
        state="open",
        last=None,
        now=None,
        goal="",
        tokens=None,
        narrator_ok=False,
        governed=False,
    )
    d = row.to_dict()
    assert d["governed"] is False


@pytest.mark.asyncio
async def test_build_lane_rows_marks_ungoverned_lane(tmp_path):
    """build_lane_rows reflects an ungoverned reattached lane distinctly."""
    from megalodon_ui.narrator.board_state import build_lane_rows

    config = _make_config(["A", "B"], governor_enabled=True)
    (tmp_path / ".fleet").mkdir(parents=True, exist_ok=True)

    # Session A reattached governed; session B reattached ungoverned.
    sessions = {
        "A": LaneSession(
            lane="A",
            name="lane-A",
            cwd=tmp_path,
            argv=[],
            env={},
            stream_log=tmp_path / ".fleet" / "A.stream.log",
            governed=True,
        ),
        "B": LaneSession(
            lane="B",
            name="lane-B",
            cwd=tmp_path,
            argv=[],
            env={},
            stream_log=tmp_path / ".fleet" / "B.stream.log",
            governed=False,
        ),
    }
    adapter = MagicMock()
    adapter.session_log_dir = MagicMock(return_value=None)
    resolver = MagicMock(return_value=adapter)

    rows = await build_lane_rows(
        tmp_path,
        {"phases": {}, "cross": []},
        sessions,
        resolver,
        config.lanes,
    )
    assert rows["A"].to_dict()["governed"] is True
    assert rows["B"].to_dict()["governed"] is False
