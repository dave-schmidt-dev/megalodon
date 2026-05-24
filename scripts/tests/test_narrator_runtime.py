"""Tests for megalodon_ui.narrator.runtime — NarratorRuntime supervisor.

The runtime owns an ``httpx.AsyncClient`` and supervises a local
``llama-server`` subprocess. These tests mock ``asyncio.create_subprocess_exec``
(via a fake process) and patch ``client.healthy`` so no real subprocess or
network is touched. Poll/backoff intervals are injected as zero so the
supervisor loop spins deterministically without sleeping real seconds.

The module is exercised under ``-W error`` so any unclosed AsyncClient
(ResourceWarning) fails the suite (PM-5).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from megalodon_ui.narrator import runtime as runtime_mod
from megalodon_ui.narrator.runtime import NarratorRuntime

# ---------------------------------------------------------------------------
# Fast-loop constructor kwargs: zeroed intervals so the supervisor spins
# without real sleeps. Every test that calls start() passes these.
# ---------------------------------------------------------------------------
FAST_KW = dict(
    poll_interval_s=0.0,
    health_timeout_s=0.0,
    backoff_base_s=0.0,
    backoff_max_s=0.0,
    terminate_wait_s=0.01,
)


# ---------------------------------------------------------------------------
# Fake subprocess
# ---------------------------------------------------------------------------


class FakeProcess:
    """Stand-in for asyncio.subprocess.Process with controllable exit."""

    def __init__(self, *, exit_immediately: bool = False) -> None:
        # returncode None == running. If exit_immediately, the supervisor's
        # "has the child died?" probe sees a non-None returncode right away.
        self.returncode: int | None = 1 if exit_immediately else None
        self.terminate_called = False
        self.kill_called = False
        self._wait_event = asyncio.Event()
        if exit_immediately:
            self._wait_event.set()

    def terminate(self) -> None:
        self.terminate_called = True
        self.returncode = -15
        self._wait_event.set()

    def kill(self) -> None:
        self.kill_called = True
        self.returncode = -9
        self._wait_event.set()

    async def wait(self) -> int:
        await self._wait_event.wait()
        return self.returncode if self.returncode is not None else 0


def _install_spawn_recorder(
    monkeypatch: pytest.MonkeyPatch,
    *,
    exit_immediately: bool = False,
) -> list[list[str]]:
    """Patch create_subprocess_exec to record argv and return a FakeProcess.

    Returns the list that accumulates each spawn's argv (list of str).
    """
    calls: list[list[str]] = []

    async def fake_create(*args, **kwargs):
        calls.append([str(a) for a in args])
        return FakeProcess(exit_immediately=exit_immediately)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    return calls


async def _await_until(predicate, *, timeout: float = 2.0) -> bool:
    """Yield to the loop until predicate() is True or timeout elapses."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0)
    return predicate()


# ---------------------------------------------------------------------------
# 1. Exact locked argv
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_spawns_with_exact_locked_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """start() spawns llama-server with the exact locked argv (order matters)."""
    model = tmp_path / "model.gguf"
    model.write_bytes(b"\x00")
    calls = _install_spawn_recorder(monkeypatch)
    # Never become healthy so the supervisor keeps the first process running.
    monkeypatch.setattr(runtime_mod, "healthy", _fake_healthy(always=False))

    rt = NarratorRuntime(model, port=8085, **FAST_KW)
    await rt.start()
    try:
        assert await _await_until(lambda: len(calls) >= 1)
        argv = calls[0]
        assert argv == [
            "llama-server",
            "-m",
            str(model),
            "--alias",
            "narrator",
            "--chat-template-kwargs",
            '{"enable_thinking":false}',
            "-ngl",
            "99",
            "-c",
            "8192",
            "--jinja",
            "--host",
            "127.0.0.1",
            "--port",
            "8085",
        ]
        assert rt.base_url == "http://127.0.0.1:8085"
    finally:
        await rt.stop()


# ---------------------------------------------------------------------------
# 2. start() is non-blocking (F3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_is_non_blocking_readiness_flips_later(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """start() returns before is_ready(); readiness flips after a health pass."""
    model = tmp_path / "model.gguf"
    model.write_bytes(b"\x00")
    _install_spawn_recorder(monkeypatch)
    gate = _GatedHealthy()
    monkeypatch.setattr(runtime_mod, "healthy", gate)

    rt = NarratorRuntime(model, port=8085, **FAST_KW)
    await rt.start()
    try:
        # Non-blocking: not ready immediately, before any poll passes.
        assert rt.is_ready() is False
        # Now let health checks pass; readiness must flip.
        gate.open()
        assert await _await_until(rt.is_ready)
    finally:
        await rt.stop()


