"""Fix-Round-3 — server-side CONTROL MODE enforcement.

CONTRACT-CONTROL-MODE: control mode is a GLOBAL, process-wide flag stored on
the app context (default OFF / read-only). When OFF, every destructive /
mutating endpoint must fail closed with 403 ``{"detail": "control mode
required"}`` AFTER passing the CSRF check (order: auth → CSRF → control-mode →
handler). The flag is toggled via ``POST /api/v1/control-mode`` (auth-gated +
CSRF-protected), body ``{"enabled": bool}``, returns ``{"control_mode": bool}``.
It is surfaced in ``GET /api/v1/config`` as ``"control_mode": <bool>``.

These tests assert:
  * default OFF → mutating endpoints 403 with the control-mode detail.
  * POST /api/v1/control-mode requires CSRF (missing → 403, wrong → 403).
  * toggling ON then OFF persists for the process (config reflects it).
  * with control mode ON + valid CSRF, mutating endpoints no longer 403 on the
    control-mode check (they proceed; may 404/422 on their own merits).
  * CSRF check runs BEFORE the control-mode check (missing CSRF → CSRF detail,
    not control-mode detail, even when control mode is OFF).
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from megalodon_ui.auth import write_token_atomic
from megalodon_ui.server import make_app


pytestmark = pytest.mark.integration

TOKEN = "control-mode-test-token"


@pytest_asyncio.fixture
async def cm_client(tmp_path: Path, monkeypatch) -> AsyncGenerator[tuple, None]:
    """Authenticated client against a minimal mission dir. Control mode OFF."""
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")

    fleet = tmp_path / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    write_token_atomic(fleet / "ui.token", TOKEN)

    (tmp_path / "STATUS.md").write_text(
        "# Status\n\n| Lane | Agent | State | Last UTC | Notes |\n"
        "|---|---|---|---|---|\n"
        "| AUDIT | agent-1 | working: P1-A | 2026-01-01T00:00Z | n |\n"
    )
    (tmp_path / "TASKS.md").write_text("# Tasks\n")
    (tmp_path / "HISTORY.md").write_text("# History\n")
    (tmp_path / "README.md").write_text("**Current: ACTIVE**\n")

    app = make_app(mission_dir=tmp_path)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            exch = await client.post("/api/v1/auth/exchange", json={"token": TOKEN})
            assert exch.status_code == 200, exch.text
            csrf = app.state.megalodon.csrf_token
            yield client, app, csrf, tmp_path


async def _enable_control_mode(client, csrf):
    return await client.post(
        "/api/v1/control-mode",
        json={"enabled": True},
        headers={"X-CSRF-Token": csrf},
    )


# ---------------------------------------------------------------------------
# POST /api/v1/control-mode contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_control_mode_default_off_in_config(cm_client):
    client, _app, _csrf, _ = cm_client
    r = await client.get("/api/v1/config")
    assert r.status_code == 200, r.text
    assert r.json()["control_mode"] is False


@pytest.mark.asyncio
async def test_control_mode_toggle_requires_csrf_missing(cm_client):
    client, _app, _csrf, _ = cm_client
    r = await client.post("/api/v1/control-mode", json={"enabled": True})
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_control_mode_toggle_requires_csrf_wrong(cm_client):
    client, _app, _csrf, _ = cm_client
    r = await client.post(
        "/api/v1/control-mode",
        json={"enabled": True},
        headers={"X-CSRF-Token": "wrong-token"},
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_control_mode_toggle_returns_flag_and_persists(cm_client):
    client, _app, csrf, _ = cm_client
    on = await _enable_control_mode(client, csrf)
    assert on.status_code == 200, on.text
    assert on.json() == {"control_mode": True}

    # Config reflects the flag for the process.
    cfg = await client.get("/api/v1/config")
    assert cfg.json()["control_mode"] is True

    # Toggle back OFF.
    off = await client.post(
        "/api/v1/control-mode",
        json={"enabled": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert off.status_code == 200, off.text
    assert off.json() == {"control_mode": False}

    cfg2 = await client.get("/api/v1/config")
    assert cfg2.json()["control_mode"] is False


@pytest.mark.asyncio
async def test_control_mode_surfaced_in_state(cm_client):
    """GET /api/v1/state aggregates config — control_mode must appear there too."""
    client, _app, csrf, _ = cm_client
    await _enable_control_mode(client, csrf)
    r = await client.get("/api/v1/state")
    assert r.status_code == 200, r.text
    assert r.json()["config"]["control_mode"] is True


# ---------------------------------------------------------------------------
# Destructive endpoints fail closed when control mode is OFF
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signal_403_when_control_mode_off(cm_client):
    client, _app, csrf, _ = cm_client
    r = await client.post(
        "/api/v1/signal",
        json={"to_lane": "AUDIT", "claim": "c", "evidence": "findings/x.md:1"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "control mode required"


@pytest.mark.asyncio
async def test_status_update_403_when_control_mode_off(cm_client):
    client, _app, csrf, _ = cm_client
    r = await client.post(
        "/api/v1/status/update",
        json={"lane": "AUDIT", "agent": "agent-1", "new_state": "idle"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "control mode required"


@pytest.mark.asyncio
async def test_mission_status_403_when_control_mode_off(cm_client):
    client, _app, csrf, _ = cm_client
    r = await client.post(
        "/api/v1/mission-status",
        json={"status": "ACTIVE"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "control mode required"


@pytest.mark.asyncio
async def test_approval_rules_403_when_control_mode_off(cm_client):
    client, _app, csrf, _ = cm_client
    r = await client.post(
        "/api/v1/approval-rules",
        json={"pattern": "Bash(npm *)", "added_by_session": "s1"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "control mode required"


@pytest.mark.asyncio
async def test_legacy_signal_403_when_control_mode_off(cm_client):
    client, _app, csrf, _ = cm_client
    r = await client.post(
        "/api/lanes/AUDIT/signal",
        json={"text": "hi", "cite": "findings/x.md:1"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "control mode required"


# ---------------------------------------------------------------------------
# CSRF check fires BEFORE the control-mode check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_csrf_runs_before_control_mode(cm_client):
    """Missing CSRF with control mode OFF → CSRF detail, not control-mode detail."""
    client, _app, _csrf, _ = cm_client
    r = await client.post(
        "/api/v1/signal",
        json={"to_lane": "AUDIT", "claim": "c", "evidence": "findings/x.md:1"},
        # no X-CSRF-Token
    )
    assert r.status_code == 403, r.text
    assert "CSRF" in r.json()["detail"]


# ---------------------------------------------------------------------------
# With control mode ON, mutating endpoints pass the control-mode gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mission_status_succeeds_when_control_mode_on(cm_client):
    client, _app, csrf, _ = cm_client
    await _enable_control_mode(client, csrf)
    r = await client.post(
        "/api/v1/mission-status",
        json={"status": "ACTIVE"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "status": "ACTIVE"}


@pytest.mark.asyncio
async def test_signal_passes_control_gate_when_on(cm_client):
    """Control mode ON + valid CSRF → no 403; signal proceeds to 202."""
    client, _app, csrf, _ = cm_client
    await _enable_control_mode(client, csrf)
    r = await client.post(
        "/api/v1/signal",
        json={"to_lane": "AUDIT", "claim": "c", "evidence": "findings/x.md:1"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code != 403, r.text
    assert r.status_code == 202, r.text
