"""NarrativeHub — per-subscriber fan-out queue registry for narrative payloads.

The hub is the passive pub/sub primitive for the summary board's SSE stream.
Endpoints subscribe to receive narrative payloads; the per-lane scheduler
(Task 2.3) calls ``publish()`` to fan out a fresh payload to every connected
client.

Design mirrors ``ActivityWall`` in ``megalodon_ui.activity_wall``:

- Per-subscriber ``asyncio.Queue(maxsize=100)``.
- Overflow drops the oldest item with a WARNING log then enqueues the new
  payload (fast publisher, slow consumer policy).
- ``subscriber_count`` is a plain ``int`` property kept in sync by
  ``subscribe`` / ``unsubscribe``.

Immediate-tick event (F4 / CV-7)
---------------------------------
``tick_now`` is an ``asyncio.Event`` exposed as a public attribute.  When
``subscribe()`` takes the count from 0 to 1, the hub **sets** the event so
the scheduler wakes up immediately and pushes a first payload without waiting
for the next scheduled tick.  The hub never clears the event — that is
exclusively the scheduler's responsibility.

Usage (from server.py lifespan)
--------------------------------
    hub = NarrativeHub()
    app.state.narrative_hub = hub
    # Scheduler calls hub.publish(payload) on its cadence.
    # Endpoint does q = hub.subscribe(); ... hub.unsubscribe(q).
"""

from __future__ import annotations

import asyncio
import logging

_log = logging.getLogger(__name__)

SUBSCRIBER_QUEUE_MAXLEN: int = 100


class NarrativeHub:
    """Fan-out registry for per-lane narrative payloads.

    Attributes:
        tick_now: asyncio.Event set when the first subscriber connects
            (0→1 transition).  The scheduler waits on this event and clears
            it itself; the hub only sets it.
    """

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue] = []
        self._count: int = 0
        self.tick_now: asyncio.Event = asyncio.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def subscriber_count(self) -> int:
        """Current number of active subscriber queues."""
        return self._count

    def subscribe(self) -> asyncio.Queue:
        """Register a new subscriber and return its per-connection queue.

        If this call takes the count from 0 to 1, ``tick_now`` is set so the
        scheduler can wake up and push an immediate payload (F4).

        Returns:
            A fresh ``asyncio.Queue`` bound to this subscriber.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_MAXLEN)
        self._subscribers.append(q)
        self._count += 1
        if self._count == 1:
            self.tick_now.set()
        return q

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Remove a subscriber queue (called on client disconnect).

        Args:
            queue: The queue previously returned by ``subscribe()``.
        """
        try:
            self._subscribers.remove(queue)
        except ValueError:
            pass
        else:
            self._count -= 1

    def publish(self, payload: dict) -> None:
        """Fan-out *payload* to every active subscriber queue.

        If a subscriber's queue is full, the oldest item is dropped and the
        new payload is enqueued, with a WARNING log.  This matches the
        backpressure policy of ``ActivityWall._fan_out``.

        Args:
            payload: Narrative payload dict to broadcast.
        """
        for q in list(self._subscribers):
            if q.full():
                try:
                    q.get_nowait()  # drop oldest
                except asyncio.QueueEmpty:
                    pass
                _log.warning(
                    "narrative-hub subscriber queue full — dropped oldest payload"
                )
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # Race between the get_nowait above and another coroutine;
                # drop silently (same policy as ActivityWall).
                pass
