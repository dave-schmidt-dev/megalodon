"""Operator's session cookie survives a server restart (P0 backend bug).

Regression: the in-memory SessionStore was not persisted, so every server
restart invalidated the ``mui_session`` cookie and ALL gated endpoints
(``/api/v1/narrative`` etc.) returned 401 forever. The fix persists the store
to ``<mission_dir>/.fleet/sessions.json`` (atomic, 0600, hashed) and reloads it
on startup.

The live branch wires the disk-backed store unconditionally; the fake-spawner
branch opts in via ``MEGALODON_FAKE_SESSIONS_PATH`` (the test seam used here so
the restart can be exercised without a real tmux fleet). This test mints a
cookie in app instance #1, tears the lifespan down, brings up a brand-new app
instance #2 against the SAME mission dir + sessions file, and asserts the
original cookie still authenticates a gated endpoint.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from megalodon_ui.auth import write_token_atomic
from megalodon_ui.server import SESSION_COOKIE_NAME, make_app

TOKEN = "restart-session-token"


def _setup_mission(tmp_path: Path) -> Path:
    fixtures = Path(__file__).resolve().parents[2] / "ui" / "tests" / "fixtures"

    def _ignore(_src, names):
        return [
            n
            for n in names
            if n.endswith(".stream.log") or n in ("tmux.sock", "dashboard.url")
        ]

    dst = tmp_path / "fix-medium"
    shutil.copytree(fixtures / "fix-medium", dst, ignore=_ignore)
    fleet = dst / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    write_token_atomic(fleet / "ui.token", TOKEN)
    return dst


@pytest.mark.asyncio
async def test_cookie_survives_restart(tmp_path: Path, monkeypatch) -> None:
    """A cookie minted before a restart still authenticates after it."""
    monkeypatch.delenv("MEGALODON_LIFESPAN_TEST_MODE", raising=False)
    monkeypatch.setenv("MEGALODON_FAKE_SPAWNER", "1")

    mission = _setup_mission(tmp_path)
    sessions_path = mission / ".fleet" / "sessions.json"
    monkeypatch.setenv("MEGALODON_FAKE_SESSIONS_PATH", str(sessions_path))

    # --- App instance #1: mint a session cookie via auth exchange. -----------
    app1 = make_app(mission_dir=mission)
    async with app1.router.lifespan_context(app1):
        async with AsyncClient(
            transport=ASGITransport(app=app1), base_url="http://test"
        ) as client1:
            exch = await client1.post("/api/v1/auth/exchange", json={"token": TOKEN})
            assert exch.status_code == 200, exch.text
            cookie_value = client1.cookies.get(SESSION_COOKIE_NAME)
            assert cookie_value, "no session cookie was set by the exchange"

            # Sanity: the cookie works while instance #1 is alive.
            ok = await client1.get("/api/v1/narrative")
            assert ok.status_code == 200, f"pre-restart gated call: {ok.text}"

    assert sessions_path.exists(), "session was not persisted to disk"

    # --- App instance #2: a fresh process reading the persisted file. --------
    app2 = make_app(mission_dir=mission)
    async with app2.router.lifespan_context(app2):
        async with AsyncClient(
            transport=ASGITransport(app=app2),
            base_url="http://test",
            cookies={SESSION_COOKIE_NAME: cookie_value},
        ) as client2:
            # Present ONLY the pre-restart cookie (no re-exchange).
            r = await client2.get("/api/v1/narrative")
            assert r.status_code == 200, (
                "session cookie did not survive the restart — the operator "
                f"would be locked out with 401. Got {r.status_code}: {r.text}"
            )
