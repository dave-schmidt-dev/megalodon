"""Back-compat shape factory for v9.0 missions lacking .mission-config.yaml.

CR-5: includes CHALLENGE-* in task_id_patterns (server.py:653 emits these).
CR-8: synthesize_utc_started precedence — frontmatter → MISSION.md mtime →
      .mission-events mtime → now.
CR-10: INIT prepended to phases (matches index.html:23 phase-segment-INIT).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from .schema import (
    HarnessBinding,
    LaneConfig,
    MissionConfig,
    MissionInfo,
    TaskIdPattern,
)


def _synthesize_utc_started(mission_dir: Path) -> str:
    """CR-8 precedence: MISSION.md frontmatter → MISSION.md mtime → .mission-events mtime → now."""
    import yaml  # local import keeps dep optional at module import time

    mission_md = mission_dir / "MISSION.md"
    if mission_md.exists():
        text = mission_md.read_text(encoding="utf-8")
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
        if m:
            try:
                fm = yaml.safe_load(m.group(1)) or {}
                if isinstance(fm, dict) and "utc_started" in fm:
                    val = fm["utc_started"]
                    if isinstance(val, str) and re.match(
                        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", val
                    ):
                        return val
            except yaml.YAMLError:
                pass
        return datetime.fromtimestamp(
            mission_md.stat().st_mtime, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    events = mission_dir / ".mission-events"
    if events.exists():
        return datetime.fromtimestamp(events.stat().st_mtime, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def synthesize(mission_dir: Path) -> MissionConfig:
    """Build a MissionConfig for a v9.0 mission without .mission-config.yaml."""
    return MissionConfig(
        mission=MissionInfo(
            id=mission_dir.name,
            utc_started=_synthesize_utc_started(mission_dir),
            type="software-engineering",
            description="auto-synthesized back-compat shape for v9.0 mission",
        ),
        lanes=[
            LaneConfig(
                name="AUDIT",
                short="A",
                harness=HarnessBinding(cli="claude", model="claude-sonnet-4-6"),
                cadence_seconds=300,
            ),
            LaneConfig(
                name="ARCHITECT",
                short="B",
                harness=HarnessBinding(cli="claude", model="claude-opus-4-7"),
                cadence_seconds=300,
            ),
            LaneConfig(
                name="BACKEND",
                short="C",
                harness=HarnessBinding(cli="claude", model="claude-opus-4-7"),
                cadence_seconds=180,
            ),
            LaneConfig(
                name="FRONTEND",
                short="D",
                harness=HarnessBinding(cli="claude", model="claude-opus-4-7"),
                cadence_seconds=180,
            ),
            LaneConfig(
                name="TEST",
                short="E",
                harness=HarnessBinding(cli="claude", model="claude-opus-4-7"),
                cadence_seconds=180,
            ),
            LaneConfig(
                name="META",
                short="F",
                harness=HarnessBinding(cli="claude", model="claude-sonnet-4-6"),
                cadence_seconds=420,
            ),
        ],
        phases=[
            "INIT",
            "PHASE-PLAN",
            "PHASE-CHALLENGE",
            "PHASE-BUILD",
            "PHASE-VERIFY",
            "PHASE-RUN",
            "PHASE-HEAL",
            "PHASE-OPERATOR-ACCEPTANCE",
            "DRAINING",
            "COMPLETE",
        ],
        task_id_patterns=TaskIdPattern(
            patterns=[
                r"^(P\d+(\.\d+)?(-[A-F](-to-[A-F])?)?|P\d+-RUN-[A-Z0-9_-]+|REPAIR-[A-Z0-9_-]+|OPERATOR-[A-Z_-]+|S-\d+|TEST-\d+|CHALLENGE-[A-Z0-9_-]+)$"
            ]
        ),
        orchestrator_pseudo_lane="META",  # v9.0 back-compat — server.py uses submitting_lane="META"
        # Match mission.js `TASK_SECTIONS_FALLBACK` 1-for-1 so the FE select
        # offers every canonical section the operator can inject into.
        # E2E spec T-A-IT-e2e exercises `CHALLENGE TASKS`; older 2-entry list
        # silently hid that option (and others) from the dropdown.
        task_sections=[
            "PHASE 1 — PLAN",
            "PHASE 2 — CHALLENGE",
            "PHASE 2.5 — Plan-v2 reconciliation",
            "PHASE 3 — BUILD",
            "PHASE 4 — VERIFY",
            "PHASE 5 — RUN",
            "OPERATOR-ACCEPTANCE TASKS",
            "CHALLENGE TASKS",
            "CROSS-LANE / SECONDARY TASK POOL",
        ],
    )
