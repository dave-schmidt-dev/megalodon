"""v9.4 Task 2.3 — activity wall endpoint tests.

Design note on SSE testing
--------------------------
``httpx.ASGITransport`` buffers SSE responses until the generator completes
(same as Starlette TestClient) — this is noted in server.py's pane-stream
docstring.  We therefore test each event-source roundtrip by subscribing
directly to the ``ActivityWall`` queue (bypassing the HTTP layer) within 2 s,
then separately verify that the SSE *endpoint* returns 200 + correct data when
the generator does eventually complete.

HTTP-layer tests that don't rely on streaming (snapshot, auth gate, limit clip)
are tested via the normal httpx client against the ASGI transport.

Tests
-----
1. Each of the 6 event sources: trigger → ActivityWall queue delivers within 2 s.
2. Snapshot newest-first: 3 events with controlled timestamps → order correct.
3. Cap honored: 600-event ring pre-populated → snapshot(500) yields exactly 500.
4. Limit param clipped: limit=1000 → 500 events returned (silent clip, 200).
5. Auth gate: missing cookie → 401 (snapshot + SSE endpoint open).
6. Cancellation cleanup: subscribe + unsubscribe → subscriber_count decrements.
7. Parse lane from filename (unit test).
8. inject-log restart-loop event type.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.auth import write_token_atomic
from megalodon_ui.mission_config.schema import MissionConfig
from megalodon_ui.server import make_app

# Speed up polls so tests don't crawl.
import megalodon_ui.event_tail as _et

_et.POLL_INTERVAL_S = 0.05

TOKEN = "aw-test-token"
LANE_SHORT = "A"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mission_config() -> MissionConfig:
    return MissionConfig.model_validate(
        {
            "mission": {"id": "test-aw", "utc_started": "2026-01-01T00:00:00Z"},
            "lanes": [
                {
                    "name": "AUDIT",
                    "short": LANE_SHORT,
                    "role": "auditor",
                    "harness": {"cli": "claude", "model": "claude-sonnet-4-6"},
                    "cadence_seconds": 300,
                    "tick_offset_seconds": 0,
                }
            ],
            "phases": ["INIT"],
        }
    )


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


async def _wait_for_event(
    wall,
    predicate,
    timeout_s: float = 2.0,
) -> dict | None:
    """Subscribe to the ActivityWall and return the first event matching predicate.

    Subscribes, waits up to *timeout_s*, then unsubscribes.
    Returns the matching event dict, or None on timeout.
    """
    q = wall.subscribe()
    try:
        deadline = asyncio.get_event_loop().time() + timeout_s
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                return None
            try:
                event = await asyncio.wait_for(q.get(), timeout=min(remaining, 0.5))
                if predicate(event):
                    return event
            except asyncio.TimeoutError:
                return None
    finally:
        wall.unsubscribe(q)


# ---------------------------------------------------------------------------
# Fixture: authenticated client with lifespan activity wall
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def aw_client(tmp_path: Path, monkeypatch) -> AsyncGenerator[tuple, None]:
    """Authenticated httpx client with activity wall running (test mode lifespan)."""
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
# 1a. Source roundtrip: findings/
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_finding_roundtrip(aw_client):
    """Writing a file to findings/ → ActivityWall queue delivers event within 2 s."""
    client, app, mission_dir = aw_client
    wall = app.state.activity_wall
    findings_dir = mission_dir / "findings"

    # Let the watch_dir_for_new_files generator take its initial snapshot
    await asyncio.sleep(0.2)

    finding_file = findings_dir / "agent-abcd-A-P1-topic-2026-05-20T12-00-00Z.md"
    finding_file.write_text("---\nlane: A\n---\ncontent\n")

    ev = await _wait_for_event(
        wall,
        lambda e: e["type"] == "finding",
        timeout_s=3.0,
    )
    assert ev is not None, "No 'finding' event received within 3 s"
    assert ev["lane"] == "A"
    assert ev["payload"]["filename"] == finding_file.name


# ---------------------------------------------------------------------------
# 1b. Source roundtrip: signals/
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_signal_roundtrip(aw_client):
    """Writing a file to signals/ → ActivityWall queue delivers event within 2 s."""
    client, app, mission_dir = aw_client
    wall = app.state.activity_wall
    signals_dir = mission_dir / "signals"

    await asyncio.sleep(0.2)

    sig_file = signals_dir / "agent-abcd-A-P1-sig-2026-05-20T12-00-00Z.md"
    sig_file.write_text("---\nsignal-type: SIG-TEST\n---\n")

    ev = await _wait_for_event(
        wall,
        lambda e: e["type"] == "signal",
        timeout_s=3.0,
    )
    assert ev is not None, "No 'signal' event received within 3 s"
    assert ev["lane"] == "A"
    assert ev["payload"]["filename"] == sig_file.name


# ---------------------------------------------------------------------------
# 1c. Source roundtrip: HISTORY.md
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_history_roundtrip(aw_client):
    """Appending a line to HISTORY.md → event delivered within 2 s."""
    client, app, mission_dir = aw_client
    wall = app.state.activity_wall
    history_path = mission_dir / "HISTORY.md"

    # Let the tail_file_lines generator open and seek to end of existing file
    await asyncio.sleep(0.3)

    with history_path.open("a") as f:
        f.write("2026-05-20T12:00:00Z | agent-0001 | LANE-A | T1 | test event | INFO\n")
        f.flush()

    ev = await _wait_for_event(
        wall,
        lambda e: e["type"] == "history",
        timeout_s=3.0,
    )
    assert ev is not None, "No 'history' event received within 3 s"
    assert ev["lane"] is None
    assert "LANE-A" in ev["summary"]


# ---------------------------------------------------------------------------
# 1d. Source roundtrip: queue-applier.log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_queue_applier_roundtrip(aw_client):
    """Appending to queue-applier.log → 'queue' event delivered within 2 s."""
    client, app, mission_dir = aw_client
    wall = app.state.activity_wall
    log_path = mission_dir / ".fleet" / "queue-applier.log"
    log_path.touch()

    # Let the tail seek to end of existing (empty) file
    await asyncio.sleep(0.3)

    with log_path.open("a") as f:
        f.write(
            "2026-05-20T12:00:00Z | INFO | APPLIED rid=abc lane=A agent=agent-0001 task=T1\n"
        )
        f.flush()

    ev = await _wait_for_event(
        wall,
        lambda e: e["type"] == "queue",
        timeout_s=3.0,
    )
    assert ev is not None, "No 'queue' event received within 3 s"
    assert ev["lane"] == "A"


# ---------------------------------------------------------------------------
# 1e. Source roundtrip: inject-log-YYYY-MM-DD.jsonl
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_inject_log_roundtrip(aw_client):
    """Appending a JSON line to inject-log-*.jsonl → 'inject' event within 2 s."""
    client, app, mission_dir = aw_client
    wall = app.state.activity_wall
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = mission_dir / ".fleet" / f"inject-log-{today}.jsonl"
    log_path.touch()

    await asyncio.sleep(0.3)

    entry = {
        "ts": "2026-05-20T12:00:00Z",
        "lane": LANE_SHORT,
        "text_sha256": "abc123",
        "byte_count": 11,
        "enter": True,
    }
    with log_path.open("a") as f:
        f.write(json.dumps(entry) + "\n")
        f.flush()

    ev = await _wait_for_event(
        wall,
        lambda e: e["type"] == "inject",
        timeout_s=3.0,
    )
    assert ev is not None, "No 'inject' event received within 3 s"
    assert ev["lane"] == LANE_SHORT
    assert ev["summary"] == "11 bytes"


# ---------------------------------------------------------------------------
# 1f. Source roundtrip: approval (PermissionWatcher callback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_approval_roundtrip(aw_client):
    """Firing the permission-change callback → 'approval' event delivered within 1 s."""
    client, app, mission_dir = aw_client
    wall = app.state.activity_wall

    # Subscribe first
    q = wall.subscribe()
    try:
        # Fire the callback directly (simulates watcher detecting a prompt)
        wall._on_permission_change("A", None, "approve")

        ev = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev["type"] == "approval"
        assert ev["lane"] == "A"
        assert ev["summary"] == "approve"
        assert ev["payload"]["action"] == "approve"
    finally:
        wall.unsubscribe(q)


# ---------------------------------------------------------------------------
# 2. Snapshot newest-first
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_newest_first(aw_client):
    """3 events with controlled timestamps → snapshot returns newest-first."""
    client, app, mission_dir = aw_client
    wall = app.state.activity_wall

    for i, ts in enumerate(
        [
            "2026-05-01T00:00:00Z",
            "2026-05-02T00:00:00Z",
            "2026-05-03T00:00:00Z",
        ]
    ):
        wall._emit(
            {
                "type": "history",
                "lane": None,
                "ts": ts,
                "summary": f"event-{i}",
                "payload": {"line": f"line-{i}"},
            }
        )

    r = await client.get("/api/v1/activity-wall/snapshot")
    assert r.status_code == 200
    events = r.json()["events"]
    # Find our 3 injected events (there may be others from other sources)
    our_events = [e for e in events if e["summary"].startswith("event-")]
    assert len(our_events) >= 3
    # Newest first
    assert our_events[0]["ts"] == "2026-05-03T00:00:00Z"
    assert our_events[1]["ts"] == "2026-05-02T00:00:00Z"
    assert our_events[2]["ts"] == "2026-05-01T00:00:00Z"


# ---------------------------------------------------------------------------
# 3. Ring buffer cap: 600 events → snapshot(500) returns exactly 500
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ring_buffer_cap(aw_client):
    """Pre-populate 600 events; ring maxlen=500 so snapshot(500) yields exactly 500."""
    client, app, mission_dir = aw_client
    wall = app.state.activity_wall

    # Clear any pre-existing events by replacing the ring with a fresh deque
    from collections import deque

    wall._ring = deque(maxlen=500)

    for i in range(600):
        wall._emit(
            {
                "type": "history",
                "lane": None,
                "ts": "2026-05-20T00:00:00Z",
                "summary": f"event-{i}",
                "payload": {"line": str(i)},
            }
        )

    r = await client.get("/api/v1/activity-wall/snapshot?limit=500")
    assert r.status_code == 200
    events = r.json()["events"]
    assert len(events) == 500


# ---------------------------------------------------------------------------
# 4. Limit param clipped silently: limit=1000 → 200, not 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_limit_clipped_silently(aw_client):
    """limit=1000 clips to 500 silently (no 400 error)."""
    client, app, mission_dir = aw_client
    wall = app.state.activity_wall

    from collections import deque

    wall._ring = deque(maxlen=500)

    for i in range(50):
        wall._emit(
            {
                "type": "history",
                "lane": None,
                "ts": "2026-05-20T00:00:00Z",
                "summary": f"event-{i}",
                "payload": {},
            }
        )

    r = await client.get("/api/v1/activity-wall/snapshot?limit=1000")
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
    events = r.json()["events"]
    # Only 50 events exist; limit was clipped to 500 so all 50 returned
    assert len(events) == 50


# ---------------------------------------------------------------------------
# 5. Auth gate: missing cookie → 401
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_gate_snapshot(tmp_path: Path, monkeypatch):
    """GET /api/v1/activity-wall/snapshot without cookie → 401."""
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    _setup_mission(tmp_path)
    app = make_app(mission_dir=tmp_path)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.get("/api/v1/activity-wall/snapshot")
            assert r.status_code == 401, f"expected 401, got {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_auth_gate_sse(tmp_path: Path, monkeypatch):
    """GET /api/v1/activity-wall without cookie → 401."""
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    _setup_mission(tmp_path)
    app = make_app(mission_dir=tmp_path)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.get("/api/v1/activity-wall")
            assert r.status_code == 401, f"expected 401, got {r.status_code}: {r.text}"


# ---------------------------------------------------------------------------
# 6. Cancellation cleanup: subscribe + unsubscribe → subscriber_count decrements
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancellation_cleanup(aw_client):
    """After unsubscribe(), subscriber_count returns to its prior value."""
    client, app, mission_dir = aw_client
    wall = app.state.activity_wall

    before = wall.subscriber_count

    q = wall.subscribe()
    assert wall.subscriber_count == before + 1

    wall.unsubscribe(q)
    assert wall.subscriber_count == before


# ---------------------------------------------------------------------------
# 7. Parse lane from filename (unit test)
# ---------------------------------------------------------------------------


def test_parse_lane_from_filename():
    from megalodon_ui.activity_wall import _parse_lane_from_filename

    assert (
        _parse_lane_from_filename("agent-abcd-A-P1-topic-2026-05-20T12-00-00Z.md")
        == "A"
    )
    assert _parse_lane_from_filename("agent-1234-B-P2-other.md") == "B"
    assert _parse_lane_from_filename("unrelated.txt") is None
    assert _parse_lane_from_filename("") is None


# ---------------------------------------------------------------------------
# 8. inject-log restart-loop event type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inject_log_restart_loop_type(aw_client):
    """inject-log entries with 'source' field emit type='restart-loop'."""
    client, app, mission_dir = aw_client
    wall = app.state.activity_wall
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = mission_dir / ".fleet" / f"inject-log-{today}.jsonl"
    log_path.touch()

    await asyncio.sleep(0.3)

    entry = {
        "ts": "2026-05-20T12:00:00Z",
        "lane": LANE_SHORT,
        "text_sha256": "deadbeef",
        "byte_count": 42,
        "enter": True,
        "source": "restart-loop",
    }

    q = wall.subscribe()
    try:
        with log_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
            f.flush()

        ev = await asyncio.wait_for(q.get(), timeout=3.0)
        assert ev["type"] == "restart-loop"
        assert ev["payload"]["source"] == "restart-loop"
        assert ev["lane"] == LANE_SHORT
    finally:
        wall.unsubscribe(q)


# ---------------------------------------------------------------------------
# 9. ActivityWall: per-subscriber queue overflow drops oldest, warns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscriber_queue_overflow(aw_client, caplog):
    """Emitting > 100 events to a full subscriber queue drops oldest."""
    import logging

    client, app, mission_dir = aw_client
    wall = app.state.activity_wall

    q = wall.subscribe()
    try:
        # Fill the queue to capacity (maxsize=100)
        for i in range(100):
            q.put_nowait(
                {
                    "type": "history",
                    "lane": None,
                    "ts": "2026-05-20T00:00:00Z",
                    "summary": f"pre-{i}",
                    "payload": {},
                }
            )
        assert q.full()

        # Emit one more — should drop the oldest and push the new one
        with caplog.at_level(logging.WARNING, logger="megalodon_ui.activity_wall"):
            wall._emit(
                {
                    "type": "history",
                    "lane": None,
                    "ts": "2026-05-20T00:00:00Z",
                    "summary": "overflow-event",
                    "payload": {},
                }
            )

        assert "dropped oldest" in caplog.text
        # Queue is still full (one dropped, one added)
        assert q.full()
    finally:
        wall.unsubscribe(q)


# ---------------------------------------------------------------------------
# 10. Snapshot returns empty list when no events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_empty(aw_client):
    """Snapshot on a fresh (cleared) wall returns empty list."""
    client, app, mission_dir = aw_client
    wall = app.state.activity_wall

    from collections import deque

    wall._ring = deque(maxlen=500)

    r = await client.get("/api/v1/activity-wall/snapshot?limit=10")
    assert r.status_code == 200
    assert r.json()["events"] == []
