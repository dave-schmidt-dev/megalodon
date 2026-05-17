"""Shared pytest fixtures for scripts/tests/."""

import shutil
from pathlib import Path

import pytest

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
