"""Unit tests for megalodon_ui.narrator.scheduler.

The scheduler is a watcher-gated loop that, per tick, builds deterministic
lane rows (via an injected ``build_rows``), narrates the narratable lanes
concurrently, updates a shared cache, and publishes a snapshot to the
NarrativeHub. These tests exercise it WITHOUT a server or live model:

- A ``FakeRuntime`` stands in for ``NarratorRuntime`` (configurable readiness,
  ``client=None``, ``base_url``, ``narrate_timeout_s``).
- The imported ``scheduler.narrate`` is monkeypatched to a fake so no HTTP
  is ever attempted.
- A real ``NarrativeHub`` exercises the watcher-gate + tick_now contract.
- ``build_rows`` is an injected ``async () -> dict[str, LaneRow]`` returning
  canned rows.

All tests use pytest-asyncio and run under ``-W error`` (any unawaited
coroutine / pending-task warning fails the suite — the scheduler task is
cancelled+awaited at the end of every loop test).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.narrator import scheduler as scheduler_mod
from megalodon_ui.narrator.board_state import LaneRow
from megalodon_ui.narrator.hub import NarrativeHub
from megalodon_ui.narrator.scheduler import (
    clamp_interval_s,
    narrate_rows,
    narrator_tick,
    run_narrator_scheduler,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeRuntime:
    """Minimal stand-in for NarratorRuntime exposing the scheduler's surface."""

    def __init__(self, *, ready: bool = True) -> None:
        self._ready = ready
        self.client = None
        self.base_url = "http://fake"
        self.narrate_timeout_s = 0.5

    def is_ready(self) -> bool:
        return self._ready


def _row(
    short: str,
    *,
    with_now: bool = True,
    with_last: bool = False,
    digest_text: str | None = "digest",
) -> LaneRow:
    """Build a canned LaneRow. ``digest_text=None`` => deterministic-only.

    ``with_last=True`` attaches a closed last task with phrase=None so the
    scheduler can narrate the Last column (OQ1).
    """
    now = (
        {"task_id": f"T-{short}", "desc": f"work {short}", "phrase": None}
        if with_now
        else None
    )
    last = (
        {"task_id": f"L-{short}", "desc": f"done {short}", "phrase": None}
        if with_last
        else None
    )
    return LaneRow(
        lane=short,
        lane_name=f"LANE-{short}",
        state="claimed",
        last=last,
        now=now,
        goal=f"goal {short}",
        tokens=123 if digest_text is not None else None,
        narrator_ok=False,
        digest_text=digest_text,
    )


def _make_build_rows(rows: dict[str, LaneRow], counter: list[int]):
    """Return an async build_rows that returns fresh copies and counts calls."""

    async def build_rows() -> dict[str, LaneRow]:
        counter[0] += 1
        # Fresh copies each call so in-place narration of one tick can't leak
        # into the next assertion.
        out: dict[str, LaneRow] = {}
        for short, r in rows.items():
            out[short] = LaneRow(
                lane=r.lane,
                lane_name=r.lane_name,
                state=r.state,
                last=dict(r.last) if r.last is not None else None,
                now=dict(r.now) if r.now is not None else None,
                goal=r.goal,
                tokens=r.tokens,
                narrator_ok=False,
                digest_text=r.digest_text,
            )
        return out

    return build_rows


# ---------------------------------------------------------------------------
# 1. clamp_interval_s [15, 120], default 30
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, 30.0),
        (0, 30.0),
        (5, 15.0),
        (999, 120.0),
        (45, 45.0),
        (15, 15.0),
        (120, 120.0),
    ],
)
def test_clamp_interval_s(raw, expected):
    """clamp_interval_s clamps to [15, 120] and defaults None/0 to 30."""
    assert clamp_interval_s(raw) == expected


