"""Phase 3.1 regression — PermissionWatcher decommission.

Verifies:
(a) Server boots cleanly in test mode without PermissionWatcher.
(b) GET /api/v1/permission_prompts returns 404 (endpoint removed).
(c) POST /api/v1/permission_prompts/{lane}/respond returns 404 (endpoint removed).
(d) _V92_GATED_PATH_RE no longer matches permission_prompts paths.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from megalodon_ui.server import _V92_GATED_PATH_RE, make_app


# ---------------------------------------------------------------------------
# (a) Server boots without PermissionWatcher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_server_boots_without_permission_watcher(queue_mission):
    """Server starts in test mode and exposes /api/v1/config without errors."""
    app = make_app(mission_dir=queue_mission)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.get("/api/v1/config")
        assert r.status_code == 200, f"Server failed to boot: {r.text}"
        # permission_watcher must not be present on app.state after lifespan start.
        assert not hasattr(app.state, "permission_watcher"), (
            "app.state.permission_watcher should not be set after Phase 3.1 removal"
        )


# ---------------------------------------------------------------------------
# (b) GET /api/v1/permission_prompts → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_permission_prompts_returns_404(queue_mission):
    """GET /api/v1/permission_prompts returns 404 after endpoint removal."""
    app = make_app(mission_dir=queue_mission)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.get("/api/v1/permission_prompts")
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
    app = make_app(mission_dir=queue_mission)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post(
                "/api/v1/permission_prompts/A/respond",
                json={"action": "approve"},
            )
    assert r.status_code in (404, 405), (
        f"Expected 404 or 405 for removed endpoint, got {r.status_code}: {r.text}"
    )
    # Must NOT return 202, which was the old success response.
    assert r.status_code != 202, "Endpoint still active — returns 202 (not removed)"


# ---------------------------------------------------------------------------
# (d) _V92_GATED_PATH_RE no longer matches permission_prompts
# ---------------------------------------------------------------------------


def test_gate_regex_does_not_match_permission_prompts():
    """_V92_GATED_PATH_RE must not match /api/v1/permission_prompts paths."""
    assert _V92_GATED_PATH_RE.match("/api/v1/permission_prompts") is None, (
        "Gate regex still matches /api/v1/permission_prompts — remove not applied"
    )
    assert _V92_GATED_PATH_RE.match("/api/v1/permission_prompts/A/respond") is None, (
        "Gate regex still matches /api/v1/permission_prompts/A/respond"
    )


def test_gate_regex_still_matches_expected_paths():
    """Sanity check: gate regex still matches the paths it should."""
    for path in [
        "/api/v1/activity-wall/stream",
        "/api/v1/lanes/stale",
        "/api/v1/lane/A/status",
        "/api/v1/approval-rules",
        "/api/v1/_test/stale_override",
    ]:
        assert _V92_GATED_PATH_RE.match(path) is not None, (
            f"Gate regex should still match {path!r}"
        )
