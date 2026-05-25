"""Supervised subprocess runtime for the local narrator model server.

``NarratorRuntime`` owns the ``httpx.AsyncClient`` and the lifecycle of a local
``llama-server`` subprocess that serves the narrator model over an
OpenAI-compatible API. The whole point is to be *non-fatal*: the dashboard's
load-bearing board never depends on the narrator, so every failure mode here
(missing model, dead server, held port) degrades to a stable "not ready" state
rather than raising.

Design notes:

- ``start()`` is **non-blocking** (F3): it spawns the subprocess (unless a URL
  override is configured) and kicks off a background supervisor task that
  health-polls and flips readiness. It returns promptly so the dashboard serves
  immediately while the model loads.
- The supervisor respawns a dead child with **capped backoff** and a
  **max-consecutive-failures ceiling** (CV-6). The consecutive-failure counter
  resets to zero on any successful health pass (PM-3), so a single transient
  failure never burns down the budget toward permanent-offline. Once the
  ceiling trips, the runtime stays in stable degraded mode until ``stop()``.
- The client is created in ``start()`` and closed in ``stop()`` (PM-5); the
  module is clean under ``-W error`` (no unclosed AsyncClient).
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import httpx

from .client import healthy

logger = logging.getLogger(__name__)

# Locked llama-server flags (order matters; tests assert the exact argv).
_LLAMA_SERVER_BIN = "llama-server"
_CHAT_TEMPLATE_KWARGS = '{"enable_thinking":false}'

# Default env idiom (read only in from_env, at lifespan start — not at import).
_DEFAULT_MODEL = "~/models/narrator-bench/gemma-e2b/gemma-4-E2B-it-Q4_K_M.gguf"
_DEFAULT_PORT = 8085
_DEFAULT_TIMEOUT_S = 6.0

# Supervisor tuning. Module-level so tests can monkey-patch, and also injectable
# per-instance via the constructor (tests pass zeroed values for determinism).
_POLL_INTERVAL_S = 1.0  # sleep BETWEEN health polls (cadence)
_HEALTH_TIMEOUT_S = 1.0  # per /health request timeout (how long one may block)
_BACKOFF_BASE_S = 0.5
_BACKOFF_MAX_S = 10.0
_TERMINATE_WAIT_S = 5.0
_MAX_CONSECUTIVE_FAILURES = 5


class NarratorRuntime:
    """Supervise a local ``llama-server`` subprocess for the narrator model.

    The runtime owns the shared ``httpx.AsyncClient`` (created in ``start``,
    closed in ``stop``) and the subprocess lifecycle. It exposes the attributes
    the later scheduler consumes: ``client``, ``base_url`` and
    ``narrate_timeout_s``.
    """

    def __init__(
        self,
        model_path: str | Path,
        port: int,
        *,
        url_override: str | None = None,
        narrate_timeout_s: float = _DEFAULT_TIMEOUT_S,
        poll_interval_s: float = _POLL_INTERVAL_S,
        health_timeout_s: float = _HEALTH_TIMEOUT_S,
        backoff_base_s: float = _BACKOFF_BASE_S,
        backoff_max_s: float = _BACKOFF_MAX_S,
        terminate_wait_s: float = _TERMINATE_WAIT_S,
        max_consecutive_failures: int = _MAX_CONSECUTIVE_FAILURES,
    ) -> None:
        """Construct a runtime (does not spawn or connect — see ``start``).

        Args:
            model_path: Filesystem path to the GGUF model. Ignored when
                ``url_override`` is set.
            port: Local port llama-server binds (also forms ``base_url``).
            url_override: If set, skip spawning a subprocess and treat this URL
                as the already-running server (best-effort health-gated).
            narrate_timeout_s: Per-narrate request timeout, exposed for the
                scheduler.
            poll_interval_s: Seconds to sleep between health polls (cadence).
            health_timeout_s: Per ``/health`` request timeout (how long one poll
                may block), distinct from the poll cadence.
            backoff_base_s: Base seconds for capped exponential respawn backoff.
            backoff_max_s: Ceiling for the respawn backoff.
            terminate_wait_s: Bounded wait for a terminated child to reap before
                ``kill()``.
            max_consecutive_failures: Ceiling of consecutive owned-child
                respawns (unexpected child exits) without an intervening healthy
                poll, before the supervisor stops respawning (CV-6).
        """
        self.model_path = Path(model_path)
        self.port = port
        self.url_override = url_override
        self.narrate_timeout_s = narrate_timeout_s

        self._poll_interval_s = poll_interval_s
        self._health_timeout_s = health_timeout_s
        self._backoff_base_s = backoff_base_s
        self._backoff_max_s = backoff_max_s
        self._terminate_wait_s = terminate_wait_s
        self._max_consecutive_failures = max_consecutive_failures

        self.base_url: str = (
            url_override if url_override is not None else f"http://127.0.0.1:{port}"
        )

        # Populated in start(); torn down in stop().
        self.client: httpx.AsyncClient | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._supervisor: asyncio.Task[None] | None = None
        self._ready = False
        self._stopped = False
        # Snapshotted in start(): does this runtime manage a llama-server child?
        self._owns_child = False

    # ------------------------------------------------------------------
    # Construction from environment
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> "NarratorRuntime":
        """Build a runtime from environment variables (read at call time).

        Reads ``MEGALODON_NARRATOR_URL`` (url_override),
        ``MEGALODON_NARRATOR_MODEL`` (model path, ``~`` expanded),
        ``MEGALODON_NARRATOR_PORT`` and ``MEGALODON_NARRATOR_TIMEOUT_S``,
        falling back to the documented defaults. Per the project idiom, env is
        read here (at lifespan start), never at import.
        """
        url_override = os.environ.get("MEGALODON_NARRATOR_URL") or None
        model_raw = os.environ.get("MEGALODON_NARRATOR_MODEL", _DEFAULT_MODEL)
        model_path = Path(model_raw).expanduser()
        port = int(os.environ.get("MEGALODON_NARRATOR_PORT", str(_DEFAULT_PORT)))
        timeout_s = float(
            os.environ.get("MEGALODON_NARRATOR_TIMEOUT_S", str(_DEFAULT_TIMEOUT_S))
        )
        return cls(
            model_path,
            port,
            url_override=url_override,
            narrate_timeout_s=timeout_s,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Spawn the server (unless overridden) and start the supervisor.

        **Non-blocking (F3, load-bearing):** this creates the httpx client,
        spawns the subprocess (or skips it for ``url_override`` / a missing
        model), launches the background supervisor task, and returns promptly
        WITHOUT awaiting readiness. ``is_ready()`` only flips True after a
        ``/health`` poll passes.

        **Non-crashing and leak-free:** consistent with the runtime's non-fatal
        contract, an unspawnable ``llama-server`` (e.g. binary not on PATH →
        ``OSError``) is treated as a degraded mode, not a crash: a WARNING is
        logged, no exception propagates, and ``is_ready()`` stays False. The
        supervisor still runs (best-effort polling), so a server appearing on
        the configured port later can still flip readiness.

        Raises:
            RuntimeError: if ``start()`` is called while already started (the
                supervisor task exists) — prevents leaking client/process/task.
        """
        if self._supervisor is not None:
            raise RuntimeError("NarratorRuntime.start() called twice without stop()")

        self._stopped = False
        self.client = httpx.AsyncClient()

        # Snapshot child ownership ONCE at start (not re-checked live). A model
        # file appearing after start must not silently re-enable spawning —
        # missing-model is a stable degraded mode. Likewise url_override never
        # owns a child. Only an owned child's unexpected exits count toward the
        # respawn ceiling; the no-owned-child cases poll /health best-effort
        # forever (a remote/slow server may come up later).
        self._owns_child = self.url_override is None and self.model_path.exists()

        if self.url_override is None and not self._owns_child:
            logger.warning(
                "narrator model not found at %s — running degraded "
                "(no llama-server spawned)",
                self.model_path,
            )
        elif self._owns_child:
            try:
                self._proc = await self._spawn()
            except OSError as exc:
                # Binary missing / not executable: degrade, don't crash the
                # lifespan. Drop ownership so the supervisor doesn't try to
                # respawn an unspawnable binary; close the client we opened so
                # there is no leak if the caller never reaches stop().
                logger.warning(
                    "narrator: failed to spawn %s (%s) — running degraded "
                    "(no llama-server)",
                    _LLAMA_SERVER_BIN,
                    exc,
                )
                self._owns_child = False

        self._supervisor = asyncio.create_task(self._supervise())

    async def stop(self) -> None:
        """Cancel the supervisor, reap the subprocess, and close the client.

        Cancels and awaits the supervisor task, terminates+reaps the child
        (terminate, bounded wait, then kill if needed), and closes the httpx
        client (PM-5). Idempotent and safe to call without a prior ``start``.
        """
        self._stopped = True
        self._ready = False

        if self._supervisor is not None:
            self._supervisor.cancel()
            try:
                await self._supervisor
            except asyncio.CancelledError:
                pass
            self._supervisor = None

        if self._proc is not None:
            await self._reap(self._proc)
            self._proc = None

        if self.client is not None:
            await self.client.aclose()
            self.client = None

    def is_ready(self) -> bool:
        """Return True only once a ``/health`` poll passed and not stopped."""
        return self._ready and not self._stopped

    def supervisor_done(self) -> bool:
        """Return True once the supervisor loop has exited (ceiling tripped).

        Test/inspection hook: the supervisor finishes when the consecutive
        failure ceiling trips (it stays in stable degraded mode thereafter).
        """
        return self._supervisor is not None and self._supervisor.done()

    # ------------------------------------------------------------------
    # Subprocess helpers
    # ------------------------------------------------------------------

    def _build_argv(self) -> list[str]:
        """Return the exact locked llama-server argv (order is load-bearing)."""
        return [
            _LLAMA_SERVER_BIN,
            "-m",
            str(self.model_path),
            "--alias",
            "narrator",
            "--chat-template-kwargs",
            _CHAT_TEMPLATE_KWARGS,
            "-ngl",
            "99",
            "-c",
            "8192",
            "--jinja",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
        ]

    async def _spawn(self) -> asyncio.subprocess.Process:
        """Spawn the llama-server subprocess with the locked argv."""
        argv = self._build_argv()
        return await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def _reap(self, proc: asyncio.subprocess.Process) -> None:
        """Terminate then (if needed) kill ``proc``, always awaiting wait()."""
        if proc.returncode is not None:
            return
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=self._terminate_wait_s)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()

    # ------------------------------------------------------------------
    # Supervisor
    # ------------------------------------------------------------------

    async def _supervise(self) -> None:
        """Health-poll loop with respawn, capped backoff, and a respawn ceiling.

        Each iteration polls ``/health``:

        - On a pass, readiness flips True and the consecutive-respawn counter
          resets to zero (PM-3).
        - On a failure, readiness flips False. If we own a child and it has
          *exited unexpectedly*, that is one respawn-worthy failure: increment
          the counter, and once it reaches the ceiling (CV-6) log ONE warning
          and return — staying in stable degraded mode until ``stop()``.
          Otherwise (counter still below ceiling) respawn with capped backoff.
        - A health failure while an owned child is still ALIVE (e.g. model still
          loading) does NOT count toward the ceiling — keep polling.
        - With no owned child (``url_override`` or missing model), the loop
          polls best-effort FOREVER and never trips the ceiling, because a
          remote/slow server may come up later; ``is_ready`` just reflects the
          current health.
        """
        assert self.client is not None
        consecutive_respawns = 0
        try:
            while not self._stopped:
                ok = await healthy(
                    self.client, self.base_url, timeout_s=self._health_timeout_s
                )

                # When we own a child, a health pass is only trustworthy if that
                # OWNED child is the one answering. If the owned child has exited
                # (e.g. it failed to bind because a stale/orphan llama-server
                # still holds the port), /health may pass against that FOREIGN
                # listener — possibly a different/stale model. Gate readiness on
                # the owned child being alive so a foreign listener can never
                # produce a false-ready (BUG 2).
                owned_child_exited = self._owns_child and (
                    self._proc is None or self._proc.returncode is not None
                )

                if ok:
                    # Readiness requires our owned child to be alive: a health
                    # pass while the owned child has exited is answering a foreign
                    # listener, so never report ready against it (BUG 2). The
                    # respawn budget still clears on any health pass (PM-3) — a
                    # served port means the supervisor is not in a respawn-storm.
                    self._ready = not owned_child_exited
                    consecutive_respawns = 0  # PM-3: a success clears the budget
                    await asyncio.sleep(self._poll_interval_s)
                    continue

                self._ready = False

                # No owned child → never respawn, never trip the ceiling.
                if not self._owns_child:
                    await asyncio.sleep(self._poll_interval_s)
                    continue

                child_exited = self._proc is None or self._proc.returncode is not None
                if not child_exited:
                    # Child still alive (still loading): does NOT count toward
                    # the ceiling. Keep polling.
                    await asyncio.sleep(self._poll_interval_s)
                    continue

                # Owned child exited unexpectedly — this is a respawn-worthy
                # failure and the only thing that counts toward the ceiling.
                consecutive_respawns += 1
                if consecutive_respawns >= self._max_consecutive_failures:
                    logger.warning(
                        "narrator runtime: %d consecutive respawns without a "
                        "healthy poll — staying in stable degraded mode (no "
                        "further respawns) until stop()",
                        consecutive_respawns,
                    )
                    return
                await self._respawn_with_backoff(consecutive_respawns)
        except asyncio.CancelledError:
            raise

    async def _respawn_with_backoff(self, respawn_count: int) -> None:
        """Reap any dead child, wait the capped backoff, then spawn anew."""
        if self._proc is not None:
            await self._reap(self._proc)
            self._proc = None
        await asyncio.sleep(self._backoff_for(respawn_count))
        if self._stopped:
            return
        self._proc = await self._spawn()

    def _backoff_for(self, failure_count: int) -> float:
        """Capped exponential backoff for the given consecutive-failure count."""
        delay = self._backoff_base_s * (2 ** max(0, failure_count - 1))
        return min(delay, self._backoff_max_s)
