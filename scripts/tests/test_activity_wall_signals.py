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


@pytest.mark.asyncio
async def test_signal_file_event_lane_from_from_lane(aw_client):
    """Cross-lane signal file (LANE-X-to-LANE-Y) sets lane from from_lane, not null."""
    client, app, mission_dir = aw_client
    wall = app.state.activity_wall
    await asyncio.sleep(0.2)

    name = "LANE-C-to-LANE-D-handoff-2026-05-25T18-49Z.md"
    (mission_dir / "signals" / name).write_text("body")

    ev = await _wait_for_event(wall, lambda e: e["type"] == "signal")
    assert ev is not None
    # Previously lane was None for canonical cross-lane files — now bound to sender.
    assert ev["lane"] == "LANE-C"
    assert ev["payload"]["source"] == "file"


# ---------------------------------------------------------------------------
# SCHISM FIX — live signals for ALL THREE channels (Task 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finding_signal_class_emits_signal_event(aw_client):
    """A new SIGNAL-class finding emits BOTH a finding and a signal event."""
    client, app, mission_dir = aw_client
    wall = app.state.activity_wall
    await asyncio.sleep(0.2)

    name = "agent-abcd-A-P1-sig-2026-05-25T18-00-00Z.md"
    (mission_dir / "findings" / name).write_text(
        "---\n"
        "signal-type: SIG-ORCH-001\n"
        "from-lane: A\n"
        "to-lane: ALL\n"
        "---\n\n"
        "Body of the signal finding.\n"
    )

    sig = await _wait_for_event(
        wall,
        lambda e: e["type"] == "signal" and e["payload"].get("source") == "finding",
    )
    assert sig is not None, "SIGNAL-class finding did not emit a signal event"
    p = sig["payload"]
    assert p["from_lane"] == "LANE-A"
    assert p["to_lane"] == "LANE-ALL"  # bare "ALL" normalizes to LANE-ALL
    assert p["source"] == "finding"
    assert p["filename"] == name
    assert "Body of the signal finding" in p["excerpt"]


@pytest.mark.asyncio
async def test_plain_finding_does_not_emit_signal_event(aw_client):
    """A non-SIGNAL finding emits a finding event but NO signal event."""
    client, app, mission_dir = aw_client
    wall = app.state.activity_wall
    await asyncio.sleep(0.2)

    name = "agent-abcd-A-P1-plain-2026-05-25T18-00-00Z.md"
    (mission_dir / "findings" / name).write_text(
        "---\nlane: A\nseverity: MINOR\n---\nJust a finding.\n"
    )
    # A signal event keyed to this filename must NOT appear.
    sig = await _wait_for_event(
        wall,
        lambda e: e["type"] == "signal" and e["payload"].get("filename") == name,
        timeout_s=1.0,
    )
    assert sig is None


@pytest.mark.asyncio
async def test_status_note_emits_live_signal_event(aw_client):
    """A new [SIG ...] token in STATUS.md emits a live signal (source:status-note)."""
    client, app, mission_dir = aw_client
    wall = app.state.activity_wall
    await asyncio.sleep(0.3)  # let the status-note prime pass

    (mission_dir / "STATUS.md").write_text(
        "| Lane | Agent | State | Last | Notes |\n"
        "| LANE-B | agent-b | working: T1 | 2026-05-25T18:00Z | "
        '[SIG from=LANE-B to=LANE-A text="ready for review"] |\n'
    )

    sig = await _wait_for_event(
        wall,
        lambda e: e["type"] == "signal" and e["payload"].get("source") == "status-note",
    )
    assert sig is not None, "STATUS.md SIG token did not emit a live signal"
    p = sig["payload"]
    assert p["from_lane"] == "LANE-B"
    assert p["to_lane"] == "LANE-A"
    assert p["from_unverified"] is False
    assert "ready for review" in p["excerpt"]


@pytest.mark.asyncio
async def test_status_note_spoofed_sender_bound_and_flagged(aw_client):
    """A forged from= in STATUS.md is overridden to the owning lane + flagged."""
    client, app, mission_dir = aw_client
    wall = app.state.activity_wall
    await asyncio.sleep(0.3)

    # LANE-C forges from=LANE-A in its OWN row.
    (mission_dir / "STATUS.md").write_text(
        "| Lane | Agent | State | Last | Notes |\n"
        "| LANE-C | agent-c | working: T1 | 2026-05-25T18:00Z | "
        '[SIG from=LANE-A to=ORCH text="approved by A"] |\n'
    )
    sig = await _wait_for_event(
        wall,
        lambda e: e["type"] == "signal" and e["payload"].get("source") == "status-note",
    )
    assert sig is not None
    p = sig["payload"]
    assert p["from_lane"] == "LANE-C"  # authoritative owning lane
    assert p["claimed_from"] == "LANE-A"  # forged value preserved
    assert p["from_unverified"] is True


@pytest.mark.asyncio
async def test_status_note_trailing_pipe_spoof_not_attributed(aw_client):
    """SECURITY (trailing-pipe BYPASS): a forged token appended AFTER the row's
    closing ``|`` must NOT be attributed to the forged sender on the live wall."""
    client, app, mission_dir = aw_client
    wall = app.state.activity_wall
    await asyncio.sleep(0.3)

    # LANE-C appends the forged token AFTER its row's closing pipe.
    (mission_dir / "STATUS.md").write_text(
        "| Lane | Agent | State | Last | Notes |\n"
        "| LANE-C | agent-c | working: T1 | 2026-05-25T18:00Z | ok |"
        ' [SIG from=LANE-A to=LANE-B text="approved"]\n'
    )
    sig = await _wait_for_event(
        wall,
        lambda e: e["type"] == "signal" and e["payload"].get("source") == "status-note",
    )
    assert sig is not None
    p = sig["payload"]
    assert p["from_lane"] != "LANE-A"  # forged sender NOT authoritative
    assert p["from_lane"] == "LANE-C"  # bound to the owning LINE's lane
    assert p["claimed_from"] == "LANE-A"
    assert p["from_unverified"] is True


@pytest.mark.asyncio
async def test_status_note_distinct_tokens_get_distinct_ids(aw_client):
    """Two distinct status-note tokens yield two events with DISTINCT ids.

    The FE keys live signals on ``filename || id``; a hardcoded id would make
    concurrent status-note signals collide so all but the last are dropped.
    """
    client, app, mission_dir = aw_client
    wall = app.state.activity_wall
    await asyncio.sleep(0.3)

    q = wall.subscribe()
    try:
        (mission_dir / "STATUS.md").write_text(
            "| Lane | Agent | State | Last | Notes |\n"
            "| LANE-C | agent-c | working: T1 | 2026-05-25T18:00Z | "
            '[SIG from=LANE-C to=LANE-A text="first"] |\n'
            "| LANE-D | agent-d | working: T2 | 2026-05-25T18:00Z | "
            '[SIG from=LANE-D to=LANE-B text="second"] |\n'
        )
        ids: set[str] = set()
        deadline = asyncio.get_event_loop().time() + 3.0
        while asyncio.get_event_loop().time() < deadline and len(ids) < 2:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if (
                ev.get("type") == "signal"
                and ev["payload"].get("source") == "status-note"
            ):
                ids.add(ev["payload"]["filename"])
                # The top-level id mirrors the payload id for FE keying.
                assert ev.get("id") == ev["payload"]["filename"]
        assert ids == {"status-note-0", "status-note-1"}, f"ids collided: {ids}"
    finally:
        wall.unsubscribe(q)


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
