"""Phase 3.1 regression — PermissionWatcher decommission.

Verifies:
(a) Server boots cleanly in test mode without PermissionWatcher.
(b) GET /api/v1/permission_prompts returns 404 (endpoint removed).
(c) POST /api/v1/permission_prompts/{lane}/respond returns 404 (endpoint removed).
(d) The deny-by-default gate still gates permission_prompts paths (under /api/)
    while the endpoint itself is gone (404 once authenticated).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from megalodon_ui.auth import write_token_atomic
from megalodon_ui.server import _v92_path_is_gated, make_app

_TOKEN = "perm-decommission-token"


def _write_token(mission_dir) -> None:
    """Ensure ``.fleet/`` exists and write the bearer token used by exchange."""
    fleet = mission_dir / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    write_token_atomic(fleet / "ui.token", _TOKEN)


async def _auth_client(app):
    """Return an AsyncClient with a valid mui_session cookie (after exchange)."""
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    r = await client.post("/api/v1/auth/exchange", json={"token": _TOKEN})
    assert r.status_code == 200, f"auth exchange failed: {r.text}"
    return client


# ---------------------------------------------------------------------------
# (a) Server boots without PermissionWatcher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_server_boots_without_permission_watcher(queue_mission):
    """Server starts in test mode and exposes /api/v1/config (auth-gated) cleanly."""
    _write_token(queue_mission)
    app = make_app(mission_dir=queue_mission)
    async with app.router.lifespan_context(app):
        client = await _auth_client(app)
        try:
            # GET /api/v1/config now REQUIRES the session cookie (deny-by-default
            # inversion: config carries the CSRF token, so it must be gated).
            r = await client.get("/api/v1/config")
        finally:
            await client.aclose()
        assert r.status_code == 200, f"Server failed to boot: {r.text}"
        # permission_watcher must not be present on app.state after lifespan start.
        assert not hasattr(app.state, "permission_watcher"), (
            "app.state.permission_watcher should not be set after Phase 3.1 removal"
        )


@pytest.mark.asyncio
async def test_config_requires_cookie(queue_mission):
    """SECURITY: GET /api/v1/config must 401 without a cookie (CSRF-token leak fix)."""
    _write_token(queue_mission)
    app = make_app(mission_dir=queue_mission)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.get("/api/v1/config")
    assert r.status_code == 401, (
        f"/api/v1/config must be gated, got {r.status_code}: {r.text}"
    )


# ---------------------------------------------------------------------------
# (b) GET /api/v1/permission_prompts → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_permission_prompts_returns_404(queue_mission):
    """GET /api/v1/permission_prompts returns 404 after endpoint removal.

    Authenticated so the gate passes and we observe routing's 404 (rather than
    the deny-by-default 401 an unauthenticated request would get).
    """
    _write_token(queue_mission)
    app = make_app(mission_dir=queue_mission)
    async with app.router.lifespan_context(app):
        client = await _auth_client(app)
        try:
            r = await client.get("/api/v1/permission_prompts")
        finally:
            await client.aclose()
    assert r.status_code == 404, (
        f"Expected 404 for removed endpoint, got {r.status_code}: {r.text}"
    )


# ---------------------------------------------------------------------------
# (c) POST /api/v1/permission_prompts/{lane}/respond → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_permission_prompts_respond_not_found(queue_mission):
    """POST /api/v1/permission_prompts/{lane}/respond is not handled after endpoint removal.

    FastAPI returns 404 when no route matches a path/method combination, and
    405 when the path matches some route but not with this method.  Either
    response confirms the endpoint is gone (not registered, not serving 202).
    """
    _write_token(queue_mission)
    app = make_app(mission_dir=queue_mission)
    async with app.router.lifespan_context(app):
        client = await _auth_client(app)
        try:
            r = await client.post(
                "/api/v1/permission_prompts/A/respond",
                json={"action": "approve"},
            )
        finally:
            await client.aclose()
    assert r.status_code in (404, 405), (
        f"Expected 404 or 405 for removed endpoint, got {r.status_code}: {r.text}"
    )
    # Must NOT return 202, which was the old success response.
    assert r.status_code != 202, "Endpoint still active — returns 202 (not removed)"


# ---------------------------------------------------------------------------
# (d) deny-by-default gate covers permission_prompts (and everything under /api)
# ---------------------------------------------------------------------------


def test_gate_covers_permission_prompts_paths():
    """Under deny-by-default, permission_prompts paths are gated (under /api/).

    The endpoint is removed, but the gate is path-based and fires before
    routing — so an unauthenticated request to these paths still 401s rather
    than leaking. (The endpoint being gone means an *authenticated* request
    404s — see the routing tests above.)
    """
    assert _v92_path_is_gated("GET", "/api/v1/permission_prompts") is True
    assert _v92_path_is_gated("POST", "/api/v1/permission_prompts/A/respond") is True


def test_gate_covers_expected_api_paths():
    """Deny-by-default: every /api/** path is gated except token-exchange."""
    for method, path in [
        ("GET", "/api/v1/activity-wall/stream"),
        ("GET", "/api/v1/lanes/stale"),
        ("GET", "/api/v1/lane/A/status"),
        ("POST", "/api/v1/approval-rules"),
        ("POST", "/api/v1/_test/stale_override"),
        ("GET", "/api/v1/state"),
        ("GET", "/api/v1/config"),
        ("GET", "/api/v1/events"),
        ("POST", "/api/v1/signal"),
    ]:
        assert _v92_path_is_gated(method, path) is True, (
            f"{method} {path} should be gated"
        )
    # The single public exception + non-/api bootstrap surface are NOT gated.
    assert _v92_path_is_gated("POST", "/api/v1/auth/exchange") is False
    assert _v92_path_is_gated("GET", "/") is False
    assert _v92_path_is_gated("GET", "/static/js/app.js") is False
    assert _v92_path_is_gated("GET", "/healthz") is False
