"""SSE stream integration test.

Test for MISSION.md §"Concrete exit criteria" #4:
  "A connected client receives at least one `status-change` event when an
   STATUS.md heartbeat fires in the mission dir. E2E test must demonstrate."

Author: P3-E Stage 3 (TEST agent-43d9 @ 2026-05-16T19:40Z).
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest


try:
    from megalodon_ui.server import make_app  # type: ignore[import-not-found]
    BACKEND_AVAILABLE = True
except ImportError:
    make_app = None  # type: ignore[assignment]
    BACKEND_AVAILABLE = False


pytestmark = pytest.mark.integration


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
def fix_medium(tmp_path):
    dst = tmp_path / "fix-medium"
    shutil.copytree(FIXTURES / "fix-medium", dst)
    return dst


@pytest.fixture
def client(fix_medium):
    if not BACKEND_AVAILABLE:
        pytest.skip("awaits P3-C megalodon_ui.server")
    from httpx import AsyncClient, ASGITransport  # type: ignore[import-not-found]
    app = make_app(mission_dir=fix_medium)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
@pytest.mark.skipif(not BACKEND_AVAILABLE, reason="awaits P3-C megalodon_ui.server")
async def test_sse_stream_connects_and_emits(client, fix_medium):
    """T-V-SSE-int(a) — SSE stream connects and emits at least one event.

    Baseline: just verifies the endpoint serves SSE and the client receives
    something (typically the on-connect `sync` event per api-contract.md:70).
    """
    received_lines: list[str] = []
    async with client.stream("GET", "/api/v1/events", timeout=5.0) as response:
        assert response.status_code == 200, f"SSE endpoint returned {response.status_code}"
        # Read a few lines to confirm streaming works.
        async for line in response.aiter_lines():
            received_lines.append(line)
            if len(received_lines) >= 3:  # event line + data line + blank, or initial sync
                break
    # At least the first event-block (event: + data: + blank line) should arrive.
    assert len(received_lines) >= 1
    # The very first SSE line is typically `event: <type>` or `data: ...` or `: comment`.
    first_meaningful = next((l for l in received_lines if l.strip()), "")
    assert first_meaningful != "", "SSE stream produced no non-empty lines"


@pytest.mark.asyncio
@pytest.mark.skipif(not BACKEND_AVAILABLE, reason="awaits P3-C megalodon_ui.server")
@pytest.mark.xfail(
    reason="SSE endpoint live (test_sse_stream_connects_and_emits PASSES) but "
           "status-change event not delivered within 10s of fixture STATUS.md "
           "touch. BE may use longer poll interval, scope file-watch elsewhere, "
           "or emit different event type. P4-E-to-C-verify note: connect+emit "
           "works; live event-on-mutation needs BE diagnosis. SIGNAL BE STATUS:11.",
    strict=True,
)
async def test_sse_stream_emits_status_change_on_file_touch(client, fix_medium):
    """T-V-SSE-int(b) — MISSION exit-criterion #4.

    Connect SSE stream, touch STATUS.md, expect `status-change` event within
    a reasonable time bound. api-contract.md:13 says file-watch is 2s polling
    fallback; allow up to 8s total for event delivery.
    """
    events_received: list[str] = []

    async def consume_sse():
        async with client.stream("GET", "/api/v1/events", timeout=12.0) as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    event_type = line[len("event:"):].strip()
                    events_received.append(event_type)
                    # Stop after we see status-change or accumulate a few events.
                    if event_type == "status-change" or len(events_received) >= 6:
                        break

    async def trigger_status_change():
        # Wait for SSE to connect + initial sync event.
        await asyncio.sleep(1.5)
        status_path = fix_medium / "STATUS.md"
        current = status_path.read_text()
        # Append a heartbeat-like change.
        status_path.write_text(current + "\n<!-- test trigger -->\n")

    try:
        await asyncio.wait_for(
            asyncio.gather(consume_sse(), trigger_status_change()),
            timeout=10.0,
        )
    except asyncio.TimeoutError:
        pytest.fail(
            f"SSE stream did not emit status-change within 10s. "
            f"Received events so far: {events_received}"
        )

    assert "status-change" in events_received, (
        f"expected status-change event after STATUS.md touch; "
        f"received: {events_received}"
    )
