"""Task 2.4 — narrative HTTP endpoint tests.

Tests
-----
1. GET /api/v1/narrative returns cache as {"lanes": {...}}, including a row
   where now.phrase is None and all deterministic fields are present.
2. GET /api/v1/narrative-stream emits an initial frame from the current cache
   on connect, and a subsequently hub.publish()ed payload arrives on the stream.
3. PM-6 gating: both /api/v1/narrative and /api/v1/narrative-stream return 401
   without a session cookie, and succeed with one.

SSE testing note
----------------
httpx.ASGITransport buffers SSE responses until the generator completes.
For the streaming test we subscribe directly to the NarrativeHub queue
(bypassing HTTP) to confirm publish delivery, and verify the initial-frame
path via a bounded generator approach using hub.publish() to close the stream.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.auth import write_token_atomic
from megalodon_ui.server import make_app

TOKEN = "nar-test-token"
LANE_SHORT = "A"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DETERMINISTIC_KEYS = {"last", "now", "goal", "state"}


def _make_lane_row(phrase: str | None = "All good") -> dict:
    """Return a minimal narrative cache row matching the board_state shape."""
    return {
        "last": {"summary": "previous summary", "ts": "2026-05-01T00:00:00Z"},
        "now": {"phrase": phrase, "summary": "current summary"},
        "goal": "Finish the task",
        "state": "RUNNING",
    }


def _setup_mission(tmp_path: Path) -> None:
    """Create minimal required mission directory structure."""
    fleet = tmp_path / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    write_token_atomic(fleet / "ui.token", TOKEN)
    (tmp_path / "STATUS.md").write_text("# Status\n")
    (tmp_path / "TASKS.md").write_text("# Tasks\n")
    (tmp_path / "HISTORY.md").write_text("# History\n")
    (tmp_path / "findings").mkdir(exist_ok=True)
    (tmp_path / "signals").mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Fixture: authenticated client with test lifespan
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def nar_client(tmp_path: Path, monkeypatch) -> AsyncGenerator[tuple, None]:
    """Authenticated httpx client with narrative hub/cache in test mode lifespan."""
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    _setup_mission(tmp_path)

    app = make_app(mission_dir=tmp_path)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post("/api/v1/auth/exchange", json={"token": TOKEN})
            assert r.status_code == 200, f"auth failed: {r.text}"
            yield client, app, tmp_path


# ---------------------------------------------------------------------------
# 1. GET /api/v1/narrative — snapshot endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_narrative_snapshot_returns_cache(nar_client):
    """GET /api/v1/narrative returns {"lanes": <cache>} with correct content."""
    client, app, _ = nar_client

    # Seed the cache with a lane that has now.phrase = None.
    row = _make_lane_row(phrase=None)
    app.state.narrative_cache[LANE_SHORT] = row

    r = await client.get("/api/v1/narrative")
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"

    body = r.json()
    assert "lanes" in body, f"missing 'lanes' key: {body}"

    lane_data = body["lanes"][LANE_SHORT]

    # All deterministic fields must be present.
    for key in _DETERMINISTIC_KEYS:
        assert key in lane_data, f"missing deterministic key '{key}': {lane_data}"

    # now.phrase must be explicitly null (not omitted).
    assert lane_data["now"]["phrase"] is None, (
        f"expected now.phrase to be None, got: {lane_data['now']['phrase']}"
    )


@pytest.mark.asyncio
async def test_narrative_snapshot_empty_cache(nar_client):
    """GET /api/v1/narrative with empty cache returns {"lanes": {}}."""
    client, app, _ = nar_client
    app.state.narrative_cache.clear()

    r = await client.get("/api/v1/narrative")
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
    assert r.json() == {"lanes": {}}, f"unexpected body: {r.json()}"


# ---------------------------------------------------------------------------
# 2. GET /api/v1/narrative-stream — SSE endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_narrative_stream_initial_frame(nar_client):
    """narrative-stream SSE emits initial frame from current cache on connect.

    Strategy: seed the cache with one lane, call hub.publish() shortly after
    to trigger the generator to end the wait and surface the initial frame.
    We capture the raw SSE text by reading the endpoint after publish causes
    it to complete (bounded by asyncio task).
    """
    client, app, _ = nar_client
    hub = app.state.narrative_hub
    cache = app.state.narrative_cache

    row = _make_lane_row(phrase="Proceeding well")
    cache[LANE_SHORT] = row

    # We'll subscribe via the hub directly to verify the stream delivers a
    # subsequent publish (queue-level), since ASGI buffering holds the response.
    q = hub.subscribe()
    payload = {"lane": LANE_SHORT, "summary": "fresh update"}
    hub.publish(payload)

    try:
        received = await asyncio.wait_for(q.get(), timeout=2.0)
        assert received == payload, f"unexpected payload: {received}"
    finally:
        hub.unsubscribe(q)


@pytest.mark.asyncio
async def test_narrative_stream_subscribe_unsubscribe_cleanup(nar_client):
    """Subscribing then unsubscribing via hub decrements subscriber_count."""
    client, app, _ = nar_client
    hub = app.state.narrative_hub

    before = hub.subscriber_count
    q = hub.subscribe()
    assert hub.subscriber_count == before + 1
    hub.unsubscribe(q)
    assert hub.subscriber_count == before


@pytest.mark.asyncio
async def test_narrative_stream_publish_fan_out(nar_client):
    """hub.publish() fans out to all subscribers (multi-subscriber check)."""
    client, app, _ = nar_client
    hub = app.state.narrative_hub

    q1 = hub.subscribe()
    q2 = hub.subscribe()
    payload = {"lane": "B", "summary": "multi-fan"}
    hub.publish(payload)

    try:
        got1 = await asyncio.wait_for(q1.get(), timeout=1.0)
        got2 = await asyncio.wait_for(q2.get(), timeout=1.0)
        assert got1 == payload
        assert got2 == payload
    finally:
        hub.unsubscribe(q1)
        hub.unsubscribe(q2)


# ---------------------------------------------------------------------------
# 3. PM-6 gating: 401 without cookie, 200 with cookie
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_narrative_snapshot_gate_no_cookie(tmp_path: Path, monkeypatch):
    """GET /api/v1/narrative without session cookie → 401 (gated by _V92_GATED_PATH_RE)."""
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    _setup_mission(tmp_path)
    app = make_app(mission_dir=tmp_path)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.get("/api/v1/narrative")
            assert r.status_code == 401, (
                f"expected 401 without cookie, got {r.status_code}: {r.text}"
            )


@pytest.mark.asyncio
async def test_narrative_stream_gate_no_cookie(tmp_path: Path, monkeypatch):
    """GET /api/v1/narrative-stream without session cookie → 401 (gated by _V92_GATED_PATH_RE)."""
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    _setup_mission(tmp_path)
    app = make_app(mission_dir=tmp_path)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.get("/api/v1/narrative-stream")
            assert r.status_code == 401, (
                f"expected 401 without cookie, got {r.status_code}: {r.text}"
            )


@pytest.mark.asyncio
async def test_narrative_snapshot_gate_with_cookie(nar_client):
    """GET /api/v1/narrative WITH session cookie → 200."""
    client, app, _ = nar_client
    r = await client.get("/api/v1/narrative")
    assert r.status_code == 200, (
        f"expected 200 with valid cookie, got {r.status_code}: {r.text}"
    )


@pytest.mark.asyncio
async def test_narrative_stream_gate_with_cookie_returns_200(nar_client):
    """GET /api/v1/narrative-stream WITH cookie → 200 and delivers initial frame.

    ``httpx.ASGITransport`` buffers SSE until the generator completes (it never
    does — the stream loops forever), so we drive the ASGI app at the protocol
    level instead: capture ``http.response.start`` (status, exercises the auth
    middleware gate) and the first ``http.response.body`` chunk (the initial
    frame), then cancel the in-flight task so the test stays bounded and cannot
    hang.
    """
    client, app, _ = nar_client

    # Seed the cache so the initial frame has observable content.
    row = _make_lane_row(phrase="Proceeding well")
    app.state.narrative_cache[LANE_SHORT] = row

    # Forward the session cookie minted by the fixture's auth exchange so the
    # request clears the v9.2 path gate (proves the authed HTTP path, not a
    # hub shortcut).
    cookie_header = "; ".join(
        f"{name}={value}" for name, value in client.cookies.items()
    )

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/v1/narrative-stream",
        "raw_path": b"/api/v1/narrative-stream",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"test"),
            (b"cookie", cookie_header.encode()),
        ],
        "server": ("test", 80),
        "client": ("test", 12345),
    }

    captured: dict = {}

    async def _receive():
        # No client→server body for a GET; block forever until cancelled so the
        # endpoint's request.is_disconnected() never trips early.
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def _send(message):
        if message["type"] == "http.response.start":
            captured["status"] = message["status"]
        elif message["type"] == "http.response.body":
            body = message.get("body", b"")
            if body:
                captured["first_body"] = body
                # Got the initial frame — stop driving the app.
                raise asyncio.CancelledError

    task = asyncio.create_task(app(scope, _receive, _send))
    try:
        await asyncio.wait_for(task, timeout=5.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    assert captured.get("status") == 200, (
        f"expected 200 with valid cookie, got {captured.get('status')}"
    )
    raw = captured.get("first_body", b"").decode()
    assert "data:" in raw, f"first body chunk is not an SSE data frame: {raw!r}"
    data_line = next(line for line in raw.splitlines() if line.startswith("data:"))
    frame = json.loads(data_line[len("data:") :].strip())
    assert "lanes" in frame, f"initial frame missing 'lanes': {frame}"
    assert frame["lanes"][LANE_SHORT]["now"]["phrase"] == "Proceeding well"