# ---------------------------------------------------------------------------
# 2. narrate_rows — one narrate call per narratable lane; phrase + narrator_ok
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_narrate_rows_one_call_per_narratable_lane(monkeypatch):
    """Each lane with digest_text gets exactly one narrate call; phrase set."""
    calls: list[tuple[str, str]] = []

    async def fake_narrate(client, base_url, lane, digest_text, *, timeout_s):
        calls.append((lane, digest_text))
        return f"phrased {lane}"

    monkeypatch.setattr(scheduler_mod, "narrate", fake_narrate)

    rows = {
        "A": _row("A", digest_text="digest-A"),
        "B": _row("B", digest_text="digest-B"),
    }
    runtime = FakeRuntime(ready=True)

    await narrate_rows(rows, runtime)

    assert len(calls) == 2
    assert {c[0] for c in calls} == {"LANE-A", "LANE-B"}
    for short in ("A", "B"):
        assert rows[short].narrator_ok is True
        assert rows[short].now["phrase"] == f"phrased LANE-{short}"


@pytest.mark.asyncio
async def test_narrate_rows_skips_deterministic_only(monkeypatch):
    """Rows with digest_text None get NO narrate call and stay narrator_ok False."""
    calls: list[str] = []

    async def fake_narrate(client, base_url, lane, digest_text, *, timeout_s):
        calls.append(lane)
        return "phrased"

    monkeypatch.setattr(scheduler_mod, "narrate", fake_narrate)

    rows = {
        "A": _row("A", digest_text="digest-A"),
        "B": _row("B", digest_text=None),  # deterministic-only
    }
    runtime = FakeRuntime(ready=True)

    await narrate_rows(rows, runtime)

    assert calls == ["LANE-A"]  # B never narrated
    assert rows["A"].narrator_ok is True
    assert rows["B"].narrator_ok is False
    assert rows["B"].now["phrase"] is None


@pytest.mark.asyncio
async def test_narrate_rows_runtime_not_ready_skips_all(monkeypatch):
    """When runtime.is_ready() is False, no narrate call happens at all."""
    calls: list[str] = []

    async def fake_narrate(client, base_url, lane, digest_text, *, timeout_s):
        calls.append(lane)
        return "phrased"

    monkeypatch.setattr(scheduler_mod, "narrate", fake_narrate)

    rows = {"A": _row("A", digest_text="digest-A")}
    runtime = FakeRuntime(ready=False)

    await narrate_rows(rows, runtime)

    assert calls == []
    assert rows["A"].narrator_ok is False
    assert rows["A"].now["phrase"] is None


@pytest.mark.asyncio
async def test_narrate_rows_none_result_leaves_phrase_none(monkeypatch):
    """A None narrate result => phrase None, narrator_ok False (narrator down)."""

    async def fake_narrate(client, base_url, lane, digest_text, *, timeout_s):
        return None

    monkeypatch.setattr(scheduler_mod, "narrate", fake_narrate)

    rows = {"A": _row("A", digest_text="digest-A")}
    runtime = FakeRuntime(ready=True)

    await narrate_rows(rows, runtime)

    assert rows["A"].narrator_ok is False
    assert rows["A"].now["phrase"] is None


@pytest.mark.asyncio
async def test_narrate_rows_one_slow_lane_does_not_break_others(monkeypatch):
    """A raising lane must not prevent other lanes from being narrated."""

    async def fake_narrate(client, base_url, lane, digest_text, *, timeout_s):
        if lane == "LANE-A":
            raise RuntimeError("boom")
        return f"phrased {lane}"

    monkeypatch.setattr(scheduler_mod, "narrate", fake_narrate)

    rows = {
        "A": _row("A", digest_text="digest-A"),
        "B": _row("B", digest_text="digest-B"),
    }
    runtime = FakeRuntime(ready=True)

    await narrate_rows(rows, runtime)

    # A failed: no phrase, narrator_ok False. B still succeeded.
    assert rows["A"].narrator_ok is False
    assert rows["A"].now["phrase"] is None
    assert rows["B"].narrator_ok is True
    assert rows["B"].now["phrase"] == "phrased LANE-B"


