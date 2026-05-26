"""Integration tests: deny-by-default gating of the formerly-open v9.1 GETs.

SECURITY INVERSION (v9.2): the auth gate is now deny-by-default. The v9.1 GET
endpoints (``/state``, ``/config``, ``/status``, ``/tasks``, ``/findings``) used
to be served WITHOUT a cookie — that leaked mission state and, via
``/config``'s ``csrf_token``, defeated CSRF. They are now GATED: a request
without a valid ``mui_session`` cookie gets 401.

The SPA bootstrap fetches these only AFTER the token exchange completes, so the
chicken-and-egg concern in the prior comment no longer applies — the bootstrap
authenticates first. Only the SPA shell + ``/healthz`` remain public.

This file is the regression net for the inversion: if a future refactor
accidentally re-opens one of these GETs, the security contract breaks and this
test fails loudly.
"""

import pytest


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/state",
        "/api/v1/config",
        "/api/v1/status",
        "/api/v1/tasks",
        "/api/v1/findings",
    ],
)
async def test_v91_get_endpoint_unauthenticated_returns_401(
    async_client_with_lifespan, path: str
):
    """Every formerly-open v9.1 GET must now 401 without a session cookie."""
    r = await async_client_with_lifespan.get(path)
    assert r.status_code == 401, (
        f"deny-by-default regressed: {path} returned {r.status_code} "
        f"without a cookie (should be 401): {r.text}"
    )


@pytest.mark.asyncio
async def test_healthz_unauthenticated(async_client_with_lifespan):
    """``/healthz`` is the readiness probe; never gated."""
    r = await async_client_with_lifespan.get("/healthz")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_static_index_unauthenticated(async_client_with_lifespan):
    """``GET /`` must be reachable so the bootstrap script can run."""
    r = await async_client_with_lifespan.get("/")
    assert r.status_code == 200
