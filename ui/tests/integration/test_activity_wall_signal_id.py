"""Fix Round 3 — activity-wall CONTRACT-SIGNAL-ID, CONTRACT-ROSTER, utc, mission-events.

These cover four Fix-Round-3 findings against ``megalodon_ui/activity_wall.py``:

1. MAJOR — cross-generation status-note key collision (CONTRACT-SIGNAL-ID): the
   status-note signal ``id`` must be a CONTENT hash
   ``"sig-" + sha1(f"{from_lane}|{claimed_from}|{to}|{text}").hexdigest()[:12]``,
   stable across STATUS.md generations, NOT a positional ``status-note-<idx>``
   (which collides across rewrites so the FE drops the earlier signal).
2. MAJOR — forged-foreign-row roster validation (CONTRACT-ROSTER): a token whose
   OWNING lane is not in the configured roster must be flagged
   ``from_unverified=True`` + ``roster_unknown=True``.
3. COSMETIC — status-note ``utc`` was empty; now the STATUS.md file mtime.
4. MINOR — ``.mission-events`` is an 8th wall source (``type:"mission-event"``).
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import megalodon_ui.event_tail as _et
from megalodon_ui.activity_wall import ActivityWall
from megalodon_ui.auth import write_token_atomic
from megalodon_ui.server import make_app


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch):
    monkeypatch.setattr(_et, "POLL_INTERVAL_S", 0.05)


TOKEN = "aw-sigid-test-token"

# Mission config with a known roster: shorts A, B, C, D (so LANE-Z is foreign).
_MISSION_YAML = """\
mission:
  id: test-aw-sigid
  utc_started: "2026-01-01T00:00:00Z"
lanes:
  - {name: ALPHA, short: A, harness: {cli: claude, model: claude-sonnet-4-6}}
  - {name: BRAVO, short: B, harness: {cli: claude, model: claude-sonnet-4-6}}
  - {name: CHARLIE, short: C, harness: {cli: claude, model: claude-sonnet-4-6}}
  - {name: DELTA, short: D, harness: {cli: claude, model: claude-sonnet-4-6}}
