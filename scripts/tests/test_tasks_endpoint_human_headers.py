"""GET /api/v1/tasks honors config.task_sections (human-readable headers).

Regression for the P0 backend bug: ``/api/v1/tasks`` called ``parse_tasks``,
whose section matcher only recognized canonical phase tokens (``## PHASE-PLAN``)
from ``config.phases`` — NOT the human section headers (``## PHASE 1 — PLAN``)
that real missions declare in ``config.task_sections``. Real missions therefore
got ``{"phases": []}`` and the kanban showed "No tasks loaded yet", even though
``/api/v1/state`` (which uses ``parse_tasks_fe_shape``) parsed fine.

These tests build a mission whose TASKS.md uses the human header format and
exercises every task state (pending/in-progress/done/blocked), then asserts
``/api/v1/tasks`` returns populated phases with each state correctly decoded.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from megalodon_ui.server import make_app, parse_tasks

# A mission config whose task_sections use the human header style. Lanes A/B/C
# so the default task-line regex (A-F shorts) matches.
_MISSION_CONFIG_YAML = """\
schema_version: 1
mission:
  id: human-header-fixture
  utc_started: "2026-01-01T00:00:00Z"
  type: software-engineering
  description: human-readable TASKS.md section headers
lanes:
  - name: AUDIT
    short: A
    role: builder
    harness:
      cli: claude
      model: claude-sonnet-4-6
    cadence_seconds: 300
  - name: BUILD
    short: B
    role: builder
    harness:
      cli: claude
      model: claude-sonnet-4-6
    cadence_seconds: 300
  - name: CHECK
    short: C
    role: builder
    harness:
      cli: claude
      model: claude-sonnet-4-6
    cadence_seconds: 300
phases:
  - INIT
  - PHASE-PLAN
  - PHASE-BUILD
  - COMPLETE
task_sections:
  - "PHASE 1 — PLAN"
  - "PHASE 2 — BUILD"
  - "CROSS-LANE / SECONDARY TASK POOL"
"""

# Real-format TASKS.md: HUMAN headers, all four states exercised.
_TASKS_MD = """\
# Tasks

## PHASE 1 — PLAN

- [ ] [LANE-A] `HH-A1` — pending audit task
- [claimed: agent-b @ 2026-01-01T01:00:00Z] [LANE-B] `HH-B1` — in-progress build task

## PHASE 2 — BUILD

- [done: agent-c @ 2026-01-01T02:00:00Z] [LANE-C] `HH-C1` — finished check task
- [blocked: waiting on signal] [LANE-A] `HH-A2` — blocked audit task

## CROSS-LANE / SECONDARY TASK POOL

- [ ] [LANE-B] `HH-X1` — cross-lane pool task
"""


@pytest.fixture
def human_header_mission(tmp_path: Path) -> Path:
    dest = tmp_path / "hh_mission"
    dest.mkdir()
    (dest / ".mission-config.yaml").write_text(_MISSION_CONFIG_YAML)
    (dest / "STATUS.md").write_text("# Status\n")
    (dest / "TASKS.md").write_text(_TASKS_MD)
    return dest


def test_parse_tasks_unit_human_headers(human_header_mission: Path) -> None:
    """parse_tasks() (no ctx) parses human headers into populated phases."""
    phases = parse_tasks(human_header_mission)
    names = {p["name"] for p in phases}
    assert "PHASE 1 — PLAN" in names
    assert "PHASE 2 — BUILD" in names
    assert "CROSS-LANE / SECONDARY TASK POOL" in names

    all_tasks = [t for p in phases for t in p["tasks"]]
    by_id = {t["id"]: t for t in all_tasks}
    assert set(by_id) == {"HH-A1", "HH-B1", "HH-C1", "HH-A2", "HH-X1"}

    # Each state decodes correctly.
    assert by_id["HH-A1"]["state"] == "open"
    assert by_id["HH-B1"]["state"] == "claimed"
    assert by_id["HH-B1"]["agent"] == "agent-b"
    assert by_id["HH-C1"]["state"] == "done"
    assert by_id["HH-C1"]["agent"] == "agent-c"
    assert by_id["HH-A2"]["state"] == "blocked"

    # Lane short is mapped to the config-declared long name (FE buckets by it).
    assert by_id["HH-A1"]["lane"] == "AUDIT"
    assert by_id["HH-C1"]["lane"] == "CHECK"


@pytest.mark.asyncio
async def test_tasks_endpoint_human_headers_non_empty(
    human_header_mission: Path,
) -> None:
    """GET /api/v1/tasks returns populated phases for a human-header TASKS.md."""
    app = make_app(mission_dir=human_header_mission)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Deny-by-default gate: /api/v1/tasks now requires a session cookie.
            client.cookies.set(
                "mui_session", app.state.megalodon.session_store.create()
            )
            r = await client.get("/api/v1/tasks")

    assert r.status_code == 200, f"unexpected status {r.status_code}: {r.text}"
    body = r.json()
    phases = body["phases"]
    assert phases, "expected non-empty phases (the kanban-blank bug regression)"

    all_tasks = [t for p in phases for t in (p.get("tasks") or [])]
    assert len(all_tasks) == 5, f"expected 5 tasks, got {len(all_tasks)}"
    states = {t["id"]: t["state"] for t in all_tasks}
    assert states["HH-A1"] == "open"
    assert states["HH-B1"] == "claimed"
    assert states["HH-C1"] == "done"
    assert states["HH-A2"] == "blocked"
