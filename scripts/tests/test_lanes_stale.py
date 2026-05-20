"""v9.4 Task 2.6 — GET /api/v1/lanes/stale endpoint tests.

Tests:
1. silent + pending      — no recent activity AND pending approval → NOT stale.
2. silent + not-pending  — no recent activity, not pending → stale=True.
3. recent-status         — STATUS.md last_utc=5min ago → NOT stale.
4. old-status-but-recent-stream — STATUS.md=20min ago, stream mtime=5min ago → NOT stale.
5. cache                 — two hits within 5s share checked_at_utc; after 6s fresh.
6. auth gate             — no cookie → 401.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.auth import write_token_atomic
from megalodon_ui.mission_config.schema import (
    MissionConfig,
)
from megalodon_ui.server import make_app, _stale_cache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TOKEN = "stale-test-token"
LANE_SHORT = "A"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_config(shorts: list[str] | None = None) -> MissionConfig:
    """Minimal two-lane MissionConfig with short codes A and B."""
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
            "mission": {"id": "stale-test", "utc_started": "2026-01-01T00:00:00Z"},
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
# Fake PermissionWatcher
# ---------------------------------------------------------------------------


class _FakeWatcher:
    """Minimal stand-in for PermissionWatcher used in tests."""

    def __init__(self, pending_lanes: set[str]) -> None:
        self._pending_lanes = pending_lanes

    def pending(self):
        from megalodon_ui.permission_watcher import PromptInfo

        return [
            PromptInfo(
                lane_short=lane,
                lane_name=f"LANE{lane}",
                command_preview="test",
                detected_at_utc="2026-01-01T00:00:00Z",
                fingerprint="abc",
            )
            for lane in self._pending_lanes
        ]


# ---------------------------------------------------------------------------
# Async fixture factory
# ---------------------------------------------------------------------------


async def _make_client(
    tmp_path: Path,
    mission_dir: Path,
    monkeypatch,
    *,
    pending_lanes: set[str] | None = None,
) -> AsyncGenerator[tuple[AsyncClient, Path], None]:
    """Yield (authenticated AsyncClient, mission_dir) for the stale endpoint."""
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")

    config = _make_config()
    # Inject .mission-config.yaml so make_app uses our config.
    import yaml

    (mission_dir / ".mission-config.yaml").write_text(
        yaml.dump(config.model_dump(mode="json"))
    )

    app = make_app(mission_dir=mission_dir)

    # Clear any stale module-level cache from prior tests.
    _stale_cache.pop(id(app), None)

    async with app.router.lifespan_context(app):
        if pending_lanes is not None:
            app.state.permission_watcher = _FakeWatcher(pending_lanes)
        else:
            app.state.permission_watcher = _FakeWatcher(set())

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Authenticate — exchange token for session cookie.
            exch = await client.post("/api/v1/auth/exchange", json={"token": TOKEN})
            assert exch.status_code == 200, f"auth failed: {exch.text}"
            yield client, mission_dir


# ---------------------------------------------------------------------------
# Test 1: silent + pending → NOT stale
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_silent_and_pending_not_stale(tmp_path, monkeypatch):
    """Lane has no recent activity but has an active permission prompt → NOT stale."""
    now = _now_utc()
    old_ts = _utc_iso(now - timedelta(minutes=20))
    mission_dir = _make_mission(tmp_path, {"A": old_ts, "B": old_ts})

    async for client, _ in _make_client(
        tmp_path, mission_dir, monkeypatch, pending_lanes={"A"}
    ):
        resp = await client.get("/api/v1/lanes/stale")
        assert resp.status_code == 200, resp.text
        data = resp.json()

        stale_shorts = {entry["lane"] for entry in data["stale_lanes"]}
        # A is pending — must NOT appear in stale_lanes.
        assert "A" not in stale_shorts, (
            f"A should not be stale (pending approval): {data}"
        )
        # B has no pending prompt and is 20min silent — SHOULD be stale.
        assert "B" in stale_shorts, f"B should be stale: {data}"

        # Check pending_approval field for any A entry that slipped through.
        for entry in data["stale_lanes"]:
            if entry["lane"] == "A":
                assert entry["pending_approval"] is True


# ---------------------------------------------------------------------------
# Test 2: silent + not-pending → stale
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_silent_not_pending_is_stale(tmp_path, monkeypatch):
    """Lane with 20-min-old timestamp, no stream log, no applier entry, NOT pending."""
    now = _now_utc()
    old_ts = _utc_iso(now - timedelta(minutes=20))
    mission_dir = _make_mission(tmp_path, {"A": old_ts})

    async for client, _ in _make_client(
        tmp_path, mission_dir, monkeypatch, pending_lanes=set()
    ):
        resp = await client.get("/api/v1/lanes/stale")
        assert resp.status_code == 200, resp.text
        data = resp.json()

        stale_shorts = {entry["lane"] for entry in data["stale_lanes"]}
        assert "A" in stale_shorts, f"A should be stale: {data}"

        a_entry = next(e for e in data["stale_lanes"] if e["lane"] == "A")
        assert a_entry["pending_approval"] is False
        assert a_entry["silent_seconds"] >= 1200.0  # 20 min = 1200 s
        assert a_entry["last_activity_source"] == "status-md"


# ---------------------------------------------------------------------------
# Test 3: recent status → NOT stale
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recent_status_not_stale(tmp_path, monkeypatch):
    """Lane with last_utc=5min ago (below 900s threshold) should NOT be stale."""
    now = _now_utc()
    recent_ts = _utc_iso(now - timedelta(minutes=5))
    mission_dir = _make_mission(tmp_path, {"A": recent_ts})

    async for client, _ in _make_client(
        tmp_path, mission_dir, monkeypatch, pending_lanes=set()
    ):
        resp = await client.get("/api/v1/lanes/stale")
        assert resp.status_code == 200, resp.text
        data = resp.json()

        stale_shorts = {entry["lane"] for entry in data["stale_lanes"]}
        assert "A" not in stale_shorts, f"A should NOT be stale (recent status): {data}"


# ---------------------------------------------------------------------------
# Test 4: old status but recent stream log → NOT stale
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_old_status_recent_stream_not_stale(tmp_path, monkeypatch):
    """STATUS.md says 20min ago, but stream.log mtime is 5min ago → NOT stale."""
    now = _now_utc()
    old_ts = _utc_iso(now - timedelta(minutes=20))
    mission_dir = _make_mission(tmp_path, {"A": old_ts})

    # Create stream log with recent mtime (5min ago).
    fleet = mission_dir / ".fleet"
    stream_log = fleet / "A.stream.log"
    stream_log.write_bytes(b"agent output")
    five_min_ago = time.time() - 5 * 60
    os.utime(stream_log, (five_min_ago, five_min_ago))

    async for client, _ in _make_client(
        tmp_path, mission_dir, monkeypatch, pending_lanes=set()
    ):
        resp = await client.get("/api/v1/lanes/stale")
        assert resp.status_code == 200, resp.text
        data = resp.json()

        stale_shorts = {entry["lane"] for entry in data["stale_lanes"]}
        assert "A" not in stale_shorts, (
            f"A should NOT be stale (stream-log is recent): {data}"
        )


# ---------------------------------------------------------------------------
# Test 5: cache — same checked_at_utc within 5s, fresh after 6s
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_within_ttl_same_checked_at(tmp_path, monkeypatch):
    """Two requests within 5s return the same checked_at_utc (proof of cache)."""
    now = _now_utc()
    old_ts = _utc_iso(now - timedelta(minutes=20))
    mission_dir = _make_mission(tmp_path, {"A": old_ts})

    async for client, _ in _make_client(
        tmp_path, mission_dir, monkeypatch, pending_lanes=set()
    ):
        r1 = await client.get("/api/v1/lanes/stale")
        assert r1.status_code == 200
        checked_at_1 = r1.json()["checked_at_utc"]

        r2 = await client.get("/api/v1/lanes/stale")
        assert r2.status_code == 200
        checked_at_2 = r2.json()["checked_at_utc"]

        assert checked_at_1 == checked_at_2, (
            f"Expected same cached checked_at_utc but got {checked_at_1!r} vs {checked_at_2!r}"
        )


@pytest.mark.asyncio
async def test_cache_expires_after_ttl(tmp_path, monkeypatch):
    """After 6s the cache expires and a fresh checked_at_utc is returned."""
    import megalodon_ui.server as server_mod

    now = _now_utc()
    old_ts = _utc_iso(now - timedelta(minutes=20))
    mission_dir = _make_mission(tmp_path, {"A": old_ts})

    async for client, _ in _make_client(
        tmp_path, mission_dir, monkeypatch, pending_lanes=set()
    ):
        r1 = await client.get("/api/v1/lanes/stale")
        assert r1.status_code == 200
        checked_at_1 = r1.json()["checked_at_utc"]
        app_key = None
        for k, v in server_mod._stale_cache.items():
            app_key = k
        assert app_key is not None

        # Expire the cache by backdating computed_mono by 6 seconds.
        server_mod._stale_cache[app_key]["computed_mono"] -= 6.0

        r2 = await client.get("/api/v1/lanes/stale")
        assert r2.status_code == 200
        checked_at_2 = r2.json()["checked_at_utc"]

        assert checked_at_1 != checked_at_2, (
            f"Expected fresh checked_at_utc after TTL expiry but both are {checked_at_1!r}"
        )


# ---------------------------------------------------------------------------
# Test 6: auth gate → 401 without cookie
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_gate_no_cookie(tmp_path, monkeypatch):
    """GET /api/v1/lanes/stale without session cookie → 401."""
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")

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
            resp = await client.get("/api/v1/lanes/stale")
            assert resp.status_code == 401, (
                f"Expected 401 without cookie, got {resp.status_code}: {resp.text}"
            )
