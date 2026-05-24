"""Observed dashboard auto-open (Phase 5 / Task D4).

Replaces the old unconditional pre-uvicorn browser-open with an *observed*
auto-open that opens a tab only when no live tab reconnects after a restart.

Rationale
---------
Persistent sessions + a stable bearer token (Tasks D1–D3) mean a browser tab
left open across a server restart will silently reconnect its SSE streams to
the new process. The old behavior — blindly calling ``webbrowser.open`` before
every uvicorn boot — piled up a duplicate tab on every restart. This module
instead *observes* the summed authenticated SSE subscriber count for a short
grace window after fleet startup:

  * a live tab reconnects within the window → do NOT open (no duplicate); or
  * the window elapses with zero subscribers → no live tab → open one.

A genuinely fresh launch (nothing reconnects) still opens a tab, so first-run
observability is preserved.

Subscriber signals
------------------
The summed count is::

    app.state.narrative_hub.subscriber_count   # NarrativeHub (int property)
  + app.state.activity_wall.subscriber_count    # ActivityWall (int attr)

The default board page holds a ``narrative-stream`` EventSource, so the hub is
the primary reconnect signal. Pane-stream subscribers are NOT aggregated here
(no clean per-server total, and the board does not need it) — a documented
limitation matching the spec's "parked-page observation gap": a browser sitting
*only* on a pane page with no board/activity stream open will not be observed
as a reconnect and may trigger a spurious open. The board page is the normal
landing page, so this gap is acceptable.

Overrides
---------
  * ``--no-browser`` → never open (forces OFF).
  * ``--rotate-token`` → open immediately, skipping the observe window (the
    operator explicitly rotated the token and wants a fresh authenticated tab).

Best-effort
-----------
Any exception anywhere in the watch is logged at WARNING and swallowed — the
watch must NEVER crash the lifespan. The browser-open itself is also non-fatal
(headless host, no default browser → log and fall back to the URL already
printed to stdout / written to the URL file).

The watch is wired into the **live-branch** lifespan only (real fleet). The
test/fake lifespan branches never start it, so it does not run under tests.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import webbrowser
from typing import Awaitable, Callable

_log = logging.getLogger(__name__)

# Default grace window: observe for this many seconds before opening.
DEFAULT_OPEN_GRACE_S: float = 8.0
# Bounds for the operator-configurable grace window.
_MIN_OPEN_GRACE_S: float = 1.0
_MAX_OPEN_GRACE_S: float = 60.0
# Poll cadence inside the observe window.
DEFAULT_OPEN_POLL_S: float = 0.5


def parse_open_grace_env() -> float:
    """Read + clamp ``MEGALODON_DASHBOARD_OPEN_GRACE_S`` to ``[1, 60]`` seconds.

    Read at lifespan start (per the project env idiom). An unset, empty, or
    unparseable value yields :data:`DEFAULT_OPEN_GRACE_S`. A parseable value is
    clamped into ``[1, 60]``.

    Returns:
        The grace-window length in seconds.
    """
    raw = os.environ.get("MEGALODON_DASHBOARD_OPEN_GRACE_S")
    if not raw:
        return DEFAULT_OPEN_GRACE_S
    try:
        value = float(raw)
    except (ValueError, TypeError):
        return DEFAULT_OPEN_GRACE_S
    return max(_MIN_OPEN_GRACE_S, min(_MAX_OPEN_GRACE_S, value))


def open_dashboard_nonfatal(url: str) -> None:
    """Open *url* in the operator's browser; never raise.

    Ported from the old ``__main__._open_dashboard`` open logic (single source
    of the open behavior now). The listener socket is already bound and
    ``listen()``-ing by the time this runs, so the browser's connection is
    queued by the kernel and served the instant uvicorn accepts — no
    connection-refused race.

    A browser-launch failure (headless host, no default browser) must NEVER
    crash the caller: we log and fall back to the URL already printed to stdout
    and written to the 0600 URL file.
    """
    try:
        opened = webbrowser.open(url, new=2)
    except Exception as exc:  # noqa: BLE001 — browser launch must not be fatal
        _log.warning("Could not auto-open dashboard (%s); open manually: %s", exc, url)
        return
    if opened:
        _log.info("Opened dashboard in browser: %s", url)
    else:
        _log.warning("No browser available to auto-open; open manually: %s", url)


async def auto_open_watch(
    *,
    enabled: bool,
    force_open: bool,
    url: str,
    get_subscriber_count: Callable[[], int],
    open_fn: Callable[[str], None],
    grace_s: float,
    poll_s: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> bool:
    """Decide-and-maybe-open the dashboard by observing SSE reconnection.

    Pure, injectable decision core (no real time, no real browser required):
    inject *get_subscriber_count*, *open_fn*, and *sleep* in tests.

    Behavior:
      * ``enabled`` False → return ``False`` immediately (never open).
      * ``force_open`` True (and enabled) → ``open_fn(url)`` once, return
        ``True``, WITHOUT consulting the subscriber count or waiting.
      * else poll: check ``get_subscriber_count()`` up to
        ``ceil(grace_s / poll_s) + 1`` times across the window; if it is ever
        ``> 0`` a live tab reconnected → return ``False`` (skip); otherwise
        ``await sleep(poll_s)`` between checks. If the window elapses with no
        subscriber → ``open_fn(url)`` once, return ``True``.

    The window is driven by a bounded poll count (not wall-clock), so an
    injected no-op ``sleep`` makes the zero-subscriber path terminate instantly
    and deterministically in tests.

    ``open_fn`` is invoked inside a try/except: any exception is logged and
    swallowed (best-effort), and the function still returns its decision.
    ANY other exception is likewise swallowed (returns ``False``) so the watch
    never crashes the lifespan.

    Args:
        enabled: Master switch (``--no-browser`` → False).
        force_open: Skip the window and open immediately (``--rotate-token``).
        url: Dashboard URL (bearer embedded) to open.
        get_subscriber_count: Returns the summed authenticated SSE subscriber
            count for THIS server.
        open_fn: Opens the dashboard; expected to be non-fatal itself.
        grace_s: Observe-window length in seconds.
        poll_s: Poll cadence in seconds.
        sleep: Awaitable sleep (injected as a fake in tests).

    Returns:
        ``True`` if it opened (or attempted to open) the dashboard, else
        ``False``.
    """
    try:
        if not enabled:
            _log.info(
                "Dashboard auto-open disabled (--no-browser); open manually: %s", url
            )
            return False

        if force_open:
            _log.info(
                "Dashboard auto-open forced (--rotate-token): opening immediately."
            )
            _safe_open(open_fn, url)
            return True

        # Bounded poll count drives the window (not wall-clock), so a fake
        # no-op sleep terminates the zero-subscriber path instantly in tests.
        # +1 so we check once before sleeping and once after the final sleep.
        polls = max(1, math.ceil(grace_s / poll_s)) + 1
        for i in range(polls):
            if get_subscriber_count() > 0:
                _log.info(
                    "Live dashboard tab reconnected within grace window; "
                    "skipping auto-open."
                )
                return False
            if i < polls - 1:
                await sleep(poll_s)

        _log.info(
            "No live dashboard tab reconnected within %.1fs; auto-opening.", grace_s
        )
        _safe_open(open_fn, url)
        return True
    except Exception:  # noqa: BLE001 — the watch must NEVER crash the lifespan
        _log.warning("dashboard auto-open watch failed", exc_info=True)
        return False


def _safe_open(open_fn: Callable[[str], None], url: str) -> None:
    """Invoke *open_fn(url)*, swallowing+logging any exception (best-effort)."""
    try:
        open_fn(url)
    except Exception:  # noqa: BLE001 — browser launch must not be fatal
        _log.warning("dashboard open_fn raised; open manually: %s", url, exc_info=True)
