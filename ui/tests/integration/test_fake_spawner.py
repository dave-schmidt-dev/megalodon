"""P7.8 — FakeFleetSpawner integration tests.

The fake spawner is what Playwright's ``chromium-v92-dashboard`` runs against,
so it must:

1. Materialize one ``LaneSession`` per ``mission_config.lanes`` on construction.
2. Serve the same surface ``subscribe``, ``unsubscribe``, ``respawn``,
   ``get`` that the v9.2 routes call.
3. Drain-then-push the ``_RESPAWN_SENTINEL`` under ``subscribers_lock`` on
   ``respawn`` (matches the real spawner's CV-12+PM-7 contract).
4. Surface ``fake_emit``, ``set_pane_dead``, ``set_pane_alive`` for tests to
   drive byte flow + lane state without tmux.
5. Be installed by the lifespan when ``MEGALODON_FAKE_SPAWNER=1`` is set.

These tests pin the contract at the Python layer; the Playwright specs that
build on top live under ``ui/tests/e2e/``.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from megalodon_ui.auth import write_token_atomic
from megalodon_ui.server import make_app
from megalodon_ui.spawn import _RESPAWN_SENTINEL
from megalodon_ui.spawn_fake import FakeFleetSpawner


pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def authed_fake_client(
    fix_medium: Path, monkeypatch
) -> AsyncGenerator[tuple, None]:
    monkeypatch.setenv("MEGALODON_FAKE_SPAWNER", "1")
    fleet = fix_medium / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    token = "fake-spawner-test-token"
    write_token_atomic(fleet / "ui.token", token)

    app = make_app(mission_dir=fix_medium)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            exch = await client.post("/api/v1/auth/exchange", json={"token": token})
            assert exch.status_code == 200, exch.text
            yield client, app


@pytest.mark.asyncio
async def test_fake_spawner_constructs_per_lane_sessions():
    """Direct unit test on FakeFleetSpawner — every mission_config lane → one session."""
    from megalodon_ui.mission_config.schema import MissionConfig

    config = MissionConfig.model_validate({
        "mission": {"id": "t", "utc_started": "2026-01-01T00:00:00Z"},
        "lanes": [
            {"name": "ALPHA", "short": "A", "role": "r",
             "harness": {"cli": "claude", "model": "sonnet"},
             "cadence_seconds": 300, "tick_offset_seconds": 0},
            {"name": "BRAVO", "short": "B", "role": "r",
             "harness": {"cli": "claude", "model": "sonnet"},
             "cadence_seconds": 300, "tick_offset_seconds": 0},
        ],
        "phases": ["INIT"],
    })
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        spawner = FakeFleetSpawner(
            Path(tmp), config, lambda cli: None, Path(tmp) / ".fleet" / "tmux.sock",
        )
        assert set(spawner.sessions.keys()) == {"A", "B"}
        for s in spawner.sessions.values():
            assert s.running is True
            assert s.stream_log.exists()


@pytest.mark.asyncio
async def test_fake_spawner_installed_when_env_var_set(authed_fake_client):
    """The lifespan installs FakeFleetSpawner when MEGALODON_FAKE_SPAWNER=1."""
    client, app = authed_fake_client
    spawner = app.state.spawner
    assert spawner is not None
    assert isinstance(spawner, FakeFleetSpawner)
    assert "A" in spawner.sessions


@pytest.mark.asyncio
async def test_fake_emit_route_pushes_bytes_to_subscribers(authed_fake_client):
    """POST /api/v1/__fake__/emit puts bytes in every subscriber's queue."""
    client, app = authed_fake_client
    spawner = app.state.spawner

    q = await spawner.subscribe("A")
    payload = base64.b64encode(b"hello pane").decode("ascii")
    r = await client.post(
        "/api/v1/__fake__/emit",
        json={"lane": "A", "data_b64": payload},
    )
    assert r.status_code == 200, r.text
    chunk = q.get_nowait()
    assert chunk == b"hello pane"
    # stream log also accumulated.
    assert spawner.sessions["A"].stream_log.read_bytes() == b"hello pane"


@pytest.mark.asyncio
async def test_fake_set_state_route_flips_lane_dead_and_alive(authed_fake_client):
    """POST /api/v1/__fake__/set_state surfaces via GET /api/v1/lane/A/state."""
    client, app = authed_fake_client

    # Initially alive.
    r = await client.get("/api/v1/lane/A/state")
    assert r.status_code == 200
    assert r.json()["running"] is True
    assert r.json()["exited_rc"] is None

    # Flip dead with rc=17.
    r = await client.post(
        "/api/v1/__fake__/set_state",
        json={"lane": "A", "running": False, "rc": 17},
    )
    assert r.status_code == 200
    r = await client.get("/api/v1/lane/A/state")
    body = r.json()
    assert body["running"] is False
    assert body["exited_rc"] == 17

    # Flip back alive.
    r = await client.post(
        "/api/v1/__fake__/set_state",
        json={"lane": "A", "running": True},
    )
    assert r.status_code == 200
    r = await client.get("/api/v1/lane/A/state")
    body = r.json()
    assert body["running"] is True
    assert body["exited_rc"] is None


@pytest.mark.asyncio
async def test_fake_respawn_drains_then_pushes_sentinel(authed_fake_client):
    """Sentinel must be the first chunk a subscriber sees after respawn."""
    client, app = authed_fake_client
    spawner = app.state.spawner

    q = await spawner.subscribe("A")
    # Pre-fill with stale bytes that drain-then-push must evict.
    q.put_nowait(b"stale-1")
    q.put_nowait(b"stale-2")

    r = await client.post(
        "/api/v1/lane/A/followup",
        json={"prompt": "new prompt"},
    )
    assert r.status_code == 202, r.text

    # First chunk out of the queue is the sentinel, NOT the stale bytes.
    first = q.get_nowait()
    assert first == _RESPAWN_SENTINEL


@pytest.mark.asyncio
async def test_fake_routes_require_cookie(fix_medium: Path, monkeypatch):
    """The /__fake__/* routes are cookie-gated via _V92_GATED_PATH_RE."""
    monkeypatch.setenv("MEGALODON_FAKE_SPAWNER", "1")
    app = make_app(mission_dir=fix_medium)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post(
                "/api/v1/__fake__/emit",
                json={"lane": "A", "data_b64": "aGk="},
            )
            assert r.status_code == 401


@pytest.mark.asyncio
async def test_fake_routes_not_registered_when_env_unset(fix_medium: Path, monkeypatch):
    """Production safety — fake routes return 404 when MEGALODON_FAKE_SPAWNER!=1."""
    monkeypatch.delenv("MEGALODON_FAKE_SPAWNER", raising=False)
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    fleet = fix_medium / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    token = "no-fake-token"
    write_token_atomic(fleet / "ui.token", token)
    app = make_app(mission_dir=fix_medium)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            exch = await client.post("/api/v1/auth/exchange", json={"token": token})
            assert exch.status_code == 200
            r = await client.post(
                "/api/v1/__fake__/emit",
                json={"lane": "A", "data_b64": "aGk="},
            )
            # 405 from SPA catch-all GET-only route OR 404 — both prove the
            # POST handler is unregistered. What matters: it's NOT 200.
            assert r.status_code in (404, 405)