# ---------------------------------------------------------------------------
# 3. url_override skips spawn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_url_override_skips_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    """With url_override set, no subprocess is spawned; base_url is the override."""
    calls = _install_spawn_recorder(monkeypatch)
    gate = _GatedHealthy()
    monkeypatch.setattr(runtime_mod, "healthy", gate)

    rt = NarratorRuntime(
        "/nonexistent/model.gguf",
        port=8085,
        url_override="http://example.test:9999",
        **FAST_KW,
    )
    await rt.start()
    try:
        gate.open()
        assert await _await_until(rt.is_ready)
        assert rt.base_url == "http://example.test:9999"
        assert calls == []
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_url_override_failed_health_is_non_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """url_override with always-failing health polls forever, never trips the ceiling.

    A remote/slow server may come up later, so a no-owned-child runtime must
    keep polling best-effort and NEVER finish via the respawn ceiling.
    """
    calls = _install_spawn_recorder(monkeypatch)
    gate = _GatedHealthy()  # never opened → always False
    monkeypatch.setattr(runtime_mod, "healthy", gate)

    rt = NarratorRuntime(
        "/nonexistent/model.gguf",
        port=8085,
        url_override="http://example.test:9999",
        max_consecutive_failures=5,
        **FAST_KW,
    )
    await rt.start()
    try:
        # Poll far more than the ceiling's worth of times.
        assert await _await_until(lambda: gate.calls > 20)
        # Never spawns, never ready, and the supervisor is STILL polling
        # (the ceiling can only be tripped by an owned child's exits).
        assert calls == []
        assert rt.is_ready() is False
        assert rt.supervisor_done() is False
    finally:
        await rt.stop()


# ---------------------------------------------------------------------------
# 4. Missing model → degraded, not fatal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_model_is_degraded_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A nonexistent model: no owned child → no respawns, no ceiling trip, stable degraded.

    Missing-model is a stable degraded mode: start() must not raise or spawn,
    is_ready() stays False, and the supervisor keeps polling forever (it never
    trips the ceiling, since there is no owned child to exit).
    """
    calls = _install_spawn_recorder(monkeypatch)
    # No server exists, so health never passes — the realistic degraded state.
    gate = _GatedHealthy()  # never opened → always False
    monkeypatch.setattr(runtime_mod, "healthy", gate)

    missing = tmp_path / "does-not-exist.gguf"
    rt = NarratorRuntime(missing, port=8085, max_consecutive_failures=5, **FAST_KW)
    await rt.start()  # must not raise
    try:
        assert await _await_until(lambda: gate.calls > 20)
        assert calls == []
        assert rt.is_ready() is False
        assert rt.supervisor_done() is False
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_model_appearing_after_start_does_not_enable_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ownership is snapshotted at start(): a model appearing later must NOT spawn."""
    calls = _install_spawn_recorder(monkeypatch, exit_immediately=True)
    gate = _GatedHealthy()  # never opened → always False
    monkeypatch.setattr(runtime_mod, "healthy", gate)

    model = tmp_path / "model.gguf"  # absent at start()
    rt = NarratorRuntime(model, port=8085, max_consecutive_failures=5, **FAST_KW)
    await rt.start()
    try:
        # Model file appears after start — must be ignored (no re-enable).
        model.write_bytes(b"\x00")
        assert await _await_until(lambda: gate.calls > 20)
        assert calls == []
        assert rt.is_ready() is False
        assert rt.supervisor_done() is False
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_spawn_oserror_is_degraded_not_fatal_and_leak_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If llama-server is not on PATH (OSError on spawn), degrade — don't crash or leak.

    start() must not raise, the client must NOT leak (it is closed by stop()
    under -W error), is_ready() stays False, and the supervisor keeps polling
    best-effort (it never trips the ceiling because ownership was dropped).
    """
    model = tmp_path / "model.gguf"
    model.write_bytes(b"\x00")

    async def fake_create_raises(*args, **kwargs):
        raise FileNotFoundError("[Errno 2] No such file or directory: 'llama-server'")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_raises)
    gate = _GatedHealthy()  # never opened → always False
    monkeypatch.setattr(runtime_mod, "healthy", gate)

    rt = NarratorRuntime(model, port=8085, max_consecutive_failures=5, **FAST_KW)
    await rt.start()  # must NOT raise
    client = rt.client
    assert isinstance(client, httpx.AsyncClient)
    try:
        # No owned child (ownership dropped) → polls forever, never trips.
        assert await _await_until(lambda: gate.calls > 20)
        assert rt.is_ready() is False
        assert rt.supervisor_done() is False
    finally:
        await rt.stop()
    # Client closed on stop() — no ResourceWarning leak under -W error.
    assert client.is_closed is True


@pytest.mark.asyncio
async def test_double_start_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Calling start() twice without stop() raises (no leaked client/process/task)."""
    model = tmp_path / "model.gguf"
    model.write_bytes(b"\x00")
    _install_spawn_recorder(monkeypatch)
    monkeypatch.setattr(runtime_mod, "healthy", _fake_healthy(always=False))

    rt = NarratorRuntime(model, port=8085, **FAST_KW)
    await rt.start()
    try:
        with pytest.raises(RuntimeError):
            await rt.start()
    finally:
        await rt.stop()


