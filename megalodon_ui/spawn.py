"""FleetSpawner: orchestrates per-mission tmux session creation, cancellation cleanup, and orphan-session purge."""

from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from megalodon_ui import tmux
from megalodon_ui._v92_constants import (
    INITIAL_PANE_COLS,
    INITIAL_PANE_ROWS,
    SSE_MAX_SUBSCRIBERS_PER_LANE,
    SSE_PER_SUBSCRIBER_QUEUE_MAXSIZE,
)
from megalodon_ui.governor.wiring import (
    argv_is_governed,
    governor_canary_selftest,
    governor_enabled,
    governor_kwargs,
    governor_settings_path,
    preflight_governor,
    read_governed_marker_is_valid,
    remove_governed_marker,
    write_governed_marker,
)
from megalodon_ui.harnesses.base import HarnessAdapter
from megalodon_ui.mission_config.schema import MissionConfig

_log = logging.getLogger(__name__)

# Session-id discovery polling parameters (Task 3.3 / PM-6).
# Module-level so tests can monkey-patch them without 5-second waits.
_SESSION_DISCOVERY_TIMEOUT: float = 5.0
_SESSION_DISCOVERY_INTERVAL: float = 0.1

# Respawn sentinel (CV-12 + PM-7): a single byte chunk pushed into every
# subscriber queue immediately after a successful respawn. ``\x1bc`` clears
# the terminal; the ``⟳ restarting…`` glyph (UTF-8) gives the operator a
# visible cue. ``\r\n`` separates from the new harness's first byte.
_RESPAWN_SENTINEL: bytes = b"\x1bc\xe2\x9f\xb3 restarting\xe2\x80\xa6\r\n"

# Live-REPL initial-prompt delivery (v9.3 dogfood). After spawning a lane in
# live_repl mode, wait this many seconds for the CLI's TUI to render its
# welcome banner + accept input, then tmux send-keys the initial_prompt.
# Module-level so tests can monkey-patch.
_LIVE_REPL_PROMPT_DELAY_SECONDS: float = 5.0

# Placeholder substituted in launch-<LANE>.md files at spawn time. The
# spawner generates a random agent-id per lane and writes it in, eliminating
# the need for agents to run python3 (which would otherwise trigger a
# permission prompt every session). See _bake_agent_id_in_launch_file.
_AGENT_ID_PLACEHOLDER: str = "{{AGENT_ID}}"


def _generate_agent_id() -> str:
    """Return a fresh per-lane agent identifier (``agent-XXXX`` hex form)."""
    return f"agent-{secrets.token_hex(2)}"


def _bake_agent_id_in_launch_file(launch_file: Path, agent_id: str) -> bool:
    """Replace ``{{AGENT_ID}}`` in ``launch_file`` with the resolved id.

    Idempotent: if the file already has an ``agent-XXXX`` id substituted in
    (no placeholder remaining) the file is left alone and we return False
    so the caller can preserve the prior identity across server restarts.
    Returns True if a substitution happened.
    """
    if not launch_file.exists():
        return False
    text = launch_file.read_text(encoding="utf-8")
    if _AGENT_ID_PLACEHOLDER not in text:
        return False
    new_text = text.replace(_AGENT_ID_PLACEHOLDER, agent_id)
    launch_file.write_text(new_text, encoding="utf-8")
    return True


def _snapshot_dir(d: Path | None) -> set[str]:
    """Return the set of entry names currently present in ``d``; empty if absent."""
    if d is None:
        return set()
    try:
        return {p.name for p in d.iterdir()}
    except FileNotFoundError:
        return set()


async def _discover_session_id(
    log_dir: Path | None,
    before: set[str],
    *,
    timeout: float | None = None,
    interval: float | None = None,
) -> str | None:
    """Poll ``log_dir`` for new entries diffed against ``before``.

    Returns the new entry's stem if exactly one new entry appears within
    the timeout; returns None and logs a WARNING if zero or 2+ entries
    appear (ambiguous — the caller degrades to no-resume).
    """
    if log_dir is None:
        return None
    t = timeout if timeout is not None else _SESSION_DISCOVERY_TIMEOUT
    i = interval if interval is not None else _SESSION_DISCOVERY_INTERVAL
    deadline = asyncio.get_event_loop().time() + t
    while True:
        new_entries = _snapshot_dir(log_dir) - before
        if len(new_entries) == 1:
            return Path(next(iter(new_entries))).stem
        if len(new_entries) >= 2:
            _log.warning(
                "ambiguous session-id discovery in %s: %d new entries (%s); "
                "leaving session_id=None for follow-up no-resume",
                log_dir,
                len(new_entries),
                sorted(new_entries),
            )
            return None
        if asyncio.get_event_loop().time() >= deadline:
            _log.warning(
                "session-id discovery timed out (%.1fs) in %s — no new entries",
                t,
                log_dir,
            )
            return None
        await asyncio.sleep(i)


