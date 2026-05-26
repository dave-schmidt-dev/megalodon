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

    Also skips ``new_run.sh``'s socket-path budget guard: tests scaffold runs
    under deep pytest tmp paths whose ``<run>/.fleet/tmux.sock`` exceeds the
    100-byte limit but never spawn, so the budget check would spuriously refuse
    them with exit 2. The dedicated budget-rejection test opts back in via
    ``monkeypatch.delenv("MEGALODON_SKIP_SOCKET_BUDGET", raising=False)``.

    Tests that need to exercise the real fleet-spawn path explicitly unset
    ``MEGALODON_LIFESPAN_TEST_MODE`` via ``monkeypatch.delenv(..., raising=False)``.

    Also seeds ``MEGALODON_CONTROL_MODE=1`` so ``make_app`` builds with the
    server-side control-mode flag ON. Every destructive ``/api/**`` endpoint is
    now ``_control_mode_or_403``-gated (read-only by default); the mutation tests
    under ``scripts/tests/`` exercise the write paths and would otherwise all 403.
    Default-OFF behaviour is asserted by ``ui/tests/integration/
    test_control_mode_server.py`` (a different conftest scope, unaffected here);
    any ``scripts/tests`` test needing the OFF path opts out via
    ``monkeypatch.delenv("MEGALODON_CONTROL_MODE", raising=False)``.
    """
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    monkeypatch.setenv("MEGALODON_SKIP_SOCKET_BUDGET", "1")
    monkeypatch.setenv("MEGALODON_CONTROL_MODE", "1")


FIXTURE_SRC = Path(__file__).parent / "fixtures" / "minimal_mission"
QUEUE_FIXTURE_SRC = Path(__file__).parent / "fixtures" / "queue_mission"

# Repo root: conftest.py lives at <repo>/scripts/tests/conftest.py.
REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_SCRIPTS = REPO_ROOT / "scripts"


def link_governor_scripts(mission_dir: Path) -> Path:
    """Wire the run-dir ``scripts/`` symlink the governor preflight requires.

    The spawn path runs ``preflight_governor`` (and the canary self-test) before
    any lane starts whenever the governor is enabled — which is the default
    (``governor_enabled`` defaults to True). Preflight resolves the PreToolUse
    hook through ``<mission_dir>/scripts/governor_hook.py``; production creates
    that path via ``new_run.sh``'s ``ln -sfn ../../scripts <run>/scripts``.

    Test mission dirs are scaffolded under throwaway tmp paths and never run
    ``new_run.sh``, so the symlink is absent and preflight raises
    ``GovernorPreflightError`` (wiring.py:331) before tmux is ever touched. This
    helper recreates the link so real-tmux tests exercise the genuine spawn
    path (preflight + canary self-test included) against the repo's real
    ``scripts/`` shim. Production uses a relative target (``../../scripts``);
    tmp mission dirs live outside the repo tree, so we point at the absolute
    repo ``scripts/`` instead — same resolved target, valid from any location.

    Idempotent: an existing matching symlink is left in place.

    Args:
        mission_dir: the run/mission directory (== ``$CLAUDE_PROJECT_DIR``).

    Returns:
        The created ``<mission_dir>/scripts`` symlink path.
    """
    link = mission_dir / "scripts"
    if link.is_symlink() or link.exists():
        return link
    link.symlink_to(REPO_SCRIPTS, target_is_directory=True)
    return link


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

    The run-dir ``scripts/`` symlink the governor preflight requires is wired up
    front (see :func:`link_governor_scripts`) so real-tmux tests reach tmux
    instead of dying in ``preflight_governor`` with ``GovernorPreflightError``.
    """
    d = Path(tempfile.mkdtemp(prefix="mgld-m-", dir="/tmp"))
    link_governor_scripts(d)
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def governor_scripts_link():
    """Return the :func:`link_governor_scripts` helper for ``tmp_path`` tests.

    ``short_mission_dir`` wires the symlink itself, but real-tmux tests that
    build their mission dir straight from ``tmp_path`` (rather than the short
    fixture) call this to satisfy the same governor preflight precondition.
    """
    return link_governor_scripts


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