# ---------------------------------------------------------------------------
# 5. Failure ceiling (CV-6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failure_ceiling_stops_respawning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After N consecutive owned-child exits (respawns), the supervisor stops, stays degraded.

    The ceiling bounds RESPAWNS (unexpected child exits), not health-poll
    failures. Every spawned child exits immediately and health never passes, so
    each poll sees a dead owned child → respawn-worthy failure. The counter
    climbs to the ceiling and the supervisor stops with a bounded spawn count.
    """
    model = tmp_path / "model.gguf"
    model.write_bytes(b"\x00")
    # Every spawned process exits immediately, and health always fails.
    calls = _install_spawn_recorder(monkeypatch, exit_immediately=True)
    monkeypatch.setattr(runtime_mod, "healthy", _fake_healthy(always=False))

    rt = NarratorRuntime(model, port=8085, max_consecutive_failures=5, **FAST_KW)
    await rt.start()
    try:
        # Wait for the supervisor to settle (stop respawning).
        assert await _await_until(lambda: rt.supervisor_done())
        # Deterministic: 1 initial spawn + (ceiling-1) respawns = ceiling.
        assert len(calls) == rt._max_consecutive_failures
        assert rt.is_ready() is False
    finally:
        await rt.stop()


# ---------------------------------------------------------------------------
# 6. Counter resets on success (PM-3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_counter_resets_on_health_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A health pass between owned-child exits resets the respawn count (no premature ceiling)."""
    model = tmp_path / "model.gguf"
    model.write_bytes(b"\x00")
    _install_spawn_recorder(monkeypatch, exit_immediately=True)

    # Each child exits immediately, so every health-fail poll is a respawn-worthy
    # failure. Sequence with ceiling=3: two fails, a PASS, two fails, a PASS, ...
    # then PASS forever (tail=True). The consecutive-respawn count never reaches
    # 3 because each PASS resets it (PM-3). If the counter did NOT reset, the
    # cumulative third exit (at poll #4) would trip the ceiling and the
    # supervisor would stop. We accumulate six exits — double the ceiling — yet
    # the supervisor must stay alive and reach readiness.
    seq = [False, False, True, False, False, True, False, False, True]
    gate = _SequencedHealthy(seq, tail=True)
    monkeypatch.setattr(runtime_mod, "healthy", gate)

    rt = NarratorRuntime(model, port=8085, max_consecutive_failures=3, **FAST_KW)
    await rt.start()
    try:
        # Let the supervisor work through the whole scripted sequence.
        assert await _await_until(lambda: gate.calls >= len(seq))
        # Six exits occurred — double the ceiling — yet the supervisor never
        # tripped (resets kept the consecutive count below 3) and reached ready.
        assert rt.supervisor_done() is False
        assert await _await_until(rt.is_ready)
    finally:
        await rt.stop()


