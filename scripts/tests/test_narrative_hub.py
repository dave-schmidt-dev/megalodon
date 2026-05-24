"""Unit tests for megalodon_ui.narrator.hub.NarrativeHub.

Covers:
1. subscribe() returns an asyncio.Queue.
2. publish() fans out to all subscribers.
3. unsubscribe() removes a subscriber queue.
4. subscriber_count tracks correctly.
5. tick_now is set on the 0→1 subscribe transition only (not on 1→2).
6. Full-queue backpressure policy: drop oldest, put newest (mirrors ActivityWall).

All tests use pytest-asyncio and run under -W error.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.narrator.hub import NarrativeHub


# ---------------------------------------------------------------------------
# 1. subscribe() returns a queue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_returns_queue():
    """subscribe() returns an asyncio.Queue instance."""
    hub = NarrativeHub()
    q = hub.subscribe()
    assert isinstance(q, asyncio.Queue)
    hub.unsubscribe(q)


# ---------------------------------------------------------------------------
# 2. publish() fans out to all subscribers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_fanout():
    """publish() delivers the payload to every active subscriber queue."""
    hub = NarrativeHub()
    q1 = hub.subscribe()
    q2 = hub.subscribe()
    payload = {"lane": "A", "narrative": "all good"}

    hub.publish(payload)

    item1 = q1.get_nowait()
    item2 = q2.get_nowait()
    assert item1 == payload
    assert item2 == payload

    hub.unsubscribe(q1)
    hub.unsubscribe(q2)


# ---------------------------------------------------------------------------
# 3. unsubscribe() removes a queue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unsubscribe_removes_queue():
    """After unsubscribe(), publish() does not deliver to that queue."""
    hub = NarrativeHub()
    q = hub.subscribe()
    hub.unsubscribe(q)

    hub.publish({"lane": "B", "narrative": "dropped"})

    assert q.empty()


# ---------------------------------------------------------------------------
# 4. subscriber_count tracks correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscriber_count():
    """subscriber_count reflects the current number of active subscribers."""
    hub = NarrativeHub()
    assert hub.subscriber_count == 0

    q1 = hub.subscribe()
    assert hub.subscriber_count == 1

    q2 = hub.subscribe()
    assert hub.subscriber_count == 2

    hub.unsubscribe(q1)
    assert hub.subscriber_count == 1

    hub.unsubscribe(q2)
    assert hub.subscriber_count == 0


# ---------------------------------------------------------------------------
# 5. tick_now is set on 0→1, NOT set on 1→2
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_now_set_on_first_subscriber():
    """tick_now Event is set when subscribe() takes count from 0 to 1."""
    hub = NarrativeHub()
    assert not hub.tick_now.is_set()

    q1 = hub.subscribe()
    assert hub.tick_now.is_set(), "tick_now must be set after first subscribe"

    hub.unsubscribe(q1)


@pytest.mark.asyncio
async def test_tick_now_not_set_on_second_subscriber():
    """tick_now is NOT set (again) when subscribe() takes count from 1 to 2.

    The event is only fired on the 0→1 transition so the scheduler gets a
    single wake-up signal when the first client connects.  It is up to the
    scheduler (a later task) to clear the event — the hub must never clear it.
    """
    hub = NarrativeHub()

    q1 = hub.subscribe()
    # Clear the event as the scheduler would after its first wake-up
    hub.tick_now.clear()

    # Second subscriber must NOT re-set the event
    q2 = hub.subscribe()
    assert not hub.tick_now.is_set(), "tick_now must NOT be set on 1→2 transition"

    hub.unsubscribe(q1)
    hub.unsubscribe(q2)


@pytest.mark.asyncio
async def test_tick_now_hub_never_clears():
    """The hub sets tick_now but never clears it — that is the scheduler's job."""
    hub = NarrativeHub()
    q = hub.subscribe()
    assert hub.tick_now.is_set()

    # Simulate external clear (scheduler's responsibility)
    hub.tick_now.clear()
    assert not hub.tick_now.is_set()

    # Unsubscribe and re-subscribe (0→1 again) must re-set
    hub.unsubscribe(q)
    assert hub.subscriber_count == 0

    q2 = hub.subscribe()
    assert hub.tick_now.is_set(), "tick_now must re-set on a fresh 0→1 transition"
    hub.unsubscribe(q2)


# ---------------------------------------------------------------------------
# 6. Full-queue policy: drop oldest, put newest (mirrors ActivityWall._fan_out)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_queue_drops_oldest(caplog):
    """When a subscriber queue is full, publish() drops the oldest item and
    enqueues the new payload — matching ActivityWall's backpressure policy."""
    import logging

    hub = NarrativeHub()
    q = hub.subscribe()

    # Fill the queue to capacity
    for i in range(q.maxsize):
        q.put_nowait({"lane": "X", "narrative": f"old-{i}"})
    assert q.full()

    new_payload = {"lane": "X", "narrative": "new"}
    with caplog.at_level(logging.WARNING, logger="megalodon_ui.narrator.hub"):
        hub.publish(new_payload)

    # Queue is still full (one dropped, one added)
    assert q.full()
    # Drain to verify new_payload is present at the end (oldest was dropped)
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    assert items[-1] == new_payload, (
        "newest payload must be the last item after overflow"
    )
    assert "dropped oldest" in caplog.text

    hub.unsubscribe(q)


# ---------------------------------------------------------------------------
# 7. Lightweight integration: hub + cache exist on app.state in test mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_app_state_narrative_hub_and_cache_in_test_mode(tmp_path, monkeypatch):
    """After lifespan startup in MEGALODON_LIFESPAN_TEST_MODE, app.state has
    narrative_hub (NarrativeHub) and narrative_cache (dict)."""
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")

    # Build minimal mission directory (mirrors _setup_mission in test_activity_wall)
    from megalodon_ui.auth import write_token_atomic

    fleet = tmp_path / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    write_token_atomic(fleet / "ui.token", "hub-test-token")
    (tmp_path / "STATUS.md").write_text("# Status\n")
    (tmp_path / "TASKS.md").write_text("# Tasks\n")
    (tmp_path / "HISTORY.md").write_text("# History\n")
    (tmp_path / "findings").mkdir(exist_ok=True)
    (tmp_path / "signals").mkdir(exist_ok=True)

    from megalodon_ui.server import make_app

    app = make_app(mission_dir=tmp_path)

    async with app.router.lifespan_context(app):
        assert hasattr(app.state, "narrative_hub"), "app.state.narrative_hub missing"
        assert hasattr(app.state, "narrative_cache"), (
            "app.state.narrative_cache missing"
        )
        assert isinstance(app.state.narrative_hub, NarrativeHub)
        assert isinstance(app.state.narrative_cache, dict)
        assert app.state.narrative_cache == {}