phases: [INIT]
"""


def _expected_sig_id(from_lane: str, claimed_from: str, to: str, text: str) -> str:
    """Re-derive the CONTRACT-SIGNAL-ID id the same way the wall must."""
    digest = hashlib.sha1(
        f"{from_lane}|{claimed_from}|{to}|{text}".encode()
    ).hexdigest()[:12]
    return f"sig-{digest}"


def _setup_mission(tmp_path: Path) -> None:
    fleet = tmp_path / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    write_token_atomic(fleet / "ui.token", TOKEN)
    (tmp_path / ".mission-config.yaml").write_text(_MISSION_YAML)
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


async def _collect_status_notes(wall, want: int, timeout_s: float = 3.0) -> list[dict]:
    """Subscribe and collect up to *want* status-note signal events."""
    q = wall.subscribe()
    out: list[dict] = []
    try:
        deadline = asyncio.get_running_loop().time() + timeout_s
        while asyncio.get_running_loop().time() < deadline and len(out) < want:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if (
                ev.get("type") == "signal"
                and ev["payload"].get("source") == "status-note"
            ):
                out.append(ev)
        return out
    finally:
        wall.unsubscribe(q)


# ---------------------------------------------------------------------------
# Finding 1 — CONTRACT-SIGNAL-ID (content hash, stable across generations)
# ---------------------------------------------------------------------------


def test_status_note_id_is_content_hash_pure(tmp_path):
    """The id is the sha1 content hash, NOT a positional status-note-<idx>."""
    _setup_mission(tmp_path)
    (tmp_path / "STATUS.md").write_text(
        "| Lane | Agent | State | Last | Notes |\n"
        "| LANE-A | agent-a | working: T1 | 2026-05-25T18:00Z | "
        '[SIG from=LANE-A to=LANE-B text="hello world"] |\n'
    )
    wall = ActivityWall(tmp_path)
    events = wall._parse_status_note_events(tmp_path / "STATUS.md")
    assert len(events) == 1
    ev = events[0]
    expected = _expected_sig_id("LANE-A", "LANE-A", "LANE-B", "hello world")
    assert ev["id"] == expected
    assert ev["payload"]["id"] == expected
    assert ev["payload"]["filename"] == expected
    assert not ev["id"].startswith("status-note-")


def test_status_note_id_idempotent_for_same_content(tmp_path):
    """Re-emitting identical content yields the SAME id (idempotent)."""
    _setup_mission(tmp_path)
    row = (
        "| Lane | Agent | State | Last | Notes |\n"
        "| LANE-A | agent-a | working: T1 | 2026-05-25T18:00Z | "
        '[SIG from=LANE-A to=LANE-B text="stable"] |\n'
    )
    (tmp_path / "STATUS.md").write_text(row)
    wall = ActivityWall(tmp_path)
    first = wall._parse_status_note_events(tmp_path / "STATUS.md")[0]
    # Rewrite STATUS.md with the SAME token (different mtime) — id must not change.
    (tmp_path / "STATUS.md").write_text(row)
    second = wall._parse_status_note_events(tmp_path / "STATUS.md")[0]
    assert first["id"] == second["id"]


@pytest.mark.asyncio
async def test_status_note_cross_generation_no_drop(aw_client):
    """Same positional slot, different text across two STATUS generations →
    DIFFERENT ids and BOTH retrievable (FE won't drop the earlier signal)."""
    client, app, mission_dir = aw_client
    wall = app.state.activity_wall
    await asyncio.sleep(0.3)  # let the status-note prime pass

    q = wall.subscribe()
    try:
        # Generation 1: text "AAA" in LANE-A's row.
        (mission_dir / "STATUS.md").write_text(
            "| Lane | Agent | State | Last | Notes |\n"
            "| LANE-A | agent-a | working: T1 | 2026-05-25T18:00Z | "
            '[SIG from=LANE-A to=LANE-B text="AAA"] |\n'
        )
        # Generation 2: SAME positional slot, text "BBB".
        await asyncio.sleep(0.2)
        (mission_dir / "STATUS.md").write_text(
            "| Lane | Agent | State | Last | Notes |\n"
            "| LANE-A | agent-a | working: T1 | 2026-05-25T18:01Z | "
            '[SIG from=LANE-A to=LANE-B text="BBB"] |\n'
        )

        ids: set[str] = set()
        deadline = asyncio.get_running_loop().time() + 3.0
        while asyncio.get_running_loop().time() < deadline and len(ids) < 2:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if (
                ev.get("type") == "signal"
                and ev["payload"].get("source") == "status-note"
            ):
                ids.add(ev["id"])
    finally:
        wall.unsubscribe(q)

    id_aaa = _expected_sig_id("LANE-A", "LANE-A", "LANE-B", "AAA")
    id_bbb = _expected_sig_id("LANE-A", "LANE-A", "LANE-B", "BBB")
    assert id_aaa != id_bbb
    assert {id_aaa, id_bbb} <= ids, f"a generation was dropped: {ids}"

    # Both must be retrievable in the ring (server does NOT dedupe on id).
    snap_ids = {
        e.get("id")
        for e in wall.snapshot(200)
        if e.get("payload", {}).get("source") == "status-note"
    }
    assert {id_aaa, id_bbb} <= snap_ids


# ---------------------------------------------------------------------------
# Finding 2 — CONTRACT-ROSTER (foreign owning lane flagged)
# ---------------------------------------------------------------------------


def test_forged_foreign_row_roster_unknown(tmp_path):
    """A fabricated row owned by LANE-Z (not in roster) → from_unverified +
    roster_unknown both True."""
    _setup_mission(tmp_path)
    (tmp_path / "STATUS.md").write_text(
        "| Lane | Agent | State | Last | Notes |\n"
        "| LANE-Z | agent-z | working: T9 | 2026-05-25T18:00Z | "
        '[SIG from=LANE-Z to=LANE-A text="trust me"] |\n'
    )
    wall = ActivityWall(tmp_path)
    events = wall._parse_status_note_events(tmp_path / "STATUS.md")
    assert len(events) == 1
    p = events[0]["payload"]
    assert p["from_lane"] == "LANE-Z"
    assert p["from_unverified"] is True
    assert p["roster_unknown"] is True


def test_roster_known_lane_not_flagged(tmp_path):
    """A token owned by an IN-ROSTER lane (matching claimed) → not flagged."""
    _setup_mission(tmp_path)
    (tmp_path / "STATUS.md").write_text(
        "| Lane | Agent | State | Last | Notes |\n"
        "| LANE-B | agent-b | working: T1 | 2026-05-25T18:00Z | "
        '[SIG from=LANE-B to=LANE-A text="ready"] |\n'
    )
    wall = ActivityWall(tmp_path)
    events = wall._parse_status_note_events(tmp_path / "STATUS.md")
    assert len(events) == 1
    p = events[0]["payload"]
    assert p["from_lane"] == "LANE-B"
    assert p["from_unverified"] is False
    assert p["roster_unknown"] is False


# ---------------------------------------------------------------------------
# Finding 3 — status-note utc populated from STATUS.md mtime
# ---------------------------------------------------------------------------


def test_status_note_utc_is_file_mtime(tmp_path):
    """utc is the STATUS.md mtime (ISO-8601 UTC), not the empty string."""
    _setup_mission(tmp_path)
    status = tmp_path / "STATUS.md"
    status.write_text(
        "| Lane | Agent | State | Last | Notes |\n"
        "| LANE-A | agent-a | working: T1 | 2026-05-25T18:00Z | "
        '[SIG from=LANE-A to=LANE-B text="hi"] |\n'
    )
    wall = ActivityWall(tmp_path)
    events = wall._parse_status_note_events(status)
    assert len(events) == 1
    utc = events[0]["payload"]["utc"]
    assert utc, "utc must not be empty"
    # Matches the event ts (both derived from the same mtime).
    assert utc == events[0]["ts"]
    assert utc.endswith("Z")


# ---------------------------------------------------------------------------
# Finding 4 — .mission-events 8th wall source
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mission_event_appears_on_wall(aw_client):
    """A line written to .mission-events surfaces as a type:'mission-event'."""
    client, app, mission_dir = aw_client
    wall = app.state.activity_wall
    await asyncio.sleep(0.2)

    q = wall.subscribe()
    try:
        events_path = mission_dir / ".mission-events"
        line = "2026-05-25T18:30:00Z INIT->PHASE-PLAN by orchestrator -- kickoff\n"
        with events_path.open("a", encoding="utf-8") as f:
            f.write(line)

        found = None
        deadline = asyncio.get_running_loop().time() + 3.0
        while asyncio.get_running_loop().time() < deadline:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if ev.get("type") == "mission-event":
                found = ev
                break
    finally:
        wall.unsubscribe(q)

    assert found is not None, "mission-event never reached the wall"
    assert "kickoff" in found["summary"]
    assert "kickoff" in found["payload"].get("raw", "")


@pytest.mark.asyncio
async def test_mission_event_json_line_parsed(aw_client):
    """A JSON mission-event line is parsed into the payload (format-tolerant)."""
    client, app, mission_dir = aw_client
    wall = app.state.activity_wall
    await asyncio.sleep(0.2)

    q = wall.subscribe()
    try:
        events_path = mission_dir / ".mission-events"
        with events_path.open("a", encoding="utf-8") as f:
            f.write('{"ts": "2026-05-25T19:00:00Z", "line": "phase change"}\n')

        found = None
        deadline = asyncio.get_running_loop().time() + 3.0
        while asyncio.get_running_loop().time() < deadline:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if ev.get("type") == "mission-event":
                found = ev
                break
    finally:
        wall.unsubscribe(q)

    assert found is not None
    assert found["ts"] == "2026-05-25T19:00:00Z"
    assert "phase change" in found["summary"]
