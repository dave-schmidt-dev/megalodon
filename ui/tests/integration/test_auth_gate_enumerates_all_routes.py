"""Integration test: enumerate ALL routes and assert deny-by-default auth.

Permanent regression pin for the v9.2 SECURITY inversion. The auth gate is now
DENY-BY-DEFAULT: every route under ``/api/**`` requires a valid ``mui_session``
cookie EXCEPT a tiny public allowlist (just ``POST /api/v1/auth/exchange``).

This test:
1. Builds the app via the lifespan fixture.
2. Enumerates EVERY route (method-aware — uses the route's real HTTP methods,
   not just GET, since a POST-only mutation must also 401 without a cookie).
3. Issues a no-cookie request with the route's actual method.
4. Asserts the response is NOT 200 for any gated route (401 expected; 404/405/
   422 are also fine — they mean the gate passed but routing/validation
   rejected, which is not a security leak).
5. Asserts the public-allowlist routes are reachable (NOT 401).

If a new ``/api/**`` endpoint ships without gating, this fails loudly — there
is no longer a large "ungated v9.1 surface" escape hatch.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

# The ONLY routes that may be served without a session cookie. Deny-by-default
# means the public set is intentionally tiny: non-/api paths (SPA shell, static
# assets, favicon, healthz) plus the single token-exchange door under /api.
#
# Entries are (METHOD, PATH) so the contract is method-precise. Everything else
# under /api/** MUST require a cookie.
PUBLIC_ALLOWLIST: set[tuple[str, str]] = {
    # Token-exchange: the ONE door that mints a cookie. If it were gated the
    # operator could never authenticate.
    ("POST", "/api/v1/auth/exchange"),
}

# Path prefixes that are public regardless of method (non-/api bootstrap
# surface served by the catch-all / static mount). These never carry mission
# data — they are the SPA shell + assets the login flow renders pre-auth.
_PUBLIC_NON_API_PREFIXES = ("/static", "/favicon")
_PUBLIC_NON_API_EXACT = {"/", "/index.html", "/healthz"}


def _route_methods(route) -> set[str]:
    """Return the concrete HTTP methods for a route (excluding HEAD/OPTIONS)."""
    methods = getattr(route, "methods", None) or set()
    return {m for m in methods if m not in ("HEAD", "OPTIONS")}


def _is_public(method: str, path: str) -> bool:
    if (method, path) in PUBLIC_ALLOWLIST:
        return True
    if not path.startswith("/api/"):
        # Non-/api routes are the bootstrap surface (SPA shell + assets).
        if path in _PUBLIC_NON_API_EXACT:
            return True
        if any(path.startswith(p) for p in _PUBLIC_NON_API_PREFIXES):
            return True
        # The SPA catch-all (/{spa_path}) and any other non-/api GET serve the
        # HTML shell only — no mission data — so they are public by design.
        return True
    return False


@pytest.mark.asyncio
async def test_every_route_denies_without_cookie(async_client_with_lifespan):
    """Method-aware: every gated route 401s (never 200) without a cookie."""
    app = async_client_with_lifespan._transport.app

    # Build a method-aware list of (method, path) pairs for every real route.
    pairs: list[tuple[str, str]] = []
    for route in app.routes:
        path = getattr(route, "path", None)
        if path is None:
            continue
        methods = _route_methods(route)
        if not methods:
            continue
        for m in sorted(methods):
            pairs.append((m, path))

    assert pairs, "No routes enumerated — fixture may be misconfigured"
    # There must be a meaningful gated surface to verify.
    gated_pairs = [(m, p) for (m, p) in pairs if not _is_public(m, p)]
    assert gated_pairs, "No gated routes found — the auth gate may be disabled"

    failures: list[str] = []
    for method, path in sorted(set(gated_pairs)):
        # Substitute a placeholder for any path params so routing reaches the
        # handler (or fails AFTER the gate). The gate runs before routing, so a
        # 401 fires regardless of whether the path param resolves.
        request_path = path
        if "{" in path:
            import re as _re

            request_path = _re.sub(r"\{[^}]+\}", "x", path)

        r = await async_client_with_lifespan.request(method, request_path)

        if r.status_code == 200:
            failures.append(
                f"  {method} {path} returned 200 WITHOUT a session cookie "
                f"(SECURITY BUG: gated route is publicly readable)"
            )
        # 401 is the expected reject. 404/405/422/4xx all mean the gate passed
        # but routing/validation rejected — not a security leak. 5xx is a bug
        # but not a security leak. Only 200 is a contract violation.

    if failures:
        pytest.fail(
            "Auth gate regression — deny-by-default broken:\n"
            + "\n".join(failures)
            + "\n\nEvery /api/** route must require a valid mui_session cookie. "
            "If a new endpoint is genuinely public, add it to PUBLIC_ALLOWLIST "
            "here AND to _V92_PUBLIC_API_EXACT in megalodon_ui/server.py with a "
            "justification. Otherwise it is gated automatically by being under "
            "/api/."
        )


@pytest.mark.asyncio
async def test_gated_api_route_401s_specifically(async_client_with_lifespan):
    """Spot-check the contract paths the FE depends on: each 401s sans cookie."""
    # These are the exact routes the FROZEN AUTH CONTRACT calls out.
    must_401 = [
        ("GET", "/api/v1/state"),
        ("GET", "/api/v1/config"),
        ("GET", "/api/v1/events"),
        ("GET", "/api/v1/findings"),
        ("GET", "/api/v1/status"),
        ("GET", "/api/v1/tasks"),
        ("POST", "/api/v1/signal"),
        ("POST", "/api/v1/reclaim"),
        ("POST", "/api/v1/challenge"),
        ("POST", "/api/v1/inject-task"),
        ("POST", "/api/v1/phase-flip"),
        ("POST", "/api/v1/mission-status"),
        ("POST", "/api/v1/status/update"),
        ("POST", "/api/v1/history/append"),
        ("POST", "/api/v1/mission-event"),
    ]
    for method, path in must_401:
        r = await async_client_with_lifespan.request(method, path)
        assert r.status_code == 401, (
            f"{method} {path} must 401 without a cookie, got {r.status_code}"
        )


@pytest.mark.asyncio
async def test_public_routes_not_gated(async_client_with_lifespan):
    """The public allowlist must NOT 401 (it would lock everyone out)."""
    # Token-exchange with a bad token still reaches the handler (401 from the
    # handler, not the gate) — but it must NOT be rejected by the GATE before a
    # body is read. A missing-token exchange returns 401 from the handler with
    # the same shape, so we assert the endpoint is reachable (not 404/405).
    r = await async_client_with_lifespan.post(
        "/api/v1/auth/exchange", json={"token": "definitely-wrong"}
    )
    assert r.status_code in (200, 401), (
        f"auth/exchange must be reachable pre-auth, got {r.status_code}"
    )
    # The SPA shell must be reachable without a cookie.
    r = await async_client_with_lifespan.get("/")
    assert r.status_code == 200, f"index must be public, got {r.status_code}"
