"""INV-2 gate test — mission-status single source of truth.

CONTRACT: POST /api/v1/mission-status must write to MISSION.md using the
same ``**Status:** <STATUS>`` marker that ``_read_mission_md_fields()`` reads.
Read == Write: what you POST you can GET back through /api/v1/state.

This is the declared gate_test for INV-2 in ledger.yaml.
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from megalodon_ui.auth import write_token_atomic
from megalodon_ui.server import _read_mission_md_fields, make_app


pytestmark = pytest.mark.integration

TOKEN = "ssot-test-token"


@pytest_asyncio.fixture
async def ssot_client(tmp_path: Path, monkeypatch) -> AsyncGenerator[tuple, None]:
    """Authenticated client against a minimal mission dir with MISSION.md."""
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")

    fleet = tmp_path / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    write_token_atomic(fleet / "ui.token", TOKEN)

    # MISSION.md with the canonical Status marker the reader expects.
    (tmp_path / "MISSION.md").write_text(
        "# Mission (test)\n\n**Status:** IDLE\n**Lanes:** 1\n"
    )
    (tmp_path / "STATUS.md").write_text(
        "# Status\n\n| Lane | Agent | State | Last UTC | Notes |\n"
        "|---|---|---|---|---|\n"
        "| AUDIT | agent-1 | working: P1-A | 2026-01-01T00:00Z | n |\n"
    )
    (tmp_path / "TASKS.md").write_text("# Tasks\n")
    (tmp_path / "HISTORY.md").write_text("# History\n")
    (tmp_path / "README.md").write_text("**Current: IDLE**\n")

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
    r = await client.post(
        "/api/v1/control-mode",
        json={"enabled": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# INV-2: read == write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mission_status_write_is_visible_via_read_helper(ssot_client):
    """POST status → _read_mission_md_fields returns the posted value.

    This is the core INV-2 SSOT assertion: the write path and the read path
    must touch the same file with the same marker.
    """
    client, _app, csrf, tmp_path = ssot_client
    await _enable_control_mode(client, csrf)

    r = await client.post(
        "/api/v1/mission-status",
        json={"status": "ACTIVE"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "status": "ACTIVE"}

    # Read back via the same helper the /state endpoint uses.
    fields = _read_mission_md_fields(tmp_path)
    assert fields.get("status") == "ACTIVE", (
        f"SSOT violation: POST wrote status=ACTIVE but _read_mission_md_fields "
        f"returned {fields!r} — write and read paths use different files/markers."
    )


@pytest.mark.asyncio
async def test_mission_status_write_visible_via_state_endpoint(ssot_client):
    """POST status → GET /api/v1/state reflects the posted value in mission.status."""
    client, _app, csrf, tmp_path = ssot_client
    await _enable_control_mode(client, csrf)

    r = await client.post(
        "/api/v1/mission-status",
        json={"status": "DRAINING"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text

    state_r = await client.get("/api/v1/state")
    assert state_r.status_code == 200, state_r.text
    state = state_r.json()
    mission_status = state.get("mission", {}).get("status")
    assert mission_status == "DRAINING", (
        f"SSOT violation: POSTed status=DRAINING but /state returned "
        f"mission.status={mission_status!r}."
    )


@pytest.mark.asyncio
async def test_mission_status_roundtrip_all_valid_values(ssot_client):
    """Each valid status value round-trips cleanly through write then read."""
    client, _app, csrf, tmp_path = ssot_client
    await _enable_control_mode(client, csrf)

    for status in ("ACTIVE", "DRAINING", "COMPLETE", "IDLE"):
        r = await client.post(
            "/api/v1/mission-status",
            json={"status": status},
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 200, f"POST failed for status={status}: {r.text}"
        fields = _read_mission_md_fields(tmp_path)
        assert fields.get("status") == status, (
            f"SSOT: POSTed {status!r} but read back {fields.get('status')!r}"
        )


@pytest.mark.asyncio
async def test_mission_md_appended_when_no_status_line(ssot_client):
    """If MISSION.md has no **Status:** line, the write appends one."""
    client, _app, csrf, tmp_path = ssot_client
    await _enable_control_mode(client, csrf)

    # Remove the Status line from MISSION.md.
    (tmp_path / "MISSION.md").write_text("# Mission (test)\n\n**Lanes:** 1\n")
    fields_before = _read_mission_md_fields(tmp_path)
    assert "status" not in fields_before

    r = await client.post(
        "/api/v1/mission-status",
        json={"status": "COMPLETE"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text

    fields_after = _read_mission_md_fields(tmp_path)
    assert fields_after.get("status") == "COMPLETE", (
        f"Append case failed: expected COMPLETE, got {fields_after!r}"
    )
