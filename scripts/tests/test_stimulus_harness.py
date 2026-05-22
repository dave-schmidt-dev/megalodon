"""v9.4 Phase 3 — stimulus harness asserts the dashboard reflects forced events.

These tests spin a REAL uvicorn server on an ephemeral port (fake-spawner mode)
and CALL the actual harness functions (run_stale_lane_check,
run_signal_fidelity_check) against its base_url, asserting on the returned
StimulusResult.passed. This is deliberately NOT a re-implementation of the
endpoint calls inline: if a harness function regresses, these tests fail.

Server contract (megalodon_ui/server.py):
  - _test/stale_override (~line 2490): query params lane+seconds, X-CSRF-Token,
    cookie-gated (so the harness authenticates via /api/v1/auth/exchange).
  - /api/v1/lanes/stale              : {stale_lanes: [{lane, silent_seconds, ...}]}
  - /api/v1/state                    : {signals: {list: [...]}, status, findings, ...}

The activity-wall + empty-state fidelity assertions are DOM-level Playwright
tests in ui/tests/e2e/visibility.spec.ts (the byte-emit / set_state paths cannot
be made fidelity-bearing at the Python level).
"""

from __future__ import annotations

import asyncio
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Iterator

import httpx
import pytest
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.auth import write_token_atomic
from megalodon_ui.mission_config.schema import MissionConfig
from megalodon_ui.server import make_app
from runs_harness.stimulus import (  # noqa: E402
    StimulusResult,
    run_signal_fidelity_check,
    run_stale_lane_check,
)

TOKEN = "stimulus-harness-test-token"
LANE_SHORT = "A"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mission_config() -> MissionConfig:
    """Minimal two-lane MissionConfig (A + B so signal A→B filename is valid)."""
    return MissionConfig.model_validate(
        {
            "mission": {"id": "stimulus-test", "utc_started": "2026-01-01T00:00:00Z"},
            "lanes": [
                {
                    "name": "AUDIT",
                    "short": "A",
                    "role": "auditor",
                    "harness": {"cli": "claude", "model": "claude-sonnet-4-6"},
                    "cadence_seconds": 300,
                    "tick_offset_seconds": 0,
                },
                {
                    "name": "BACKEND",
                    "short": "B",
                    "role": "backend",
                    "harness": {"cli": "claude", "model": "claude-sonnet-4-6"},
                    "cadence_seconds": 300,
                    "tick_offset_seconds": 0,
                },
            ],
            "phases": ["INIT"],
        }
    )


def _setup_mission(mission_dir: Path) -> None:
    """Create minimal required mission directory structure + token + config."""
    fleet = mission_dir / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    write_token_atomic(fleet / "ui.token", TOKEN)
    (mission_dir / "STATUS.md").write_text(
        "# Status board\n\n"
        "| Lane | Agent | State | Last UTC | Notes |\n"
        "|---|---|---|---|---|\n"
        "| A | agent-a | working | 2026-01-01T00:00:00Z | - |\n"
        "| B | agent-b | working | 2026-01-01T00:00:00Z | - |\n"
    )
    (mission_dir / "TASKS.md").write_text("# Tasks\n")
    (mission_dir / "HISTORY.md").write_text("# History\n")
    (mission_dir / "findings").mkdir(exist_ok=True)
    (mission_dir / "signals").mkdir(exist_ok=True)

    import yaml

    (mission_dir / ".mission-config.yaml").write_text(
        yaml.dump(_make_mission_config().model_dump(mode="json"))
    )


