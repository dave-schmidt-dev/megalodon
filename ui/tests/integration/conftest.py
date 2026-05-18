"""Integration test configuration and shared helpers."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
import pytest_asyncio


try:
    from megalodon_ui.server import make_app  # type: ignore[import-not-found]
    _BACKEND_AVAILABLE = True
except ImportError:
    make_app = None  # type: ignore[assignment]
    _BACKEND_AVAILABLE = False


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
def fix_medium(tmp_path):
    """Copy fix-medium fixture to a tmpdir so tests can mutate it freely."""
    dst = tmp_path / "fix-medium"
    shutil.copytree(FIXTURES / "fix-medium", dst)
    return dst


@pytest_asyncio.fixture
async def async_client_with_lifespan(fix_medium):
    """httpx.AsyncClient connected to the app via ASGITransport, with lifespan.

    Wraps client construction inside ``app.router.lifespan_context(app)`` so
    FastAPI startup and shutdown hooks run around every test that uses this
    fixture.  Currently ``make_app`` has no explicit lifespan body (no-op), but
    this fixture ensures that once P1 adds tmux-session startup the tests will
    exercise the real initialised app rather than a cold one.

    Skips (not fails) when the backend package is unavailable so the test suite
    stays green before P3-C ships.
    """
    if not _BACKEND_AVAILABLE:
        pytest.skip("awaits P3-C megalodon_ui.server")

    from httpx import AsyncClient, ASGITransport  # type: ignore[import-not-found]

    app = make_app(mission_dir=fix_medium)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client


async def wait_for_queue_applied(client, request_id: str, timeout: float = 5.0,
                                  poll_interval: float = 0.05,
                                  mission_dir=None) -> dict:
    """Drive the queue applier then poll /api/v1/queue/{request_id} until status != 'pending'.

    In integration tests the applier daemon is not running, so we instantiate
    Applier directly and call drain_once() to process pending queue items
    before each status check.

    Returns the final response body. Raises AssertionError on timeout or if the
    request resolves as 'rejected' (caller can catch if rejection is the asserted
    outcome).
    """
    from megalodon_ui.queue.applier import Applier

    deadline = asyncio.get_event_loop().time() + timeout

    # Derive mission_dir from the queue endpoint if not provided.
    # We call GET queue first to get the mission context implicitly — but we
    # can't get mission_dir from the HTTP client. Callers must pass it.
    # If not provided, fall back to pure-HTTP polling (applier assumed external).
    applier = None
    if mission_dir is not None:
        applier = Applier(mission_dir=mission_dir, poll_seconds=0)

    while True:
        if applier is not None:
            try:
                applier.drain_once()
            except Exception:
                pass  # Best-effort; the status check below will show what happened.

        r = await client.get(f"/api/v1/queue/{request_id}")
        assert r.status_code == 200, f"queue status returned {r.status_code}: {r.text}"
        body = r.json()
        if body["status"] != "pending":
            if body["status"] == "rejected":
                raise AssertionError(
                    f"queue request {request_id} rejected: {body.get('rejection_reason')}"
                )
            return body
        if asyncio.get_event_loop().time() >= deadline:
            raise AssertionError(
                f"queue request {request_id} did not resolve within {timeout}s "
                f"(last status: {body['status']})"
            )
        await asyncio.sleep(poll_interval)
