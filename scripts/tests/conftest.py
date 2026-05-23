"""Shared pytest fixtures for scripts/tests/."""

import shutil
import tempfile
from pathlib import Path

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "smoke_harness: spawn a real harness CLI with --help; skipped if CLI absent or CI=1",
    )


@pytest.fixture(autouse=True)
def _lifespan_test_mode(monkeypatch):
    """Default the v9.2 lifespan into test mode (no fleet spawn, no socket-len guard).

    Tests that need to exercise the real fleet-spawn path explicitly unset
    this env var via ``monkeypatch.delenv("MEGALODON_LIFESPAN_TEST_MODE", raising=False)``.
    """
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")


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
def tmux_socket():
    """Short-path tmux control socket for real-tmux integration tests.

    A Unix-domain socket path must fit the OS ``sun_path`` limit (104 bytes on
    macOS). The default pytest ``tmp_path`` (``/private/var/folders/.../
    pytest-of-<user>/...``) is ~120 bytes and blows it, so real-tmux tests would
    fail with ``error connecting to … (File name too long)``. Bind under a short
    ``/tmp`` dir instead — the same ≤104-byte precondition production enforces
    (``megalodon_ui/__main__.py`` exits 10 on an over-limit mission path).
    """
    d = Path(tempfile.mkdtemp(prefix="mgld-tmux-", dir="/tmp"))
    try:
        yield d / "s.sock"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def short_mission_dir():
    """Short-path mission dir for real-tmux tests whose socket lives at
    ``<mission>/.fleet/tmux.sock``.

    Same 104-byte ``sun_path`` constraint as ``tmux_socket``: the default pytest
    ``tmp_path`` is too long, so a mission dir derived from it pushes the socket
    over the limit. Rooting the mission under a short ``/tmp`` dir keeps it well
    under 104 bytes.
    """
    d = Path(tempfile.mkdtemp(prefix="mgld-m-", dir="/tmp"))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


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
