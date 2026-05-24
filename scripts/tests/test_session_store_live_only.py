"""Task D2 guard tests — session store persistence is LIVE-mode-only.

Design rule WR-3: only the live lifespan branch wires up the disk-backed
SessionStore.  Test mode (MEGALODON_LIFESPAN_TEST_MODE=1) and fake-spawner
mode (MEGALODON_FAKE_SPAWNER=1) must NEVER write a sessions.json file.

Tests
-----
1. test_mode_session_store_path_is_none
   — LIFESPAN_TEST_MODE=1: drive an auth exchange, assert ctx.session_store
     has path=None (in-memory) AND no sessions.json written under tmp_path.
2. fake_spawner_session_store_path_is_none
   — MEGALODON_FAKE_SPAWNER=1: same assertions for the fake-spawner branch.
3. test_no_sessions_json_in_fixtures
   — Repo hygiene: glob scripts/tests/fixtures for any sessions.json and
     assert none exist (prevents test pollution from leaking into tracked files).

All tests run under ``pytest -W error``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.auth import write_token_atomic
from megalodon_ui.server import make_app

TOKEN = "d2-guard-test-token"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_mission(tmp_path: Path) -> None:
    """Create minimal required mission directory structure for make_app."""
    fleet = tmp_path / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    write_token_atomic(fleet / "ui.token", TOKEN)
    (tmp_path / "STATUS.md").write_text("# Status\n")
    (tmp_path / "TASKS.md").write_text("# Tasks\n")
    (tmp_path / "HISTORY.md").write_text("# History\n")
    (tmp_path / "findings").mkdir(exist_ok=True)
    (tmp_path / "signals").mkdir(exist_ok=True)


def _assert_no_sessions_json(root: Path) -> None:
    """Assert that no sessions.json file exists anywhere under *root*."""
    found = list(root.rglob("sessions.json"))
    assert found == [], (
        f"sessions.json should not exist in test directories, but found: {found}"
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def test_mode_client(tmp_path: Path, monkeypatch) -> AsyncGenerator[tuple, None]:
    """Authenticated client under MEGALODON_LIFESPAN_TEST_MODE=1."""
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    monkeypatch.delenv("MEGALODON_FAKE_SPAWNER", raising=False)
    _setup_mission(tmp_path)

    app = make_app(mission_dir=tmp_path)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post("/api/v1/auth/exchange", json={"token": TOKEN})
            assert r.status_code == 200, f"auth exchange failed: {r.text}"
            yield client, app, tmp_path


@pytest_asyncio.fixture
async def fake_spawner_client(
    tmp_path: Path, monkeypatch
) -> AsyncGenerator[tuple, None]:
    """Authenticated client under MEGALODON_FAKE_SPAWNER=1."""
    monkeypatch.delenv("MEGALODON_LIFESPAN_TEST_MODE", raising=False)
    monkeypatch.delenv("MEGALODON_FAKE_SESSIONS_PATH", raising=False)
    monkeypatch.setenv("MEGALODON_FAKE_SPAWNER", "1")
    _setup_mission(tmp_path)

    app = make_app(mission_dir=tmp_path)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post("/api/v1/auth/exchange", json={"token": TOKEN})
            assert r.status_code == 200, f"auth exchange failed: {r.text}"
            yield client, app, tmp_path


# ---------------------------------------------------------------------------
# 1. Test-mode guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode_session_store_path_is_none(test_mode_client) -> None:
    """MEGALODON_LIFESPAN_TEST_MODE=1: session store stays in-memory; no sessions.json written."""
    _client, app, tmp_path = test_mode_client

    ctx = app.state.megalodon
    assert ctx.session_store._path is None, (
        f"expected path=None in test mode, got: {ctx.session_store._path}"
    )
    _assert_no_sessions_json(tmp_path)


# ---------------------------------------------------------------------------
# 2. Fake-spawner guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_spawner_session_store_path_is_none(
    fake_spawner_client,
) -> None:
    """MEGALODON_FAKE_SPAWNER=1: session store stays in-memory; no sessions.json written."""
    _client, app, tmp_path = fake_spawner_client

    ctx = app.state.megalodon
    assert ctx.session_store._path is None, (
        f"expected path=None in fake-spawner mode, got: {ctx.session_store._path}"
    )
    _assert_no_sessions_json(tmp_path)


# ---------------------------------------------------------------------------
# 3. Repo-hygiene: no sessions.json in tracked fixtures
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 2b. Fake-spawner persistence opt-in seam (Task D6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_spawner_sessions_path_opt_in(tmp_path: Path, monkeypatch) -> None:
    """MEGALODON_FAKE_SESSIONS_PATH set → fake branch wires a persistent store.

    This is the test-only seam the restart-reconnect e2e (PW-3) relies on. It
    must NOT change any other branch; the default (env unset) remains path=None,
    asserted by test_fake_spawner_session_store_path_is_none above.
    """
    monkeypatch.delenv("MEGALODON_LIFESPAN_TEST_MODE", raising=False)
    monkeypatch.setenv("MEGALODON_FAKE_SPAWNER", "1")
    _setup_mission(tmp_path)
    sessions_path = tmp_path / ".fleet" / "sessions.json"
    monkeypatch.setenv("MEGALODON_FAKE_SESSIONS_PATH", str(sessions_path))

    app = make_app(mission_dir=tmp_path)
    async with app.router.lifespan_context(app):
        ctx = app.state.megalodon
        assert ctx.session_store._path == sessions_path, (
            "fake branch should use the opted-in sessions path, got: "
            f"{ctx.session_store._path}"
        )
        # The store persists on create — exercise the exchange so the file
        # actually lands at the configured path.
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post("/api/v1/auth/exchange", json={"token": TOKEN})
            assert r.status_code == 200, f"auth exchange failed: {r.text}"
    assert sessions_path.exists(), "persistent store should have written sessions.json"


def test_no_sessions_json_in_fixtures() -> None:
    """No sessions.json file must exist anywhere under scripts/tests/fixtures/.

    This guards against any test (in any branch) polluting the tracked
    fixture directory with session state that would be committed.
    """
    fixtures_dir = Path(__file__).resolve().parent / "fixtures"
    if not fixtures_dir.exists():
        # No fixtures directory — nothing to check.
        return
    found = list(fixtures_dir.rglob("sessions.json"))
    assert found == [], (
        f"sessions.json found in tracked fixtures (must not be committed): {found}"
    )
