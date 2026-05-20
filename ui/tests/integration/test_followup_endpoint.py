"""P6.2 — POST /api/v1/lane/<NAME>/followup endpoint.

Plan §6.4 row: `POST /api/v1/lane/<NAME>/followup | cookie | Body
{prompt, model?} → respawn-pane`. The handler:

1. Looks up the LaneSession on `app.state.spawner.sessions`.
2. Resolves the adapter via `spawner.adapter_resolver(harness.cli)`.
3. Calls `adapter.build_followup_argv(prompt, prior_session_id=session.session_id, model=..., cwd=...)`.
4. Calls `spawner.respawn(lane, argv, env)` — implemented in P6.3; mocked here.
5. Returns 202.

Error paths:
  - 401 without cookie (existing `v92_auth_gate` middleware).
  - 404 unknown lane (no LaneSession for that short).
  - 404 when spawner is None (test-mode lifespan).
  - 422 missing or empty prompt.

The handler does NOT block on session-id discovery — discovery happens
asynchronously inside `spawner.respawn` and is observable via the
existing `<mission>/.fleet/<short>.session.txt` file.
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from megalodon_ui.auth import write_token_atomic
from megalodon_ui.mission_config.schema import MissionConfig
from megalodon_ui.server import make_app
from megalodon_ui.spawn import FleetSpawner


pytestmark = pytest.mark.integration


def _make_config(shorts: list[str], cli: str = "claude") -> MissionConfig:
    lanes = [
        {
            "name": f"LANE{s}",
            "short": s,
            "role": f"role-{s.lower()}",
            "harness": {"cli": cli, "model": "claude-sonnet-4-6"},
            "cadence_seconds": 300,
            "tick_offset_seconds": 0,
        }
        for s in shorts
    ]
    return MissionConfig.model_validate(
        {
            "mission": {"id": "test-mission", "utc_started": "2026-01-01T00:00:00Z"},
            "lanes": lanes,
            "phases": ["INIT"],
        }
    )


@pytest_asyncio.fixture
async def authed_client_with_spawner(
    fix_medium: Path, monkeypatch
) -> AsyncGenerator[tuple, None]:
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    fleet = fix_medium / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    token = "followup-test-token"
    write_token_atomic(fleet / "ui.token", token)

    socket = fleet / "tmux.sock"
    config = _make_config(["A"])

    claude_adapter = MagicMock()
    claude_adapter.build_argv = MagicMock(return_value=(["stub"], {}))
    claude_adapter.build_followup_argv = MagicMock(
        return_value=(["claude", "--print", "--model", "claude-sonnet-4-6", "--resume", "prior-sid-xyz", "follow up prompt"], {}),
    )
    claude_adapter.session_log_dir = MagicMock(return_value=None)

    adapter_resolver = MagicMock(return_value=claude_adapter)
    spawner = FleetSpawner(fix_medium, config, adapter_resolver, socket)

    # Pre-populate sessions WITHOUT running start_all (avoids tmux dependency).
    from megalodon_ui.spawn import LaneSession
    stream_log = fleet / "A.stream.log"
    stream_log.touch()
    spawner.sessions["A"] = LaneSession(
        lane="A",
        name="lane-A",
        cwd=fix_medium,
        argv=["stub"],
        env={},
        stream_log=stream_log,
        session_id="prior-sid-xyz",
        running=True,
    )

    respawn_calls = []

    async def mock_respawn(lane, argv, env):
        respawn_calls.append((lane, argv, env))

    spawner.respawn = mock_respawn  # type: ignore[attr-defined]

    app = make_app(mission_dir=fix_medium)
    async with app.router.lifespan_context(app):
        app.state.spawner = spawner
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            exch = await client.post("/api/v1/auth/exchange", json={"token": token})
            assert exch.status_code == 200, exch.text
            yield client, spawner, claude_adapter, respawn_calls


@pytest.mark.asyncio
async def test_followup_returns_202_and_calls_respawn(authed_client_with_spawner):
    client, spawner, adapter, respawn_calls = authed_client_with_spawner
    resp = await client.post(
        "/api/v1/lane/A/followup",
        json={"prompt": "follow up prompt"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body.get("lane") == "A"
    assert body.get("status") == "respawned"

    # Adapter.build_followup_argv was called with the prior session id from
    # the LaneSession + the lane's configured model.
    adapter.build_followup_argv.assert_called_once()
    _args, kwargs = adapter.build_followup_argv.call_args
    assert kwargs["prior_session_id"] == "prior-sid-xyz"
    assert kwargs["model"] == "claude-sonnet-4-6"
    assert kwargs["cwd"] == spawner.mission_dir

    # Spawner.respawn was called with the argv adapter returned.
    assert len(respawn_calls) == 1
    lane, argv, env = respawn_calls[0]
    assert lane == "A"
    assert "follow up prompt" in argv
    assert "--resume" in argv


@pytest.mark.asyncio
async def test_followup_with_model_override(authed_client_with_spawner):
    client, _spawner, adapter, _calls = authed_client_with_spawner
    resp = await client.post(
        "/api/v1/lane/A/followup",
        json={"prompt": "hello", "model": "claude-opus-4-7"},
    )
    assert resp.status_code == 202
    _args, kwargs = adapter.build_followup_argv.call_args
    assert kwargs["model"] == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_followup_unknown_lane_returns_404(authed_client_with_spawner):
    client, _spawner, _adapter, _calls = authed_client_with_spawner
    resp = await client.post(
        "/api/v1/lane/ZZZ/followup",
        json={"prompt": "anything"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_followup_missing_prompt_returns_422(authed_client_with_spawner):
    client, _spawner, _adapter, _calls = authed_client_with_spawner
    resp = await client.post("/api/v1/lane/A/followup", json={})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_followup_empty_prompt_returns_422(authed_client_with_spawner):
    client, _spawner, _adapter, _calls = authed_client_with_spawner
    resp = await client.post(
        "/api/v1/lane/A/followup",
        json={"prompt": "   "},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_followup_without_cookie_returns_401(fix_medium: Path, monkeypatch):
    """The middleware gates /api/v1/lane/* — no cookie means no access."""
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    app = make_app(mission_dir=fix_medium)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/lane/A/followup",
                json={"prompt": "hello"},
            )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_followup_when_spawner_is_none_returns_404(fix_medium: Path, monkeypatch):
    """Test-mode lifespan leaves spawner=None — even a valid cookie can't reach lanes."""
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    fleet = fix_medium / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    token = "no-spawner-token"
    write_token_atomic(fleet / "ui.token", token)

    app = make_app(mission_dir=fix_medium)
    async with app.router.lifespan_context(app):
        app.state.spawner = None
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            exch = await client.post("/api/v1/auth/exchange", json={"token": token})
            assert exch.status_code == 200
            resp = await client.post(
                "/api/v1/lane/A/followup",
                json={"prompt": "hello"},
            )
    assert resp.status_code == 404
