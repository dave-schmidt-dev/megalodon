"""P1 coverage: /healthz 503 branch and TEST_MODE bypass path.

Covers two uncovered branches in megalodon_ui/server.py:

1. /healthz returns 503 {"status": "starting"} when startup_complete is False
   (server.py:473-474). The existing test only covers the 200 path after full
   lifespan startup; 503 is only reachable before startup_complete is set.

2. MEGALODON_LIFESPAN_TEST_MODE=1 sets app.state.spawner = None and
   app.state.startup_complete = True (server.py:378-385). The conftest fixture
   exercises this implicitly but never explicitly asserts spawner is None.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fix_three_lane(tmp_path):
    """Copy fix-medium to /tmp to keep the socket path under 100 bytes."""
    import tempfile

    with tempfile.TemporaryDirectory(dir="/tmp", prefix="meg_hlt_") as td:
        dst = Path(td) / "m"
        shutil.copytree(FIXTURES / "fix-medium", dst)
        yield dst


# ---------------------------------------------------------------------------
# Test 1: /healthz returns 503 before startup_complete is set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_healthz_returns_503_before_startup_complete(fix_three_lane, monkeypatch):
    """/healthz must return 503 {"status": "starting"} when startup_complete is False.

    We use TEST_MODE so the lifespan doesn't actually spawn tmux sessions, then
    manually flip startup_complete to False after the lifespan completes to
    simulate the pre-startup window.
    """
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")

    from megalodon_ui.server import make_app
    from httpx import AsyncClient, ASGITransport

    app = make_app(mission_dir=fix_three_lane)
    async with app.router.lifespan_context(app):
        # Force the flag back to False to simulate the pre-startup window.
        app.state.startup_complete = False

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            r = await client.get("/healthz")

    assert r.status_code == 503, (
        f"expected 503 before startup_complete, got {r.status_code}: {r.text}"
    )
    assert r.json() == {"status": "starting"}, (
        f"expected {{status: starting}}, got {r.json()}"
    )


# ---------------------------------------------------------------------------
# Test 2: TEST_MODE sets spawner=None, startup_complete=True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_test_mode_spawner_is_none_startup_complete(fix_three_lane, monkeypatch):
    """MEGALODON_LIFESPAN_TEST_MODE=1 must set spawner=None and startup_complete=True.

    Verifies server.py:378-385: the test-mode fast-path sets app.state.spawner = None
    and app.state.startup_complete = True, then yields without touching tmux.
    """
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")

    from megalodon_ui.server import make_app
    from httpx import AsyncClient, ASGITransport

    app = make_app(mission_dir=fix_three_lane)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            # spawner must be None in TEST_MODE — no tmux available.
            assert app.state.spawner is None, (
                f"expected spawner=None in TEST_MODE, got {app.state.spawner!r}"
            )
            # startup_complete must be True so /healthz returns 200.
            assert app.state.startup_complete is True, (
                "expected startup_complete=True in TEST_MODE"
            )
            # /healthz must return 200 in TEST_MODE.
            r = await client.get("/healthz")
            assert r.status_code == 200, (
                f"expected 200 in TEST_MODE, got {r.status_code}: {r.text}"
            )
