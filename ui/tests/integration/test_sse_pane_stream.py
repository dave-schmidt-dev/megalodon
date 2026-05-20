"""Integration tests for ``GET /api/v1/lane/{lane}/pane-stream`` (Task 4.2).

Plan §6.4 + §P4: the endpoint returns ``EventSourceResponse`` (sse-starlette).
First event: base64(``\\x1bc``) (terminal-clear sentinel). Second event (if
the stream log isn't empty): base64 of the trailing ``TAIL_ON_CONNECT_BYTES``.
Subsequent events: each fan-out chunk as ``base64.b64encode(chunk).decode()``
until the client disconnects.

``SSE_MAX_SUBSCRIBERS_PER_LANE`` cap: an 11th concurrent connection to one
lane returns HTTP 503 with ``Retry-After: 5``.

Streaming-correctness tests iterate the standalone
``generate_lane_pane_stream_events`` async generator directly (no HTTP
transport): both ``httpx.ASGITransport`` 0.28 and Starlette ``TestClient``
buffer the response body until the generator completes, which deadlocks for
infinite SSE. End-to-end SSE behaviour against a real uvicorn process is
covered by Playwright in Phase 5.

Error-path tests (401/404/503) work over httpx because they return
synchronous responses.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from megalodon_ui.auth import write_token_atomic
from megalodon_ui.mission_config.schema import MissionConfig
from megalodon_ui.server import generate_lane_pane_stream_events
from megalodon_ui.spawn import FleetSpawner


pytestmark = pytest.mark.integration


def _make_config(shorts: list[str]) -> MissionConfig:
    lanes = [
        {
            "name": f"LANE{s}",
            "short": s,
            "role": f"role-{s.lower()}",
            "harness": {"cli": "claude", "model": "sonnet"},
            "cadence_seconds": 300,
            "tick_offset_seconds": 0,
        }
        for s in shorts
    ]
    return MissionConfig.model_validate(
        {
            "mission": {"id": "test-mission", "utc_started": "2026-01-01T00:00:00Z"},
            "lanes": lanes,
            "phases": ["INIT"],
        }
    )


# ---------------------------------------------------------------------------
# Test doubles for the producer side
# ---------------------------------------------------------------------------


class _FakeStdout:
    def __init__(self) -> None:
        self._q: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def feed(self, data: bytes | None) -> None:
        await self._q.put(data)

    async def read(self, _n: int) -> bytes:
        chunk = await self._q.get()
        if chunk is None:
            return b""
        return chunk


class _FakeProc:
    def __init__(self) -> None:
        self.stdout = _FakeStdout()
        self.returncode: int | None = None

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


@pytest_asyncio.fixture
async def spawner_with_lane_A(tmp_path: Path, monkeypatch):
    """Build a FleetSpawner with one lane "A"; fan-out runs against a fake proc.

    Yields ``(spawner, fake_proc, stream_log_path)``. The fake proc lets
    tests inject specific byte chunks; the stream log file exists and is
    writeable.
    """
    mission_dir = tmp_path / "mission"
    (mission_dir / ".fleet").mkdir(parents=True)
    stream_log = mission_dir / ".fleet" / "A.stream.log"
    stream_log.touch()

    config = _make_config(["A"])
    adapter = MagicMock()
    adapter.build_argv = MagicMock(return_value=(["stub"], {}))
    adapter.session_log_dir = MagicMock(return_value=None)
    socket = mission_dir / ".fleet" / "tmux.sock"
    spawner = FleetSpawner(mission_dir, config, MagicMock(return_value=adapter), socket)
    fake_proc = _FakeProc()

    async def _fake_spawn(_path: Path) -> _FakeProc:
        return fake_proc

    import megalodon_ui.spawn as spawn_mod

    monkeypatch.setattr(spawn_mod.tmux, "list_sessions", AsyncMock(return_value=[]))
    monkeypatch.setattr(spawn_mod.tmux, "new_session", AsyncMock(return_value=0))
    monkeypatch.setattr(spawn_mod.tmux, "pipe_pane", AsyncMock(return_value=0))
    monkeypatch.setattr(spawn_mod, "_spawn_tail_subprocess", _fake_spawn)

    await spawner.start_all()
    try:
        yield spawner, fake_proc, stream_log
    finally:
        await fake_proc.stdout.feed(None)
        await spawner.stop_all()


# ---------------------------------------------------------------------------
# Generator-level tests — direct iteration, no HTTP transport
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generator_emits_clear_then_replay_then_live(spawner_with_lane_A):
    """Sequence: clear → replay (when stream log has bytes) → live chunks."""
    spawner, fake_proc, stream_log = spawner_with_lane_A
    stream_log.write_bytes(b"replay-bytes")

    q = await spawner.subscribe("A")
    gen = generate_lane_pane_stream_events(spawner, "A", stream_log, q)

    # First two events are emitted synchronously by the generator (no await
    # needed on q before them).
    e1 = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    e2 = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    assert base64.b64decode(e1["data"]) == b"\x1bc"
    assert base64.b64decode(e2["data"]) == b"replay-bytes"

    # Push a live chunk via the producer; the fan-out delivers it; gen yields.
    await fake_proc.stdout.feed(b"live-chunk")
    e3 = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
    assert base64.b64decode(e3["data"]) == b"live-chunk"

    await gen.aclose()
    # Cleanup: aclose() runs the finally block which unsubscribes.
    assert q not in spawner.get("A").subscribers


@pytest.mark.asyncio
async def test_generator_skips_replay_when_stream_log_empty(spawner_with_lane_A):
    """An empty stream log → only the clear sentinel is emitted before live chunks."""
    spawner, fake_proc, stream_log = spawner_with_lane_A
    assert stream_log.read_bytes() == b""  # touched-only

    q = await spawner.subscribe("A")
    gen = generate_lane_pane_stream_events(spawner, "A", stream_log, q)

    e1 = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    assert base64.b64decode(e1["data"]) == b"\x1bc"

    # No replay event — the next event waits for a live chunk.
    await fake_proc.stdout.feed(b"first-live")
    e2 = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
    assert base64.b64decode(e2["data"]) == b"first-live"

    await gen.aclose()


@pytest.mark.asyncio
async def test_generator_base64_round_trips_non_utf8_bytes(spawner_with_lane_A):
    """Non-UTF-8 byte sequences survive the SSE base64 boundary."""
    spawner, fake_proc, stream_log = spawner_with_lane_A
    non_utf8 = b"\xff\xfe\x00\x80\xc0\xc1"

    q = await spawner.subscribe("A")
    gen = generate_lane_pane_stream_events(spawner, "A", stream_log, q)

    # Skip clear sentinel.
    _ = await asyncio.wait_for(gen.__anext__(), timeout=1.0)

    await fake_proc.stdout.feed(non_utf8)
    e = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
    assert base64.b64decode(e["data"]) == non_utf8

    await gen.aclose()


@pytest.mark.asyncio
async def test_generator_aclose_unsubscribes(spawner_with_lane_A):
    """``aclose`` on the gen runs the finally clause and removes the queue."""
    spawner, _fake, stream_log = spawner_with_lane_A
    q = await spawner.subscribe("A")
    assert q in spawner.get("A").subscribers

    gen = generate_lane_pane_stream_events(spawner, "A", stream_log, q)
    # Advance past the clear sentinel so the gen has reached its main loop.
    _ = await asyncio.wait_for(gen.__anext__(), timeout=1.0)

    await gen.aclose()
    assert q not in spawner.get("A").subscribers


@pytest.mark.asyncio
async def test_generator_truncates_replay_to_tail_on_connect_bytes(
    spawner_with_lane_A, monkeypatch
):
    """Replay event is capped at ``TAIL_ON_CONNECT_BYTES`` (most recent bytes)."""
    spawner, _fake, stream_log = spawner_with_lane_A
    monkeypatch.setattr("megalodon_ui.server.TAIL_ON_CONNECT_BYTES", 8)
    # Write 20 bytes; only the last 8 should appear in the replay.
    stream_log.write_bytes(b"0123456789ABCDEFGHIJ")  # 20 bytes

    q = await spawner.subscribe("A")
    gen = generate_lane_pane_stream_events(spawner, "A", stream_log, q)

    e1 = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    assert base64.b64decode(e1["data"]) == b"\x1bc"
    e2 = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    assert base64.b64decode(e2["data"]) == b"CDEFGHIJ"

    await gen.aclose()


# ---------------------------------------------------------------------------
# HTTP-level error tests (httpx is fine for sync responses)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def authed_async_client_with_spawner(
    fix_medium: Path, monkeypatch
) -> AsyncGenerator[tuple, None]:
    """httpx AsyncClient + spawner ready for the synchronous error-path tests."""
    from httpx import AsyncClient, ASGITransport
    from megalodon_ui.server import make_app

    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    fleet = fix_medium / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    token = "sse-err-token"
    write_token_atomic(fleet / "ui.token", token)

    socket = fleet / "tmux.sock"
    config = _make_config(["A"])
    adapter = MagicMock()
    adapter.build_argv = MagicMock(return_value=(["stub"], {}))
    adapter.session_log_dir = MagicMock(return_value=None)
    spawner = FleetSpawner(fix_medium, config, MagicMock(return_value=adapter), socket)
    fake_proc = _FakeProc()

    async def _fake_spawn(_path: Path) -> _FakeProc:
        return fake_proc

    import megalodon_ui.spawn as spawn_mod

    monkeypatch.setattr(spawn_mod.tmux, "list_sessions", AsyncMock(return_value=[]))
    monkeypatch.setattr(spawn_mod.tmux, "new_session", AsyncMock(return_value=0))
    monkeypatch.setattr(spawn_mod.tmux, "pipe_pane", AsyncMock(return_value=0))
    monkeypatch.setattr(spawn_mod, "_spawn_tail_subprocess", _fake_spawn)

    app = make_app(mission_dir=fix_medium)

    async with app.router.lifespan_context(app):
        await spawner.start_all()
        app.state.spawner = spawner
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            exch = await client.post("/api/v1/auth/exchange", json={"token": token})
            assert exch.status_code == 200, exch.text
            try:
                yield client, spawner, fake_proc, "A"
            finally:
                await fake_proc.stdout.feed(None)
                await spawner.stop_all()


@pytest.mark.asyncio
async def test_pane_stream_eleventh_subscriber_returns_503(
    authed_async_client_with_spawner, monkeypatch
) -> None:
    """When ``SSE_MAX_SUBSCRIBERS_PER_LANE`` is reached the next request gets 503 + Retry-After."""
    client, spawner, _fake, lane = authed_async_client_with_spawner
    monkeypatch.setattr("megalodon_ui.spawn.SSE_MAX_SUBSCRIBERS_PER_LANE", 2)

    await spawner.subscribe(lane)
    await spawner.subscribe(lane)

    resp = await client.get(f"/api/v1/lane/{lane}/pane-stream")
    assert resp.status_code == 503, resp.text
    assert resp.headers.get("retry-after") == "5"


@pytest.mark.asyncio
async def test_pane_stream_unknown_lane_returns_404(
    authed_async_client_with_spawner,
) -> None:
    """Unknown lane → 404 (auth-gate already cleared by middleware)."""
    client, _spawner, _fake, _lane = authed_async_client_with_spawner
    resp = await client.get("/api/v1/lane/ZZZ/pane-stream")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_pane_stream_unauthenticated_returns_401(fix_medium: Path, monkeypatch):
    """No cookie → middleware returns 401 before any subscribe call."""
    from httpx import AsyncClient, ASGITransport
    from megalodon_ui.server import make_app

    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    app = make_app(mission_dir=fix_medium)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.get("/api/v1/lane/A/pane-stream")
            assert r.status_code == 401
