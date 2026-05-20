"""v9.4 Task 1.3 — POST /api/v1/lane/{short}/inject endpoint tests.

Covers:
- Happy path: valid CSRF + valid body → 202; send_keys called correctly
- CSRF mismatch → 403
- Missing CSRF header → 403
- Text > 16384 bytes → 413
- Rate limit: 11th call within 60 s → 429
- Audit log: file written with correct JSON shape, SHA-256 correct
- Audit log: raw text NOT stored in the log file
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

TOKEN = "inject-test-token"
CSRF = "inject-test-csrf"
LANE_SHORT = "A"
SESSION_NAME = f"lane-{LANE_SHORT}"


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
async def inject_client(tmp_path: Path, monkeypatch) -> AsyncGenerator[tuple, None]:
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

    # Pre-populate session, bypassing real tmux
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
    )

    # send_keys stub — tracks calls, returns 0
    send_keys_calls: list[tuple] = []

    async def stub_send_keys(socket_, name, keys, *, enter=True):
        send_keys_calls.append((socket_, name, keys, enter))
        return 0

    import megalodon_ui.tmux as tmux_mod

    monkeypatch.setattr(tmux_mod, "send_keys", stub_send_keys)

    # Override the CSRF token so tests can match it exactly
    app = make_app(mission_dir=tmp_path)
    # Patch ctx.csrf_token after app creation
    app.state  # ensure state attr exists after construction
    # Find the MissionContext and patch csrf_token
    # We need access to the ctx closure — grab it via the app's routes' closure
    # The cleanest approach: override via env or patch app internals.
    # Actually, make_app uses config.csrf_token; we patch it post-build by
    # reaching into the routes. Simpler: inject a known CSRF via config.
    # But config is constructed inside make_app. Instead we read the real token
    # from the config and use it in tests.
    # Actually the cleaner way: accept whatever csrf_token is generated and
    # fetch it from the /api/v1/config endpoint or the ctx attribute.
    # Per the existing pattern (server.py:2230) csrf_token is embedded in HTML.
    # For tests we use the /api/v1/config endpoint if available, or we can
    # look at app state after lifespan starts.

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

            yield client, send_keys_calls, csrf_token, tmp_path


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inject_happy_path(inject_client):
    client, calls, csrf_token, _ = inject_client
    resp = await client.post(
        f"/api/v1/lane/{LANE_SHORT}/inject",
        json={"text": "hello world", "enter": True},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 202, resp.text
    assert resp.json() == {"ok": True}

    # send_keys called with the correct args
    assert len(calls) == 1
    socket_, name, keys, enter = calls[0]
    assert name == SESSION_NAME
    assert keys == "hello world"
    assert enter is True


# ---------------------------------------------------------------------------
# CSRF checks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inject_csrf_mismatch_returns_403(inject_client):
    client, _, _, _ = inject_client
    resp = await client.post(
        f"/api/v1/lane/{LANE_SHORT}/inject",
        json={"text": "hello", "enter": False},
        headers={"X-CSRF-Token": "wrong-token"},
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_inject_missing_csrf_header_returns_403(inject_client):
    client, _, _, _ = inject_client
    resp = await client.post(
        f"/api/v1/lane/{LANE_SHORT}/inject",
        json={"text": "hello", "enter": False},
        # No X-CSRF-Token header
    )
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# Text size limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inject_text_too_large_returns_413(inject_client):
    client, _, csrf_token, _ = inject_client
    big_text = "x" * (16384 + 1)
    resp = await client.post(
        f"/api/v1/lane/{LANE_SHORT}/inject",
        json={"text": big_text, "enter": False},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 413, resp.text


@pytest.mark.asyncio
async def test_inject_text_at_limit_is_accepted(inject_client):
    """Exactly 16384 bytes is OK."""
    client, _, csrf_token, _ = inject_client
    boundary_text = "x" * 16384
    resp = await client.post(
        f"/api/v1/lane/{LANE_SHORT}/inject",
        json={"text": boundary_text, "enter": False},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 202, resp.text


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inject_rate_limit_11th_call_returns_429(inject_client):
    client, _, csrf_token, _ = inject_client
    for i in range(10):
        r = await client.post(
            f"/api/v1/lane/{LANE_SHORT}/inject",
            json={"text": f"call-{i}", "enter": False},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert r.status_code == 202, f"call {i} failed: {r.text}"
    # 11th call must be rejected
    r11 = await client.post(
        f"/api/v1/lane/{LANE_SHORT}/inject",
        json={"text": "over-limit", "enter": False},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert r11.status_code == 429, r11.text


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inject_audit_log_written(inject_client):
    """Audit log file is created and the entry has the correct shape."""
    client, _, csrf_token, mission_dir = inject_client
    resp = await client.post(
        f"/api/v1/lane/{LANE_SHORT}/inject",
        json={"text": "hello world", "enter": True},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 202, resp.text

    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = mission_dir / ".fleet" / f"inject-log-{today}.jsonl"
    assert log_path.exists(), f"audit log not found at {log_path}"

    lines = log_path.read_text().strip().splitlines()
    assert lines, "audit log is empty"

    # Parse the last entry (in case previous tests wrote some)
    entry = json.loads(lines[-1])
    assert set(entry.keys()) >= {"ts", "lane", "text_sha256", "byte_count", "enter"}
    assert entry["lane"] == LANE_SHORT
    expected_sha256 = hashlib.sha256("hello world".encode("utf-8")).hexdigest()
    assert entry["text_sha256"] == expected_sha256, (
        f"expected sha256 {expected_sha256!r}, got {entry['text_sha256']!r}"
    )
    assert entry["byte_count"] == len("hello world".encode("utf-8"))
    assert entry["enter"] is True


@pytest.mark.asyncio
async def test_inject_audit_log_does_not_store_raw_text(inject_client):
    """Raw injected text must never appear in the audit log (PII safety)."""
    client, _, csrf_token, mission_dir = inject_client
    sentinel = "super-secret-payload-xyz"
    resp = await client.post(
        f"/api/v1/lane/{LANE_SHORT}/inject",
        json={"text": sentinel, "enter": False},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 202, resp.text

    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = mission_dir / ".fleet" / f"inject-log-{today}.jsonl"
    assert log_path.exists()

    log_content = log_path.read_text()
    assert sentinel not in log_content, (
        f"raw text {sentinel!r} found in audit log — PII leak!"
    )


# ---------------------------------------------------------------------------
# Unknown lane
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inject_unknown_lane_returns_404(inject_client):
    client, _, csrf_token, _ = inject_client
    resp = await client.post(
        "/api/v1/lane/UNKNOWN/inject",
        json={"text": "hi", "enter": False},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 404, resp.text
