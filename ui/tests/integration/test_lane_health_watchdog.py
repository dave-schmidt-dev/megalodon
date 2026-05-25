"""Integration: the lifespan lane-health watchdog feeds GET /api/v1/alerts (Task C).

The standalone ``watchdog.daemon.run()`` is a SYNC signal-driven loop and cannot
run inside the server lifespan. ``server._lane_health_watchdog`` drives the
extracted ``check_lanes_once`` off-loop on an interval instead. This test proves
the full wiring under a fake fleet: a stale STATUS row (the fix-medium fixture's
rows are dated 2026-01-01, far in the past) makes the watchdog fire a
``STATUS-STALE`` alert that becomes reachable via ``GET /api/v1/alerts`` within a
poll interval — no real tmux required.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import megalodon_ui.server as server_mod
from megalodon_ui.auth import write_token_atomic
from megalodon_ui.server import make_app


pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def authed_fake_client(
    fix_medium: Path, monkeypatch
) -> AsyncGenerator[tuple[AsyncClient, Path], None]:
    """Fake-fleet app with a fast lane-health watchdog + authenticated client."""
    monkeypatch.setenv("MEGALODON_FAKE_SPAWNER", "1")
    # Tick the watchdog quickly so the test does not wait a full minute.
    monkeypatch.setattr(server_mod, "_LANE_WATCHDOG_INTERVAL_SECONDS", 0.05)

    fleet = fix_medium / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    token = "lane-health-watchdog-token"
    write_token_atomic(fleet / "ui.token", token)

    app = make_app(mission_dir=fix_medium)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            exch = await client.post("/api/v1/auth/exchange", json={"token": token})
            assert exch.status_code == 200, exch.text
            yield client, fix_medium


@pytest.mark.asyncio
async def test_watchdog_alert_reachable_via_alerts_endpoint(authed_fake_client):
    """A stale STATUS row → STATUS-STALE alert surfaced by GET /api/v1/alerts."""
    client, _mission_dir = authed_fake_client

    # Poll the endpoint until the watchdog has run at least once and persisted an
    # alert. The fixture's STATUS rows are all far in the past → every configured
    # lane is STATUS-STALE on the first pass.
    deadline = asyncio.get_event_loop().time() + 5.0
    alerts: list[dict] = []
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get("/api/v1/alerts")
        assert resp.status_code == 200, resp.text
        alerts = resp.json()["alerts"]
        if alerts:
            break
        await asyncio.sleep(0.05)

    assert alerts, "lane-health watchdog produced no alert within the deadline"
    assert all("kind" in a and "lane" in a and "ts" in a for a in alerts)
    assert any(a["kind"] == "STATUS-STALE" for a in alerts), alerts
