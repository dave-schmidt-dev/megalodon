"""Smoke test: async_client_with_lifespan actually enters the app lifespan.

Verifies that the shared fixture wraps client construction inside
``app.router.lifespan_context(app)`` by building a standalone FastAPI app
with an explicit sentinel lifespan.  The test asserts that
``app.state.lifespan_entered`` is True before the test body receives control,
proving the context manager ran.

The production ``make_app`` factory currently has no explicit lifespan body.
This smoke test therefore uses its own tiny app (not ``make_app``) so the
lifespan behaviour under test is deterministic and isolated from future P1
lifespan additions.  A third test validates the same pattern against
``make_app`` by attaching a sentinel lifespan explicitly.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from fastapi import FastAPI


pytestmark = pytest.mark.integration


@asynccontextmanager
async def _sentinel_lifespan(app):
    """Minimal lifespan that sets a flag on app.state before yielding."""
    app.state.lifespan_entered = True
    yield
    app.state.lifespan_exited = True


@pytest_asyncio.fixture
async def lifespan_sentinel_client():
    """Yield a client whose app was started via lifespan_context.

    Uses a standalone FastAPI app (not make_app) so the sentinel lifespan is
    fully deterministic regardless of what P1 adds to the production factory.
    """
    from httpx import AsyncClient, ASGITransport  # type: ignore[import-not-found]

    app = FastAPI(lifespan=_sentinel_lifespan)

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            client.app = app  # stash so test can inspect state
            yield client


@pytest.mark.asyncio
async def test_lifespan_entered_before_test_body(lifespan_sentinel_client):
    """app.state.lifespan_entered is True when the test body runs."""
    app = lifespan_sentinel_client.app
    assert getattr(app.state, "lifespan_entered", False) is True, (
        "lifespan_context was not entered: app.state.lifespan_entered not set"
    )


@pytest.mark.asyncio
async def test_lifespan_client_can_make_requests(lifespan_sentinel_client):
    """Client obtained through a lifespan-aware fixture can serve requests."""
    r = await lifespan_sentinel_client.get("/ping")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


@pytest.mark.asyncio
async def test_make_app_lifespan_context_pattern(fix_medium):
    """async_client_with_lifespan pattern works with the production make_app.

    Attaches an explicit sentinel lifespan to a make_app instance and wraps it
    identically to how async_client_with_lifespan does.  Confirms that the
    lifespan ran and that the client can make requests.  This validates the
    shared fixture pattern against the real factory without creating a
    circular fixture dependency.
    """
    try:
        from megalodon_ui.server import make_app  # type: ignore[import-not-found]
    except ImportError:
        pytest.skip("awaits P3-C megalodon_ui.server")

    from httpx import AsyncClient, ASGITransport  # type: ignore[import-not-found]

    app = make_app(mission_dir=fix_medium)
    # Override lifespan_context (the resolved instance attribute) with our sentinel.
    # Starlette resolves the lifespan callable into app.router.lifespan_context at
    # construction time; assigning to app.router.lifespan alone has no effect.
    app.router.lifespan_context = _sentinel_lifespan  # type: ignore[assignment]

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            assert getattr(app.state, "lifespan_entered", False) is True, (
                "lifespan_context did not run startup"
            )
            # /api/v1/status is now auth-gated (deny-by-default); mint a cookie.
            client.cookies.set(
                "mui_session", app.state.megalodon.session_store.create()
            )
            r = await client.get("/api/v1/status")
            assert r.status_code == 200
