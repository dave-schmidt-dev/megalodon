"""Integration tests: POST /api/v1/auth/exchange + middleware gating.

Plan §6.3/§6.4 contract:
- POST with the live bearer token from ``.fleet/ui.token`` → 200,
  Set-Cookie ``mui_session=<sid>; HttpOnly; SameSite=Strict; Path=/;
  Max-Age=86400`` (NOT Secure — localhost is plain HTTP).
- Invalid token → 401, no cookie.
- The auth-exchange endpoint itself must NOT require a prior cookie.
- v9.2-NEW endpoints (``/api/v1/lane/<NAME>/*``, ``DELETE /api/v1/fleet``)
  return 401 without a valid session cookie, even when the underlying
  route handler hasn't been wired yet — the middleware gates the path
  before routing reaches the handler.
"""

from pathlib import Path

import pytest

from megalodon_ui.auth import write_token_atomic


pytestmark = pytest.mark.integration


@pytest.fixture
def seeded_token(fix_medium: Path) -> str:
    """Place a bearer token at ``.fleet/ui.token`` and return its value."""
    fleet = fix_medium / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    token = "test-bearer-token-2-1-vN"
    write_token_atomic(fleet / "ui.token", token)
    return token


@pytest.mark.asyncio
async def test_exchange_with_valid_token_returns_200_and_sets_cookie(
    async_client_with_lifespan, seeded_token: str
):
    r = await async_client_with_lifespan.post(
        "/api/v1/auth/exchange", json={"token": seeded_token}
    )
    assert r.status_code == 200, r.text
    set_cookie = r.headers.get("set-cookie", "")
    assert "mui_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=Strict" in set_cookie or "samesite=strict" in set_cookie.lower()
    assert "Path=/" in set_cookie
    assert "Max-Age=86400" in set_cookie
    # NOT Secure on localhost (plain HTTP).
    assert "Secure" not in set_cookie.replace("SameSite=", "").replace("HttpOnly", "")


@pytest.mark.asyncio
async def test_exchange_with_invalid_token_returns_401(
    async_client_with_lifespan, seeded_token: str
):
    r = await async_client_with_lifespan.post(
        "/api/v1/auth/exchange", json={"token": "not-the-real-token"}
    )
    assert r.status_code == 401
    assert "mui_session=" not in r.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_exchange_with_empty_token_returns_401(
    async_client_with_lifespan, seeded_token: str
):
    r = await async_client_with_lifespan.post(
        "/api/v1/auth/exchange", json={"token": ""}
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_exchange_with_missing_token_field_returns_422_or_401(
    async_client_with_lifespan, seeded_token: str
):
    r = await async_client_with_lifespan.post("/api/v1/auth/exchange", json={})
    assert r.status_code in (401, 422)


@pytest.mark.asyncio
async def test_exchange_without_token_file_returns_401(async_client_with_lifespan):
    """If ``.fleet/ui.token`` is absent the server cannot mint anything."""
    r = await async_client_with_lifespan.post(
        "/api/v1/auth/exchange", json={"token": "anything"}
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_v92_lane_endpoint_without_cookie_returns_401(
    async_client_with_lifespan, seeded_token: str
):
    """v9.2-new ``/api/v1/lane/<NAME>/*`` is gated; cookie required even if
    the underlying handler doesn't exist yet (middleware runs first)."""
    r = await async_client_with_lifespan.get("/api/v1/lane/AUDIT/pane-stream")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_v92_lane_endpoint_with_valid_cookie_passes_middleware(
    async_client_with_lifespan, seeded_token: str
):
    """After exchange, the cookie is set and the middleware lets the request
    through to routing (404 is fine — proves middleware was not the rejector)."""
    exch = await async_client_with_lifespan.post(
        "/api/v1/auth/exchange", json={"token": seeded_token}
    )
    assert exch.status_code == 200
    r = await async_client_with_lifespan.get("/api/v1/lane/AUDIT/pane-stream")
    assert r.status_code != 401, (
        f"middleware rejected an authenticated request: {r.status_code} / {r.text}"
    )


@pytest.mark.asyncio
async def test_delete_fleet_without_cookie_returns_401(async_client_with_lifespan):
    r = await async_client_with_lifespan.delete("/api/v1/fleet")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_invalid_cookie_value_returns_401(
    async_client_with_lifespan, seeded_token: str
):
    """An attacker-supplied cookie with a never-issued sid must not be accepted."""
    r = await async_client_with_lifespan.get(
        "/api/v1/lane/AUDIT/pane-stream",
        cookies={"mui_session": "definitely-not-a-real-sid"},
    )
    assert r.status_code == 401
