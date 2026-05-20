"""P7.1 — DELETE /api/v1/fleet endpoint (destructive teardown).

Plan §7 row: ``DELETE /api/v1/fleet | cookie | tmux.kill_server + unlink
ui.token + tmux.sock + dashboard.url; server exits 0 shortly after``.

The handler must:

1. Require a valid ``mui_session`` cookie (gated via ``_V92_GATED_EXACT``).
2. Call ``tmux.kill_server(socket)`` to terminate every ``lane-*`` session
   on the mission's per-mission socket. Best-effort: a non-zero rc (e.g.,
   the server is already gone) is NOT a failure.
3. Unlink the three artifact files under ``<mission>/.fleet/``:
   - ``ui.token`` — bootstrap shared secret (no longer usable).
   - ``tmux.sock`` — the server socket file (gone after kill_server, but
     unlink defensively in case tmux leaves the inode).
   - ``dashboard.url`` — recovery URL (CV-11).
   Missing files are not failures — operation is idempotent.
4. Return 200 ``{"status": "shutdown"}``.
5. Schedule the uvicorn shutdown via ``request.app.state.shutdown_requested =
   True`` so the process exits 0 shortly after the response is flushed.
   (Inside this integration test we assert the flag flips, not the
   actual process exit — that is exercised by the standalone ``shutdown.py``
   CLI test under P7.2 + by uvicorn's lifespan in production.)

Error paths:
  - 401 without cookie.
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from megalodon_ui.auth import write_token_atomic
from megalodon_ui.mission_config.schema import MissionConfig
from megalodon_ui.server import make_app
from megalodon_ui.spawn import FleetSpawner, LaneSession


pytestmark = pytest.mark.integration


def _make_config(shorts: list[str]) -> MissionConfig:
    lanes = [
        {
            "name": f"LANE{s}",
            "short": s,
            "role": f"role-{s.lower()}",
            "harness": {"cli": "claude", "model": "claude-sonnet-4-6"},
            "cadence_seconds": 300,
            "tick_offset_seconds": 0,
        }
        for s in shorts
    ]
    return MissionConfig.model_validate(
        {
            "mission": {"id": "teardown-test", "utc_started": "2026-01-01T00:00:00Z"},
            "lanes": lanes,
            "phases": ["INIT"],
        }
    )


@pytest_asyncio.fixture
async def authed_teardown_client(
    fix_medium: Path, monkeypatch
) -> AsyncGenerator[tuple, None]:
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    fleet = fix_medium / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)

    # Pre-populate the three artifact files the endpoint must unlink.
    token = "teardown-test-token"
    write_token_atomic(fleet / "ui.token", token)
    (fleet / "tmux.sock").write_bytes(b"")  # placeholder file
    (fleet / "dashboard.url").write_text(
        "http://127.0.0.1:8080/#t=teardown-test-token\n"
    )

    socket = fleet / "tmux.sock"
    config = _make_config(["A", "B"])
    adapter = MagicMock()
    adapter.session_log_dir = MagicMock(return_value=None)
    spawner = FleetSpawner(fix_medium, config, MagicMock(return_value=adapter), socket)

    # Pre-populate one LaneSession so the endpoint has something to kill.
    stream_log_a = fleet / "A.stream.log"
    stream_log_a.touch()
    spawner.sessions["A"] = LaneSession(
        lane="A",
        name="lane-A",
        cwd=fix_medium,
        argv=["stub"],
        env={},
        stream_log=stream_log_a,
        running=True,
    )

    app = make_app(mission_dir=fix_medium)
    async with app.router.lifespan_context(app):
        app.state.spawner = spawner
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            exch = await client.post("/api/v1/auth/exchange", json={"token": token})
            assert exch.status_code == 200, exch.text
            yield client, app, spawner, fleet


@pytest.mark.asyncio
async def test_delete_fleet_kills_server_and_unlinks_artifacts(authed_teardown_client):
    client, app, spawner, fleet = authed_teardown_client

    # Sanity preconditions.
    assert (fleet / "ui.token").exists()
    assert (fleet / "tmux.sock").exists()
    assert (fleet / "dashboard.url").exists()

    with patch(
        "megalodon_ui.server.tmux.kill_server", new=AsyncMock(return_value=0)
    ) as ks:
        resp = await client.delete("/api/v1/fleet")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"status": "shutdown"}

    ks.assert_awaited_once_with(spawner.socket)
    assert not (fleet / "ui.token").exists()
    assert not (fleet / "tmux.sock").exists()
    assert not (fleet / "dashboard.url").exists()
    assert getattr(app.state, "shutdown_requested", False) is True


@pytest.mark.asyncio
async def test_delete_fleet_idempotent_when_files_missing(authed_teardown_client):
    """Calling twice — second call hits absent files, still returns 200."""
    client, app, spawner, fleet = authed_teardown_client

    with patch("megalodon_ui.server.tmux.kill_server", new=AsyncMock(return_value=0)):
        r1 = await client.delete("/api/v1/fleet")
        r2 = await client.delete("/api/v1/fleet")

    assert r1.status_code == 200
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_delete_fleet_tolerates_kill_server_nonzero_rc(authed_teardown_client):
    """kill_server returning non-zero (server already gone) is best-effort."""
    client, app, spawner, fleet = authed_teardown_client

    with patch("megalodon_ui.server.tmux.kill_server", new=AsyncMock(return_value=1)):
        resp = await client.delete("/api/v1/fleet")

    assert resp.status_code == 200
    # Files still unlinked even though kill_server reported non-zero.
    assert not (fleet / "ui.token").exists()
    assert not (fleet / "tmux.sock").exists()
    assert not (fleet / "dashboard.url").exists()


@pytest.mark.asyncio
async def test_delete_fleet_without_cookie_returns_401(fix_medium: Path, monkeypatch):
    """The v92_auth_gate middleware blocks DELETE /api/v1/fleet without a cookie."""
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    fleet = fix_medium / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    write_token_atomic(fleet / "ui.token", "some-token")

    app = make_app(mission_dir=fix_medium)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete("/api/v1/fleet")
    assert resp.status_code == 401
    # ui.token MUST still exist — auth failed, no destructive action ran.
    assert (fleet / "ui.token").exists()
