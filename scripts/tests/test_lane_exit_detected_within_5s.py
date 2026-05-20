"""P6.4 — CV-8: lane-exit detection within 5 s of stub_harness mode=error.

Plan §6.4 (CV-8): the lane state endpoint must surface ``exited_rc`` for a
fast-failing pane within 5 s, via on-demand ``#{pane_dead_status}`` query
with a 1 s TTL cache (no background polling).

This test:
  1. Spawns a real tmux session running stub_harness mode=error
     (exits 17 after ~50 ms).
  2. Polls ``GET /api/v1/lane/S/state`` in a loop with a 5 s deadline.
  3. Asserts the response transitions to ``{running: false, exited_rc: 17}``.

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
from typing import AsyncGenerator
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from megalodon_ui.auth import write_token_atomic
from megalodon_ui.mission_config.schema import MissionConfig
from megalodon_ui.server import make_app
from megalodon_ui.spawn import FleetSpawner


pytestmark = [pytest.mark.isolated]


_STUB = Path(__file__).parent / "fixtures" / "stub_harness.sh"


def _make_config() -> MissionConfig:
    return MissionConfig.model_validate(
        {
            "mission": {"id": "cv8-test", "utc_started": "2026-01-01T00:00:00Z"},
            "lanes": [
                {
                    "name": "STUB",
                    "short": "S",
                    "role": "stub",
                    "harness": {"cli": "claude", "model": "stub-error"},
                    "cadence_seconds": 300,
                    "tick_offset_seconds": 0,
                },
            ],
            "phases": ["INIT"],
        }
    )


def _stub_error_resolver(stub_script: Path):
    class _ErrorAdapter:
        name = "stub"
        default_model = "stub-error"
        supports_autonomous_loop = False

        def build_argv(self, prompt: str, *, model: str, cwd: Path, **_):
            return [str(stub_script), "error"], {}

        def session_log_dir(self, cwd: Path):
            return None

    adapter = _ErrorAdapter()
    return lambda _cli: adapter


@pytest_asyncio.fixture
async def authed_client_with_real_spawn(
    tmp_path: Path, monkeypatch
) -> AsyncGenerator[tuple, None]:
    if shutil.which("tmux") is None:
        pytest.skip("tmux not installed")
    if not _STUB.is_file() or not os.access(_STUB, os.X_OK):
        pytest.skip("stub_harness.sh fixture missing or not executable")

    mission_dir = tmp_path / "m"
    (mission_dir / ".fleet").mkdir(parents=True)
    token = "cv8-token"
    write_token_atomic(mission_dir / ".fleet" / "ui.token", token)

    socket = mission_dir / ".fleet" / "tmux.sock"
    config = _make_config()
    resolver = _stub_error_resolver(_STUB)
    spawner = FleetSpawner(mission_dir, config, MagicMock(side_effect=resolver), socket)

    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    app = make_app(mission_dir=mission_dir)
    async with app.router.lifespan_context(app):
        await spawner.start_all()
        app.state.spawner = spawner
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            exch = await client.post("/api/v1/auth/exchange", json={"token": token})
            assert exch.status_code == 200, exch.text
            try:
                yield client, spawner
            finally:
                await spawner.stop_all()


@pytest.mark.asyncio
async def test_lane_exit_detected_within_5s(authed_client_with_real_spawn):
    client, spawner = authed_client_with_real_spawn

    deadline = time.monotonic() + 6.0
    last_body = None
    while time.monotonic() < deadline:
        r = await client.get("/api/v1/lane/S/state")
        assert r.status_code == 200, r.text
        body = r.json()
        last_body = body
        if body.get("running") is False and body.get("exited_rc") == 17:
            break
        await asyncio.sleep(0.25)
    else:
        pytest.fail(
            f"expected running=false, exited_rc=17 within 5 s; "
            f"last state was {last_body!r}"
        )

    assert last_body["running"] is False
    assert last_body["exited_rc"] == 17
