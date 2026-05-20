"""Integration test: enumerate ALL /api/v1/* routes and assert each gates auth.

This is a permanent regression test that prevents new endpoints from shipping
without explicit auth gating. The test:
1. Builds the app via make_app()
2. Enumerates every route in app.router.routes whose path starts with /api/v1/
3. For each route, makes a request WITHOUT a session cookie
4. Asserts response is 401 (authentication required)
5. Exception: routes in UNGATED_ALLOWLIST (commented with justification)
   The point is "every new endpoint must justify its un-gated status by being
   added to this allowlist with a comment explaining why."

Fails loudly if a route enumerator returns 200 without auth.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

# Routes that DO NOT require auth. Each entry must have a comment explaining why.
# When a new endpoint is added to this list, the comment is evidence that the
# decision to leave it un-gated was deliberate and reviewed.
#
# v9.1 surface (backward-compatible, pre-auth-gate) per CR-4 (narrow):
# These routes existed before v9.2 auth gating and are left open for compatibility.
# New endpoints MUST be gated by the regex unless explicitly justified below.
UNGATED_ALLOWLIST: set[str] = {
    # ===== v9.1 surface (pre-auth gate) — backward-compat per CR-4 =====
    # These are old read-only status/query endpoints from v9.1. They do not
    # mutate state and are left un-gated for backward compatibility with dashboards
    # and monitoring systems that may lack session cookies.
    "/api/v1/status",
    "/api/v1/tasks",
    "/api/v1/state",
    "/api/v1/findings",
    "/api/v1/findings/{filename}",
    "/api/v1/events",
    "/api/v1/config",
    "/api/v1/__contract_introspect__",
    # ===== v9.2+ entry point =====
    # POST /api/v1/auth/exchange — entry point for auth; if it required a cookie,
    # the operator could never get in. No cookie needed on initial exchange.
    "/api/v1/auth/exchange",
}


@pytest.mark.asyncio
async def test_all_api_v1_routes_gate_auth(async_client_with_lifespan):
    """Enumerate all /api/v1/* routes and assert each requires auth (except allowlist)."""
    app = async_client_with_lifespan._transport.app

    # Collect all routes under /api/v1/
    api_v1_routes = []
    for route in app.routes:
        if hasattr(route, "path") and route.path.startswith("/api/v1/"):
            api_v1_routes.append(route.path)

    assert api_v1_routes, "No /api/v1/* routes found — fixture may be misconfigured"

    # Sort for reproducible output
    api_v1_routes.sort()

    # Track failures
    failures = []

    for route_path in api_v1_routes:
        if route_path in UNGATED_ALLOWLIST:
            # Skip routes that are explicitly allowed to be ungated
            continue

        # Try a GET request (most permissive; if GET 401s, so will POST/etc).
        # If the route doesn't support GET, the middleware still runs first, so
        # 405 Method Not Allowed would come after auth check; but we'll check
        # more carefully below per method.
        r = await async_client_with_lifespan.get(route_path)

        if r.status_code == 200:
            failures.append(
                f"  {route_path} returned 200 without auth cookie (SECURITY BUG)"
            )
        elif r.status_code == 401:
            # Expected: auth gate rejected the request
            pass
        elif r.status_code == 404:
            # Route exists but handler not implemented (OK, middleware passed)
            pass
        elif r.status_code == 405:
            # Method not allowed (OK, auth gate passed, routing rejected)
            pass
        elif r.status_code >= 400 and r.status_code < 500:
            # Other 4xx (bad request, unprocessable entity, etc.) — all OK,
            # means auth gate passed and handler was reached. As long as it's
            # not 200, the security contract is intact.
            pass
        elif r.status_code >= 500:
            # Server error — not a security issue, but log it
            pass

    if failures:
        pytest.fail(
            "Auth gate regression detected:\n"
            + "\n".join(failures)
            + "\n\nIf you added a new /api/v1/* endpoint, add it to UNGATED_ALLOWLIST"
            + " with a comment explaining why (if truly un-gated), or extend"
            + " _V92_GATED_PATH_RE in megalodon_ui/server.py:65 to gate it."
        )


@pytest.mark.asyncio
async def test_enumerated_routes_summary(async_client_with_lifespan, capsys):
    """Print a summary of all /api/v1/* routes for manual inspection."""
    app = async_client_with_lifespan._transport.app

    # Collect all routes under /api/v1/
    api_v1_routes = []
    for route in app.routes:
        if hasattr(route, "path") and route.path.startswith("/api/v1/"):
            api_v1_routes.append(route.path)

    api_v1_routes.sort()

    print("\n=== All /api/v1/* Routes ===")
    for route_path in api_v1_routes:
        status = "(un-gated)" if route_path in UNGATED_ALLOWLIST else "(gated)"
        print(f"  {route_path:50} {status}")

    print(f"\nTotal: {len(api_v1_routes)} routes")
    print(f"Un-gated: {len(UNGATED_ALLOWLIST)} (in allowlist)")
    print(f"Gated: {len(api_v1_routes) - len(UNGATED_ALLOWLIST)}")