# ---------------------------------------------------------------------------
# 7. stop() terminates + closes client
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_terminates_and_closes_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """stop() terminates the subprocess and closes the httpx client."""
    model = tmp_path / "model.gguf"
    model.write_bytes(b"\x00")

    procs: list[FakeProcess] = []

    async def fake_create(*args, **kwargs):
        p = FakeProcess()
        procs.append(p)
        return p

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    monkeypatch.setattr(runtime_mod, "healthy", _fake_healthy(always=False))

    rt = NarratorRuntime(model, port=8085, **FAST_KW)
    await rt.start()
    assert await _await_until(lambda: len(procs) >= 1)
    client = rt.client
    assert isinstance(client, httpx.AsyncClient)

    await rt.stop()

    assert procs[0].terminate_called is True
    assert client.is_closed is True
    assert rt.is_ready() is False


@pytest.mark.asyncio
async def test_stop_kills_if_terminate_does_not_reap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If terminate() does not reap within the bounded wait, stop() calls kill()."""
    model = tmp_path / "model.gguf"
    model.write_bytes(b"\x00")

    class StubbornProcess(FakeProcess):
        def terminate(self) -> None:
            # Pretend terminate was ignored: stays running, wait() blocks.
            self.terminate_called = True

    procs: list[StubbornProcess] = []

    async def fake_create(*args, **kwargs):
        p = StubbornProcess()
        procs.append(p)
        return p

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    monkeypatch.setattr(runtime_mod, "healthy", _fake_healthy(always=False))

    rt = NarratorRuntime(model, port=8085, **FAST_KW)
    await rt.start()
    assert await _await_until(lambda: len(procs) >= 1)
    client = rt.client
    assert isinstance(client, httpx.AsyncClient)

    await rt.stop()

    assert procs[0].terminate_called is True
    assert procs[0].kill_called is True
    assert client.is_closed is True


# ---------------------------------------------------------------------------
# from_env
# ---------------------------------------------------------------------------


def test_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """from_env with no env set picks the documented defaults."""
    for var in (
        "MEGALODON_NARRATOR_URL",
        "MEGALODON_NARRATOR_MODEL",
        "MEGALODON_NARRATOR_PORT",
        "MEGALODON_NARRATOR_TIMEOUT_S",
    ):
        monkeypatch.delenv(var, raising=False)
    rt = NarratorRuntime.from_env()
    assert rt.url_override is None
    assert rt.port == 8085
    assert rt.narrate_timeout_s == pytest.approx(6.0)
    assert str(rt.model_path).endswith(
        "models/narrator-bench/gemma-e2b/gemma-4-E2B-it-Q4_K_M.gguf"
    )
    # ~ must be expanded.
    assert "~" not in str(rt.model_path)


def test_from_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """from_env reads each env var override."""
    monkeypatch.setenv("MEGALODON_NARRATOR_URL", "http://h:1234")
    monkeypatch.setenv("MEGALODON_NARRATOR_MODEL", "/tmp/m.gguf")
    monkeypatch.setenv("MEGALODON_NARRATOR_PORT", "9001")
    monkeypatch.setenv("MEGALODON_NARRATOR_TIMEOUT_S", "3.5")
    rt = NarratorRuntime.from_env()
    assert rt.url_override == "http://h:1234"
    assert str(rt.model_path) == "/tmp/m.gguf"
    assert rt.port == 9001
    assert rt.narrate_timeout_s == pytest.approx(3.5)


# ---------------------------------------------------------------------------
# Health-control helpers
# ---------------------------------------------------------------------------


def _fake_healthy(*, always: bool):
    """Return an async healthy() stub that always returns ``always``."""

    async def _healthy(client, base_url, *, timeout_s=1.0):
        return always

    return _healthy


class _GatedHealthy:
    """Async healthy() stub: returns False until open() is called, then True."""

    def __init__(self) -> None:
        self._open = False
        self.calls = 0

    def open(self) -> None:
        self._open = True

    async def __call__(self, client, base_url, *, timeout_s=1.0):
        self.calls += 1
        return self._open


class _SequencedHealthy:
    """Async healthy() stub returning a scripted sequence, then a tail value."""

    def __init__(self, seq: list[bool], *, tail: bool) -> None:
        self._seq = list(seq)
        self._tail = tail
        self.calls = 0

    async def __call__(self, client, base_url, *, timeout_s=1.0):
        self.calls += 1
        if self._seq:
            return self._seq.pop(0)
        return self._tail
