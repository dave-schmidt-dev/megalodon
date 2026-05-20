"""CV-1 — ctx-bound regex drives status and tasks parsing.

Regression net: if the migration regressed and parse_status / parse_tasks
still used module-level v9.0 regex globals compiled against ABCDEF shorts,
a mission with non-default lane shorts (PQRSTU) would return empty rows
here, causing these tests to fail.

This file builds a minimal mission in tmp_path with:
  - 6 lanes whose shorts are P, Q, R, S, T, U (not in the v9.0 A-F set).
  - STATUS.md with one row per lane.
  - TASKS.md with one phase and one task per lane.

Then verifies that GET /api/v1/state and GET /api/v1/tasks correctly parse
those rows/tasks via the ctx-bound regex.
"""
from __future__ import annotations

from pathlib import Path

import pytest

try:
    from megalodon_ui.server import make_app
    _BACKEND_AVAILABLE = True
except ImportError:
    make_app = None  # type: ignore[assignment]
    _BACKEND_AVAILABLE = False


# Non-default shorts: P, Q, R, S, T, U — none of these appear in the v9.0
# default set (A, B, C, D, E, F), so any leakage of the old module-level
# regex will produce empty parse results.
_NON_DEFAULT_SHORTS = ["P", "Q", "R", "S", "T", "U"]
_LANE_NAMES = ["PAPA", "QUEBEC", "ROMEO", "SIERRA", "TANGO", "UNIFORM"]

_MISSION_CONFIG_YAML = """\
schema_version: 1
mission:
  id: cv1-non-default-shorts
  utc_started: "2026-01-01T00:00:00Z"
  type: software-engineering
  description: CV-1 regression fixture with non-v9.0-default lane shorts
lanes:
  - name: PAPA
    short: P
    role: builder
    harness:
      cli: claude
      model: claude-sonnet-4-6
    cadence_seconds: 300
  - name: QUEBEC
    short: Q
    role: builder
    harness:
      cli: claude
      model: claude-sonnet-4-6
    cadence_seconds: 300
  - name: ROMEO
    short: R
    role: builder
    harness:
      cli: claude
      model: claude-sonnet-4-6
    cadence_seconds: 300
  - name: SIERRA
    short: S
    role: builder
    harness:
      cli: claude
      model: claude-sonnet-4-6
    cadence_seconds: 300
  - name: TANGO
    short: T
    role: builder
    harness:
      cli: claude
      model: claude-sonnet-4-6
    cadence_seconds: 300
  - name: UNIFORM
    short: U
    role: builder
    harness:
      cli: claude
      model: claude-sonnet-4-6
    cadence_seconds: 300
phases:
  - INIT
  - PHASE-PLAN
  - COMPLETE
"""

_STATUS_MD = """\
# Status board

| Lane | Agent | State | Last UTC | Notes |
|---|---|---|---|---|
| PAPA    | agent-cv1p | idle | 2026-01-01T00:00:00Z | ok |
| QUEBEC  | agent-cv1q | idle | 2026-01-01T00:01:00Z | ok |
| ROMEO   | agent-cv1r | idle | 2026-01-01T00:02:00Z | ok |
| SIERRA  | agent-cv1s | idle | 2026-01-01T00:03:00Z | ok |
| TANGO   | agent-cv1t | idle | 2026-01-01T00:04:00Z | ok |
| UNIFORM | agent-cv1u | idle | 2026-01-01T00:05:00Z | ok |
"""

_TASKS_MD = """\
# Tasks

## PHASE-PLAN

- [ ] [LANE-P] `CV1-P1` — task for PAPA lane
- [ ] [LANE-Q] `CV1-Q1` — task for QUEBEC lane
- [ ] [LANE-R] `CV1-R1` — task for ROMEO lane
- [ ] [LANE-S] `CV1-S1` — task for SIERRA lane
- [ ] [LANE-T] `CV1-T1` — task for TANGO lane
- [ ] [LANE-U] `CV1-U1` — task for UNIFORM lane
"""


@pytest.fixture
def cv1_mission(tmp_path: Path) -> Path:
    """Build a minimal mission dir with non-default lane shorts P-U."""
    dest = tmp_path / "cv1_mission"
    dest.mkdir()
    (dest / ".mission-config.yaml").write_text(_MISSION_CONFIG_YAML)
    (dest / "STATUS.md").write_text(_STATUS_MD)
    (dest / "TASKS.md").write_text(_TASKS_MD)
    return dest


