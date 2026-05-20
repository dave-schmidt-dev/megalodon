"""Regression test for v9.3.1 — POST /api/v1/...?wait=true blocks for applier
resolution and returns the final status in a single round-trip.

Why it matters: without ?wait=true, agents wrote `for i in 1..5; do curl /queue/<rid>; done`
polling loops. Compound bash trips the operator-approval prompt. The sync path
eliminates polling so one curl per intent is the contract.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from megalodon_ui.server import make_app  # type: ignore[import-not-found]

    BACKEND_AVAILABLE = True
except ImportError:
    make_app = None  # type: ignore[assignment]
    BACKEND_AVAILABLE = False


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


FIXTURES = Path(__file__).resolve().parents[2] / "ui" / "tests" / "fixtures"


def _ignore_runtime_state(_src, names):
    return [
        n
        for n in names
        if n.endswith(".stream.log") or n == "tmux.sock" or n == "dashboard.url"
    ]


@pytest_asyncio.fixture
async def async_client(tmp_path, monkeypatch):
    """AsyncClient with lifespan + in-process applier so ?wait=true resolves."""
    if not BACKEND_AVAILABLE:
        pytest.skip("megalodon_ui.server not importable")

    mission = tmp_path / "mission"
    shutil.copytree(FIXTURES / "fix-medium", mission, ignore=_ignore_runtime_state)

    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    monkeypatch.setenv("MEGALODON_INPROCESS_APPLIER", "1")

    from httpx import AsyncClient, ASGITransport  # type: ignore[import-not-found]

    app = make_app(mission_dir=mission)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client, mission


async def test_task_claim_wait_true_returns_final_status(async_client):
    client, _mission = async_client
    r = await client.post(
        "/api/v1/task/claim?wait=true",
        json={"lane": "AUDIT", "task_id": "P1-A", "agent": "agent-test"},
    )
    # Sync mode: 200 (applied) or 409 (rejected) — never 202.
    assert r.status_code in (200, 409), (r.status_code, r.text)
    body = r.json()
    assert body["status"] in ("applied", "rejected"), body
    assert body["request_id"]
    assert body["intent"] == "TASKS_BRACKET"


async def test_task_claim_default_is_async_202(async_client):
    client, _mission = async_client
    r = await client.post(
        "/api/v1/task/claim",
        json={"lane": "AUDIT", "task_id": "P1-A", "agent": "agent-test"},
    )
    # Backwards compat: legacy callers still get 202.
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "pending"
    assert "/api/v1/queue/" in r.headers.get("location", "")


async def test_status_update_wait_true(async_client):
    client, _mission = async_client
    r = await client.post(
        "/api/v1/status/update?wait=true",
        json={
            "lane": "AUDIT",
            "agent": "agent-test",
            "new_state": "working: P1-A",
            "new_utc": "2026-05-19T22:30:00Z",
        },
    )
    # Status update may be 200 applied or 409 rejected (e.g. row-not-found);
    # the test only cares that it's NOT 202 — sync mode returned a verdict.
    assert r.status_code in (200, 409), r.text
    assert r.json()["intent"] == "STATUS_UPDATE"


async def test_history_append_wait_true(async_client):
    client, _mission = async_client
    r = await client.post(
        "/api/v1/history/append?wait=true",
        json={
            "lane": "AUDIT",
            "agent": "agent-test",
            "task_id": "P1-A",
            "finding_path": "agent-test-A-P1-foo-2026-05-19T22-30Z.md",
            "severity": "INFO",
        },
    )
    assert r.status_code in (200, 409), r.text
    assert r.json()["intent"] == "HISTORY_APPEND"


async def test_mission_event_wait_true(async_client):
    client, _mission = async_client
    r = await client.post(
        "/api/v1/mission-event?wait=true",
        json={"lane": "AUDIT", "agent": "agent-test", "event_text": "ping"},
    )
    assert r.status_code in (200, 409), r.text
    assert r.json()["intent"] == "MISSION_EVENT_APPEND"


@pytest.mark.parametrize(
    "wait_value,expect_async",
    [
        ("true", False),
        ("1", False),
        ("yes", False),
        ("TRUE", False),
        ("false", True),
        ("0", True),
        ("", True),
    ],
)
async def test_wait_param_parsing(async_client, wait_value, expect_async):
    client, _mission = async_client
    suffix = f"?wait={wait_value}" if wait_value else ""
    r = await client.post(
        f"/api/v1/task/claim{suffix}",
        json={"lane": "AUDIT", "task_id": "P1-A", "agent": "agent-test"},
    )
    if expect_async:
        assert r.status_code == 202, r.text
    else:
        assert r.status_code in (200, 409), r.text
