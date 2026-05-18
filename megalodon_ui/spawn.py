"""FleetSpawner: orchestrates per-mission tmux session creation, cancellation cleanup, and orphan-session purge."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from megalodon_ui import tmux
from megalodon_ui._v92_constants import INITIAL_PANE_COLS, INITIAL_PANE_ROWS
from megalodon_ui.harnesses.base import HarnessAdapter
from megalodon_ui.mission_config.schema import MissionConfig

_log = logging.getLogger(__name__)


class SpawnError(RuntimeError):
    """Raised when tmux new-session fails for a lane."""

    pass


@dataclass
class LaneSession:
    """Runtime state for a single fleet lane's tmux session."""

    lane: str
    name: str
    cwd: Path
    argv: list[str]
    env: dict[str, str]
    stream_log: Path
    session_id: str | None = None
    running: bool = False
    exited_rc: int | None = None
    pane_dead_checked_at: float = 0.0
    # subscribers_lock is allocated here as a forward-hook for v9.2 P4 SSE
    # fan-out (SR-3); not yet acquired in P1. asyncio.Lock binds to the loop
    # in use at instantiation time — do not transfer LaneSession instances
    # across event loops.
    subscribers_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class FleetSpawner:
    """Spawn, track, and clean up per-lane tmux sessions for a mission fleet.

    Parameters
    ----------
    mission_dir:
        Absolute path to the mission directory.
    mission_config:
        Loaded and validated MissionConfig for this mission.
    adapter_resolver:
        Callable that maps a harness CLI name (e.g. ``"claude"``) to its
        HarnessAdapter instance.
    socket:
        Path to the per-mission tmux socket (``<mission_dir>/.fleet/tmux.sock``).
    """

    def __init__(
        self,
        mission_dir: Path,
        mission_config: MissionConfig,
        adapter_resolver: Callable[[str], HarnessAdapter],
        socket: Path,
    ) -> None:
        self.mission_dir = mission_dir
        self.mission_config = mission_config
        self.adapter_resolver = adapter_resolver
        self.socket = socket
        self.sessions: dict[str, LaneSession] = {}

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def start_all(self, prompt_override: str | None = None) -> None:
        """Spawn all configured lanes in parallel, with orphan purge and reattach.

        Steps:
        1. Orphan purge: kill any ``lane-*`` sessions on the socket that carry
           the ``MEGALODON_FLEET_OWNED=1`` marker and are NOT part of the
           current config. Sessions without the marker are left alone.
        2. Reattach: if a session for a configured lane already exists with the
           marker, populate ``self.sessions`` and skip spawning that lane.
        3. Build ``LaneSession`` objects for each lane that needs spawning.
        4. Parallel-spawn via ``asyncio.gather``.
        5. Cancellation cleanup (OW-3): on ``CancelledError`` or any
           ``BaseException``, kill every session that successfully spawned in
           this invocation before re-raising.
        """
        # ---- 1. Orphan purge + 2. reattach --------------------------------
        existing = await tmux.list_sessions(self.socket)
        configured_names = {f"lane-{lane.short}" for lane in self.mission_config.lanes}
        reattached: set[str] = set()

        for session_name in existing:
            owned = await self._is_fleet_owned(session_name)
            if not owned:
                continue  # operator's manual session — leave it alone
            if session_name in configured_names:
                # Reattach branch: session already running for a configured lane
                short = session_name.removeprefix("lane-")
                lane_cfg = self._lane_cfg_by_short(short)
                if lane_cfg is not None:
                    adapter = self.adapter_resolver(lane_cfg.harness.cli)
                    prompt = prompt_override if prompt_override is not None else (lane_cfg.role or "")
                    argv, env = adapter.build_argv(
                        prompt,
                        model=lane_cfg.harness.model,
                        cwd=self.mission_dir,
                    )
                    ls = LaneSession(
                        lane=short,
                        name=session_name,
                        cwd=self.mission_dir,
                        argv=argv,
                        env=env,
                        stream_log=self.mission_dir / ".fleet" / f"{short}.stream.log",
                        running=True,
                    )
                    self.sessions[short] = ls
                    reattached.add(short)
                    _log.info("reattached existing session %s", session_name)
                    await tmux.display_message_pane_pipe(self.socket, session_name)
            else:
                # Orphan: owned but not in current config -> kill
                _log.warning("purging orphan fleet session %s", session_name)
                await tmux.kill_session(self.socket, session_name)

        # ---- 3. Build LaneSession objects for lanes needing spawn ----------
        to_spawn: list[LaneSession] = []
        for lane_cfg in self.mission_config.lanes:
            short = lane_cfg.short
            assert short is not None  # guaranteed by schema validator
            if short in reattached:
                continue
            adapter = self.adapter_resolver(lane_cfg.harness.cli)
            prompt = prompt_override if prompt_override is not None else (lane_cfg.role or "")
            argv, env = adapter.build_argv(
                prompt,
                model=lane_cfg.harness.model,
                cwd=self.mission_dir,
            )
            session_name = f"lane-{short}"
            ls = LaneSession(
                lane=short,
                name=session_name,
                cwd=self.mission_dir,
                argv=argv,
                env=env,
                stream_log=self.mission_dir / ".fleet" / f"{short}.stream.log",
            )
            self.sessions[short] = ls
            to_spawn.append(ls)

        if not to_spawn:
            return

        # ---- 4 + 5. Parallel spawn with cancellation cleanup (OW-3) -------
        # Catch BaseException so both CancelledError and SpawnError trigger the
        # cleanup path; CancelledError is itself a BaseException subclass so a
        # plain `except BaseException` is sufficient.
        spawned: list[LaneSession] = []
        try:
            await asyncio.gather(*(self._spawn_one(s, spawned) for s in to_spawn))
        except BaseException:
            cleanup_tasks = [
                tmux.kill_session(self.socket, s.name)
                for s in spawned
            ]
            if cleanup_tasks:
                await asyncio.gather(*cleanup_tasks, return_exceptions=True)
            raise

    async def stop_all(self) -> None:
        """Kill all running sessions in parallel."""
        tasks = [
            tmux.kill_session(self.socket, s.name)
            for s in self.sessions.values()
            if s.running
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for s in self.sessions.values():
            s.running = False

    def get(self, lane: str) -> LaneSession:
        """Return the LaneSession for the given short lane code."""
        return self.sessions[lane]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _spawn_one(self, session: LaneSession, spawned: list[LaneSession]) -> None:
        """Create one tmux session; append to spawned list on success.

        Note: new_session() runs the three-step chain new-session →
        set-option remain-on-exit → set-environment MEGALODON_FLEET_OWNED 1
        non-atomically. A concurrent FleetSpawner.start_all on the same socket
        (extremely unusual in practice) could observe the session via
        list_sessions before the env-var lands; the marker check will then
        treat it as unowned and leave it alone, which is the conservative
        choice. Documented here so a future refactor doesn't try to "fix" it
        by removing the marker check.

        TODO(P3.1): call tmux.pipe_pane(socket, name, session.stream_log)
        after rc check so the byte stream lands in .fleet/<short>.stream.log.
        Intentionally NOT wired in P1 — the stream_log path is reserved.
        """
        rc = await tmux.new_session(
            socket=self.socket,
            name=session.name,
            argv=session.argv,
            cwd=session.cwd,
            env=session.env,
            cols=INITIAL_PANE_COLS,
            rows=INITIAL_PANE_ROWS,
        )
        if rc != 0:
            raise SpawnError(f"new-session failed for {session.lane}: rc={rc}")
        session.running = True
        spawned.append(session)

    async def _is_fleet_owned(self, session_name: str) -> bool:
        """Return True if the session carries MEGALODON_FLEET_OWNED=1."""
        proc = await asyncio.create_subprocess_exec(
            "tmux",
            "-S", str(self.socket),
            "show-environment", "-t", session_name,
            "MEGALODON_FLEET_OWNED",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return False
        # tmux show-environment prints: VARNAME=value (or -VARNAME if unset)
        line = stdout.decode().strip()
        return line == "MEGALODON_FLEET_OWNED=1"

    def _lane_cfg_by_short(self, short: str):
        """Return the LaneConfig whose short code matches, or None."""
        for lane in self.mission_config.lanes:
            if lane.short == short:
                return lane
        return None
