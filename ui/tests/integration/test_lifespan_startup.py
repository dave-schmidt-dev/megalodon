"""Integration test: lifespan wires FleetSpawner and /healthz returns 200.

Mocks the tmux module so no real tmux binary is required.  Verifies:
  - app.state.spawner is a FleetSpawner instance.
  - app.state.startup_complete is True after lifespan startup.
  - GET /healthz returns 200 {"status": "ok"} inside the lifespan.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
def fix_three_lane():
    """Copy fix-medium to /tmp so the socket path stays under 100 bytes.

    macOS tmp_path resolves to /private/var/folders/... which is typically
    >80 chars, causing the socket-path-length guard (exit 10) to trigger.
    /tmp is a symlink to /private/tmp on macOS and stays short enough.
    The directory is cleaned up after the test.
    """
    import tempfile

    with tempfile.TemporaryDirectory(dir="/tmp", prefix="meg_") as td:
        dst = Path(td) / "m"
        shutil.copytree(FIXTURES / "fix-medium", dst)
        yield dst


@pytest_asyncio.fixture
async def spawner_mocked_client(fix_three_lane, monkeypatch):
    """httpx.AsyncClient with full lifespan, tmux calls mocked out.

    Patches megalodon_ui.tmux so no real tmux sessions are started.
    - list_sessions returns [] (no existing sessions to reattach/purge).
    - new_session returns 0 (success).
    - kill_session is a no-op.
    - display_message_pane_pipe is a no-op.
    """
    import megalodon_ui.tmux as tmux_mod

    monkeypatch.setattr(tmux_mod, "list_sessions", AsyncMock(return_value=[]))
    monkeypatch.setattr(tmux_mod, "new_session", AsyncMock(return_value=0))
    monkeypatch.setattr(tmux_mod, "kill_session", AsyncMock(return_value=None))
    monkeypatch.setattr(
        tmux_mod, "display_message_pane_pipe", AsyncMock(return_value=None)
    )

    # Prevent the _df_watchdog from exiting the process during tests.
    import shutil as shutil_mod

    fake_usage = MagicMock()
    fake_usage.free = 10 * 1024 * 1024 * 1024  # 10 GB free
    monkeypatch.setattr(shutil_mod, "disk_usage", MagicMock(return_value=fake_usage))

    from megalodon_ui.server import make_app
    from httpx import AsyncClient, ASGITransport

    app = make_app(mission_dir=fix_three_lane)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            client.app = app
            yield client


@pytest.mark.asyncio
async def test_startup_complete_flag(spawner_mocked_client):
    """app.state.startup_complete is True after lifespan startup."""
    app = spawner_mocked_client.app
    assert app.state.startup_complete is True, (
        "startup_complete not set True after lifespan ran"
    )


@pytest.mark.asyncio
async def test_spawner_instance(spawner_mocked_client):
    """app.state.spawner is a FleetSpawner instance after lifespan startup."""
    from megalodon_ui.spawn import FleetSpawner

    app = spawner_mocked_client.app
    assert isinstance(app.state.spawner, FleetSpawner), (
        f"expected FleetSpawner, got {type(app.state.spawner)}"
    )


@pytest.mark.asyncio
async def test_healthz_returns_200_after_startup(spawner_mocked_client):
    """GET /healthz returns 200 {"status": "ok"} after startup completes."""
    r = await spawner_mocked_client.get("/healthz")
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
    assert r.json() == {"status": "ok"}, f"unexpected body: {r.json()}"