def _free_port() -> int:
    """Bind to port 0 to discover a free ephemeral port, then release it."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------------------------------------------------------------------------
# Fixture: a REAL uvicorn server on an ephemeral port (fake-spawner mode)
# ---------------------------------------------------------------------------


@pytest.fixture
def live_server(tmp_path: Path, monkeypatch) -> Iterator[tuple[str, Path]]:
    """Start a real uvicorn server in a background thread; yield (base_url, mission_dir).

    The fake-spawner env (MEGALODON_FAKE_SPAWNER=1) must be set BEFORE make_app()
    so the _test/* and __fake__/* routes register and the fake-spawner lifespan
    branch runs. Bound to a discovered free port so the harness's TCP client can
    reach it via base_url.
    """
    monkeypatch.setenv("MEGALODON_FAKE_SPAWNER", "1")
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")

    mission_dir = tmp_path / "mission"
    mission_dir.mkdir()
    _setup_mission(mission_dir)

    port = _free_port()
    app = make_app(mission_dir=mission_dir, port=port)

    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for readiness (healthz returns 200 once lifespan startup completes).
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 15.0
    ready = False
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base_url}/healthz", timeout=1.0)
            if r.status_code == 200:
                ready = True
                break
        except Exception:
            pass
        time.sleep(0.1)
    if not ready:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError(f"live server did not become ready on {base_url}")

    try:
        yield base_url, mission_dir
    finally:
        server.should_exit = True
        thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Task 3.1 — StimulusResult shape
# ---------------------------------------------------------------------------


def test_stimulus_result_shape():
    """StimulusResult dataclass has the required fields."""
    r = StimulusResult(name="x", passed=True, detail="ok", latency_ms=12.0)
    assert r.passed
    assert r.name == "x"
    assert r.latency_ms == 12.0
    assert r.detail == "ok"


# ---------------------------------------------------------------------------
# Check 1 — stale lane (CALLS the real harness function)
# ---------------------------------------------------------------------------


def test_run_stale_lane_check_passes(live_server):
    """run_stale_lane_check returns passed=True against a healthy live server."""
    base_url, _ = live_server
    result = asyncio.run(
        run_stale_lane_check(base_url, LANE_SHORT, deadline_s=10.0, token=TOKEN)
    )
    assert isinstance(result, StimulusResult)
    assert result.name == "stale-lane"
    assert result.passed, result.detail


def test_run_stale_lane_check_fails_without_auth(live_server):
    """Without a token, the cookie-gated stale endpoints 401 → check fails.

    Proves the check genuinely exercises the gated endpoint (not hollow): if it
    ignored auth or hardcoded success, this would wrongly pass.
    """
    base_url, _ = live_server
    result = asyncio.run(
        run_stale_lane_check(base_url, LANE_SHORT, deadline_s=3.0, token=None)
    )
    assert isinstance(result, StimulusResult)
    assert not result.passed, "expected failure without auth (401 gate)"


# ---------------------------------------------------------------------------
# Check 2 — signal fidelity (CALLS the real harness function)
# ---------------------------------------------------------------------------


def test_run_signal_fidelity_check_passes(live_server):
    """run_signal_fidelity_check writes a unique signal file; asserts /state reflects it."""
    base_url, mission_dir = live_server
    result = asyncio.run(
        run_signal_fidelity_check(
            base_url, str(mission_dir), deadline_s=10.0, token=TOKEN
        )
    )
    assert isinstance(result, StimulusResult)
    assert result.name == "signal-fidelity"
    assert result.passed, result.detail


def test_run_signal_fidelity_check_unique_per_call(live_server):
    """Two calls write distinct files; both pass on their OWN file (not a stale one)."""
    base_url, mission_dir = live_server
    r1 = asyncio.run(
        run_signal_fidelity_check(
            base_url, str(mission_dir), deadline_s=10.0, token=TOKEN
        )
    )
    r2 = asyncio.run(
        run_signal_fidelity_check(
            base_url, str(mission_dir), deadline_s=10.0, token=TOKEN
        )
    )
    assert r1.passed, r1.detail
    assert r2.passed, r2.detail
    # The two checks reference different filenames in their detail strings.
    assert r1.detail != r2.detail, "filenames must be unique per call"


def test_run_signal_fidelity_check_fails_when_not_served(live_server):
    """If the harness writes outside the served mission dir, /state never reflects it.

    Proves the signal check genuinely round-trips through the server: pointing it
    at a sibling directory the server does NOT watch makes /api/v1/state never
    show the file, so the check must fail.
    """
    base_url, mission_dir = live_server
    other_dir = mission_dir.parent / "not-the-served-mission"
    other_dir.mkdir(exist_ok=True)

    result = asyncio.run(
        run_signal_fidelity_check(base_url, str(other_dir), deadline_s=3.0, token=TOKEN)
    )
    assert isinstance(result, StimulusResult)
    assert not result.passed, "expected failure when signal written outside served dir"
