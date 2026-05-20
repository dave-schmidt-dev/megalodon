"""P6.3 — PM-7: respawn sentinel must survive slow-consumer backpressure.

Pre-mortem note (PM-7) in the plan: under producer fan-out's drop-oldest
backpressure, a single ``\\x1bc`` sentinel chunk could be evicted before
the subscriber reads it — leaving the terminal in a mixed-state mess
(old harness output below the new prompt with no visible clear).

Mitigation: ``FleetSpawner.respawn()`` drains every subscriber queue to
empty FIRST, then pushes the sentinel under ``subscribers_lock``. The
slow consumer loses the tail of the old run (which is fine — they were
behind anyway) but gets a clean clear-then-new-output sequence.

This test wedges a queue at maxsize=1 with stale bytes, calls respawn,
and asserts the next ``get()`` returns the exact sentinel bytes, not any
stale chunk that might have lingered.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from megalodon_ui.mission_config.schema import MissionConfig
from megalodon_ui.spawn import FleetSpawner, LaneSession


SENTINEL = b"\x1bc\xe2\x9f\xb3 restarting\xe2\x80\xa6\r\n"


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
            "mission": {"id": "test", "utc_started": "2026-01-01T00:00:00Z"},
            "lanes": lanes,
            "phases": ["INIT"],
        }
    )


@pytest.mark.asyncio
async def test_respawn_sentinel_is_first_chunk_after_drain(tmp_path: Path, monkeypatch):
    # Force tiny queue so backpressure is easy to wedge.
    monkeypatch.setattr("megalodon_ui.spawn.SSE_PER_SUBSCRIBER_QUEUE_MAXSIZE", 2)

    mission_dir = tmp_path / "mission"
    (mission_dir / ".fleet").mkdir(parents=True)
    stream_log = mission_dir / ".fleet" / "A.stream.log"
    stream_log.touch()
    config = _make_config(["A"])
    adapter = MagicMock()
    adapter.session_log_dir = MagicMock(return_value=None)
    spawner = FleetSpawner(
        mission_dir, config, MagicMock(return_value=adapter),
        mission_dir / ".fleet" / "tmux.sock",
    )
    spawner.sessions["A"] = LaneSession(
        lane="A", name="lane-A", cwd=mission_dir,
        argv=["old"], env={}, stream_log=stream_log,
        session_id="prior-sid", running=True,
    )

    # Slow consumer: subscriber queue full of stale bytes.
    q = await spawner.subscribe("A")
    q.put_nowait(b"stale-A")
    q.put_nowait(b"stale-B")
    assert q.full()

    with (
        patch("megalodon_ui.spawn.tmux.respawn_pane", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
        patch(
            "megalodon_ui.spawn.tmux.display_message_pane_pipe",
            new=AsyncMock(return_value=True),
        ),
    ):
        await spawner.respawn("A", ["new"], {})

    # First chunk available to the subscriber is the sentinel.
    first = q.get_nowait()
    assert first == SENTINEL, (
        f"expected sentinel as first post-respawn chunk, got {first!r}; "
        "drop-oldest backpressure leaked a stale chunk past the drain"
    )


@pytest.mark.asyncio
async def test_respawn_drain_clears_queue_even_for_idle_subscriber(tmp_path: Path):
    """Subscriber with no pending bytes still gets exactly the sentinel."""
    mission_dir = tmp_path / "mission"
    (mission_dir / ".fleet").mkdir(parents=True)
    stream_log = mission_dir / ".fleet" / "A.stream.log"
    stream_log.touch()
    config = _make_config(["A"])
    adapter = MagicMock()
    adapter.session_log_dir = MagicMock(return_value=None)
    spawner = FleetSpawner(
        mission_dir, config, MagicMock(return_value=adapter),
        mission_dir / ".fleet" / "tmux.sock",
    )
    spawner.sessions["A"] = LaneSession(
        lane="A", name="lane-A", cwd=mission_dir,
        argv=["old"], env={}, stream_log=stream_log,
        session_id="prior-sid", running=True,
    )
    q = await spawner.subscribe("A")
    assert q.empty()

    with (
        patch("megalodon_ui.spawn.tmux.respawn_pane", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
        patch(
            "megalodon_ui.spawn.tmux.display_message_pane_pipe",
            new=AsyncMock(return_value=True),
        ),
    ):
        await spawner.respawn("A", ["new"], {})

    # Exactly the sentinel + nothing else (no double-push).
    first = await asyncio.wait_for(q.get(), timeout=0.5)
    assert first == SENTINEL
    assert q.empty()
