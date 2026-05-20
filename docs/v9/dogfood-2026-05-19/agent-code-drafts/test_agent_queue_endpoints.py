"""Integration tests: agent-facing queue proxy endpoints (BUG-STATUS-NOT-WRITTEN).

Tests POST /api/v1/auth/exchange, /api/v1/status/update, /api/v1/task/claim,
/api/v1/task/done, /api/v1/history/append.

These endpoints bridge HTTP ↔ file queue so agents can call them via curl.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import pytest_asyncio

from ui.tests.integration.conftest import wait_for_queue_applied


try:
    from megalodon_ui.server import make_app
    BACKEND_AVAILABLE = True
except ImportError:
    make_app = None  # type: ignore[assignment]
    BACKEND_AVAILABLE = False


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
def fix_medium(tmp_path):
    dst = tmp_path / "fix-medium"
    shutil.copytree(FIXTURES / "fix-medium", dst)
    # Write a known token so auth tests can succeed.
    fleet_dir = dst / ".fleet"
    fleet_dir.mkdir(exist_ok=True)
    (fleet_dir / "ui.token").write_text("test-token-abc123")
    return dst


@pytest_asyncio.fixture
async def client(fix_medium, monkeypatch):
    if not BACKEND_AVAILABLE:
        pytest.skip("awaits megalodon_ui.server")
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    from httpx import AsyncClient, ASGITransport
    app = make_app(mission_dir=fix_medium)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c


@pytest_asyncio.fixture
async def authed_client(client):
    """Client with a valid session cookie already set."""
    r = await client.post("/api/v1/auth/exchange", json={"token": "test-token-abc123"})
    assert r.status_code == 200, f"auth failed: {r.text}"
    assert r.json()["ok"] is True
    return client


# ── auth/exchange ──────────────────────────────────────────────────────────


async def test_auth_exchange_valid_token(client):
    r = await client.post("/api/v1/auth/exchange", json={"token": "test-token-abc123"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert "megalodon_session" in r.cookies


async def test_auth_exchange_wrong_token(client):
    r = await client.post("/api/v1/auth/exchange", json={"token": "wrong-token"})
    assert r.status_code == 401


# ── status/update ──────────────────────────────────────────────────────────


async def test_status_update_requires_auth(client):
    r = await client.post("/api/v1/status/update",
                          json={"lane": "BACKEND", "agent": "agent-8cd0", "new_state": "idle"})
    assert r.status_code == 401


async def test_status_update_missing_fields(authed_client):
    r = await authed_client.post("/api/v1/status/update",
                                 json={"lane": "BACKEND"})
    assert r.status_code == 422


async def test_status_update_enqueues(authed_client, fix_medium):
    r = await authed_client.post("/api/v1/status/update", json={
        "lane": "BACKEND", "agent": "agent-8cd0",
        "new_state": "idle", "new_utc": "2026-05-20T00:00:00Z",
    })
    assert r.status_code == 202
    rid = r.json()["request_id"]
    final = await wait_for_queue_applied(authed_client, rid, mission_dir=fix_medium)
    assert final["status"] == "applied"
    status_text = (fix_medium / "STATUS.md").read_text()
    assert "idle" in status_text


# ── task/claim ─────────────────────────────────────────────────────────────


async def test_task_claim_requires_auth(client):
    r = await client.post("/api/v1/task/claim",
                          json={"lane": "C", "task_id": "P1-C", "agent": "agent-8cd0"})
    assert r.status_code == 401


async def test_task_claim_missing_fields(authed_client):
    r = await authed_client.post("/api/v1/task/claim",
                                 json={"lane": "C"})
    assert r.status_code == 422


async def test_task_claim_enqueues(authed_client, fix_medium):
    r = await authed_client.post("/api/v1/task/claim", json={
        "lane": "C", "task_id": "P1-C", "agent": "agent-8cd0",
    })
    assert r.status_code == 202
    rid = r.json()["request_id"]
    final = await wait_for_queue_applied(authed_client, rid, mission_dir=fix_medium)
    assert final["status"] == "applied"
    tasks_text = (fix_medium / "TASKS.md").read_text()
    assert "[claimed: agent-8cd0" in tasks_text


# ── task/done ──────────────────────────────────────────────────────────────


async def test_task_done_enqueues(authed_client, fix_medium):
    # First claim it so done can succeed.
    r = await authed_client.post("/api/v1/task/claim", json={
        "lane": "C", "task_id": "P2-C", "agent": "agent-8cd0",
    })
    assert r.status_code == 202
    await wait_for_queue_applied(authed_client, r.json()["request_id"], mission_dir=fix_medium)

    r = await authed_client.post("/api/v1/task/done", json={
        "lane": "C", "task_id": "P2-C", "agent": "agent-8cd0",
    })
    assert r.status_code == 202
    rid = r.json()["request_id"]
    final = await wait_for_queue_applied(authed_client, rid, mission_dir=fix_medium)
    assert final["status"] == "applied"
    tasks_text = (fix_medium / "TASKS.md").read_text()
    assert "[done: agent-8cd0" in tasks_text


# ── history/append ─────────────────────────────────────────────────────────


async def test_history_append_missing_fields(authed_client):
    r = await authed_client.post("/api/v1/history/append",
                                 json={"lane": "C", "agent": "agent-8cd0"})
    assert r.status_code == 422


async def test_history_append_enqueues(authed_client, fix_medium):
    r = await authed_client.post("/api/v1/history/append", json={
        "lane": "C", "agent": "agent-8cd0",
        "task_id": "P1-C",
        "finding_path": "agent-8cd0-C-P1-test-2026-05-20T00-00-00Z.md",
        "severity": "INFO",
    })
    assert r.status_code == 202
    rid = r.json()["request_id"]
    final = await wait_for_queue_applied(authed_client, rid, mission_dir=fix_medium)
    assert final["status"] == "applied"
    history_text = (fix_medium / "HISTORY.md").read_text()
    assert "agent-8cd0" in history_text
    assert "P1-C" in history_text
