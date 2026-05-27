"""SSE stream integration test.

Test for MISSION.md §"Concrete exit criteria" #4:
  "A connected client receives at least one `status-change` event when an
   STATUS.md heartbeat fires in the mission dir. E2E test must demonstrate."

Author: P3-E Stage 3 (TEST agent-43d9 @ 2026-05-16T19:40Z).

P3.5 rework (2026-05-27): the original two tests drove the `/api/v1/events`
endpoint through ``httpx.ASGITransport``, which BUFFERS the entire streaming
response body before exposing any bytes. The SSE generator runs a bounded 30s
poll loop, so the first chunk (even the on-connect ``sync``) only surfaced when
that loop ended — making each test take ~30s and forcing an ``xfail`` on the
file-touch case. Both are now replaced with a generator-unit test that drives
the real ``event_generator`` async generator DIRECTLY (via the endpoint's
``EventSourceResponse.body_iterator``), so incremental delivery is observed in
sub-second time without any HTTP transport.
"""

from __future__ import annotations

import asyncio

import pytest
from unittest.mock import AsyncMock

from megalodon_ui.server import make_app
from megalodon_ui.constants import API_EVENTS, SSE_STATUS_CHANGE, SSE_SYNC


pytestmark = pytest.mark.integration


def _events_endpoint(app):
    """Return the bound ``/api/v1/events`` route handler from ``app``."""
    for route in app.routes:
        if getattr(route, "path", None) == API_EVENTS:
            return route.endpoint
    raise AssertionError(f"{API_EVENTS} route not registered on app")


async def _drive_generator(app):
    """Invoke the events endpoint and return its raw async body iterator.

    The endpoint returns an ``EventSourceResponse`` whose ``body_iterator`` IS
    the real ``event_generator`` async generator defined in ``server.py``. We
    feed it a Request whose ``is_disconnected()`` always resolves False so the
    bounded poll loop keeps ticking (0.25s clock), exactly as it does under a
    live ASGI server — no re-mocking of the generator itself.
    """
    endpoint = _events_endpoint(app)
    request = AsyncMock()
    request.is_disconnected = AsyncMock(return_value=False)
    response = await endpoint(request)
    return response.body_iterator


@pytest.mark.asyncio
async def test_event_generator_emits_sync_then_status_change_on_touch(fix_medium):
    """T-V-SSE-int — MISSION exit-criterion #4, via direct generator drive.

    Drives the real ``event_generator`` (no HTTP/ASGITransport):
      1. First yield is the on-connect ``sync`` event.
      2. After a STATUS.md mtime touch, the generator emits ``status-change``
         within its 0.25s poll clock — observed here in well under a second.

    A single ``__anext__()`` on the generator runs the whole 30s bounded loop
    internally and only returns when an event is yielded, so we touch STATUS.md
    concurrently (after ``sync``) and await the next event under a short
    timeout. This exercises incremental delivery that ASGITransport buffering
    made impossible to observe.
    """
    app = make_app(mission_dir=fix_medium)
    body_iter = await _drive_generator(app)
    try:
        # 1. On-connect sync event.
        sync_event = await asyncio.wait_for(body_iter.__anext__(), timeout=5.0)
        assert sync_event["event"] == SSE_SYNC, (
            f"first SSE event must be 'sync', got {sync_event!r}"
        )

        # 2. Touch STATUS.md while the generator's poll loop is running. The
        #    generator captures last_mtime at the start of the loop (right after
        #    sync), so the touch must land AFTER __anext__ resumes — schedule it
        #    on the loop with a small delay rather than touching synchronously.
        status_path = fix_medium / "STATUS.md"

        async def _touch_status():
            await asyncio.sleep(0.3)
            current = status_path.read_text()
            status_path.write_text(current + "\n<!-- test heartbeat -->\n")

        touch_task = asyncio.create_task(_touch_status())
        change_event = await asyncio.wait_for(body_iter.__anext__(), timeout=5.0)
        await touch_task

        assert change_event["event"] == SSE_STATUS_CHANGE, (
            f"expected '{SSE_STATUS_CHANGE}' after STATUS.md touch, "
            f"got {change_event!r}"
        )
        # Payload should be JSON-ish with the refreshed lane snapshot.
        assert change_event.get("data"), "status-change event carried no data"
    finally:
        await body_iter.aclose()