@pytest.mark.asyncio
async def test_narrate_rows_phrase_skipped_when_now_none(monkeypatch):
    """A narratable row with now=None still narrates but never sets phrase."""
    calls: list[str] = []

    async def fake_narrate(client, base_url, lane, digest_text, *, timeout_s):
        calls.append(lane)
        return "phrased"

    monkeypatch.setattr(scheduler_mod, "narrate", fake_narrate)

    rows = {"A": _row("A", with_now=False, digest_text="digest-A")}
    runtime = FakeRuntime(ready=True)

    await narrate_rows(rows, runtime)

    assert calls == ["LANE-A"]
    assert rows["A"].now is None
    # narrator_ok still flips True on a successful result even without now.
    assert rows["A"].narrator_ok is True


# ---------------------------------------------------------------------------
# 2b. narrate_rows — Last column (OQ1): a SEPARATE single-phrase call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_narrate_rows_narrates_both_now_and_last(monkeypatch):
    """A row with a closed last + digest + ready → BOTH Now and Last narrated."""
    now_calls: list[str] = []
    last_calls: list[tuple[str, str]] = []

    async def fake_narrate(client, base_url, lane, digest_text, *, timeout_s):
        now_calls.append(lane)
        return f"now {lane}"

    async def fake_narrate_last(
        client, base_url, lane, last_task_desc, digest_text, *, timeout_s
    ):
        last_calls.append((lane, last_task_desc))
        return f"last {lane}"

    monkeypatch.setattr(scheduler_mod, "narrate", fake_narrate)
    monkeypatch.setattr(scheduler_mod, "narrate_last", fake_narrate_last)

    rows = {"A": _row("A", with_last=True, digest_text="digest-A")}
    runtime = FakeRuntime(ready=True)

    await narrate_rows(rows, runtime)

    assert now_calls == ["LANE-A"]
    assert last_calls == [("LANE-A", "done A")]  # closed-task desc passed through
    assert rows["A"].now["phrase"] == "now LANE-A"
    assert rows["A"].last["phrase"] == "last LANE-A"
    assert rows["A"].narrator_ok is True


@pytest.mark.asyncio
async def test_narrate_rows_last_not_narrated_without_closed_last(monkeypatch):
    """No closed last task → Last is never narrated (Now still is)."""
    last_calls: list[str] = []

    async def fake_narrate(client, base_url, lane, digest_text, *, timeout_s):
        return f"now {lane}"

    async def fake_narrate_last(
        client, base_url, lane, last_task_desc, digest_text, *, timeout_s
    ):
        last_calls.append(lane)
        return "last"

    monkeypatch.setattr(scheduler_mod, "narrate", fake_narrate)
    monkeypatch.setattr(scheduler_mod, "narrate_last", fake_narrate_last)

    rows = {"A": _row("A", with_last=False, digest_text="digest-A")}
    runtime = FakeRuntime(ready=True)

    await narrate_rows(rows, runtime)

    assert last_calls == []  # no last → no Last call
    assert rows["A"].now["phrase"] == "now LANE-A"


@pytest.mark.asyncio
async def test_narrate_rows_last_not_narrated_without_digest(monkeypatch):
    """Deterministic-only row (digest_text None) → neither Now nor Last narrated."""
    now_calls: list[str] = []
    last_calls: list[str] = []

    async def fake_narrate(client, base_url, lane, digest_text, *, timeout_s):
        now_calls.append(lane)
        return "now"

    async def fake_narrate_last(
        client, base_url, lane, last_task_desc, digest_text, *, timeout_s
    ):
        last_calls.append(lane)
        return "last"

    monkeypatch.setattr(scheduler_mod, "narrate", fake_narrate)
    monkeypatch.setattr(scheduler_mod, "narrate_last", fake_narrate_last)

    rows = {"A": _row("A", with_last=True, digest_text=None)}
    runtime = FakeRuntime(ready=True)

    await narrate_rows(rows, runtime)

    assert now_calls == []
    assert last_calls == []
    assert rows["A"].last["phrase"] is None


