"""v9.4 ship-time gap-fill — inject-log date rollover in ActivityWall._source_inject_log.

The _source_inject_log coroutine polls datetime.now(UTC) each iteration and
restarts its inner drainer task when the date changes (midnight UTC rotation).
This is the first test of that rollover path.

Scenario
--------
1. Start ActivityWall with a mocked "today" date.
2. Write a JSON line to today's inject-log → confirm event arrives.
3. Advance the mocked date to "tomorrow".
4. Write a JSON line to tomorrow's inject-log → confirm event arrives.

If the rollover code is broken the second event never arrives and the test
fails with a timeout, catching the bug at CI rather than in production at
midnight.

Why this matters
----------------
`_source_inject_log` is the only source coroutine that has a dynamically
re-opened file target. A regression here would silently drop all inject and
restart-loop activity-wall events after midnight — no crash, no log error,
just silent data loss.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import megalodon_ui.event_tail as _et

# Speed up polls so the test doesn't crawl.
_et.POLL_INTERVAL_S = 0.05


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_TODAY = "2026-05-20"
FAKE_TOMORROW = "2026-05-21"


def _make_inject_entry(lane: str, byte_count: int = 5) -> dict:
    return {
        "ts": "2026-05-20T23:59:59Z",
        "lane": lane,
        "text_sha256": "abc123",
        "byte_count": byte_count,
        "enter": True,
    }


async def _wait_for_event(wall, predicate, timeout_s: float = 3.0) -> dict | None:
    """Subscribe to wall, return first event matching predicate, then unsubscribe."""
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
# Test: inject-log date rollover switches drainer to new file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inject_log_date_rollover_switches_to_new_file(
    tmp_path: Path, monkeypatch
):
    """ActivityWall._source_inject_log picks up tomorrow's log file after date rollover.

    Monkeypatches megalodon_ui.activity_wall.datetime so that the wall's internal
    date check first returns FAKE_TODAY, then FAKE_TOMORROW on subsequent polls,
    triggering the drainer-restart path.
    """
    from megalodon_ui.activity_wall import ActivityWall
    import megalodon_ui.activity_wall as _aw_mod

    fleet_dir = tmp_path / ".fleet"
    fleet_dir.mkdir(parents=True, exist_ok=True)

    today_log = fleet_dir / f"inject-log-{FAKE_TODAY}.jsonl"
    tomorrow_log = fleet_dir / f"inject-log-{FAKE_TOMORROW}.jsonl"

    # Pre-create both files so the tail generator can open them immediately.
    today_log.touch()
    tomorrow_log.touch()

    # Phase 1: wall sees FAKE_TODAY
    # We patch datetime in the activity_wall module's namespace so that
    # datetime.now(timezone.utc).strftime("%Y-%m-%d") returns our fake dates.
    # Use a mutable flag to control which "day" the fake datetime reports.
    # FAKE_TODAY first; flipped to FAKE_TOMORROW after we confirm today's event.
    use_tomorrow = [False]

    class _FakeDatetime:
        """Minimal datetime stand-in whose reported date is controlled by use_tomorrow[0]."""

        @staticmethod
        def now(tz=None):  # noqa: ANN001, ANN202
            if use_tomorrow[0]:
                return datetime(2026, 5, 21, 0, 0, 1, tzinfo=timezone.utc)
            return datetime(2026, 5, 20, 23, 59, 0, tzinfo=timezone.utc)

        @staticmethod
        def fromtimestamp(ts, tz=None):  # noqa: ANN001, ANN202
            return datetime.fromtimestamp(ts, tz=tz)

    # Patch only the datetime name inside activity_wall's module namespace.
    monkeypatch.setattr(_aw_mod, "datetime", _FakeDatetime)

    wall = ActivityWall(tmp_path)
    await wall.start()

    try:
        # Let the fan-in task start and the drainer for today's file open.
        await asyncio.sleep(0.3)

        # --- Phase 1: write to today's log → event must arrive ---
        entry_today = _make_inject_entry(lane="A", byte_count=11)
        with today_log.open("a") as f:
            f.write(json.dumps(entry_today) + "\n")
            f.flush()

        ev_today = await _wait_for_event(
            wall,
            lambda e: e["type"] == "inject" and e["lane"] == "A",
            timeout_s=4.0,
        )
        assert ev_today is not None, (
            "No 'inject' event from today's log within 4 s — today's drainer not working"
        )
        assert ev_today["summary"] == "11 bytes"

        # --- Phase 2: flip date to tomorrow and wait for the rollover check to fire ---
        # The outer while-loop polls datetime.now() at the top of each iteration.
        # With timeout=1.0 on the queue.get(), one iteration completes every ≤1 s.
        # Flip the flag, then wait 1.5 s for the drainer to restart on tomorrow's file.
        use_tomorrow[0] = True
        await asyncio.sleep(1.5)

        # Write to tomorrow's log — if rollover worked, event must arrive.
        entry_tomorrow = _make_inject_entry(lane="B", byte_count=22)
        with tomorrow_log.open("a") as f:
            f.write(json.dumps(entry_tomorrow) + "\n")
            f.flush()

        ev_tomorrow = await _wait_for_event(
            wall,
            lambda e: e["type"] == "inject" and e["lane"] == "B",
            timeout_s=4.0,
        )
        assert ev_tomorrow is not None, (
            "No 'inject' event from tomorrow's log within 4 s — "
            "date rollover did not switch the drainer to the new file"
        )
        assert ev_tomorrow["summary"] == "22 bytes"

    finally:
        await wall.stop()
