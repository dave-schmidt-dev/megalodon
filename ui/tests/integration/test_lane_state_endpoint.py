"""P6.4 — GET /api/v1/lane/<NAME>/state endpoint.

Plan §6.4: returns ``{running, exited_rc, started_utc, last_bytes_offset}``.
Handler runs ``tmux display-message -p -F '#{pane_dead}|#{pane_dead_status}'``
on demand, with a 1 s TTL cache on ``LaneSession.pane_dead_checked_at``
(CV-8 — no background polling).

Behaviour:
  * Pane alive → ``{running: true, exited_rc: null}``.
  * Pane dead with rc=17 → ``{running: false, exited_rc: 17}``.
  * Repeated calls within 1 s do NOT re-query tmux (TTL cache).
  * Unknown lane → 404.
  * No cookie → 401 (middleware).
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


def _make_config() -> MissionConfig:
    return MissionConfig.model_validate(
        {
            "mission": {"id": "test", "utc_started": "2026-01-01T00:00:00Z"},
            "lanes": [
                {
                    "name": "LANEA",
                    "short": "A",
                    "role": "test",
                    "harness": {"cli": "claude", "model": "sonnet"},
                    "cadence_seconds": 300,
                    "tick_offset_seconds": 0,
                },
            ],
            "phases": ["INIT"],
        }
    )


@pytest_asyncio.fixture
async def authed_client_with_lane_A(
    fix_medium: Path, monkeypatch
) -> AsyncGenerator[tuple, None]:
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    fleet = fix_medium / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    token = "state-endpoint-token"
    write_token_atomic(fleet / "ui.token", token)

    stream_log = fleet / "A.stream.log"
    stream_log.write_bytes(b"hello-world")  # 11 bytes
    socket = fleet / "tmux.sock"

    config = _make_config()
    adapter = MagicMock()
    adapter.session_log_dir = MagicMock(return_value=None)
    spawner = FleetSpawner(fix_medium, config, MagicMock(return_value=adapter), socket)
    spawner.sessions["A"] = LaneSession(
        lane="A",
        name="lane-A",
        cwd=fix_medium,
        argv=["stub"],
        env={},
        stream_log=stream_log,
        session_id="sid-A",
        running=True,
    )

    app = make_app(mission_dir=fix_medium)
    async with app.router.lifespan_context(app):
        app.state.spawner = spawner
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            exch = await client.post("/api/v1/auth/exchange", json={"token": token})
            assert exch.status_code == 200
            yield client, spawner


@pytest.mark.asyncio
async def test_state_returns_running_true_when_pane_alive(authed_client_with_lane_A):
    client, _spawner = authed_client_with_lane_A
    with patch(
        "megalodon_ui.tmux.display_message_pane_dead",
        new=AsyncMock(return_value=(False, None)),
    ):
        r = await client.get("/api/v1/lane/A/state")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["running"] is True
    assert body["exited_rc"] is None
    assert body["last_bytes_offset"] == 11  # stream_log was 11 bytes


@pytest.mark.asyncio
async def test_state_returns_exited_rc_when_pane_dead(authed_client_with_lane_A):
    client, _spawner = authed_client_with_lane_A
    with patch(
        "megalodon_ui.tmux.display_message_pane_dead",
        new=AsyncMock(return_value=(True, 17)),
    ):
        r = await client.get("/api/v1/lane/A/state")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["running"] is False
    assert body["exited_rc"] == 17


@pytest.mark.asyncio
async def test_state_caches_pane_dead_query_within_1s_ttl(authed_client_with_lane_A):
    """Two GETs inside the TTL → one tmux call."""
    client, _spawner = authed_client_with_lane_A
    mock = AsyncMock(return_value=(False, None))
    with patch("megalodon_ui.tmux.display_message_pane_dead", new=mock):
        r1 = await client.get("/api/v1/lane/A/state")
        r2 = await client.get("/api/v1/lane/A/state")
    assert r1.status_code == 200 and r2.status_code == 200
    assert mock.await_count == 1, (
        f"expected 1 tmux call within 1s TTL, got {mock.await_count}"
    )


@pytest.mark.asyncio
async def test_state_unknown_lane_returns_404(authed_client_with_lane_A):
    client, _spawner = authed_client_with_lane_A
    r = await client.get("/api/v1/lane/ZZZ/state")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_state_without_cookie_returns_401(fix_medium: Path, monkeypatch):
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    app = make_app(mission_dir=fix_medium)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            r = await client.get("/api/v1/lane/A/state")
    assert r.status_code == 401