async def _spawn_tail_subprocess(path: Path) -> asyncio.subprocess.Process:
    """Launch ``tail -c +1 -F <path>`` as an asyncio subprocess.

    Factored out so unit tests can monkey-patch the spawn helper to inject a
    canned stdout stream without a real ``tail`` process. Also keeps the
    create-subprocess call out of the producer hot loop.
    """
    create = asyncio.create_subprocess_exec
    return await create(
        "tail",
        "-c",
        "+1",
        "-F",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )


class SpawnError(RuntimeError):
    """Raised when tmux new-session fails for a lane."""

    pass


class TooManySubscribersError(RuntimeError):
    """Raised when ``subscribe`` would exceed ``SSE_MAX_SUBSCRIBERS_PER_LANE``."""

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
    # Governance PROVENANCE of the LIVE process (Task 2.5). True only when the
    # current process was spawned/respawned UNDER the governor (verified via the
    # per-lane .fleet/<short>.governed marker on reattach, NOT the rebuilt argv,
    # which lies). False == ``ungoverned`` — surfaced distinctly so an operator
    # can respawn. Distinct from the P3.2 deny-loop ``governor-blocked`` status.
    governed: bool = False
    exited_rc: int | None = None
    pane_dead_checked_at: float = 0.0
    # P4 SSE fan-out state. `subscribers_lock` serializes list mutation AND
    # producer iteration (SR-3). asyncio.Lock and asyncio.Queue both bind to
    # the loop in use at instantiation time — do not transfer LaneSession
    # instances across event loops.
    subscribers_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    subscribers: list[asyncio.Queue[bytes]] = field(default_factory=list)
    tail_task: asyncio.Task | None = None
    last_bytes_offset: int = 0
    _tail_proc: asyncio.subprocess.Process | None = None
    # Live-REPL: if set, sent via tmux send-keys after the CLI's TUI is ready.
    initial_prompt: str | None = None


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

    def _ensure_protocol_dirs(self) -> None:
        """v9.3: pre-create protocol directories so agents never need mkdir.

        The v9 protocol expects ``claims/``, ``findings/``, and ``feedback/``
        to exist at the mission root, plus ``.fleet/`` for runtime state.
        Pre-creating them at startup means agents don't trigger permission
        prompts for ``mkdir -p`` invocations on parent directories. The
        per-task ``mkdir claims/<task-id>/`` is still the atomic claim
        primitive — that one the agent MUST do.
        """
        for sub in (".fleet", "claims", "findings", "feedback"):
            (self.mission_dir / sub).mkdir(parents=True, exist_ok=True)

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
        # ---- 0. Pre-create protocol directories (v9.3) --------------------
        self._ensure_protocol_dirs()

        # ---- 0b. Governor preflight (Task 2.2) ----------------------------
        # When the governor is enabled, verify it is wirable ONCE up front so a
        # broken hook fails the whole spawn loudly here, rather than degrading
        # to a silent per-lane `claude` failure. When disabled (kill-switch),
        # skip preflight entirely — a disabled governor must not block spawn.
        _governor_on = governor_enabled(self.mission_config)
        _gov_settings: Path | None = None
        if _governor_on:
            preflight_governor(self.mission_dir)
            # Canary self-test (Task 2.3): preflight proves the hook is REACHABLE;
            # this proves it actually DENIES. Pipe the sentinel probe through the
            # run-dir shim exactly as claude will and require a canary deny. A
            # non-enforcing governor (allow / error / malformed) raises
            # GovernorCanaryError here and aborts the whole spawn LOUDLY before
            # any lane starts — converting silent non-enforcement into a loud stop.
            governor_canary_selftest(self.mission_dir)
            _gov_settings = governor_settings_path()

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
                    prompt = (
                        prompt_override
                        if prompt_override is not None
                        else (lane_cfg.role or "")
                    )
                    # Governor --settings (Task 2.2): single-source gate. Reuses
                    # the precomputed settings path (preflight already ran in
                    # step 0b); helper applies the enabled + claude-cli check.
                    # Task 3.3: approval-rules are no longer plumbed into a
                    # --allowedTools allowlist here — the governor's policy.decide
                    # reads .fleet/approval-rules.json directly as an audited
                    # allow-override, so the spawner passes nothing approval-related.
                    _gov_kw = governor_kwargs(
                        self.mission_config, lane_cfg, settings_path=_gov_settings
                    )
                    argv, env = adapter.build_argv(
                        prompt,
                        model=lane_cfg.harness.model,
                        cwd=self.mission_dir,
                        **({"live_repl": True} if lane_cfg.live_repl else {}),
                        **_gov_kw,
                    )
                    stream_log = self.mission_dir / ".fleet" / f"{short}.stream.log"
                    # Governance PROVENANCE (Task 2.5): the LIVE process is the
                    # OLD one — its regime is whatever it was BORN with, which the
                    # rebuilt `argv` (now carrying --settings via Task 2.2) does
                    # NOT prove. Derive `governed` from the per-lane spawn-time
                    # marker, verifying its fingerprint against the CURRENT
                    # governor settings. Absent/stale/mismatched ⇒ ungoverned
                    # (fail toward ungoverned). We deliberately IGNORE the argv
                    # here; it is kept as the would-be-governed template for a
                    # future operator respawn, but it must not decide governance.
                    reattach_governed = read_governed_marker_is_valid(
                        self.mission_dir, short
                    )
                    ls = LaneSession(
                        lane=short,
                        name=session_name,
                        cwd=self.mission_dir,
                        argv=argv,
                        env=env,
                        stream_log=stream_log,
                        running=True,
                        governed=reattach_governed,
                    )
                    if not reattach_governed:
                        _log.warning(
                            "reattached lane %s is UNGOVERNED (no valid spawn-time "
                            "governor marker) — its live process predates the "
                            "current governor; respawn to bring it under governance",
                            short,
                        )
                    self.sessions[short] = ls
                    reattached.add(short)
                    _log.info("reattached existing session %s", session_name)
                    # Idempotent re-pipe: only wire if no active pipe (Task 3.1).
                    pipe_active = await tmux.display_message_pane_pipe(
                        self.socket, session_name
                    )
                    if not pipe_active:
                        await tmux.pipe_pane(self.socket, session_name, stream_log)
                    # Re-establish the per-lane tail task on server restart.
                    stream_log.parent.mkdir(parents=True, exist_ok=True)
                    stream_log.touch(exist_ok=True)
                    await self._start_tail_task(ls)
            else:
                # Orphan: owned but not in current config -> kill
                _log.warning("purging orphan fleet session %s", session_name)
                await tmux.kill_session(self.socket, session_name)

        # ---- 3. Build LaneSession objects for lanes needing spawn ----------
        spawn_jobs: list[tuple[LaneSession, HarnessAdapter, set[str], Path | None]] = []
        for lane_cfg in self.mission_config.lanes:
            short = lane_cfg.short
            assert short is not None  # guaranteed by schema validator
            if short in reattached:
                continue
            # v9.3 pre-bake: if the lane has a launch-<NAME>.md with the
            # {{AGENT_ID}} placeholder, generate a fresh agent-id and write
            # it into the file. Eliminates the runtime python3 call (and the
            # permission prompt it would trigger). Idempotent — restart
            # preserves the existing identity.
            if lane_cfg.live_repl:
                launch_file = self.mission_dir / f"launch-{lane_cfg.name}.md"
                if (
                    launch_file.exists()
                    and _AGENT_ID_PLACEHOLDER in launch_file.read_text(encoding="utf-8")
                ):
                    _bake_agent_id_in_launch_file(launch_file, _generate_agent_id())
            adapter = self.adapter_resolver(lane_cfg.harness.cli)
            prompt = (
                prompt_override
                if prompt_override is not None
                else (lane_cfg.role or "")
            )
            # Governor --settings (Task 2.2): single-source gate. Reuses the
            # precomputed settings path (preflight already ran in step 0b).
            # Task 3.3: approval-rules are no longer plumbed into --allowedTools
            # here — the governor reads them directly as an audited allow-override.
            _gov_kw = governor_kwargs(
                self.mission_config, lane_cfg, settings_path=_gov_settings
            )
            argv, env = adapter.build_argv(
                prompt,
                model=lane_cfg.harness.model,
                cwd=self.mission_dir,
                **({"live_repl": True} if lane_cfg.live_repl else {}),
                **_gov_kw,
            )
            session_name = f"lane-{short}"
            # Governance PROVENANCE (Task 2.5): a FRESH spawn's process is born
            # exactly with `argv`, so `_gov_kw` is authoritative — when it carries
            # the governor settings the lane is genuinely governed. The marker is
            # WRITTEN/REMOVED in _spawn_one (only after new-session rc=0), so it
            # always reflects a live process.
            ls = LaneSession(
                lane=short,
                name=session_name,
                cwd=self.mission_dir,
                argv=argv,
                env=env,
                stream_log=self.mission_dir / ".fleet" / f"{short}.stream.log",
                governed=bool(_gov_kw),
            )
            if lane_cfg.live_repl and lane_cfg.initial_prompt:
                ls.initial_prompt = lane_cfg.initial_prompt
            self.sessions[short] = ls
            # PM-6 BEFORE-snapshot: captured sync, immediately before scheduling
            # the spawn coroutine, so each lane's diff is taken against the
            # state at its own spawn-start instant (not a shared pre-gather one).
            log_dir = adapter.session_log_dir(self.mission_dir)
            before = _snapshot_dir(log_dir)
            spawn_jobs.append((ls, adapter, before, log_dir))

        if not spawn_jobs:
            return

        # ---- 4 + 5. Parallel spawn with cancellation cleanup (OW-3) -------
        # Catch BaseException so both CancelledError and SpawnError trigger the
        # cleanup path; CancelledError is itself a BaseException subclass so a
        # plain `except BaseException` is sufficient.
        spawned: list[LaneSession] = []
        try:
            await asyncio.gather(
                *(self._spawn_one(s, a, b, ld, spawned) for s, a, b, ld in spawn_jobs)
            )
        except BaseException:
            cleanup_tasks = [tmux.kill_session(self.socket, s.name) for s in spawned]
            if cleanup_tasks:
                await asyncio.gather(*cleanup_tasks, return_exceptions=True)
            raise

    async def stop_all(self) -> None:
        """Kill all running sessions and per-lane tail tasks in parallel."""
        # Cancel tail tasks first so producers stop fanning out before we
        # kill the tmux sessions (avoids spurious EOF chunks landing on the
        # very last subscribers).
        tail_tasks = [
            s.tail_task
            for s in self.sessions.values()
            if s.tail_task is not None and not s.tail_task.done()
        ]
        for t in tail_tasks:
            t.cancel()
        if tail_tasks:
            await asyncio.gather(*tail_tasks, return_exceptions=True)

        kill_tasks = [
            tmux.kill_session(self.socket, s.name)
            for s in self.sessions.values()
            if s.running
        ]
        if kill_tasks:
            await asyncio.gather(*kill_tasks, return_exceptions=True)
        for s in self.sessions.values():
            s.running = False

    def get(self, lane: str) -> LaneSession:
        """Return the LaneSession for the given short lane code."""
        return self.sessions[lane]

    async def subscribe(self, lane: str) -> asyncio.Queue[bytes]:
        """Register a new SSE subscriber queue for ``lane`` and return it.

        Raises:
            KeyError: ``lane`` is not a configured lane.
            TooManySubscribersError: the lane already has
                ``SSE_MAX_SUBSCRIBERS_PER_LANE`` subscribers.

        Acquires ``LaneSession.subscribers_lock`` for the list mutation
        (SR-3) so subscribe is serialized against producer fan-out.
        """
        session = self.sessions[lane]
        async with session.subscribers_lock:
            if len(session.subscribers) >= SSE_MAX_SUBSCRIBERS_PER_LANE:
                raise TooManySubscribersError(
                    f"lane {lane} already has {len(session.subscribers)} subscribers "
                    f"(max {SSE_MAX_SUBSCRIBERS_PER_LANE})"
                )
            q: asyncio.Queue[bytes] = asyncio.Queue(
                maxsize=SSE_PER_SUBSCRIBER_QUEUE_MAXSIZE
            )
            session.subscribers.append(q)
            return q

    async def unsubscribe(self, lane: str, q: asyncio.Queue[bytes]) -> None:
        """Remove ``q`` from ``lane``'s subscribers list. No-op if absent."""
        session = self.sessions[lane]
        async with session.subscribers_lock:
            try:
                session.subscribers.remove(q)
            except ValueError:
                pass

    async def respawn(
        self,
        lane: str,
        argv: list[str],
        env: dict[str, str],
    ) -> None:
        """Replace the running child in lane's tmux pane with ``argv`` (Task 6.3).

        Steps (plan §6.4 + §13):
          1. ``tmux.respawn_pane(socket, name, argv, env)`` — fail loud on rc!=0.
          2. ``tmux.pipe_pane(socket, name, stream_log)`` — re-establish the
             byte stream that ``respawn-pane -k`` dropped (PM-3).
          3. ``tmux.display_message_pane_pipe(socket, name)`` — verify the
             new pipe attached; raise RuntimeError if it didn't.
          4. Under ``session.subscribers_lock``: drain every subscriber queue
             to empty, then push the pinned sentinel
             ``b"\\x1bc\\xe2\\x9f\\xb3 restarting\\xe2\\x80\\xa6\\r\\n"`` into
             each (CV-12 + PM-7). Drain-then-push guarantees the sentinel is
             the first chunk every subscriber sees post-respawn, even under
             slow-consumer backpressure that would otherwise drop-oldest it.

        After the sentinel is queued, ``session.argv`` is updated so a future
        reattach (server restart) re-issues the same prompt.

        Raises:
            KeyError: ``lane`` is not a known session.
            RuntimeError: tmux respawn or re-pipe failed.
        """
        session = self.sessions[lane]

        rc = await tmux.respawn_pane(self.socket, session.name, argv, env)
        if rc != 0:
            raise RuntimeError(f"respawn-pane failed for {session.name} (rc={rc})")

        await tmux.pipe_pane(self.socket, session.name, session.stream_log)
        pipe_active = await tmux.display_message_pane_pipe(self.socket, session.name)
        if not pipe_active:
            raise RuntimeError(
                f"pipe-pane did not attach for {session.name} after respawn"
            )

        # CV-12 + PM-7: drain then push under lock so the sentinel is the
        # first chunk every subscriber sees, even if the queue was full of
        # stale bytes (drop-oldest would otherwise evict the sentinel).
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
                    # Shouldn't happen — we just drained — but guard anyway.
                    pass

        session.argv = list(argv)
        session.env = dict(env)
        # Governance PROVENANCE (Task 2.5): a respawn REPLACES the live process
        # with exactly `argv`, so — unlike reattach — the argv genuinely
        # describes the new process and `argv_is_governed` is trustworthy here.
        # An operator respawn of an ungoverned lane therefore re-governs it
        # (marker rewritten, governed=True); a respawn without --settings clears
        # the marker so the marker always tracks the live process.
        session.governed = argv_is_governed(argv)
        if session.governed:
            write_governed_marker(self.mission_dir, lane)
        else:
            remove_governed_marker(self.mission_dir, lane)

    async def _start_tail_task(self, session: LaneSession) -> None:
        """Launch the per-lane tail coroutine. Override-point for unit tests."""
        session.tail_task = asyncio.create_task(self._tail_lane(session))

    async def _tail_lane(self, session: LaneSession) -> None:
        """Run ``tail -c +1 -F <stream_log>``; fan-out 8 KiB chunks to subscribers.

        Holds ``session.subscribers_lock`` around iteration so concurrent
        ``subscribe``/``unsubscribe`` cannot mutate the list mid-fanout (SR-3).
        On ``QueueFull``, drop oldest then push (canonical drop-oldest pattern).
        """
        proc = await _spawn_tail_subprocess(session.stream_log)
        session._tail_proc = proc
        assert proc.stdout is not None  # PIPE was requested
        try:
            while True:
                chunk = await proc.stdout.read(8192)
                if not chunk:
                    return  # tail exited (file unlinked or fatal); leave subscribers alone
                async with session.subscribers_lock:
                    for q in session.subscribers:
                        try:
                            q.put_nowait(chunk)
                        except asyncio.QueueFull:
                            # Drop oldest, then push current chunk.
                            try:
                                q.get_nowait()
                            except asyncio.QueueEmpty:
                                pass
                            try:
                                q.put_nowait(chunk)
                            except asyncio.QueueFull:
                                # Subscriber genuinely overwhelmed; skip.
                                pass
                session.last_bytes_offset += len(chunk)
        except asyncio.CancelledError:
            raise
        finally:
            if proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    await proc.wait()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _spawn_one(
        self,
        session: LaneSession,
        adapter: HarnessAdapter,
        before_snapshot: set[str],
        log_dir: Path | None,
        spawned: list[LaneSession],
    ) -> None:
        """Create one tmux session, wire pipe-pane, discover session_id, mark running.

        Ordering invariant: pipe-pane must NOT be invoked until new_session
        returns rc=0. A pipe call against a non-existent target would fail
        silently and leave the lane producing no bytes.

        Note: new_session() runs the three-step chain new-session →
        set-option remain-on-exit → set-environment MEGALODON_FLEET_OWNED 1
        non-atomically. A concurrent FleetSpawner.start_all on the same socket
        (extremely unusual in practice) could observe the session via
        list_sessions before the env-var lands; the marker check will then
        treat it as unowned and leave it alone, which is the conservative
        choice. Documented here so a future refactor doesn't try to "fix" it
        by removing the marker check.
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
        # Track the lane in `spawned` BEFORE any further await — if a sibling
        # lane raises during pipe_pane/discovery, OW-3 cleanup must still see
        # this lane's session as one to kill (the tmux session is already real).
        session.running = True
        spawned.append(session)
        # Governance PROVENANCE marker (Task 2.5): the process is now live and
        # was born with `session.argv`, so `session.governed` is authoritative.
        # Write the fingerprinted marker when governed, else remove any stale one
        # — so a later reattach reads the truth of THIS process, never the argv.
        if session.governed:
            write_governed_marker(self.mission_dir, session.lane)
        else:
            remove_governed_marker(self.mission_dir, session.lane)
        await tmux.pipe_pane(self.socket, session.name, session.stream_log)
        # PM-6 AFTER-poll: diff against this lane's BEFORE; single new entry =
        # session id; 0 or 2+ degrades to no-resume.
        session.session_id = await _discover_session_id(log_dir, before_snapshot)
        # CV-5: persist resolved session_id so a fresh server start can
        # compose --resume <id> without rerunning discovery.
        if session.session_id is not None:
            txt = self.mission_dir / ".fleet" / f"{session.lane}.session.txt"
            txt.parent.mkdir(parents=True, exist_ok=True)
            txt.write_text(session.session_id + "\n")
            txt.chmod(0o644)
        # P4 Task 4.1: launch the per-lane tail task so SSE subscribers can
        # attach immediately. The stream log file may not yet exist; ``tail
        # -F`` retries until the file appears.
        session.stream_log.parent.mkdir(parents=True, exist_ok=True)
        session.stream_log.touch(exist_ok=True)
        await self._start_tail_task(session)
        # Live-REPL initial-prompt delivery. After the TUI has had time to
        # render its welcome banner + accept input, send the per-lane
        # initial_prompt as a keystroke sequence terminated by Enter. This is
        # how /loop autonomous bootstraps for the Claude lanes.
        if session.initial_prompt:
            asyncio.create_task(self._deliver_initial_prompt(session))

    async def _deliver_initial_prompt(self, session: LaneSession) -> None:
        """Background task: sleep then send the lane's initial_prompt via tmux."""
        try:
            await asyncio.sleep(_LIVE_REPL_PROMPT_DELAY_SECONDS)
            assert session.initial_prompt is not None
            rc = await tmux.send_keys(self.socket, session.name, session.initial_prompt)
            if rc != 0:
                _log.warning(
                    "send-keys initial_prompt failed for %s (rc=%d)",
                    session.name,
                    rc,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("initial-prompt delivery failed for %s", session.name)

    async def _is_fleet_owned(self, session_name: str) -> bool:
        """Return True if the session carries MEGALODON_FLEET_OWNED=1."""
        proc = await asyncio.create_subprocess_exec(
            "tmux",
            "-S",
            str(self.socket),
            "show-environment",
            "-t",
            session_name,
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
