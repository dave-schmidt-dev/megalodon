"""Session store persistence guard tests.

Design rule (Task D2, amended Wave 4):
  * TEST mode (MEGALODON_LIFESPAN_TEST_MODE=1) stays pure in-memory
    (path=None) — never writes a sessions.json file.
  * LIVE mode persists to <mission>/.fleet/sessions.json.
  * FAKE/DEMO mode (MEGALODON_FAKE_SPAWNER=1) ALSO persists by default to
    <mission>/.fleet/sessions.json (Wave 4 A2 seam) so an operator demo
    restart reattaches instead of bricking the session cookie.
    MEGALODON_FAKE_SESSIONS_PATH overrides the location.

Tests
-----
1. test_mode_session_store_path_is_none
   — LIFESPAN_TEST_MODE=1: drive an auth exchange, assert ctx.session_store
     has path=None (in-memory) AND no sessions.json written under tmp_path.
2. test_fake_spawner_session_store_persists_by_default
   — MEGALODON_FAKE_SPAWNER=1 (no override): the store is disk-backed at the
     mission's .fleet/sessions.json and the file lands after an exchange.
3. test_fake_spawner_sessions_path_opt_in
   — MEGALODON_FAKE_SESSIONS_PATH still overrides the persistence location.
4. test_no_sessions_json_in_fixtures
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
from megalodon_ui.server import SESSION_COOKIE_NAME, make_app

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
async def test_fake_spawner_session_store_persists_by_default(
    fake_spawner_client,
) -> None:
    """MEGALODON_FAKE_SPAWNER=1 (no override): store persists to .fleet/sessions.json.

    Wave 4 A2 seam: fake/demo mode now persists sessions by DEFAULT (like the
    live branch) so an operator restart of a demo reattaches the cookie instead
    of being locked out. The default path mirrors live: <mission>/.fleet/.
    """
    _client, app, tmp_path = fake_spawner_client

    ctx = app.state.megalodon
    expected = tmp_path / ".fleet" / "sessions.json"
    assert ctx.session_store._path == expected, (
        f"expected fake-spawner default path {expected}, got: {ctx.session_store._path}"
    )
    # The exchange in the fixture minted + persisted a session.
    assert expected.exists(), "fake/demo session was not persisted to disk by default"


# ---------------------------------------------------------------------------
# 3. Repo-hygiene: no sessions.json in tracked fixtures
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 2b. Fake-spawner persistence opt-in seam (Task D6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_spawner_sessions_path_opt_in(tmp_path: Path, monkeypatch) -> None:
    """MEGALODON_FAKE_SESSIONS_PATH set → fake branch uses the OVERRIDE path.

    The restart-reconnect e2e (PW-3) relies on this seam to point persistence
    at a tmp file. Wave 4 made fake mode persist by default
    (test_fake_spawner_session_store_persists_by_default); this test pins that
    the env var still overrides the default location.
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


@pytest.mark.asyncio
async def test_fake_demo_session_survives_restart_by_default(
    tmp_path: Path, monkeypatch
) -> None:
    """Fake/demo session survives a server restart WITHOUT the env override.

    Wave 4 A2 seam regression guard: mint a cookie in app instance #1 (fake
    spawner, default persistence), tear the lifespan down, bring up instance #2
    against the SAME mission dir, and assert the original cookie still
    authenticates a gated endpoint — i.e. an operator restarting a demo is not
    locked out. Mirrors scripts/tests/test_session_survives_server_restart.py
    but exercises the DEFAULT path (no MEGALODON_FAKE_SESSIONS_PATH).
    """
    monkeypatch.delenv("MEGALODON_LIFESPAN_TEST_MODE", raising=False)
    monkeypatch.delenv("MEGALODON_FAKE_SESSIONS_PATH", raising=False)
    monkeypatch.setenv("MEGALODON_FAKE_SPAWNER", "1")
    _setup_mission(tmp_path)
    sessions_path = tmp_path / ".fleet" / "sessions.json"

    # --- App instance #1: mint a session cookie via auth exchange. ----------
    app1 = make_app(mission_dir=tmp_path)
    async with app1.router.lifespan_context(app1):
        async with AsyncClient(
            transport=ASGITransport(app=app1), base_url="http://test"
        ) as client1:
            exch = await client1.post("/api/v1/auth/exchange", json={"token": TOKEN})
            assert exch.status_code == 200, exch.text
            cookie_value = client1.cookies.get(SESSION_COOKIE_NAME)
            assert cookie_value, "no session cookie was set by the exchange"
    assert sessions_path.exists(), "fake/demo session not persisted by default"

    # --- App instance #2: fresh app reading the persisted file. -------------
    app2 = make_app(mission_dir=tmp_path)
    async with app2.router.lifespan_context(app2):
        async with AsyncClient(
            transport=ASGITransport(app=app2),
            base_url="http://test",
            cookies={SESSION_COOKIE_NAME: cookie_value},
        ) as client2:
            r = await client2.get("/api/v1/narrative")
            assert r.status_code == 200, (
                "fake/demo session cookie did not survive restart — the "
                f"operator would be locked out. Got {r.status_code}: {r.text}"
            )


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
