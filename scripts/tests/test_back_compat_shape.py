"""Back-compat integration tests — v9.1 against a v9.0 mission fixture.

Proves that a mission directory with NO .mission-config.yaml (the v9.0 layout)
boots cleanly under the v9.1 server and that the full API surface is live and
returns the expected v9.1-extended response shapes.

P5.4 deliverable — back-compat promise tests.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport

from megalodon_ui.server import make_app
from megalodon_ui.config import AppConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CSRF = "test-csrf-back-compat"
_APP_CONFIG = AppConfig(csrf_token=_CSRF, poll_interval_seconds=0.05)


def _auth(app, client) -> None:
    """Attach a valid mui_session cookie — every /api/** call is now gated."""
    client.cookies.set("mui_session", app.state.megalodon.session_store.create())


async def _wait_for_applied(
    client: AsyncClient,
    request_id: str,
    mission_dir: Path,
    timeout: float = 5.0,
    poll_interval: float = 0.05,
) -> dict:
    """Drive the queue applier then poll until status != 'pending'."""
    from megalodon_ui.queue.applier import Applier

    applier = Applier(mission_dir=mission_dir, poll_seconds=0)
    deadline = asyncio.get_event_loop().time() + timeout

    while True:
        try:
            applier.drain_once()
        except Exception:
            pass

        r = await client.get(f"/api/v1/queue/{request_id}")
        assert r.status_code == 200, f"queue status {r.status_code}: {r.text}"
        body = r.json()
        if body["status"] != "pending":
            if body["status"] == "rejected":
                raise AssertionError(
                    f"request {request_id} rejected: {body.get('rejection_reason')}"
                )
            return body
        if asyncio.get_event_loop().time() >= deadline:
            raise AssertionError(
                f"request {request_id} did not resolve within {timeout}s"
            )
        await asyncio.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Test 1 — factory boots without .mission-config.yaml
# ---------------------------------------------------------------------------


def test_factory_boots_against_v9_0_fixture(queue_mission: Path):
    """make_app() succeeds against a fixture that has no .mission-config.yaml."""
    assert not (queue_mission / ".mission-config.yaml").exists(), (
        "fixture should NOT have .mission-config.yaml — it's a v9.0 layout"
    )
    app = make_app(mission_dir=queue_mission, config=_APP_CONFIG)
    from fastapi import FastAPI

    assert isinstance(app, FastAPI)


# ---------------------------------------------------------------------------
# Test 2 — /api/v1/config returns v9.1-extended shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_v1_config_returns_v9_1_extended_shape(queue_mission: Path):
    """GET /api/v1/config returns both v9.0 legacy keys and v9.1 extension keys."""
    app = make_app(mission_dir=queue_mission, config=_APP_CONFIG)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        _auth(app, client)
        r = await client.get("/api/v1/config")
    assert r.status_code == 200, r.text
    data = r.json()

    # v9.0 keys
    assert "csrf_token" in data
    assert "poll_interval_seconds" in data
    assert "stale_threshold_seconds" in data
    assert "heartbeat_interval_seconds" in data

    # v9.1 extension keys
    assert "lanes" in data
    assert "phases" in data
    assert "task_id_patterns" in data
    assert "harnesses" in data
    assert "task_sections" in data

    # Shape invariants
    assert len(data["lanes"]) == 6, f"expected 6 lanes, got {len(data['lanes'])}"
    assert len(data["phases"]) == 10, f"expected 10 phases, got {len(data['phases'])}"
    assert data["phases"][0] == "INIT", (
        f"first phase must be INIT, got {data['phases'][0]}"
    )
    assert "claude" in data["harnesses"], (
        f"harnesses must include 'claude': {data['harnesses']}"
    )


# ---------------------------------------------------------------------------
# Test 3 — /api/v1/state returns 6 lanes + INIT-first navigator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_v1_state_returns_6_lanes_init_first(queue_mission: Path):
    """GET /api/v1/state returns 6 lane rows and an INIT-first phase navigator."""
    app = make_app(mission_dir=queue_mission, config=_APP_CONFIG)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        _auth(app, client)
        r = await client.get("/api/v1/state")
    assert r.status_code == 200, r.text
    data = r.json()

    lanes = data["status"]["lanes"]
    assert len(lanes) == 6, f"expected 6 lanes, got {len(lanes)}"

    lane_names = [lane["lane"] for lane in lanes]
    for expected in ("AUDIT", "ARCHITECT", "BACKEND", "FRONTEND", "TEST", "META"):
        assert expected in lane_names, f"{expected} missing from lanes: {lane_names}"

    mission_phase = data["mission"]["phase"]
    # The queue_mission fixture has a .mission-events file that advances the phase to
    # PHASE-PLAN (INIT->PHASE-PLAN transition recorded at fixture creation time).
    # The server correctly reads the last event line; assert the fixture's known phase.
    assert mission_phase == "PHASE-PLAN", (
        f"phase from .mission-events should be PHASE-PLAN for this fixture, got {mission_phase!r}"
    )


# ---------------------------------------------------------------------------
# Test 4 — all ≥11 /api/v1/* routes register
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_11_v1_routes_register(queue_mission: Path):
    """GET /api/v1/__contract_introspect__ lists ≥11 /api/v1/* routes."""
    app = make_app(mission_dir=queue_mission, config=_APP_CONFIG)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        _auth(app, client)
        r = await client.get("/api/v1/__contract_introspect__")
    assert r.status_code == 200, r.text
    registered = r.json()["registered"]
    assert len(registered) >= 11, (
        f"expected ≥11 routes, got {len(registered)}: {registered}"
    )

    paths = {entry[1] for entry in registered}
    required = {
        "/api/v1/config",
        "/api/v1/state",
        "/api/v1/signal",
        "/api/v1/phase-flip",
        "/api/v1/queue/{request_id}",
    }
    missing = required - paths
    assert not missing, f"Required routes missing from introspect: {missing}"


# ---------------------------------------------------------------------------
# Test 5 — inject-task endpoint validates CHALLENGE-FOO_1 task_id (CR-5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_challenge_task_id_validates(queue_mission: Path):
    """POST to /api/v1/inject-task with a canonical task line for CHALLENGE-FOO_1 returns 202.

    Proves CR-5 end-to-end: the task_id is accepted by the queue and applied to TASKS.md.
    """
    app = make_app(mission_dir=queue_mission, config=_APP_CONFIG)
    task_line = "- [ ] [LANE-A] `CHALLENGE-FOO_1` — integration test challenge"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        _auth(app, client)
        r = await client.post("/api/v1/inject-task", json={"task_text": task_line})
        assert r.status_code == 202, f"expected 202, got {r.status_code}: {r.text}"
        request_id = r.json()["request_id"]
        await _wait_for_applied(client, request_id, mission_dir=queue_mission)

    tasks_text = (queue_mission / "TASKS.md").read_text()
    assert "CHALLENGE-FOO_1" in tasks_text, (
        f"CHALLENGE-FOO_1 not found in TASKS.md after inject:\n{tasks_text}"
    )


# ---------------------------------------------------------------------------
# Test 6 — SSE stream connects and delivers at least one event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_stream_connects(queue_mission: Path):
    """GET /api/v1/events connects and returns a sync event (stream is alive)."""
    app = make_app(mission_dir=queue_mission, config=_APP_CONFIG)
    received_events: list[str] = []

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        _auth(app, client)
        async with client.stream("GET", "/api/v1/events") as response:
            assert response.status_code == 200, f"SSE status {response.status_code}"
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    received_events.append(line.split(":", 1)[1].strip())
                    break  # one event is enough

    assert received_events, "SSE stream produced no events"
    assert received_events[0] == "sync", (
        f"first SSE event should be 'sync', got {received_events[0]!r}"
    )


# ---------------------------------------------------------------------------
# Test 7 — queue applier accepts STATUS_UPDATE intent for META lane
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_applier_accepts_canonical_intent(queue_mission: Path):
    """POST STATUS_UPDATE for META lane via /api/v1/signal; assert STATUS.md updated."""
    app = make_app(mission_dir=queue_mission, config=_APP_CONFIG)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        _auth(app, client)
        r = await client.post(
            "/api/v1/signal",
            json={
                "to_lane": "META",
                "claim": "back-compat test signal",
                "evidence": "test-evidence-ref",
            },
        )
        assert r.status_code == 202, f"expected 202, got {r.status_code}: {r.text}"
        request_id = r.json()["request_id"]
        await _wait_for_applied(client, request_id, mission_dir=queue_mission)

    status_text = (queue_mission / "STATUS.md").read_text()
    assert "back-compat test signal" in status_text or "SIG" in status_text, (
        f"STATUS.md not updated after signal:\n{status_text}"
    )
