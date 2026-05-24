"""Per-lane narrative scheduler for the summary board.

A watcher-gated loop that, on each tick:

1. Builds deterministic lane rows via an injected ``build_rows`` coroutine.
2. Asks the narrator to phrase the "Now" line for each *narratable* lane and,
   via a separate single-phrase call, the "Last" line for lanes with a closed
   task — all concurrently (lanes with a transcript digest, when ready).
3. Updates a shared cache (``cache[short] = row.to_dict()``).
4. Publishes a snapshot frame to the :class:`NarrativeHub`.

Everything is dependency-injected (hub, runtime, cache, ``build_rows``,
``stop_event``) so the loop is unit-testable without a server or a live model.

Gating contract (load-bearing):

- **CV-7 — clear tick_now FIRST.** ``hub.tick_now`` is set by the hub on the
  0→1 subscribe transition; the scheduler is the ONLY thing that clears it,
  and it does so immediately after the per-tick wait, before any work. A
  set-and-never-cleared event would turn the gated loop into a continuous
  hot loop.
- **Watcher-gate.** With zero subscribers the tick is skipped entirely (no
  rows built, no narrate calls).
- **Deterministic-only rows are not narrated.** A row whose ``digest_text`` is
  None has no transcript (non-Claude harness, missing session, etc.) — it is
  published with its deterministic fields and ``narrator_ok=False``.

The loop is cancellation-clean: ``CancelledError`` propagates. Teardown (the
lifespan, Task 4.1) cancels+awaits this loop and then calls ``runtime.stop()``
— this module never calls ``runtime.stop()`` itself.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from .board_state import LaneRow
from .client import narrate, narrate_last
from .hub import NarrativeHub
from .runtime import NarratorRuntime

_log = logging.getLogger(__name__)

_DEFAULT_INTERVAL_S = 30.0
_MIN_INTERVAL_S = 15.0
_MAX_INTERVAL_S = 120.0

BuildRows = Callable[[], Awaitable[dict[str, LaneRow]]]


def clamp_interval_s(raw: float | None) -> float:
    """Clamp the configured narrate interval to ``[15, 120]`` seconds.

    The caller reads ``MEGALODON_NARRATOR_INTERVAL_S`` at lifespan start (per
    the project env idiom) and passes it here; this helper only clamps. A
    falsy value (``None`` or ``0``) defaults to 30 seconds.

    Args:
        raw: Raw interval seconds, or None/0 to request the default.

    Returns:
        The clamped interval in seconds.
    """
    value = float(raw) if raw else _DEFAULT_INTERVAL_S
    return max(_MIN_INTERVAL_S, min(_MAX_INTERVAL_S, value))


async def narrate_rows(rows: dict[str, LaneRow], runtime: NarratorRuntime) -> None:
    """Narrate the Now AND Last phrases concurrently, mutating ``rows`` in place.

    A lane's "Now" is narrated when it has a transcript (``digest_text is not
    None``) AND the runtime is ready. Independently, a lane's "Last" is narrated
    via a SEPARATE single-phrase call (OQ1) when it has BOTH a closed ``last``
    task AND a transcript (``digest_text is not None``) AND the runtime is ready.

    ALL calls — Now-per-lane and Last-per-lane — run concurrently via a single
    :func:`asyncio.gather` with ``return_exceptions=True`` so one slow or failing
    call never delays or breaks the others (a Last failure cannot affect Now or
    any other lane). Each call carries the per-call timeout. On a non-None Now
    result the row's ``now["phrase"]`` is set (only if ``now`` exists) and
    ``narrator_ok`` flips True. On a non-None Last result the row's
    ``last["phrase"]`` is set. On None / timeout / exception the relevant phrase
    stays None (the deterministic ``desc`` remains the fallback) and
    ``narrator_ok`` is unaffected by the Last call. Deterministic-only rows and
    every row when the runtime is not ready are left untouched.

    Args:
        rows: Mapping of lane short code to :class:`LaneRow`, mutated in place.
        runtime: Narrator runtime supplying the client, base URL, timeout and
            readiness.
    """
    if not runtime.is_ready():
        return

    async def _now(row: LaneRow) -> None:
        # digest_text is non-None by construction of the task list below.
        result = await narrate(
            runtime.client,
            runtime.base_url,
            row.lane_name,
            row.digest_text,
            timeout_s=runtime.narrate_timeout_s,
        )
        if result is not None:
            if row.now is not None:
                row.now["phrase"] = result
            row.narrator_ok = True

    async def _last(row: LaneRow) -> None:
        # last + digest_text are non-None by construction of the task list below.
        result = await narrate_last(
            runtime.client,
            runtime.base_url,
            row.lane_name,
            row.last["desc"],
            row.digest_text,
            timeout_s=runtime.narrate_timeout_s,
        )
        if result is not None:
            row.last["phrase"] = result

    # Build the full set of independent calls. A row contributes a Now call iff
    # it has a transcript; it ALSO contributes a Last call iff it has a closed
    # last task. Both are gathered together so the tick stays concurrent+bounded.
    coros: list = []
    labels: list[tuple[str, str]] = []  # (lane, "now"|"last") parallel to coros
    for row in rows.values():
        if row.digest_text is None:
            continue
        coros.append(_now(row))
        labels.append((row.lane, "now"))
        if row.last is not None:
            coros.append(_last(row))
            labels.append((row.lane, "last"))

    if not coros:
        return

    # return_exceptions=True: a per-call failure is isolated; the gather still
    # resolves every other call. Failed calls simply leave their phrase None.
    results = await asyncio.gather(*coros, return_exceptions=True)
    for (lane, kind), res in zip(labels, results):
        if isinstance(res, Exception):
            _log.debug("narrator: lane %s %s narrate failed: %s", lane, kind, res)


async def narrator_tick(
    *,
    hub: NarrativeHub,
    runtime: NarratorRuntime,
    cache: dict,
    build_rows: BuildRows,
) -> None:
    """Run one narrate tick: build rows, narrate, update cache, publish.

    Args:
        hub: The narrative hub to publish the snapshot frame to.
        runtime: Narrator runtime (readiness + client/url/timeout).
        cache: Shared cache mutated so ``cache[short] = row.to_dict()`` for
            every built row.
        build_rows: Injected coroutine returning the deterministic lane rows.
    """
    rows = await build_rows()
    await narrate_rows(rows, runtime)
    for short, row in rows.items():
        cache[short] = row.to_dict()
    # Publish a snapshot copy of the cache (decoupled from later mutations).
    hub.publish({"lanes": dict(cache)})


async def run_narrator_scheduler(
    *,
    hub: NarrativeHub,
    runtime: NarratorRuntime,
    cache: dict,
    build_rows: BuildRows,
    interval_s: float,
    stop_event: asyncio.Event,
) -> None:
    """Run the watcher-gated narrate loop until ``stop_event`` is set.

    Each iteration:

    1. Waits for EITHER ``interval_s`` to elapse, ``hub.tick_now`` to be set,
       or ``stop_event`` to be set (whichever comes first) so an immediate-tick
       request wakes the loop and a stop is prompt.
    2. Clears ``hub.tick_now`` immediately (CV-7) before doing any work.
    3. Skips the tick entirely if there are no subscribers (watcher-gate).
    4. Otherwise runs :func:`narrator_tick`.

    Cancellation-clean: :class:`asyncio.CancelledError` propagates untouched.
    Does NOT call ``runtime.stop()`` — that is the lifespan's responsibility.

    Args:
        hub: Narrative hub (subscriber count + tick_now + publish).
        runtime: Narrator runtime passed through to each tick.
        cache: Shared cache mutated by each tick.
        build_rows: Injected coroutine returning deterministic lane rows.
        interval_s: Seconds between scheduled ticks (already clamped).
        stop_event: Set by the lifespan to request a clean shutdown.
    """
    while not stop_event.is_set():
        await _wait_for_tick(hub, stop_event, interval_s)

        # CV-7: clear BEFORE any work so a single set never causes a hot loop.
        hub.tick_now.clear()

        if stop_event.is_set():
            break

        # Watcher-gate: no subscribers => no rows, no narrate, no publish.
        if hub.subscriber_count == 0:
            continue

        await narrator_tick(
            hub=hub, runtime=runtime, cache=cache, build_rows=build_rows
        )


async def _wait_for_tick(
    hub: NarrativeHub, stop_event: asyncio.Event, interval_s: float
) -> None:
    """Block until ``tick_now`` or ``stop_event`` fires, or ``interval_s`` elapses.

    Races the two events so an immediate-tick request and a shutdown both wake
    the loop promptly, while a quiet period still wakes on the interval. Pending
    waiter tasks are cancelled+awaited before returning so the loop leaves no
    dangling tasks (clean under ``-W error``).

    Args:
        hub: Narrative hub exposing ``tick_now``.
        stop_event: Shutdown event.
        interval_s: Maximum seconds to wait before returning.
    """
    tick_task = asyncio.ensure_future(hub.tick_now.wait())
    stop_task = asyncio.ensure_future(stop_event.wait())
    try:
        await asyncio.wait(
            {tick_task, stop_task},
            timeout=interval_s,
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        for task in (tick_task, stop_task):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
