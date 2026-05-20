"""In-process fake FleetSpawner for browser-level acceptance tests.

The real ``FleetSpawner`` requires tmux on disk + macOS-incompatible socket
paths under ``tmp_path``. For Playwright specs that just need to verify the
dashboard's behavior against the v9.2 endpoint surface, this module provides
a deterministic, dependency-free stand-in installed by the lifespan when the
env var ``MEGALODON_FAKE_SPAWNER=1`` is set.

The fake matches the public surface of ``FleetSpawner`` that the routes touch:

* ``sessions: dict[str, LaneSession]`` — populated on construction from the
  lane list in ``MissionConfig``.
* ``subscribe(lane)`` / ``unsubscribe(lane, q)`` — same maxsize=8, drop-oldest
  semantics as the real spawner.
* ``respawn(lane, argv, env)`` — drains every subscriber queue under
  ``subscribers_lock`` and pushes ``_RESPAWN_SENTINEL`` (matches CV-12 contract).
* ``get(lane)`` — returns the LaneSession.
* ``adapter_resolver`` — same callable shape.
* ``socket`` — synthetic path so ``DELETE /api/v1/fleet`` can still unlink it.
* ``mission_dir``, ``mission_config`` — passthrough.

Plus three fake-only hooks for tests to drive byte flow without tmux:

* ``await fake_emit(lane, data: bytes)`` — fan out a byte chunk to every
  subscriber as if it came off ``pipe-pane``.
* ``set_pane_dead(lane, rc: int)`` — flip a lane to running=False+exited_rc=rc.
* ``set_pane_alive(lane)`` — flip back to running=True+exited_rc=None.

These are no-ops on the real ``FleetSpawner`` — callers should only invoke
them through the ``/api/v1/__fake__/*`` test-only endpoints (also gated by
``MEGALODON_FAKE_SPAWNER`` so production never exposes them).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable

from .mission_config.schema import MissionConfig
from .spawn import (
    LaneSession,
    TooManySubscribersError,
    _RESPAWN_SENTINEL,
)
from ._v92_constants import SSE_MAX_SUBSCRIBERS_PER_LANE


class FakeFleetSpawner:
    """In-process fake of ``FleetSpawner`` for browser-level acceptance tests.

    Constructed eagerly: every lane in ``mission_config`` materializes a
    LaneSession with ``running=True`` and an empty stream log file under
    ``<mission_dir>/.fleet/<short>.stream.log``. The lifespan replaces the
    real spawner with this instance when ``MEGALODON_FAKE_SPAWNER=1``.
    """

    def __init__(
        self,
        mission_dir: Path,
        mission_config: MissionConfig,
        adapter_resolver: Callable[[str], object],
        socket: Path,
    ) -> None:
        self.mission_dir = mission_dir
        self.mission_config = mission_config
        self.adapter_resolver = adapter_resolver
        self.socket = socket
        self.sessions: dict[str, LaneSession] = {}
        fleet = mission_dir / ".fleet"
        fleet.mkdir(parents=True, exist_ok=True)
        for lane in mission_config.lanes:
            stream_log = fleet / f"{lane.short}.stream.log"
            stream_log.touch()
            self.sessions[lane.short] = LaneSession(
                lane=lane.short,
                name=f"lane-{lane.name}",
                cwd=mission_dir,
                argv=["fake"],
                env={},
                stream_log=stream_log,
                session_id=f"fake-session-{lane.short}",
                running=True,
            )

    # ------------------------------------------------------------------
    # Public surface that the v9.2 routes depend on
    # ------------------------------------------------------------------

    def get(self, lane: str) -> LaneSession:
        return self.sessions[lane]

    async def subscribe(self, lane: str) -> asyncio.Queue[bytes]:
        session = self.sessions[lane]
        async with session.subscribers_lock:
            if len(session.subscribers) >= SSE_MAX_SUBSCRIBERS_PER_LANE:
                raise TooManySubscribersError(
                    f"lane {lane}: max {SSE_MAX_SUBSCRIBERS_PER_LANE} subscribers"
                )
            q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=8)
            session.subscribers.append(q)
            return q

    async def unsubscribe(self, lane: str, q: asyncio.Queue[bytes]) -> None:
        session = self.sessions.get(lane)
        if session is None:
            return
        async with session.subscribers_lock:
            try:
                session.subscribers.remove(q)
            except ValueError:
                pass

    async def respawn(self, lane: str, argv: list[str], env: dict[str, str]) -> None:
        session = self.sessions[lane]
        async with session.subscribers_lock:
            for q in session.subscribers:
                while not q.empty():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                try:
                    q.put_nowait(_RESPAWN_SENTINEL)
                except asyncio.QueueFull:
                    pass
        session.argv = list(argv)
        session.env = dict(env)
        session.running = True
        session.exited_rc = None

    async def stop_all(self) -> None:
        for s in self.sessions.values():
            s.running = False

    # ------------------------------------------------------------------
    # Fake-only hooks (drive byte flow / lane state from tests)
    # ------------------------------------------------------------------

    async def fake_emit(self, lane: str, data: bytes) -> None:
        """Fan out a byte chunk as if it came off ``pipe-pane``."""
        session = self.sessions[lane]
        async with session.subscribers_lock:
            for q in session.subscribers:
                if q.full():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                try:
                    q.put_nowait(data)
                except asyncio.QueueFull:
                    pass
        with session.stream_log.open("ab") as fh:
            fh.write(data)
        session.last_bytes_offset += len(data)

    def set_pane_dead(self, lane: str, rc: int) -> None:
        session = self.sessions[lane]
        session.running = False
        session.exited_rc = rc

    def set_pane_alive(self, lane: str) -> None:
        session = self.sessions[lane]
        session.running = True
        session.exited_rc = None
