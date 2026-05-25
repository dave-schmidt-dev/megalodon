"""Wave 2 BE — activity-wall signal enrichment, ts-ordering, backfill (§D/§E).

Complements ``test_activity_wall.py`` (which owns the 6-source roundtrips).
"""

from __future__ import annotations

import asyncio
import sys
from collections import deque
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import megalodon_ui.event_tail as _et
from megalodon_ui.activity_wall import ActivityWall
from megalodon_ui.auth import write_token_atomic
from megalodon_ui.server import make_app

_et.POLL_INTERVAL_S = 0.05

TOKEN = "aws-test-token"


def _setup_mission(tmp_path: Path) -> None:
    fleet = tmp_path / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    write_token_atomic(fleet / "ui.token", TOKEN)
    (tmp_path / "STATUS.md").write_text("# Status\n")
    (tmp_path / "TASKS.md").write_text("# Tasks\n")
    (tmp_path / "HISTORY.md").write_text("# History\n")
    (tmp_path / "findings").mkdir(exist_ok=True)
    (tmp_path / "signals").mkdir(exist_ok=True)


@pytest_asyncio.fixture
async def aw_client(tmp_path: Path, monkeypatch) -> AsyncGenerator[tuple, None]:
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


async def _wait_for_event(wall, predicate, timeout_s: float = 3.0):
    q = wall.subscribe()
    try:
        deadline = asyncio.get_event_loop().time() + timeout_s
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                return None
            try:
                ev = await asyncio.wait_for(q.get(), timeout=min(remaining, 0.5))
                if predicate(ev):
                    return ev
            except asyncio.TimeoutError:
                return None
    finally:
        wall.unsubscribe(q)


# ---------------------------------------------------------------------------
# §D — enriched signal event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signal_event_enriched_with_grammar(aw_client):
    """A canonical signals file emits from/to/topic/utc + directional summary."""
    client, app, mission_dir = aw_client
    wall = app.state.activity_wall
    await asyncio.sleep(0.2)

    name = "LANE-ORCH-to-LANE-D-handoff-2026-05-25T18-49Z.md"
    (mission_dir / "signals" / name).write_text("here is the body of the signal")

    ev = await _wait_for_event(wall, lambda e: e["type"] == "signal")
    assert ev is not None, "no signal event"
    p = ev["payload"]
    assert p["from_lane"] == "LANE-ORCH"
    assert p["to_lane"] == "LANE-D"
    assert p["topic"] == "handoff"
    assert p["utc"] == "2026-05-25T18-49Z"
    assert p["excerpt"].startswith("here is the body")
    assert ev["summary"] == "LANE-ORCH→LANE-D: handoff"


@pytest.mark.asyncio
async def test_signal_event_non_canonical_still_emits(aw_client):
    """A non-canonical signal filename still emits (no from/to) — nothing dropped."""
    client, app, mission_dir = aw_client
    wall = app.state.activity_wall
    await asyncio.sleep(0.2)

    name = "agent-abcd-A-P1-sig-2026-05-20T12-00-00Z.md"
    (mission_dir / "signals" / name).write_text("x")

    ev = await _wait_for_event(wall, lambda e: e["type"] == "signal")
    assert ev is not None
    assert ev["payload"]["filename"] == name
    assert ev["payload"]["from_lane"] == ""


# ---------------------------------------------------------------------------
# §E — snapshot ts ordering
# ---------------------------------------------------------------------------


def test_snapshot_sorts_by_ts_not_insertion_order(tmp_path):
    """Out-of-order insertion → snapshot returns strictly newest-first by ts."""
    wall = ActivityWall(tmp_path)
    wall._ring = deque(maxlen=500)
    # Insert in scrambled order.
    for ts, tag in [
        ("2026-05-02T00:00:00Z", "mid"),
        ("2026-05-03T00:00:00Z", "new"),
        ("2026-05-01T00:00:00Z", "old"),
    ]:
        wall._emit(
            {"type": "history", "lane": None, "ts": ts, "summary": tag, "payload": {}}
        )
    snap = wall.snapshot(10)
    assert [e["summary"] for e in snap] == ["new", "mid", "old"]


def test_snapshot_empty_ts_sorts_last(tmp_path):
    wall = ActivityWall(tmp_path)
    wall._ring = deque(maxlen=500)
    wall._emit(
        {"type": "history", "lane": None, "ts": "", "summary": "nots", "payload": {}}
    )
    wall._emit(
        {
            "type": "history",
            "lane": None,
            "ts": "2026-05-01T00:00:00Z",
            "summary": "has",
            "payload": {},
        }
    )
    snap = wall.snapshot(10)
    assert snap[0]["summary"] == "has"
    assert snap[-1]["summary"] == "nots"


# ---------------------------------------------------------------------------
# §E — backfill on start
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_replays_existing_state_on_start(tmp_path):
    """start() backfills existing HISTORY/findings/signals into the ring."""
    (tmp_path / "findings").mkdir()
    (tmp_path / "signals").mkdir()
    (tmp_path / "HISTORY.md").write_text(
        "# History\n2026-05-20T12:00:00Z | agent-1 | LANE-A | T1 | did a thing | INFO\n"
    )
    (tmp_path / "findings" / "agent-x-A-P1-f-2026-05-20T12-00-00Z.md").write_text(
        "---\nlane: A\n---\nbody\n"
    )
    (tmp_path / "signals" / "LANE-ORCH-to-LANE-D-go-2026-05-21T00-00Z.md").write_text(
        "ship"
    )

    wall = ActivityWall(tmp_path)
    try:
        await wall.start()
        # Backfill ran synchronously inside start() before live watchers.
        snap = wall.snapshot(50)
        types = {e["type"] for e in snap}
        assert "history" in types
        assert "finding" in types
        assert "signal" in types
        # Newest-first overall (signal 05-21 newer than history/finding 05-20).
        assert snap[0]["type"] == "signal"
    finally:
        await wall.stop()


@pytest.mark.asyncio
async def test_backfill_empty_dirs_no_events(tmp_path):
    """Fresh mission with only header HISTORY → backfill emits nothing."""
    (tmp_path / "findings").mkdir()
    (tmp_path / "signals").mkdir()
    (tmp_path / "HISTORY.md").write_text("# History\n")
    wall = ActivityWall(tmp_path)
    try:
        await wall.start()
        assert wall.snapshot(50) == []
    finally:
        await wall.stop()