@pytest.mark.asyncio
async def test_narrate_rows_last_not_narrated_when_not_ready(monkeypatch):
    """runtime not ready → no Now and no Last calls; last phrase stays None."""
    last_calls: list[str] = []

    async def fake_narrate(client, base_url, lane, digest_text, *, timeout_s):
        return "now"

    async def fake_narrate_last(
        client, base_url, lane, last_task_desc, digest_text, *, timeout_s
    ):
        last_calls.append(lane)
        return "last"

    monkeypatch.setattr(scheduler_mod, "narrate", fake_narrate)
    monkeypatch.setattr(scheduler_mod, "narrate_last", fake_narrate_last)

    rows = {"A": _row("A", with_last=True, digest_text="digest-A")}
    runtime = FakeRuntime(ready=False)

    await narrate_rows(rows, runtime)

    assert last_calls == []
    assert rows["A"].last["phrase"] is None


@pytest.mark.asyncio
async def test_narrate_rows_last_failure_leaves_phrase_none_now_unaffected(monkeypatch):
    """A Last failure (exception) must not affect Now or narrator_ok."""

    async def fake_narrate(client, base_url, lane, digest_text, *, timeout_s):
        return f"now {lane}"

    async def fake_narrate_last(
        client, base_url, lane, last_task_desc, digest_text, *, timeout_s
    ):
        raise RuntimeError("last boom")

    monkeypatch.setattr(scheduler_mod, "narrate", fake_narrate)
    monkeypatch.setattr(scheduler_mod, "narrate_last", fake_narrate_last)

    rows = {"A": _row("A", with_last=True, digest_text="digest-A")}
    runtime = FakeRuntime(ready=True)

    await narrate_rows(rows, runtime)

    # Last failed → phrase stays None (deterministic desc remains the fallback).
    assert rows["A"].last["phrase"] is None
    # Now unaffected, narrator_ok still True from the successful Now call.
    assert rows["A"].now["phrase"] == "now LANE-A"
    assert rows["A"].narrator_ok is True


@pytest.mark.asyncio
async def test_narrate_rows_last_none_result_leaves_phrase_none(monkeypatch):
    """A None Last result leaves last['phrase'] None without raising."""

    async def fake_narrate(client, base_url, lane, digest_text, *, timeout_s):
        return f"now {lane}"

    async def fake_narrate_last(
        client, base_url, lane, last_task_desc, digest_text, *, timeout_s
    ):
        return None

    monkeypatch.setattr(scheduler_mod, "narrate", fake_narrate)
    monkeypatch.setattr(scheduler_mod, "narrate_last", fake_narrate_last)

    rows = {"A": _row("A", with_last=True, digest_text="digest-A")}
    runtime = FakeRuntime(ready=True)

    await narrate_rows(rows, runtime)

    assert rows["A"].last["phrase"] is None
    assert rows["A"].now["phrase"] == "now LANE-A"


@pytest.mark.asyncio
async def test_narrate_rows_now_and_last_run_concurrently(monkeypatch):
    """Now + Last across lanes are gathered concurrently (no serialization).

    Each fake call sleeps; if the calls were serialized the total would be ~N *
    delay. We assert the wall time is well under the serial sum, proving a single
    concurrent gather over Now-per-lane AND Last-per-lane.
    """
    import time

    delay = 0.05

    async def fake_narrate(client, base_url, lane, digest_text, *, timeout_s):
        await asyncio.sleep(delay)
        return f"now {lane}"

    async def fake_narrate_last(
        client, base_url, lane, last_task_desc, digest_text, *, timeout_s
    ):
        await asyncio.sleep(delay)
        return f"last {lane}"

    monkeypatch.setattr(scheduler_mod, "narrate", fake_narrate)
    monkeypatch.setattr(scheduler_mod, "narrate_last", fake_narrate_last)

    rows = {
        "A": _row("A", with_last=True, digest_text="d"),
        "B": _row("B", with_last=True, digest_text="d"),
    }
    runtime = FakeRuntime(ready=True)

    t0 = time.monotonic()
    await narrate_rows(rows, runtime)
    elapsed = time.monotonic() - t0

    # 4 calls (2 Now + 2 Last). Serial would be ~4*delay=0.2s; concurrent ~delay.
    assert elapsed < delay * 3, f"calls not concurrent (elapsed {elapsed:.3f}s)"
    assert rows["A"].last["phrase"] == "last LANE-A"
    assert rows["B"].now["phrase"] == "now LANE-B"


