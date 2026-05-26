"""Real ``tail -F`` end-to-end smoke for the SSE producer (Task 4.1).

Plan §6.2: the producer is ``tail -c +1 -F <stream_log>``. Test asserts:
  - Bytes appended to the stream log file appear in subscriber queues
    within ~2 s of the write.
  - Bytes are delivered byte-identical (no encoding mangling at the
    producer level — base64 encoding is applied later, at the SSE emit
    boundary, per CR-5).

Marked ``@pytest.mark.isolated`` because shared event-loop state across
tests can interleave file writes and subprocess stdout buffering.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from megalodon_ui.mission_config.schema import MissionConfig
from megalodon_ui.spawn import FleetSpawner


pytestmark = [
    pytest.mark.skipif(shutil.which("tail") is None, reason="tail not on PATH"),
    pytest.mark.isolated,
]


SOCKET = Path("/tmp/test-fleet-tail-real.sock")


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


async def _drain_until(
    q: asyncio.Queue[bytes], expected_total: int, timeout: float = 4.0
) -> bytes:
    """Read from q until ``expected_total`` bytes accumulated or timeout."""
    buf = b""
    deadline = asyncio.get_event_loop().time() + timeout
    while len(buf) < expected_total:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        try:
            chunk = await asyncio.wait_for(q.get(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        buf += chunk
    return buf


@pytest.mark.asyncio
async def test_real_tail_delivers_appended_bytes(
    tmp_path: Path, governor_scripts_link
) -> None:
    """Bytes appended to the stream log appear in subscriber queues."""
    mission_dir = tmp_path / "mission"
    (mission_dir / ".fleet").mkdir(parents=True)
    # start_all() runs the (default-enabled) governor preflight before the tmux
    # mocks below ever apply; without the run-dir scripts/ symlink it raises
    # GovernorPreflightError. Wire it as new_run.sh would.
    governor_scripts_link(mission_dir)
    stream_log = mission_dir / ".fleet" / "A.stream.log"
    stream_log.touch()

    adapter = MagicMock()
    adapter.build_argv = MagicMock(return_value=(["stub"], {}))
    adapter.session_log_dir = MagicMock(return_value=None)
    spawner = FleetSpawner(
        mission_dir, _make_config(["A"]), MagicMock(return_value=adapter), SOCKET
    )

    with (
        patch("megalodon_ui.spawn.tmux.list_sessions", new=AsyncMock(return_value=[])),
        patch("megalodon_ui.spawn.tmux.new_session", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
    ):
        await spawner.start_all()

    try:
        q = await spawner.subscribe("A")

        # Append non-trivial bytes including ANSI escape + non-UTF-8 byte.
        payload = b"\x1b[31mred\x1b[0m\xff\xfetail-test\n"
        with stream_log.open("ab") as f:
            f.write(payload)
            f.flush()

        got = await _drain_until(q, len(payload), timeout=4.0)
        assert payload in got, f"expected {payload!r} in {got!r}"
    finally:
        await spawner.stop_all()
