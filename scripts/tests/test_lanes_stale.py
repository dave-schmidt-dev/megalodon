"""v9.4 Task 2.6 — GET /api/v1/lanes/stale endpoint tests.

Tests:
1. silent → stale=True (no recent activity).
2. recent-status         — STATUS.md last_utc=5min ago → NOT stale.
3. old-status-but-recent-stream — STATUS.md=20min ago, stream mtime=5min ago → NOT stale.
4. cache                 — two hits within 5s share checked_at_utc; after 6s fresh.
5. auth gate             — no cookie → 401.
6. governor-blocked deny-loop — ≥5 denies in window → governor_blocked, NOT stale.
7. governor-blocked negatives — <5 denies / denies outside window → not blocked.
"""

from __future__ import annotations

import json
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
from megalodon_ui.server import (
    make_app,
    _compute_governor_blocked,
    _GOVERNOR_BLOCK_DENY_COUNT,
    _GOVERNOR_BLOCK_WINDOW_SECONDS,
)


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


def _governor_log_lines(entries: list[dict]) -> str:
    """Serialize governor-log JSONL entries (one JSON object per line)."""
    return "".join(json.dumps(e) + "\n" for e in entries)


def _write_governor_log(mission_dir: Path, entries: list[dict]) -> Path:
    """Write today's .fleet/governor-log-<UTC date>.jsonl and return the path."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = mission_dir / ".fleet" / f"governor-log-{today}.jsonl"
    path.write_text(_governor_log_lines(entries))
    return path


# ---------------------------------------------------------------------------
# Async fixture factory
# ---------------------------------------------------------------------------


async def _make_client(
    tmp_path: Path,
    mission_dir: Path,
    monkeypatch,
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
    # No teardown needed: stale cache is app-scoped on ctx (P2.4).

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Authenticate — exchange token for session cookie.
            exch = await client.post("/api/v1/auth/exchange", json={"token": TOKEN})
            assert exch.status_code == 200, f"auth failed: {exch.text}"
            yield client, mission_dir


# ---------------------------------------------------------------------------
# Test 1: silent → stale
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_silent_is_stale(tmp_path, monkeypatch):
    """Lane with 20-min-old timestamp, no stream log, no applier entry → stale."""
    now = _now_utc()
    old_ts = _utc_iso(now - timedelta(minutes=20))
    mission_dir = _make_mission(tmp_path, {"A": old_ts})

    async for client, _ in _make_client(tmp_path, mission_dir, monkeypatch):
        resp = await client.get("/api/v1/lanes/stale")
        assert resp.status_code == 200, resp.text
        data = resp.json()

        stale_shorts = {entry["lane"] for entry in data["stale_lanes"]}
        assert "A" in stale_shorts, f"A should be stale: {data}"

        a_entry = next(e for e in data["stale_lanes"] if e["lane"] == "A")
        assert "pending_approval" not in a_entry, "pending_approval key dropped"
        assert a_entry["silent_seconds"] >= 1200.0  # 20 min = 1200 s
        assert a_entry["last_activity_source"] == "status-md"


# ---------------------------------------------------------------------------
# Test 2: recent status → NOT stale
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recent_status_not_stale(tmp_path, monkeypatch):
    """Lane with last_utc=5min ago (below 900s threshold) should NOT be stale."""
    now = _now_utc()
    recent_ts = _utc_iso(now - timedelta(minutes=5))
    mission_dir = _make_mission(tmp_path, {"A": recent_ts})

    async for client, _ in _make_client(tmp_path, mission_dir, monkeypatch):
        resp = await client.get("/api/v1/lanes/stale")
        assert resp.status_code == 200, resp.text
        data = resp.json()

        stale_shorts = {entry["lane"] for entry in data["stale_lanes"]}
        assert "A" not in stale_shorts, f"A should NOT be stale (recent status): {data}"


# ---------------------------------------------------------------------------
# Test 3: old status but recent stream log → NOT stale
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

    async for client, _ in _make_client(tmp_path, mission_dir, monkeypatch):
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

    async for client, _ in _make_client(tmp_path, mission_dir, monkeypatch):
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
    now = _now_utc()
    old_ts = _utc_iso(now - timedelta(minutes=20))
    mission_dir = _make_mission(tmp_path, {"A": old_ts})

    async for client, _ in _make_client(tmp_path, mission_dir, monkeypatch):
        r1 = await client.get("/api/v1/lanes/stale")
        assert r1.status_code == 200
        checked_at_1 = r1.json()["checked_at_utc"]

        # The stale cache is app-scoped on ctx (P2.4): reach it via the app
        # behind the client's ASGI transport.
        ctx = client._transport.app.state.megalodon
        assert "entry" in ctx.stale_cache, "expected a cached entry after first GET"

        # Expire the cache by backdating computed_mono by 6 seconds.
        ctx.stale_cache["entry"]["computed_mono"] -= 6.0

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


# ---------------------------------------------------------------------------
# Test 7: governor deny-loop → governor_blocked, NOT stale (plan §8.3/§8.7)
# ---------------------------------------------------------------------------


def _deny_entries(lane: str, count: int, *, age_seconds: float) -> list[dict]:
    """Build *count* governor-log 'deny' entries for *lane*, *age_seconds* old."""
    base = _now_utc() - timedelta(seconds=age_seconds)
    return [
        {
            "ts": _utc_iso(base + timedelta(seconds=i)),
            "lane": lane,
            "tool": "Write",
            "permission": "deny",
            "category": "outside-mission",
            "reason": f"deny #{i}",
        }
        for i in range(count)
    ]


def test_compute_governor_blocked_deny_loop(tmp_path):
    """≥ DENY_COUNT denies within the window → lane is governor-blocked."""
    mission_dir = _make_mission(tmp_path, {"A": _utc_iso(_now_utc())})
    _write_governor_log(
        mission_dir, _deny_entries("A", _GOVERNOR_BLOCK_DENY_COUNT, age_seconds=10.0)
    )

    blocked = _compute_governor_blocked(mission_dir)
    assert "A" in blocked, f"A should be governor-blocked: {blocked}"
    assert blocked["A"]["deny_count"] == _GOVERNOR_BLOCK_DENY_COUNT
    assert blocked["A"]["window_seconds"] == _GOVERNOR_BLOCK_WINDOW_SECONDS
    assert blocked["A"]["last_category"] == "outside-mission"


def test_compute_governor_blocked_below_threshold(tmp_path):
    """Fewer than DENY_COUNT denies → NOT governor-blocked."""
    mission_dir = _make_mission(tmp_path, {"A": _utc_iso(_now_utc())})
    _write_governor_log(
        mission_dir,
        _deny_entries("A", _GOVERNOR_BLOCK_DENY_COUNT - 1, age_seconds=10.0),
    )
    assert _compute_governor_blocked(mission_dir) == {}


def test_compute_governor_blocked_outside_window(tmp_path):
    """Enough denies but all older than the window → NOT governor-blocked."""
    mission_dir = _make_mission(tmp_path, {"A": _utc_iso(_now_utc())})
    # Age them well past the window.
    _write_governor_log(
        mission_dir,
        _deny_entries(
            "A",
            _GOVERNOR_BLOCK_DENY_COUNT + 2,
            age_seconds=_GOVERNOR_BLOCK_WINDOW_SECONDS + 120.0,
        ),
    )
    assert _compute_governor_blocked(mission_dir) == {}


def test_compute_governor_blocked_missing_file(tmp_path):
    """No governor-log file → empty dict, never raises."""
    mission_dir = _make_mission(tmp_path, {"A": _utc_iso(_now_utc())})
    assert _compute_governor_blocked(mission_dir) == {}


def test_compute_governor_blocked_allow_not_counted(tmp_path):
    """Allow decisions never count toward the deny-loop."""
    mission_dir = _make_mission(tmp_path, {"A": _utc_iso(_now_utc())})
    entries = [
        {
            "ts": _utc_iso(_now_utc()),
            "lane": "A",
            "tool": "Read",
            "permission": "allow",
            "category": "read",
            "reason": "ok",
        }
        for _ in range(_GOVERNOR_BLOCK_DENY_COUNT + 5)
    ]
    _write_governor_log(mission_dir, entries)
    assert _compute_governor_blocked(mission_dir) == {}


@pytest.mark.asyncio
async def test_governor_blocked_excluded_from_stale(tmp_path, monkeypatch):
    """A deny-looping, silent lane appears in governor_blocked, NOT in stale_lanes."""
    now = _now_utc()
    old_ts = _utc_iso(now - timedelta(minutes=20))  # silent → would be stale
    mission_dir = _make_mission(tmp_path, {"A": old_ts})
    _write_governor_log(
        mission_dir, _deny_entries("A", _GOVERNOR_BLOCK_DENY_COUNT, age_seconds=10.0)
    )

    async for client, _ in _make_client(tmp_path, mission_dir, monkeypatch):
        resp = await client.get("/api/v1/lanes/stale")
        assert resp.status_code == 200, resp.text
        data = resp.json()

        stale_shorts = {e["lane"] for e in data["stale_lanes"]}
        blocked_shorts = {e["lane"] for e in data["governor_blocked"]}

        assert "A" in blocked_shorts, f"A should be governor_blocked: {data}"
        assert "A" not in stale_shorts, (
            f"A must NOT be reported as stale when governor-blocked: {data}"
        )
        a_block = next(e for e in data["governor_blocked"] if e["lane"] == "A")
        assert a_block["deny_count"] == _GOVERNOR_BLOCK_DENY_COUNT


@pytest.mark.asyncio
async def test_governor_blocked_seam_consecutive_denies_over_wire(
    tmp_path, monkeypatch
):
    """Seam: deny-log → GET /api/v1/lanes/stale → governor_blocked w/ full fields.

    The board's BLOCKED pill reads ``governor_blocked[].consecutive_denies``
    (added in Wave 3). The unit tests above prove ``_compute_governor_blocked``
    computes it, but nothing pinned that it survives the HTTP boundary intact —
    that the endpoint actually serializes ``consecutive_denies`` (and the
    sibling ``deny_count`` / ``window_seconds`` / ``last_category`` /
    ``last_reason``) into the JSON the board consumes. This exercises the real
    endpoint (no mock of the computation) end to end: write >= DENY_COUNT trailing
    denies inside the 60 s window for lane A, hit ``/api/v1/lanes/stale``, and
    assert A is reported ``governor_blocked`` with every field the board needs.
    """
    now = _now_utc()
    mission_dir = _make_mission(tmp_path, {"A": _utc_iso(now)})
    deny_n = _GOVERNOR_BLOCK_DENY_COUNT + 1  # 6 trailing denies, no allow → run==6
    _write_governor_log(mission_dir, _deny_entries("A", deny_n, age_seconds=10.0))

    async for client, _ in _make_client(tmp_path, mission_dir, monkeypatch):
        resp = await client.get("/api/v1/lanes/stale")
        assert resp.status_code == 200, resp.text
        data = resp.json()

        blocked = {e["lane"]: e for e in data["governor_blocked"]}
        assert "A" in blocked, f"A should be governor_blocked over the wire: {data}"
        a = blocked["A"]
        # consecutive_denies is the Wave 3 field the board's BLOCKED pill reads;
        # 6 trailing denies with no intervening allow → run of 6.
        assert a["consecutive_denies"] == deny_n, a
        # Sibling fields the board surface also depends on must come through too.
        assert a["deny_count"] == deny_n, a
        assert a["window_seconds"] == _GOVERNOR_BLOCK_WINDOW_SECONDS, a
        assert a["last_category"] == "outside-mission", a
        assert a["last_reason"] == f"deny #{deny_n - 1}", a
        # And it must NOT also be reported as merely silent/stale.
        assert "A" not in {e["lane"] for e in data["stale_lanes"]}, data


# ---------------------------------------------------------------------------
# Test 8: consecutive_denies — trailing deny run since last allow (contract §3)
# ---------------------------------------------------------------------------


def test_consecutive_denies_counts_trailing_run(tmp_path):
    """6 trailing denies with no allow → consecutive_denies == 6."""
    mission_dir = _make_mission(tmp_path, {"A": _utc_iso(_now_utc())})
    _write_governor_log(mission_dir, _deny_entries("A", 6, age_seconds=10.0))

    blocked = _compute_governor_blocked(mission_dir)
    assert "A" in blocked, f"A should be governor-blocked: {blocked}"
    assert blocked["A"]["consecutive_denies"] == 6


def test_consecutive_denies_reset_by_allow_in_middle(tmp_path):
    """An allow in the middle resets the trailing run to the denies AFTER it."""
    mission_dir = _make_mission(tmp_path, {"A": _utc_iso(_now_utc())})
    base = _now_utc() - timedelta(seconds=30)

    def _entry(offset: int, permission: str) -> dict:
        return {
            "ts": _utc_iso(base + timedelta(seconds=offset)),
            "lane": "A",
            "tool": "Write",
            "permission": permission,
            "category": "outside-mission",
            "reason": f"{permission} @{offset}",
        }

    # deny x3, allow, deny x3  → window deny_count = 6 (blocked), but the
    # trailing consecutive run is only the final 3 (the allow reset it).
    entries = (
        [_entry(i, "deny") for i in range(3)]
        + [_entry(3, "allow")]
        + [_entry(4 + i, "deny") for i in range(3)]
    )
    _write_governor_log(mission_dir, entries)

    blocked = _compute_governor_blocked(mission_dir)
    assert "A" in blocked, f"A should be governor-blocked: {blocked}"
    # 6 denies in the window → still blocked; but consecutive trailing run is 3.
    assert blocked["A"]["deny_count"] == 6
    assert blocked["A"]["consecutive_denies"] == 3


# ---------------------------------------------------------------------------
# Test 9: GET /api/v1/alerts — watchdog alert feed (contract §2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alerts_endpoint_returns_jsonl_records(tmp_path, monkeypatch):
    """An AlertManager.alert() JSONL line is returned by GET /api/v1/alerts."""
    from megalodon_ui.watchdog.alerts import AlertManager

    mission_dir = _make_mission(tmp_path, {"A": _utc_iso(_now_utc())})
    # Fire one alert: writes findings markdown + the structured JSONL.
    AlertManager(mission_dir).alert("A", "CRASHED", evidence=["pid 4242 not alive"])

    async for client, _ in _make_client(tmp_path, mission_dir, monkeypatch):
        resp = await client.get("/api/v1/alerts")
        assert resp.status_code == 200, resp.text
        alerts = resp.json()["alerts"]
        assert len(alerts) == 1
        a = alerts[0]
        assert a["lane"] == "A"
        assert a["kind"] == "CRASHED"
        assert a["severity"] == "critical"
        assert a["evidence"] == ["pid 4242 not alive"]
        assert "CRASHED" in a["message"]


@pytest.mark.asyncio
async def test_alerts_newest_first(tmp_path, monkeypatch):
    """Multiple alerts are returned newest-first."""
    from megalodon_ui.watchdog.alerts import AlertManager

    mission_dir = _make_mission(tmp_path, {"A": _utc_iso(_now_utc())})
    mgr = AlertManager(mission_dir)
    # Two DISTINCT alert types on the same lane bypass the dedup guard.
    mgr.alert("A", "STATUS-STALE", evidence=["first"])
    mgr.alert("A", "CRASHED", evidence=["second"])

    async for client, _ in _make_client(tmp_path, mission_dir, monkeypatch):
        resp = await client.get("/api/v1/alerts")
        assert resp.status_code == 200, resp.text
        alerts = resp.json()["alerts"]
        assert [a["kind"] for a in alerts] == ["CRASHED", "STATUS-STALE"]


@pytest.mark.asyncio
async def test_alerts_requires_auth(tmp_path, monkeypatch):
    """GET /api/v1/alerts without a session cookie → 401."""
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    mission_dir = _make_mission(tmp_path, {"A": _utc_iso(_now_utc())})

    import yaml

    config = _make_config()
    (mission_dir / ".mission-config.yaml").write_text(
        yaml.dump(config.model_dump(mode="json"))
    )
    app = make_app(mission_dir=mission_dir)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # No auth exchange → no cookie.
            resp = await client.get("/api/v1/alerts")
            assert resp.status_code == 401, resp.text
