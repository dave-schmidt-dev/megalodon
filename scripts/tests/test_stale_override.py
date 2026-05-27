"""v9.4 Task 2.7 — POST /api/v1/_test/stale_override endpoint tests.

Tests:
1. Without MEGALODON_FAKE_SPAWNER env var: endpoint should not exist (404).
2. With MEGALODON_FAKE_SPAWNER=1: endpoint registers, accepts valid params, and
   populates _TEST_STALE_OVERRIDES for the next GET /api/v1/lanes/stale call.
3. GET /api/v1/lanes/stale consumes the override one-shot (popped from dict).
4. CSRF validation: missing or invalid X-CSRF-Token returns 403.
5. Query param validation: missing lane or seconds returns 422.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.auth import write_token_atomic
from megalodon_ui.mission_config.schema import MissionConfig
from megalodon_ui.server import make_app


# ---------------------------------------------------------------------------
# Helpers (reused from test_lanes_stale.py)
# ---------------------------------------------------------------------------

TOKEN = "stale-override-test-token"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_config(shorts: list[str] | None = None) -> MissionConfig:
    """Minimal two-lane MissionConfig."""
    shorts = shorts or ["A", "B"]
    lanes = [
        {
            "name": f"LANE{s}",
            "short": s,
            "role": f"role-{s.lower()}",
            "harness": {"cli": "claude", "model": "claude-sonnet-4-6"},
            "cadence_seconds": 300,
            "tick_offset_seconds": 0,
        }
        for s in shorts
    ]
    return MissionConfig.model_validate(
        {
            "mission": {
                "id": "stale-override-test",
                "utc_started": "2026-01-01T00:00:00Z",
            },
            "lanes": lanes,
            "phases": ["INIT"],
        }
    )


def _status_md(rows: dict[str, str]) -> str:
    """Build a STATUS.md table. *rows*: {short → last_utc_str}."""
    lines = [
        "# Status board",
        "",
        "| Lane | Agent | State | Last UTC | Notes |",
        "|---|---|---|---|---|",
    ]
    for short, last_utc in rows.items():
        lines.append(
            f"| {short} | agent-{short.lower()}000 | working | {last_utc} | - |"
        )
    return "\n".join(lines) + "\n"


def _make_mission(tmp_path: Path, status_rows: dict[str, str]) -> Path:
    """Create minimal mission directory tree with STATUS.md + token."""
    fleet = tmp_path / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    write_token_atomic(fleet / "ui.token", TOKEN)

    (tmp_path / "STATUS.md").write_text(_status_md(status_rows))
    (tmp_path / "TASKS.md").write_text("# Tasks\n")
    (tmp_path / "HISTORY.md").write_text("# History\n")
    return tmp_path


# ---------------------------------------------------------------------------
# Async fixture for authenticated client with MEGALODON_FAKE_SPAWNER=1
# ---------------------------------------------------------------------------


async def _make_fake_spawner_client(
    tmp_path: Path,
    mission_dir: Path,
    monkeypatch,
) -> AsyncGenerator[tuple[AsyncClient, Path], None]:
    """Yield (authenticated AsyncClient, mission_dir) with MEGALODON_FAKE_SPAWNER=1."""
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    monkeypatch.setenv("MEGALODON_FAKE_SPAWNER", "1")

    config = _make_config()
    import yaml

    (mission_dir / ".mission-config.yaml").write_text(
        yaml.dump(config.model_dump(mode="json"))
    )

    app = make_app(mission_dir=mission_dir)
    # No teardown needed: stale cache + overrides are app-scoped on ctx (P2.4).

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Authenticate — exchange token for session cookie.
            exch = await client.post("/api/v1/auth/exchange", json={"token": TOKEN})
            assert exch.status_code == 200, f"auth failed: {exch.text}"
            yield client, mission_dir


# ---------------------------------------------------------------------------
# Test 1: Without env var — endpoint doesn't exist (404)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_fake_spawner_env_returns_404(tmp_path, monkeypatch):
    """Without MEGALODON_FAKE_SPAWNER=1, POST /api/v1/_test/stale_override → 404."""
    # Explicitly unset the env var.
    monkeypatch.delenv("MEGALODON_FAKE_SPAWNER", raising=False)
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")

    now = _now_utc()
    old_ts = _utc_iso(now - timedelta(minutes=20))
    mission_dir = _make_mission(tmp_path, {"A": old_ts})

    config = _make_config()
    import yaml

    (mission_dir / ".mission-config.yaml").write_text(
        yaml.dump(config.model_dump(mode="json"))
    )

    app = make_app(mission_dir=mission_dir)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            exch = await client.post("/api/v1/auth/exchange", json={"token": TOKEN})
            assert exch.status_code == 200

            # Try to call the endpoint — should 404/405 since it's not registered.
            # (405 Method Not Allowed is the correct FastAPI response when a route
            # exists for GET but not for POST; 404 would occur if no route exists at all.)
            resp = await client.post(
                "/api/v1/_test/stale_override?lane=A&seconds=999.0",
                headers={"X-CSRF-Token": "dummy"},
            )
            assert resp.status_code in (404, 405), (
                f"Expected 404/405 without MEGALODON_FAKE_SPAWNER but got {resp.status_code}: {resp.text}"
            )


# ---------------------------------------------------------------------------
# Test 2: With env var — endpoint accepts valid params and populates override
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_with_fake_spawner_accepts_valid_params(tmp_path, monkeypatch):
    """With MEGALODON_FAKE_SPAWNER=1, valid params → 200 with {ok, lane, seconds}."""
    now = _now_utc()
    old_ts = _utc_iso(now - timedelta(minutes=20))
    mission_dir = _make_mission(tmp_path, {"A": old_ts})

    async for client, _ in _make_fake_spawner_client(
        tmp_path, mission_dir, monkeypatch
    ):
        # Get the app's CSRF token via a config endpoint.
        config_resp = await client.get("/api/v1/config")
        assert config_resp.status_code == 200
        csrf_token = config_resp.json().get("csrf_token")
        assert csrf_token is not None

        # POST to stale_override with valid params.
        resp = await client.post(
            "/api/v1/_test/stale_override?lane=A&seconds=999.5",
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        assert data.get("ok") is True
        assert data.get("lane") == "A"
        assert data.get("seconds") == 999.5


# ---------------------------------------------------------------------------
# Test 3: Override is consumed one-shot by next GET /api/v1/lanes/stale
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_override_consumed_by_next_stale_call(tmp_path, monkeypatch):
    """After POST /api/v1/_test/stale_override, GET /api/v1/lanes/stale uses override.

    The override is one-shot — it's popped after being consumed by the next stale
    check, so a third call shouldn't see the override effect.
    """
    now = _now_utc()
    # Set up lane A with recent timestamp AND create a fresh stream log.
    # This ensures A is genuinely not-stale (within the 15-min threshold).
    recent_ts = _utc_iso(now - timedelta(minutes=5))
    mission_dir = _make_mission(tmp_path, {"A": recent_ts})

    # Create a fresh stream log so A appears recent from all sources.
    fleet = mission_dir / ".fleet"
    stream_log = fleet / "A.stream.log"
    stream_log.write_bytes(b"recent output")
    # Set mtime to 2 minutes ago (well within threshold).
    two_min_ago = time.time() - 2 * 60
    import os as os_module

    os_module.utime(stream_log, (two_min_ago, two_min_ago))

    async for client, _ in _make_fake_spawner_client(
        tmp_path, mission_dir, monkeypatch
    ):
        config_resp = await client.get("/api/v1/config")
        csrf_token = config_resp.json().get("csrf_token")

        # GET stale before override — A should NOT be stale (5min + 2min stream = recent).
        before = await client.get("/api/v1/lanes/stale")
        assert before.status_code == 200
        stale_before = {e["lane"] for e in before.json()["stale_lanes"]}
        assert "A" not in stale_before, (
            f"A should not be stale before override: {before.json()}"
        )

        # POST override for lane A with 1200s (> 900s threshold).
        override_resp = await client.post(
            "/api/v1/_test/stale_override?lane=A&seconds=1200.0",
            headers={"X-CSRF-Token": csrf_token},
        )
        assert override_resp.status_code == 200

        # Now GET stale again — A should be stale due to override (1200s > 900s).
        after = await client.get("/api/v1/lanes/stale")
        assert after.status_code == 200
        stale_after_data = after.json()
        stale_after = {e["lane"] for e in stale_after_data["stale_lanes"]}
        assert "A" in stale_after, (
            f"A should be stale after override: {stale_after_data}"
        )

        # Find the A entry and verify silent_seconds.
        a_entry = next(e for e in stale_after_data["stale_lanes"] if e["lane"] == "A")
        assert a_entry["silent_seconds"] == 1200.0, (
            f"Expected 1200.0, got {a_entry['silent_seconds']}"
        )

        # Call GET stale a third time — override should be consumed (one-shot).
        third = await client.get("/api/v1/lanes/stale")
        assert third.status_code == 200
        stale_third = {e["lane"] for e in third.json()["stale_lanes"]}
        # A should revert to not-stale (stream log and status are both recent).
        assert "A" not in stale_third, (
            f"A should not be stale after override consumed: {third.json()}"
        )


# ---------------------------------------------------------------------------
# Test 3b: two apps in one process do NOT share stale-override state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_apps_do_not_share_override_state(tmp_path, monkeypatch):
    """P2.4 isolation: an override set on app A must not leak into app B.

    Both apps run in ONE process and define a lane "A" whose status is recent
    (NOT stale). We set a stale-override for lane "A" on app A only. Because the
    override storage used to be a lane-keyed module global (no app scoping), the
    first read on EITHER app would consume it — so app B could see (or steal)
    app A's override. After moving the storage onto each app's MissionContext,
    app B's lane "A" must remain not-stale.
    """
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    monkeypatch.setenv("MEGALODON_FAKE_SPAWNER", "1")
    import yaml

    now = _now_utc()
    recent_ts = _utc_iso(now - timedelta(minutes=5))  # well within 900s threshold

    def _build(sub: str):
        mission_dir = _make_mission(tmp_path / sub, {"A": recent_ts})
        # Fresh stream log so lane A is genuinely recent from all sources.
        stream_log = mission_dir / ".fleet" / "A.stream.log"
        stream_log.write_bytes(b"recent output")
        two_min_ago = time.time() - 2 * 60
        import os as os_module

        os_module.utime(stream_log, (two_min_ago, two_min_ago))
        (mission_dir / ".mission-config.yaml").write_text(
            yaml.dump(_make_config().model_dump(mode="json"))
        )
        return make_app(mission_dir=mission_dir)

    app_a = _build("app_a")
    app_b = _build("app_b")

    async with (
        app_a.router.lifespan_context(app_a),
        app_b.router.lifespan_context(app_b),
    ):
        async with (
            AsyncClient(
                transport=ASGITransport(app=app_a), base_url="http://a"
            ) as client_a,
            AsyncClient(
                transport=ASGITransport(app=app_b), base_url="http://b"
            ) as client_b,
        ):
            for c in (client_a, client_b):
                exch = await c.post("/api/v1/auth/exchange", json={"token": TOKEN})
                assert exch.status_code == 200, f"auth failed: {exch.text}"

            csrf_a = (await client_a.get("/api/v1/config")).json()["csrf_token"]

            # Set a stale override for lane "A" on app A ONLY.
            ov = await client_a.post(
                "/api/v1/_test/stale_override?lane=A&seconds=1200.0",
                headers={"X-CSRF-Token": csrf_a},
            )
            assert ov.status_code == 200, ov.text

            # App B reads first — it must NOT see/consume app A's override.
            b_resp = await client_b.get("/api/v1/lanes/stale")
            assert b_resp.status_code == 200, b_resp.text
            b_stale = {e["lane"] for e in b_resp.json()["stale_lanes"]}
            assert "A" not in b_stale, (
                f"app B leaked app A's override (state shared): {b_resp.json()}"
            )

            # App A still has its override pending and consumes it on its own read.
            a_resp = await client_a.get("/api/v1/lanes/stale")
            assert a_resp.status_code == 200, a_resp.text
            a_stale = {e["lane"] for e in a_resp.json()["stale_lanes"]}
            assert "A" in a_stale, (
                f"app A lost its own override (consumed elsewhere): {a_resp.json()}"
            )


# ---------------------------------------------------------------------------
# Test 4: CSRF validation — missing or invalid token returns 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_csrf_token_returns_403(tmp_path, monkeypatch):
    """POST without X-CSRF-Token header → 403."""
    now = _now_utc()
    mission_dir = _make_mission(tmp_path, {"A": _utc_iso(now - timedelta(minutes=20))})

    async for client, _ in _make_fake_spawner_client(
        tmp_path, mission_dir, monkeypatch
    ):
        # Omit X-CSRF-Token header.
        resp = await client.post(
            "/api/v1/_test/stale_override?lane=A&seconds=999.0",
            # No headers
        )
        assert resp.status_code == 403, (
            f"Expected 403 without CSRF token, got {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        assert "CSRF" in data.get("detail", "")


@pytest.mark.asyncio
async def test_invalid_csrf_token_returns_403(tmp_path, monkeypatch):
    """POST with wrong X-CSRF-Token → 403."""
    now = _now_utc()
    mission_dir = _make_mission(tmp_path, {"A": _utc_iso(now - timedelta(minutes=20))})

    async for client, _ in _make_fake_spawner_client(
        tmp_path, mission_dir, monkeypatch
    ):
        resp = await client.post(
            "/api/v1/_test/stale_override?lane=A&seconds=999.0",
            headers={"X-CSRF-Token": "wrong-token"},
        )
        assert resp.status_code == 403, (
            f"Expected 403 with wrong CSRF token, got {resp.status_code}: {resp.text}"
        )


# ---------------------------------------------------------------------------
# Test 5: Query param validation — missing lane or seconds returns 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_lane_param_returns_422(tmp_path, monkeypatch):
    """POST without 'lane' query param → 422."""
    now = _now_utc()
    mission_dir = _make_mission(tmp_path, {"A": _utc_iso(now - timedelta(minutes=20))})

    async for client, _ in _make_fake_spawner_client(
        tmp_path, mission_dir, monkeypatch
    ):
        config_resp = await client.get("/api/v1/config")
        csrf_token = config_resp.json().get("csrf_token")

        resp = await client.post(
            "/api/v1/_test/stale_override?seconds=999.0",
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 422, (
            f"Expected 422 without lane param, got {resp.status_code}: {resp.text}"
        )
        assert "lane" in resp.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_missing_seconds_param_returns_422(tmp_path, monkeypatch):
    """POST without 'seconds' query param → 422."""
    now = _now_utc()
    mission_dir = _make_mission(tmp_path, {"A": _utc_iso(now - timedelta(minutes=20))})

    async for client, _ in _make_fake_spawner_client(
        tmp_path, mission_dir, monkeypatch
    ):
        config_resp = await client.get("/api/v1/config")
        csrf_token = config_resp.json().get("csrf_token")

        resp = await client.post(
            "/api/v1/_test/stale_override?lane=A",
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 422, (
            f"Expected 422 without seconds param, got {resp.status_code}: {resp.text}"
        )
        assert "seconds" in resp.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_invalid_seconds_float_returns_422(tmp_path, monkeypatch):
    """POST with non-float 'seconds' param → 422."""
    now = _now_utc()
    mission_dir = _make_mission(tmp_path, {"A": _utc_iso(now - timedelta(minutes=20))})

    async for client, _ in _make_fake_spawner_client(
        tmp_path, mission_dir, monkeypatch
    ):
        config_resp = await client.get("/api/v1/config")
        csrf_token = config_resp.json().get("csrf_token")

        resp = await client.post(
            "/api/v1/_test/stale_override?lane=A&seconds=not-a-number",
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 422, (
            f"Expected 422 with invalid seconds, got {resp.status_code}: {resp.text}"
        )
        assert "float" in resp.json().get("detail", "").lower()


# ---------------------------------------------------------------------------
# Test 6: Auth gate — no cookie returns 401
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_gate_no_cookie_returns_401(tmp_path, monkeypatch):
    """POST without session cookie → 401 (caught by middleware)."""
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    monkeypatch.setenv("MEGALODON_FAKE_SPAWNER", "1")

    now = _now_utc()
    mission_dir = _make_mission(tmp_path, {"A": _utc_iso(now - timedelta(minutes=20))})

    config = _make_config()
    import yaml

    (mission_dir / ".mission-config.yaml").write_text(
        yaml.dump(config.model_dump(mode="json"))
    )

    app = make_app(mission_dir=mission_dir)

    async with app.router.lifespan_context(app):
        # Use a clean client with NO cookies.
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/_test/stale_override?lane=A&seconds=999.0",
                headers={"X-CSRF-Token": "dummy"},
            )
            assert resp.status_code == 401, (
                f"Expected 401 without cookie, got {resp.status_code}: {resp.text}"
            )
