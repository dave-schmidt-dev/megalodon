"""Shared pytest fixtures for scripts/tests/."""

import shutil
from pathlib import Path

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "smoke_harness: spawn a real harness CLI with --help; skipped if CLI absent or CI=1",
    )

FIXTURE_SRC = Path(__file__).parent / "fixtures" / "minimal_mission"
QUEUE_FIXTURE_SRC = Path(__file__).parent / "fixtures" / "queue_mission"


@pytest.fixture
def mission_dir(tmp_path: Path) -> Path:
    """Per-test writable copy of the minimal_mission fixture."""
    dest = tmp_path / "mission"
    shutil.copytree(FIXTURE_SRC, dest)
    return dest


@pytest.fixture
def queue_mission(tmp_path: Path) -> Path:
    """Per-test writable copy of the queue_mission fixture (V9 M1)."""
    dest = tmp_path / "queue_mission"
    shutil.copytree(QUEUE_FIXTURE_SRC, dest)
    return dest


@pytest.fixture
def agent() -> str:
    return "agent-abcd"


@pytest.fixture
def default_config():
    """v9.0 back-compat MissionConfig synthesized from a tmp path.

    Provides a stable, valid MissionConfig for tests that need to inject
    a config (P2.x and later phases). mission.id and utc_started are
    arbitrary; only lane/phase/pattern data is relied upon.
    """
    from megalodon_ui.mission_config.default_v9_0_shape import synthesize
    from pathlib import Path
    return synthesize(Path("/tmp"))