@pytest.mark.asyncio
@pytest.mark.skipif(not _BACKEND_AVAILABLE, reason="megalodon_ui.server not available")
async def test_state_endpoint_parses_non_default_lane_rows(cv1_mission: Path):
    """GET /api/v1/state returns all 6 lanes with non-default shorts P-U.

    If the regex migration regressed and the old v9.0 module-level regex (A-F)
    is still used, this returns 0 rows. All 6 rows must be present.
    """
    from httpx import AsyncClient, ASGITransport

    app = make_app(mission_dir=cv1_mission)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            r = await client.get("/api/v1/state")

    assert r.status_code == 200, f"unexpected status {r.status_code}: {r.text}"
    body = r.json()
    lanes = body["status"]["lanes"]
    assert len(lanes) == 6, (
        f"expected 6 lanes in /api/v1/state, got {len(lanes)} — "
        f"regex migration may have regressed (v9.0 A-F charclass still in use)"
    )
    lane_names_returned = {row["lane"].strip() for row in lanes}
    for name in _LANE_NAMES:
        assert name in lane_names_returned, (
            f"lane {name!r} missing from /api/v1/state response; "
            f"got: {lane_names_returned}"
        )


@pytest.mark.asyncio
@pytest.mark.skipif(not _BACKEND_AVAILABLE, reason="megalodon_ui.server not available")
async def test_tasks_endpoint_parses_non_default_lane_tasks(cv1_mission: Path):
    """GET /api/v1/tasks returns all 6 tasks whose LANE- shorts are P-U.

    If the regex migration regressed and the old v9.0 module-level regex (A-F)
    is still used, this returns 0 tasks. All 6 tasks must be present under
    the PHASE-PLAN phase.
    """
    from httpx import AsyncClient, ASGITransport

    app = make_app(mission_dir=cv1_mission)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            r = await client.get("/api/v1/tasks")

    assert r.status_code == 200, f"unexpected status {r.status_code}: {r.text}"
    body = r.json()
    phases = body["phases"]
    assert len(phases) == 1, f"expected 1 phase (PHASE-PLAN), got {len(phases)}"

    phase = phases[0]
    assert phase["name"] == "PHASE-PLAN", f"expected PHASE-PLAN, got {phase['name']!r}"

    tasks = phase["tasks"]
    assert len(tasks) == 6, (
        f"expected 6 tasks in PHASE-PLAN, got {len(tasks)} — "
        f"regex migration may have regressed (v9.0 A-F charclass still in use)"
    )

    task_ids = {t["id"] for t in tasks}
    expected_ids = {"CV1-P1", "CV1-Q1", "CV1-R1", "CV1-S1", "CV1-T1", "CV1-U1"}
    assert task_ids == expected_ids, (
        f"task id mismatch: expected {expected_ids}, got {task_ids}"
    )

    # Verify lane attribution maps each short to its config-declared long
    # name (PAPA/QUEBEC/ROMEO/SIERRA/TANGO/UNIFORM). The FE kanban buckets
    # tasks by `lane.name` from config; parse_tasks does this mapping so the
    # kanban renders rows in the right column.
    task_lanes = {t["lane"] for t in tasks}
    expected_lanes = set(_LANE_NAMES)
    assert task_lanes == expected_lanes, (
        f"task lane mismatch: expected {expected_lanes}, got {task_lanes}"
    )


@pytest.mark.asyncio
@pytest.mark.skipif(not _BACKEND_AVAILABLE, reason="megalodon_ui.server not available")
async def test_ctx_bound_regex_not_v9_default(cv1_mission: Path):
    """Direct assertion: the ctx.task_line_re charclass must include P-U, not just A-F."""
    from megalodon_ui.server import make_app as _make_app

    app = _make_app(mission_dir=cv1_mission)
    ctx = app.state.megalodon

    pattern_str = ctx.task_line_re.pattern
    # The v9.0 default charclass is [A-F]. With P-U lanes, the pattern must
    # reference those shorts. A contiguous P-U set yields [P-U].
    assert "[A-F]" not in pattern_str, (
        f"ctx.task_line_re still contains v9.0 default charclass [A-F]: {pattern_str!r}"
    )
    # Each non-default short must be representable by the compiled pattern.
    sample_task_line = "- [ ] [LANE-P] `CV1-P1` — task for PAPA lane"
    assert ctx.task_line_re.search(sample_task_line) is not None, (
        f"ctx.task_line_re does not match LANE-P task line; pattern: {pattern_str!r}"
    )
