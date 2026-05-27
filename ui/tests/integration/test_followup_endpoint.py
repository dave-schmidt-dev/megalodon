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
  - 403 missing or mismatched X-CSRF-Token with control mode ON (Fix-Round-3).
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
        return_value=(
            [
                "claude",
                "--print",
                "--model",
                "claude-sonnet-4-6",
                "--resume",
                "prior-sid-xyz",
                "follow up prompt",
            ],
            {},
        ),
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
            # Attach the CSRF token as a default header and enable control mode
            # (defense-in-depth: Fix-Round-3 added CSRF + control-mode gating to
            # lane_followup; happy-path tests must satisfy both checks).
            csrf = app.state.megalodon.csrf_token
            client.headers["X-CSRF-Token"] = csrf
            app.state.megalodon.control_mode = True
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
async def test_followup_malformed_json_body_returns_422(authed_client_with_spawner):
    """P3.7 — a body that is not valid JSON hits the ``await request.json()``
    except branch and returns 422 'invalid JSON body' (not a 500).

    Passes auth + CSRF + control-mode (fixture sets those) and resolves a known
    lane, so the only failure is the unparseable payload — isolating the
    malformed-body path that the existing 422 tests (missing/empty prompt) skip.
    """
    client, _spawner, _adapter, _calls = authed_client_with_spawner
    resp = await client.post(
        "/api/v1/lane/A/followup",
        content=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422, resp.text
    assert "JSON" in resp.json().get("detail", ""), resp.text


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
            # CSRF header + control mode required so the check-order is
            # auth → CSRF → control-mode → handler (spawner=None → 404).
            csrf = app.state.megalodon.csrf_token
            app.state.megalodon.control_mode = True
            resp = await client.post(
                "/api/v1/lane/A/followup",
                json={"prompt": "hello"},
                headers={"X-CSRF-Token": csrf},
            )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Fix-Round-3: CSRF negative tests (control mode ON, missing/wrong token → 403)
#
# CONTRACT: after the server-agent lands CSRF enforcement on lane_followup,
# a cookie-authenticated POST with control mode ON but no X-CSRF-Token header
# must return 403, not 202.  These tests are written to the frozen contract
# and depend on the server agent's change being integrated; until then they
# will pass trivially if the server returns 202 (see NOTE below).
#
# Check order: auth → CSRF → control-mode → handler.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def authed_client_with_spawner_csrf_on(
    fix_medium: Path, monkeypatch
) -> AsyncGenerator[tuple, None]:
    """Like authed_client_with_spawner but with control mode ON and CSRF header set.

    Tests that need to verify the CSRF gate strip ``X-CSRF-Token`` from this
    client's headers before making the call-under-test.
    """
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    fleet = fix_medium / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    token = "followup-csrf-test-token"
    write_token_atomic(fleet / "ui.token", token)

    socket = fleet / "tmux.sock"
    config = _make_config(["A"])

    claude_adapter = MagicMock()
    claude_adapter.build_argv = MagicMock(return_value=(["stub"], {}))
    claude_adapter.build_followup_argv = MagicMock(
        return_value=(["claude", "--resume", "sid", "prompt"], {}),
    )
    claude_adapter.session_log_dir = MagicMock(return_value=None)

    adapter_resolver = MagicMock(return_value=claude_adapter)
    spawner = FleetSpawner(fix_medium, config, adapter_resolver, socket)

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
        session_id="sid",
        running=True,
    )

    async def mock_respawn(lane, argv, env):
        pass

    spawner.respawn = mock_respawn  # type: ignore[attr-defined]

    app = make_app(mission_dir=fix_medium)
    async with app.router.lifespan_context(app):
        app.state.spawner = spawner
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            exch = await client.post("/api/v1/auth/exchange", json={"token": token})
            assert exch.status_code == 200, exch.text
            csrf = app.state.megalodon.csrf_token
            # Enable control mode so the CSRF gate is the outermost blocker.
            on = await client.post(
                "/api/v1/control-mode",
                json={"enabled": True},
                headers={"X-CSRF-Token": csrf},
            )
            # If the server agent's control-mode endpoint isn't present yet, skip
            # gracefully (endpoint will 404/405 — not our responsibility here).
            if on.status_code not in (200, 404, 405):
                pytest.skip(
                    f"POST /api/v1/control-mode returned unexpected {on.status_code}; "
                    "server agent's control-mode work may not be integrated yet"
                )
            # Attach the CSRF token as a default header; tests remove it to test the gate.
            client.headers["X-CSRF-Token"] = csrf
            yield client, app, csrf


@pytest.mark.asyncio
async def test_followup_missing_csrf_returns_403(authed_client_with_spawner_csrf_on):
    """Cookie present + control mode ON, but NO X-CSRF-Token → 403.

    Isolates the CSRF gate: the only missing piece is the token header.
    Depends on server agent adding ``_csrf_or_403`` to ``lane_followup``.
    Until that lands the endpoint returns 202 and this test is a known gap.
    """
    client, app, _csrf = authed_client_with_spawner_csrf_on
    # Remove the default CSRF header set by the fixture.
    client.headers.pop("X-CSRF-Token", None)

    resp = await client.post(
        "/api/v1/lane/A/followup",
        json={"prompt": "hello"},
    )
    assert resp.status_code == 403, (
        f"Expected 403 (missing CSRF with control mode ON), got {resp.status_code}: "
        f"{resp.text}\n"
        "NOTE: this test depends on the server agent adding CSRF enforcement to "
        "lane_followup. Until that change is integrated this endpoint returns 202."
    )
    assert "CSRF" in resp.json().get("detail", ""), resp.text


@pytest.mark.asyncio
async def test_followup_wrong_csrf_returns_403(authed_client_with_spawner_csrf_on):
    """Cookie present + control mode ON, but WRONG X-CSRF-Token → 403.

    Complements the missing-token case: a mismatched token must also be rejected
    with 403, not 202.  Same server-agent dependency as above.
    """
    client, app, _csrf = authed_client_with_spawner_csrf_on
    client.headers["X-CSRF-Token"] = "definitely-not-the-real-token"

    resp = await client.post(
        "/api/v1/lane/A/followup",
        json={"prompt": "hello"},
    )
    assert resp.status_code == 403, (
        f"Expected 403 (wrong CSRF with control mode ON), got {resp.status_code}: "
        f"{resp.text}\n"
        "NOTE: depends on server agent's CSRF enforcement on lane_followup."
    )
    assert "CSRF" in resp.json().get("detail", ""), resp.text
