"""v9.4 ship-time gap-fill — inject rate limit is isolated per lane.

Existing coverage (test_inject_endpoint.py):
  - Lane A exhausts its 10-call bucket → 429 on 11th call (tested)

Missing coverage:
  - Two lanes each have INDEPENDENT 10-call buckets.
  - Exhausting lane A's bucket must NOT affect lane B.

Why this matters
----------------
The rate-limit state is `_inject_rl: dict[str, deque]` keyed by lane short-
code (server.py:1516). If the key were accidentally shared or defaulted to a
global bucket, exhausting lane A would block operators from sending keystrokes
to lane B — locking them out of an otherwise-healthy lane. This is a silent
operational hazard because the lane would still show 200-status in the UI but
all inject calls would return 429.

Test strategy
-------------
1. Make 10 successful calls to lane A (exhausts A's bucket).
2. Verify lane A is now rate-limited (11th call → 429).
3. Make 10 successful calls to lane B (B's bucket should be independent).
4. Verify lane B accepts up to 10 calls and only 429s on the 11th.
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from megalodon_ui.auth import write_token_atomic
from megalodon_ui.mission_config.schema import MissionConfig
from megalodon_ui.server import make_app
from megalodon_ui.spawn import FleetSpawner, LaneSession


TOKEN = "rl-per-lane-test-token"
LANE_A = "A"
LANE_B = "B"


def _make_config(shorts: list[str]) -> MissionConfig:
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
                "id": "rl-per-lane-test",
                "utc_started": "2026-01-01T00:00:00Z",
            },
            "lanes": lanes,
            "phases": ["INIT"],
        }
    )


@pytest_asyncio.fixture
async def two_lane_client(tmp_path: Path, monkeypatch) -> AsyncGenerator[tuple, None]:
    """Authenticated httpx client with two lanes (A and B) registered."""
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")

    fleet = tmp_path / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    write_token_atomic(fleet / "ui.token", TOKEN)

    (tmp_path / "STATUS.md").write_text("# Status\n")
    (tmp_path / "TASKS.md").write_text("# Tasks\n")
    (tmp_path / "HISTORY.md").write_text("# History\n")

    socket = fleet / "tmux.sock"
    config = _make_config([LANE_A, LANE_B])

    adapter_resolver = MagicMock()
    spawner = FleetSpawner(tmp_path, config, adapter_resolver, socket)

    for short in [LANE_A, LANE_B]:
        stream_log = fleet / f"{short}.stream.log"
        stream_log.touch()
        spawner.sessions[short] = LaneSession(
            lane=short,
            name=f"lane-{short}",
            cwd=tmp_path,
            argv=["stub"],
            env={},
            stream_log=stream_log,
            session_id=f"test-session-{short}",
            running=True,
        )

    import megalodon_ui.tmux as tmux_mod

    monkeypatch.setattr(tmux_mod, "send_keys", AsyncMock(return_value=0))

    app = make_app(mission_dir=tmp_path)

    async with app.router.lifespan_context(app):
        app.state.spawner = spawner
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            exch = await client.post("/api/v1/auth/exchange", json={"token": TOKEN})
            assert exch.status_code == 200, f"auth failed: {exch.text}"

            config_r = await client.get("/api/v1/config")
            csrf_token = config_r.json().get("csrf_token", "")

            yield client, csrf_token


async def _inject(client, csrf_token: str, lane: str, i: int):
    return await client.post(
        f"/api/v1/lane/{lane}/inject",
        json={"text": f"call-{i}", "enter": False},
        headers={"X-CSRF-Token": csrf_token},
    )


# ---------------------------------------------------------------------------
# Test: rate limit buckets are independent per lane
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inject_rate_limit_is_per_lane_independent(two_lane_client):
    """Exhausting lane A's rate limit must not affect lane B's independent bucket.

    After A's 10-call bucket is exhausted, lane B should still accept 10 calls
    and only reject the 11th.
    """
    client, csrf_token = two_lane_client

    # Step 1: exhaust lane A's bucket (10 calls).
    for i in range(10):
        r = await _inject(client, csrf_token, LANE_A, i)
        assert r.status_code == 202, f"lane A call {i} should succeed: {r.text}"

    # Step 2: 11th call to lane A must be rate-limited.
    r_a_11 = await _inject(client, csrf_token, LANE_A, 10)
    assert r_a_11.status_code == 429, (
        f"lane A 11th call should be 429, got {r_a_11.status_code}: {r_a_11.text}"
    )

    # Step 3: lane B's bucket is INDEPENDENT — it should accept 10 fresh calls.
    for i in range(10):
        r = await _inject(client, csrf_token, LANE_B, i)
        assert r.status_code == 202, (
            f"lane B call {i} should succeed (independent bucket), got {r.status_code}: {r.text}"
        )

    # Step 4: 11th call to lane B is rate-limited (its own bucket is now full).
    r_b_11 = await _inject(client, csrf_token, LANE_B, 10)
    assert r_b_11.status_code == 429, (
        f"lane B 11th call should be 429, got {r_b_11.status_code}: {r_b_11.text}"
    )
