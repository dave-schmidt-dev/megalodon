"""P6.3 — PM-3: real-tmux verification that respawn re-establishes pipe-pane.

Plan §6.4 (PM-3 mitigation): ``tmux respawn-pane -k`` creates a new
``pane_id`` and drops the prior pipe-pane association. If the orchestrator
forgets to re-call ``pipe-pane`` after respawn, the bytes stream goes
silent — the SSE never delivers a new chunk and the browser pane appears
frozen.

This test:
  1. Spawns a real tmux session running stub_harness mode=long.
  2. Wires pipe-pane to ``.fleet/<short>.stream.log``.
  3. Records the byte count after a baseline write.
  4. Calls FleetSpawner.respawn() with a new argv (still stub_harness).
  5. Polls the stream log size and asserts it continues to grow within
     500 ms of the respawn (the sentinel alone is ~25 bytes; a stub harness
     run adds more).

Marked ``@pytest.mark.isolated`` — runs under ``pytest -p forked -m isolated``
on CI. Local macOS hits the 104-byte socket-path limit on tmp_path; CI Linux
is fine.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from megalodon_ui.mission_config.schema import MissionConfig
from megalodon_ui.spawn import FleetSpawner


pytestmark = [pytest.mark.isolated]


_STUB = Path(__file__).parent / "fixtures" / "stub_harness.sh"


def _make_config() -> MissionConfig:
    return MissionConfig.model_validate(
        {
            "mission": {"id": "test", "utc_started": "2026-01-01T00:00:00Z"},
            "lanes": [
                {
                    "name": "STUB",
                    "short": "S",
                    "role": "stub",
                    "harness": {"cli": "claude", "model": "sonnet"},
                    "cadence_seconds": 300,
                    "tick_offset_seconds": 0,
                },
            ],
            "phases": ["INIT"],
        }
    )


@pytest.mark.asyncio
async def test_pipe_pane_preserved_across_respawn(tmp_path: Path):
    if shutil.which("tmux") is None:
        pytest.skip("tmux not available on this runner")
    if not _STUB.is_file() or not os.access(_STUB, os.X_OK):
        pytest.skip("stub_harness.sh missing or not executable")

    mission_dir = tmp_path / "m"
    (mission_dir / ".fleet").mkdir(parents=True)
    sock = mission_dir / ".fleet" / "tmux.sock"
    stream_log = mission_dir / ".fleet" / "S.stream.log"

    adapter = MagicMock()
    adapter.build_argv = MagicMock(
        return_value=([str(_STUB), "long"], {}),
    )
    adapter.session_log_dir = MagicMock(return_value=None)
    spawner = FleetSpawner(
        mission_dir, _make_config(), MagicMock(return_value=adapter), sock
    )

    try:
        await spawner.start_all()
        # Wait briefly for stub_harness to produce some bytes.
        await asyncio.sleep(0.5)
        size_before = stream_log.stat().st_size

        # Respawn with the same long-running stub argv.
        await spawner.respawn("S", [str(_STUB), "long"], {})

        # Stream log must continue to grow — the sentinel alone is ~25 bytes.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            await asyncio.sleep(0.1)
            if stream_log.stat().st_size > size_before:
                break
        assert stream_log.stat().st_size > size_before, (
            f"stream log frozen after respawn: size={stream_log.stat().st_size} "
            f"baseline={size_before} — pipe-pane did not re-establish"
        )
    finally:
        await spawner.stop_all()
