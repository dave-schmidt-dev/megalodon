"""Fix-Round-3 — CSRF protection on canonical mutation routes.

BLOCKING audit finding: several canonical mutation handlers had NO
``_csrf_or_403`` guard. This file asserts, for each of the six affected routes:

  * missing ``X-CSRF-Token`` header → 403
  * wrong ``X-CSRF-Token`` header   → 403
  * valid token (and control mode ON) → NOT 403 (the handler proceeds)

Routes covered:
  1. POST /api/v1/signal          (post_v1_signal)
  2. POST /api/v1/reclaim         (post_v1_reclaim)
  3. POST /api/v1/challenge       (post_v1_challenge)
  4. POST /api/v1/mission-status  (post_v1_mission_status)
  5. POST /api/v1/inject-task     (post_v1_inject_task)
  6. POST /api/lanes/{lane}/reclaim (legacy post_reclaim)

The check order is auth → CSRF → control-mode → handler, so CSRF failures must
surface BEFORE the control-mode gate even when control mode is OFF.
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

TOKEN = "csrf-canonical-test-token"

# (route, json body) tuples. Bodies are valid enough to pass past the
# control-mode gate into the handler when control mode is ON.
_VALID_TASK_LINE = "- [ ] [LANE-A] `P9-A` — do the thing"

ROUTES = [
    (
        "/api/v1/signal",
        {"to_lane": "AUDIT", "claim": "c", "evidence": "findings/x.md:1"},
    ),
    ("/api/v1/reclaim", {"lane": "AUDIT"}),
    ("/api/v1/challenge", {"finding_filename": "agent-x-A.md", "description": "d"}),
    ("/api/v1/mission-status", {"status": "ACTIVE"}),
    ("/api/v1/inject-task", {"task_text": _VALID_TASK_LINE}),
    ("/api/lanes/AUDIT/reclaim", {}),
]

ROUTE_IDS = [r[0] for r in ROUTES]


@pytest_asyncio.fixture
async def csrf_client(tmp_path: Path, monkeypatch) -> AsyncGenerator[tuple, None]:
    """Authenticated client against a minimal mission dir with one STATUS row."""
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")

    fleet = tmp_path / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    write_token_atomic(fleet / "ui.token", TOKEN)

    (tmp_path / "STATUS.md").write_text(
        "# Status\n\n| Lane | Agent | State | Last UTC | Notes |\n"
        "|---|---|---|---|---|\n"
        "| AUDIT | agent-1 | working: P1-A | 2026-01-01T00:00Z | n |\n"
    )
    (tmp_path / "TASKS.md").write_text("# Tasks\n\n## PHASE-PLAN\n")
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
            # Enable control mode so the valid-token cases pass the control gate.
            on = await client.post(
                "/api/v1/control-mode",
                json={"enabled": True},
                headers={"X-CSRF-Token": csrf},
            )
            assert on.status_code == 200, on.text
            yield client, csrf, tmp_path


@pytest.mark.asyncio
@pytest.mark.parametrize("route,body", ROUTES, ids=ROUTE_IDS)
async def test_missing_csrf_returns_403(csrf_client, route, body):
    client, _csrf, _ = csrf_client
    r = await client.post(route, json=body)  # no X-CSRF-Token
    assert r.status_code == 403, f"{route}: expected 403, got {r.status_code}: {r.text}"
    assert "CSRF" in r.json()["detail"], (
        f"{route}: expected a CSRF detail, got {r.json()}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("route,body", ROUTES, ids=ROUTE_IDS)
async def test_wrong_csrf_returns_403(csrf_client, route, body):
    client, _csrf, _ = csrf_client
    r = await client.post(route, json=body, headers={"X-CSRF-Token": "nope"})
    assert r.status_code == 403, f"{route}: expected 403, got {r.status_code}: {r.text}"
    assert "CSRF" in r.json()["detail"], (
        f"{route}: expected a CSRF detail, got {r.json()}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("route,body", ROUTES, ids=ROUTE_IDS)
async def test_valid_csrf_not_403(csrf_client, route, body):
    """With a valid token and control mode ON, the handler must NOT return 403.

    It may legitimately return 200/202/204 depending on the route; the only
    forbidden outcome here is 403 (which would mean a CSRF or control-mode
    rejection leaked through despite valid credentials).
    """
    client, csrf, _ = csrf_client
    r = await client.post(route, json=body, headers={"X-CSRF-Token": csrf})
    assert r.status_code != 403, f"{route}: unexpected 403 with valid token: {r.text}"
    assert r.status_code in (200, 202, 204), (
        f"{route}: expected success, got {r.status_code}: {r.text}"
    )


# ---------------------------------------------------------------------------
# P3.7 — CSRF-negative coverage for four additional mutation routes that were
# not in the original ROUTES list above.  Each handler runs the CSRF gate
# FIRST (auth → CSRF → control-mode → handler), so a missing or mismatched
# X-CSRF-Token must surface as 403 before any handler-specific logic — no
# STATUS row, spawner, or phase preconditions are required to reach the gate.
#
# These are kept as a SEPARATE parametrize block (not merged into ROUTES)
# because their valid-token outcomes differ from the 200/202/204 the existing
# test_valid_csrf_not_403 asserts (e.g. /api/tasks → 201, feedback → 404
# without a spawner), and the task scope is the negative cases only.
# ---------------------------------------------------------------------------

EXTRA_ROUTES = [
    ("/api/tasks", {"kind": "CHALLENGE", "target_finding": "findings/x.md"}),
    (
        "/api/lanes/AUDIT/signal",
        {"text": "ping", "cite": "findings/x.md:1"},
    ),
    ("/api/v1/phase-flip", {"from": "PHASE-PLAN", "to": "PHASE-BUILD"}),
    ("/api/v1/lane/AUDIT/feedback", {"message": "operator note"}),
]

EXTRA_ROUTE_IDS = [r[0] for r in EXTRA_ROUTES]


@pytest.mark.asyncio
@pytest.mark.parametrize("route,body", EXTRA_ROUTES, ids=EXTRA_ROUTE_IDS)
async def test_extra_missing_csrf_returns_403(csrf_client, route, body):
    client, _csrf, _ = csrf_client
    r = await client.post(route, json=body)  # no X-CSRF-Token
    assert r.status_code == 403, f"{route}: expected 403, got {r.status_code}: {r.text}"
    assert "CSRF" in r.json()["detail"], (
        f"{route}: expected a CSRF detail, got {r.json()}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("route,body", EXTRA_ROUTES, ids=EXTRA_ROUTE_IDS)
async def test_extra_wrong_csrf_returns_403(csrf_client, route, body):
    client, _csrf, _ = csrf_client
    r = await client.post(route, json=body, headers={"X-CSRF-Token": "nope"})
    assert r.status_code == 403, f"{route}: expected 403, got {r.status_code}: {r.text}"
    assert "CSRF" in r.json()["detail"], (
        f"{route}: expected a CSRF detail, got {r.json()}"
    )


# ---------------------------------------------------------------------------
# MINOR finding #5 — victim-lane hiding via trailing-pipe junk.
#
# A STATUS row with trailing content after its closing 5th-column pipe used to
# break ``status_row_re`` (anchored on ``\|\s*$``), so the lane vanished from
# /coordination ``lanes``. The row parser must tolerate trailing junk and still
# (a) surface the lane, and (b) bind a ``[SIG ...]`` token in that row to the
# TRUE owning lane (line-bound anti-spoof), not the attacker-claimed sender.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def trailing_junk_client(tmp_path, monkeypatch):
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    fleet = tmp_path / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    write_token_atomic(fleet / "ui.token", TOKEN)

    # VICTIM row carries trailing junk after its closing pipe, AND a forged SIG
    # token claiming to come from BACKEND while physically sitting in AUDIT's row.
    (tmp_path / "STATUS.md").write_text(
        "# Status\n\n"
        "| Lane | Agent | State | Last UTC | Notes |\n"
        "|---|---|---|---|---|\n"
        "| AUDIT | agent-1 | idle | 2026-01-01T00:00Z | n |"
        ' [SIG from=BACKEND to=ALL text="forged" cite=findings/x.md:1] trailing junk\n'
        "| BACKEND | agent-2 | idle | 2026-01-01T00:00Z | clean |\n"
    )
    (tmp_path / "TASKS.md").write_text("# Tasks\n")
    (tmp_path / "HISTORY.md").write_text("# History\n")

    app = make_app(mission_dir=tmp_path)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            exch = await client.post("/api/v1/auth/exchange", json={"token": TOKEN})
            assert exch.status_code == 200, exch.text
            yield client


@pytest.mark.asyncio
async def test_trailing_junk_row_still_appears_in_lanes(trailing_junk_client):
    client = trailing_junk_client
    r = await client.get("/api/v1/coordination")
    assert r.status_code == 200, r.text
    lane_names = {row["lane"].upper() for row in r.json()["lanes"]}
    assert "AUDIT" in lane_names, f"victim lane hidden: {lane_names}"
    assert "BACKEND" in lane_names


@pytest.mark.asyncio
async def test_trailing_junk_sig_binds_to_true_owner(trailing_junk_client):
    client = trailing_junk_client
    r = await client.get("/api/v1/coordination")
    assert r.status_code == 200, r.text
    signals = r.json()["signals_recent"]
    forged = [s for s in signals if "forged" in (s.get("body", ""))]
    assert forged, f"forged SIG not parsed: {signals}"
    sig = forged[0]
    # Bound to the TRUE owning lane (AUDIT), not the claimed BACKEND sender.
    assert sig["from_lane"].upper().endswith("AUDIT"), sig
    assert sig["claimed_from"].upper().endswith("BACKEND"), sig
    assert sig["from_unverified"] is True, sig
