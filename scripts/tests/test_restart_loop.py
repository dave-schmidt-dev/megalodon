"""v9.4 Task 2.5 — POST /api/v1/lane/{short}/restart-loop endpoint tests.

Covers:
- Happy path: valid CSRF + known lane with initial_prompt → 202; send_keys called correctly
- CSRF mismatch → 403
- Missing CSRF header → 403
- Unknown lane → 404
- Missing/empty initial_prompt → 409
- Audit log: file written with source="restart-loop" and correct SHA-256
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from megalodon_ui.auth import write_token_atomic
from megalodon_ui.mission_config.schema import MissionConfig
from megalodon_ui.server import make_app
from megalodon_ui.spawn import FleetSpawner, LaneSession


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TOKEN = "restart-loop-test-token"
CSRF = "restart-loop-test-csrf"
LANE_SHORT = "A"
SESSION_NAME = f"lane-{LANE_SHORT}"
INITIAL_PROMPT_TEXT = "python3 -c 'print(123)'"


def _make_config(shorts: list[str], cli: str = "claude") -> MissionConfig:
    lanes = [
        {
            "name": f"LANE{s}",
            "short": s,
            "role": f"role-{s.lower()}",
            "harness": {"cli": cli, "model": "claude-sonnet-4-6"},
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
# Fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def restart_loop_client(
    tmp_path: Path, monkeypatch
) -> AsyncGenerator[tuple, None]:
    """Authenticated httpx client with mocked spawner and send_keys stub."""
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")

    fleet = tmp_path / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    write_token_atomic(fleet / "ui.token", TOKEN)

    # Minimal mission files
    (tmp_path / "STATUS.md").write_text("# Status\n")
    (tmp_path / "TASKS.md").write_text("# Tasks\n")
    (tmp_path / "HISTORY.md").write_text("# History\n")

    socket = fleet / "tmux.sock"
    config = _make_config([LANE_SHORT])

    adapter_resolver = MagicMock()
    spawner = FleetSpawner(tmp_path, config, adapter_resolver, socket)

    # Pre-populate session with initial_prompt
    stream_log = fleet / f"{LANE_SHORT}.stream.log"
    stream_log.touch()
    spawner.sessions[LANE_SHORT] = LaneSession(
        lane=LANE_SHORT,
        name=SESSION_NAME,
        cwd=tmp_path,
        argv=["stub"],
        env={},
        stream_log=stream_log,
        session_id="test-session-id",
        running=True,
        initial_prompt=INITIAL_PROMPT_TEXT,
    )

    # send_keys stub — tracks calls, returns 0
    send_keys_calls: list[tuple] = []

    async def stub_send_keys(socket_, name, keys, *, enter=True):
        send_keys_calls.append((socket_, name, keys, enter))
        return 0

    import megalodon_ui.tmux as tmux_mod

    monkeypatch.setattr(tmux_mod, "send_keys", stub_send_keys)

    app = make_app(mission_dir=tmp_path)

    async with app.router.lifespan_context(app):
        app.state.spawner = spawner
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Authenticate
            exch = await client.post("/api/v1/auth/exchange", json={"token": TOKEN})
            assert exch.status_code == 200, f"auth failed: {exch.text}"

            # Retrieve the actual CSRF token from the app's config endpoint
            config_r = await client.get("/api/v1/config")
            csrf_token = config_r.json().get("csrf_token", "")

            yield client, send_keys_calls, csrf_token, tmp_path, spawner


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restart_loop_happy_path(restart_loop_client):
    """Happy path: valid CSRF + known lane with initial_prompt → 202."""
    client, calls, csrf_token, _, _ = restart_loop_client
    resp = await client.post(
        f"/api/v1/lane/{LANE_SHORT}/restart-loop",
        json={},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 202, resp.text
    assert resp.json() == {"ok": True}

    # send_keys called with the initial_prompt and enter=True
    assert len(calls) == 1
    socket_, name, keys, enter = calls[0]
    assert name == SESSION_NAME
    assert keys == INITIAL_PROMPT_TEXT
    assert enter is True


# ---------------------------------------------------------------------------
# CSRF checks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restart_loop_csrf_mismatch_returns_403(restart_loop_client):
    client, _, _, _, _ = restart_loop_client
    resp = await client.post(
        f"/api/v1/lane/{LANE_SHORT}/restart-loop",
        json={},
        headers={"X-CSRF-Token": "wrong-token"},
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_restart_loop_missing_csrf_header_returns_403(restart_loop_client):
    client, _, _, _, _ = restart_loop_client
    resp = await client.post(
        f"/api/v1/lane/{LANE_SHORT}/restart-loop",
        json={},
        # No X-CSRF-Token header
    )
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# Unknown lane
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restart_loop_unknown_lane_returns_404(restart_loop_client):
    client, _, csrf_token, _, _ = restart_loop_client
    resp = await client.post(
        "/api/v1/lane/UNKNOWN/restart-loop",
        json={},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Missing/empty initial_prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restart_loop_no_initial_prompt_returns_409(restart_loop_client):
    """When initial_prompt is None, return 409 Conflict."""
    client, _, csrf_token, _, spawner = restart_loop_client
    # Clear the initial_prompt
    spawner.sessions[LANE_SHORT].initial_prompt = None

    resp = await client.post(
        f"/api/v1/lane/{LANE_SHORT}/restart-loop",
        json={},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 409, resp.text
    assert "no initial_prompt" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_restart_loop_empty_initial_prompt_returns_409(restart_loop_client):
    """When initial_prompt is empty string, return 409 Conflict."""
    client, _, csrf_token, _, spawner = restart_loop_client
    # Set to empty string
    spawner.sessions[LANE_SHORT].initial_prompt = ""

    resp = await client.post(
        f"/api/v1/lane/{LANE_SHORT}/restart-loop",
        json={},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 409, resp.text


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restart_loop_audit_log_written(restart_loop_client):
    """Audit log file is created with source='restart-loop'."""
    client, _, csrf_token, mission_dir, _ = restart_loop_client
    resp = await client.post(
        f"/api/v1/lane/{LANE_SHORT}/restart-loop",
        json={},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 202, resp.text

    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = mission_dir / ".fleet" / f"inject-log-{today}.jsonl"
    assert log_path.exists(), f"audit log not found at {log_path}"

    lines = log_path.read_text().strip().splitlines()
    assert lines, "audit log is empty"

    # Find the restart-loop entry (should be the last one)
    entry = json.loads(lines[-1])
    assert entry["source"] == "restart-loop"
    assert entry["lane"] == LANE_SHORT
    expected_sha256 = hashlib.sha256(INITIAL_PROMPT_TEXT.encode("utf-8")).hexdigest()
    assert entry["text_sha256"] == expected_sha256, (
        f"expected sha256 {expected_sha256!r}, got {entry['text_sha256']!r}"
    )
    assert entry["byte_count"] == len(INITIAL_PROMPT_TEXT.encode("utf-8"))
    assert entry["enter"] is True


@pytest.mark.asyncio
async def test_restart_loop_audit_log_reuses_inject_file(restart_loop_client):
    """Both inject and restart-loop write to the same inject-log-YYYY-MM-DD.jsonl file."""
    client, _, csrf_token, mission_dir, _ = restart_loop_client

    # Make an inject call
    resp1 = await client.post(
        f"/api/v1/lane/{LANE_SHORT}/inject",
        json={"text": "test inject", "enter": True},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp1.status_code == 202, resp1.text

    # Make a restart-loop call
    resp2 = await client.post(
        f"/api/v1/lane/{LANE_SHORT}/restart-loop",
        json={},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp2.status_code == 202, resp2.text

    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = mission_dir / ".fleet" / f"inject-log-{today}.jsonl"
    assert log_path.exists()

    lines = log_path.read_text().strip().splitlines()
    assert len(lines) >= 2, "expected at least 2 entries (inject + restart-loop)"

    # First entry: inject with source="inject" (or no source field)
    entry1 = json.loads(lines[0])
    assert entry1.get("source") != "restart-loop"

    # Last entry: restart-loop with source="restart-loop"
    entry2 = json.loads(lines[-1])
    assert entry2["source"] == "restart-loop"