# ---------------------------------------------------------------------------
# 3. narrator_tick — builds rows, narrates, updates cache, publishes snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_narrator_tick_populates_cache_and_publishes(monkeypatch):
    """narrator_tick fills cache[short]=row.to_dict() and publishes a snapshot."""

    async def fake_narrate(client, base_url, lane, digest_text, *, timeout_s):
        return f"phrased {lane}"

    monkeypatch.setattr(scheduler_mod, "narrate", fake_narrate)

    hub = NarrativeHub()
    q = hub.subscribe()
    runtime = FakeRuntime(ready=True)
    cache: dict = {}
    counter = [0]
    build_rows = _make_build_rows(
        {"A": _row("A", digest_text="d"), "B": _row("B", digest_text=None)}, counter
    )

    await narrator_tick(hub=hub, runtime=runtime, cache=cache, build_rows=build_rows)

    assert counter[0] == 1
    assert set(cache.keys()) == {"A", "B"}
    # to_dict omits digest_text
    assert "digest_text" not in cache["A"]
    assert cache["A"]["narrator_ok"] is True
    assert cache["A"]["now"]["phrase"] == "phrased LANE-A"
    assert cache["B"]["narrator_ok"] is False

    payload = q.get_nowait()
    assert "lanes" in payload
    assert payload["lanes"]["A"]["now"]["phrase"] == "phrased LANE-A"
    # Snapshot is a copy, not the live cache object.
    assert payload["lanes"] is not cache

    hub.unsubscribe(q)


# ---------------------------------------------------------------------------
# 4. run_narrator_scheduler — paused at 0 subscribers, resumes on subscribe
# ---------------------------------------------------------------------------


async def _wait_until(predicate, *, timeout_s: float = 1.0, step_s: float = 0.005):
    """Poll predicate() until truthy or timeout; returns bool result."""
    loops = max(1, int(timeout_s / step_s))
    for _ in range(loops):
        if predicate():
            return True
        await asyncio.sleep(step_s)
    return predicate()


