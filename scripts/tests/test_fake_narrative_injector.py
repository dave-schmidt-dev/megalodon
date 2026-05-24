"""Task 2.5 — fake narrative injector endpoint tests.

Tests
-----
1. POST /api/v1/__fake__/narrative (authed, fake mode) → 200 ack AND the
   posted lane appears in GET /api/v1/narrative (cache was written).
2. The hub receives the published frame after the POST (direct queue
   subscribe, same pattern as test_narrative_endpoint.py hub tests).
3. The endpoint is NOT registered when fake mode is OFF:
   POST /api/v1/__fake__/narrative → 404 or 405 under a non-fake app.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.auth import write_token_atomic
from megalodon_ui.server import make_app

TOKEN = "fake-nar-injector-token"
LANE_SHORT = "A"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(phrase: str = "Injected narrative") -> dict:
    """Minimal narrative cache row matching the board_state shape."""
    return {
        "last": {"summary": "prior summary", "ts": "2026-05-01T00:00:00Z"},
        "now": {"phrase": phrase, "summary": "current injected summary"},
        "goal": "Test the injector",
        "state": "RUNNING",
    }


def _setup_mission(tmp_path: Path) -> None:
    """Create the minimal required mission directory structure."""
    fleet = tmp_path / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    write_token_atomic(fleet / "ui.token", TOKEN)
    (tmp_path / "STATUS.md").write_text("# Status\n")
    (tmp_path / "TASKS.md").write_text("# Tasks\n")
    (tmp_path / "HISTORY.md").write_text("# History\n")
    (tmp_path / "findings").mkdir(exist_ok=True)
    (tmp_path / "signals").mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Fixture: authenticated httpx client under FAKE spawner mode
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def fake_nar_client(tmp_path: Path, monkeypatch) -> AsyncGenerator[tuple, None]:
    """Authenticated httpx client with fake-spawner lifespan (hub + cache ready)."""
    # The conftest autouse fixture sets MEGALODON_LIFESPAN_TEST_MODE=1; we need
    # fake-spawner mode instead so the fake routes are registered.
    monkeypatch.delenv("MEGALODON_LIFESPAN_TEST_MODE", raising=False)
    monkeypatch.setenv("MEGALODON_FAKE_SPAWNER", "1")
    _setup_mission(tmp_path)

    app = make_app(mission_dir=tmp_path)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post("/api/v1/auth/exchange", json={"token": TOKEN})
            assert r.status_code == 200, f"auth failed: {r.text}"
            yield client, app, tmp_path


# ---------------------------------------------------------------------------
# 1. POST → 200 ack AND cache is written (GET /api/v1/narrative reflects it)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_narrative_inject_returns_ack_and_writes_cache(fake_nar_client):
    """Injecting a lane row returns a 200 ack and persists the row in the cache."""
    client, app, _ = fake_nar_client

    row = _make_row()
    r = await client.post(
        "/api/v1/__fake__/narrative",
        json={"lanes": {LANE_SHORT: row}},
    )
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"

    body = r.json()
    assert body.get("ok") is True, f"missing 'ok': {body}"
    assert LANE_SHORT in body.get("lanes", []), f"lane not in ack: {body}"

    # Confirm the cache was written by reading the snapshot endpoint.
    snap = await client.get("/api/v1/narrative")
    assert snap.status_code == 200, f"snapshot failed: {snap.status_code}: {snap.text}"
    snap_body = snap.json()
    assert LANE_SHORT in snap_body["lanes"], f"lane not in snapshot: {snap_body}"
    assert snap_body["lanes"][LANE_SHORT]["now"]["phrase"] == row["now"]["phrase"]


@pytest.mark.asyncio
async def test_fake_narrative_inject_merges_multiple_lanes(fake_nar_client):
    """Injecting two lanes merges both into the cache without clearing others."""
    client, app, _ = fake_nar_client

    # Pre-seed an unrelated lane directly.
    app.state.narrative_cache["Z"] = _make_row("pre-existing Z")

    r = await client.post(
        "/api/v1/__fake__/narrative",
        json={
            "lanes": {
                "A": _make_row("lane A"),
                "B": _make_row("lane B"),
            }
        },
    )
    assert r.status_code == 200, f"expected 200: {r.text}"
    body = r.json()
    assert set(body["lanes"]) == {"A", "B"}, f"unexpected ack lanes: {body['lanes']}"

    # All three lanes must be present in the cache (merge, not replace).
    snap = await client.get("/api/v1/narrative")
    lanes = snap.json()["lanes"]
    assert "A" in lanes and "B" in lanes and "Z" in lanes, (
        f"expected A, B, Z in cache: {list(lanes)}"
    )
    assert lanes["A"]["now"]["phrase"] == "lane A"
    assert lanes["Z"]["now"]["phrase"] == "pre-existing Z"


# ---------------------------------------------------------------------------
# 2. Hub receives the published frame after the POST
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_narrative_inject_publishes_to_hub(fake_nar_client):
    """POST to /__fake__/narrative publishes the full cache frame to the hub."""
    client, app, _ = fake_nar_client

    hub = app.state.narrative_hub
    q = hub.subscribe()

    row = _make_row("Hub subscriber check")
    try:
        r = await client.post(
            "/api/v1/__fake__/narrative",
            json={"lanes": {LANE_SHORT: row}},
        )
        assert r.status_code == 200, f"POST failed: {r.text}"

        received = await asyncio.wait_for(q.get(), timeout=2.0)

        # The published frame must use the same shape the real scheduler emits:
        # {"lanes": {<short>: <row_payload>, ...}}.
        assert "lanes" in received, f"frame missing 'lanes': {received}"
        assert LANE_SHORT in received["lanes"], (
            f"lane not in published frame: {received}"
        )
        assert received["lanes"][LANE_SHORT]["now"]["phrase"] == "Hub subscriber check"
    finally:
        hub.unsubscribe(q)


# ---------------------------------------------------------------------------
# 3. Endpoint NOT registered when fake mode is OFF
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_narrative_not_registered_without_fake_mode(
    tmp_path: Path, monkeypatch
):
    """POST /api/v1/__fake__/narrative returns 404/405 when MEGALODON_FAKE_SPAWNER is unset."""
    monkeypatch.delenv("MEGALODON_FAKE_SPAWNER", raising=False)
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")

    _setup_mission(tmp_path)
    app = make_app(mission_dir=tmp_path)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post("/api/v1/auth/exchange", json={"token": TOKEN})
            assert r.status_code == 200, f"auth failed: {r.text}"

            r = await client.post(
                "/api/v1/__fake__/narrative",
                json={"lanes": {LANE_SHORT: _make_row()}},
            )
            # 405 from SPA catch-all GET-only route OR 404 — both prove the
            # POST handler is unregistered. What matters: it is NOT 200.
            assert r.status_code in (404, 405), (
                f"expected 404/405 (no fake mode), got {r.status_code}: {r.text}"
            )
