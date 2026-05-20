"""Integration tests: v9.1 existing endpoints stay unauthenticated (CR-4 narrow).

Plan §6.3: cookie required for v9.2-NEW endpoints + v9.1 mutation endpoints
that already had CSRF gating. Existing v9.1 GETs remain unauthenticated in
v9.2 because ``ui/static/js/sse.js`` fetches them at module load before the
token exchange could possibly complete (chicken-and-egg with bootstrap).

This file is the regression net: if a future refactor accidentally gates a
v9.1 GET endpoint, the bootstrap chain breaks and the UI never loads.
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
async def test_v91_get_endpoint_unauthenticated_returns_200(
    async_client_with_lifespan, path: str
):
    """Every v9.1 GET listed in plan §6.3 must respond 200 without a cookie."""
    r = await async_client_with_lifespan.get(path)
    assert r.status_code == 200, (
        f"v9.1 GET {path} regressed to {r.status_code}: {r.text}"
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
