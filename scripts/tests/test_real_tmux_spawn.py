"""Integration tests for FleetSpawner against a real tmux process.

All tests are skipped when tmux is not installed.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui import tmux
from megalodon_ui.mission_config.schema import MissionConfig
from megalodon_ui.spawn import FleetSpawner

pytestmark = [
    pytest.mark.isolated,
    pytest.mark.skipif(
        shutil.which("tmux") is None,
        reason="tmux not installed",
    ),
]

FIXTURES_DIR = Path(__file__).parent / "fixtures"
STUB_ADAPTER_PATH = FIXTURES_DIR / "stub_adapter.py"
STUB_HARNESS_PATH = FIXTURES_DIR / "stub_harness.sh"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_3lane_config(mission_dir: Path) -> MissionConfig:
    """Build a 3-lane MissionConfig using the stub harness (stub-long mode)."""
    return MissionConfig.model_validate(
        {
            "mission": {
                "id": "real-tmux-test",
                "utc_started": "2026-01-01T00:00:00Z",
            },
            "lanes": [
                {
                    "name": "ALPHA",
                    "short": "A",
                    "role": "test-lane-a",
                    "harness": {"cli": "claude", "model": "stub-long"},
                    "cadence_seconds": 300,
                    "tick_offset_seconds": 0,
                },
                {
                    "name": "BETA",
                    "short": "B",
                    "role": "test-lane-b",
                    "harness": {"cli": "claude", "model": "stub-long"},
                    "cadence_seconds": 300,
                    "tick_offset_seconds": 0,
                },
                {
                    "name": "GAMMA",
                    "short": "C",
                    "role": "test-lane-c",
                    "harness": {"cli": "claude", "model": "stub-long"},
                    "cadence_seconds": 300,
                    "tick_offset_seconds": 0,
                },
            ],
            "phases": ["INIT"],
        }
    )


def _stub_resolver(stub_script: Path):
    """Return an adapter_resolver that always returns a StubAdapter-like object."""

    # Inline stub to avoid import path complexity in the fixture dir
    class _InlineStubAdapter:
        name = "stub"
        default_model = "stub-long"
        supports_autonomous_loop = False

        def build_argv(self, prompt: str, *, model: str, cwd: Path, **_):
            return [str(stub_script), "long"], {}

    adapter = _InlineStubAdapter()

    def resolver(cli: str):
        return adapter

    return resolver


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_tmux_start_all_creates_sessions(tmp_path: Path):
    """start_all must create one tmux session per configured lane on the real socket."""
    # Ensure stub harness is executable; if not, skip gracefully
    if not STUB_HARNESS_PATH.exists():
        pytest.skip("stub_harness.sh fixture missing")

    fleet_dir = tmp_path / ".fleet"
    fleet_dir.mkdir(parents=True, exist_ok=True)
    socket = fleet_dir / "tmux.sock"

    config = _make_3lane_config(tmp_path)
    resolver = _stub_resolver(STUB_HARNESS_PATH)
    spawner = FleetSpawner(tmp_path, config, resolver, socket)

    try:
        await spawner.start_all()

        for short in ["A", "B", "C"]:
            session_name = f"lane-{short}"
            exists = await tmux.has_session(socket, session_name)
            assert exists, f"expected session {session_name} to exist after start_all"

        for short in ["A", "B", "C"]:
            assert spawner.sessions[short].running is True

    finally:
        await spawner.stop_all()
        # Verify sessions are gone after stop_all
        for short in ["A", "B", "C"]:
            session_name = f"lane-{short}"
            exists = await tmux.has_session(socket, session_name)
            assert not exists, (
                f"expected session {session_name} to be gone after stop_all"
            )