@pytest.mark.asyncio
async def test_scheduler_paused_at_zero_subscribers_then_resumes(monkeypatch):
    """With 0 subscribers no work happens; subscribe() triggers a tick."""

    async def fake_narrate(client, base_url, lane, digest_text, *, timeout_s):
        return f"phrased {lane}"

    monkeypatch.setattr(scheduler_mod, "narrate", fake_narrate)

    hub = NarrativeHub()
    runtime = FakeRuntime(ready=True)
    cache: dict = {}
    counter = [0]
    build_rows = _make_build_rows({"A": _row("A", digest_text="d")}, counter)
    stop_event = asyncio.Event()

    task = asyncio.create_task(
        run_narrator_scheduler(
            hub=hub,
            runtime=runtime,
            cache=cache,
            build_rows=build_rows,
            interval_s=0.02,
            stop_event=stop_event,
        )
    )
    try:
        # No subscribers: give the loop a few interval windows; nothing happens.
        await asyncio.sleep(0.1)
        assert counter[0] == 0, "build_rows must NOT run with 0 subscribers"
        assert cache == {}

        # Subscribe (0->1 sets tick_now): a tick must fire promptly.
        q = hub.subscribe()
        fired = await _wait_until(lambda: counter[0] >= 1)
        assert fired, "a tick must fire after first subscribe"
        assert "A" in cache
        payload = await asyncio.wait_for(q.get(), timeout=1.0)
        assert payload["lanes"]["A"]["now"]["phrase"] == "phrased LANE-A"
        hub.unsubscribe(q)
    finally:
        stop_event.set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_scheduler_narrator_down_deterministic_still_published(monkeypatch):
    """Narrator down (None result) => narrator_ok False / no phrase, but the
    deterministic fields + cache are still populated and published."""

    async def fake_narrate(client, base_url, lane, digest_text, *, timeout_s):
        return None

    monkeypatch.setattr(scheduler_mod, "narrate", fake_narrate)

    hub = NarrativeHub()
    runtime = FakeRuntime(ready=True)
    cache: dict = {}
    counter = [0]
    build_rows = _make_build_rows({"A": _row("A", digest_text="d")}, counter)
    stop_event = asyncio.Event()

    task = asyncio.create_task(
        run_narrator_scheduler(
            hub=hub,
            runtime=runtime,
            cache=cache,
            build_rows=build_rows,
            interval_s=0.02,
            stop_event=stop_event,
        )
    )
    try:
        q = hub.subscribe()
        payload = await asyncio.wait_for(q.get(), timeout=1.0)
        lane = payload["lanes"]["A"]
        assert lane["narrator_ok"] is False
        assert lane["now"]["phrase"] is None
        # Deterministic fields intact.
        assert lane["goal"] == "goal A"
        assert lane["state"] == "claimed"
        assert lane["tokens"] == 123
        assert cache["A"]["narrator_ok"] is False
        hub.unsubscribe(q)
    finally:
        stop_event.set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_scheduler_bounded_ticks_no_hot_loop(monkeypatch):
    """CV-7: with one persistent subscriber the loop ticks ~window/interval
    times, not hundreds — proving tick_now is cleared each iteration."""

    async def fake_narrate(client, base_url, lane, digest_text, *, timeout_s):
        return f"phrased {lane}"

    monkeypatch.setattr(scheduler_mod, "narrate", fake_narrate)

    hub = NarrativeHub()
    runtime = FakeRuntime(ready=True)
    cache: dict = {}
    counter = [0]
    build_rows = _make_build_rows({"A": _row("A", digest_text="d")}, counter)
    stop_event = asyncio.Event()
    interval_s = 0.05
    window_s = 0.5

    q = hub.subscribe()  # persistent subscriber for the whole window
    task = asyncio.create_task(
        run_narrator_scheduler(
            hub=hub,
            runtime=runtime,
            cache=cache,
            build_rows=build_rows,
            interval_s=interval_s,
            stop_event=stop_event,
        )
    )
    try:
        await asyncio.sleep(window_s)
        ticks = counter[0]
        # Expected ~ window/interval = 10. Generous upper bound rules out a hot
        # loop (which would be hundreds/thousands in 0.5s).
        assert 1 <= ticks <= 30, f"unexpected tick count {ticks} (hot loop?)"
    finally:
        hub.unsubscribe(q)
        stop_event.set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_scheduler_cancellation_clean(monkeypatch):
    """Cancelling the scheduler task propagates CancelledError (no swallow)."""

    async def fake_narrate(client, base_url, lane, digest_text, *, timeout_s):
        return "phrased"

    monkeypatch.setattr(scheduler_mod, "narrate", fake_narrate)

    hub = NarrativeHub()
    runtime = FakeRuntime(ready=True)
    cache: dict = {}
    counter = [0]
    build_rows = _make_build_rows({"A": _row("A", digest_text="d")}, counter)
    stop_event = asyncio.Event()

    task = asyncio.create_task(
        run_narrator_scheduler(
            hub=hub,
            runtime=runtime,
            cache=cache,
            build_rows=build_rows,
            interval_s=10.0,  # long: prove cancel is prompt regardless
            stop_event=stop_event,
        )
    )
    await asyncio.sleep(0.02)  # let it reach the wait
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
