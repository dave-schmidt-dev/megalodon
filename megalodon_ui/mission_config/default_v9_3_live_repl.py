"""Template factory for v9.3 live-REPL missions (claude REPL + /loop autonomous).

Returns a MissionConfig where each lane spawns ``claude`` in interactive REPL
mode (no --print) and is bootstrapped post-spawn by sending the lane's
``initial_prompt`` via ``tmux send-keys``. The initial prompt is a single
``/loop`` directive that puts Claude Code into autonomous self-scheduled
iteration on the v9 protocol (claim → work → release → repeat).

Role → model assignment defaults match the v9.3 dogfood plan:

    AUDIT, ARCHITECT  → claude-opus-4-7    (deep reasoning, high stakes)
    BACKEND, FRONTEND → claude-sonnet-4-6  (strong code, faster iteration)
    TEST              → claude-sonnet-4-6  (test design has subtlety)
    META              → claude-haiku-4-5   (structured observation; cheap is fine)

Use ``python -m megalodon_ui.mission_config init --live-repl`` to write this
template to ``<mission_dir>/.mission-config.yaml``. Operators can then edit
initial_prompts, models, or lanes as needed.
"""

from __future__ import annotations

from pathlib import Path

from . import default_v9_0_shape
from .schema import (
    HarnessBinding,
    LaneConfig,
    MissionConfig,
    MissionInfo,
    TaskIdPattern,
)


# Short inline prompt: stays under Claude Code's TUI paste-detection heuristic
# (~57 chars max, observed-safe; over it, send-keys is buffered as a "[Pasted
# text]" placeholder and the bootstrap never fires). The full per-iteration
# instructions live in launch-<NAME>.md files in the mission directory; /loop
# re-fires this short prompt each tick, the agent re-reads the file each tick,
# so operators can edit the file mid-mission without restarting any lane.
#
# The "./" prefix is load-bearing, not cosmetic. The agent is spawned with cwd =
# mission dir (where launch-<NAME>.md lives) and Claude Code injects that cwd
# into its environment — so a cwd-relative path resolves with the Read tool and
# NO shell. A bare filename instead makes the agent run `ls`/`find` to locate
# the file before reading; under the hardened tool surface those gate to a
# permission prompt that stalls the lane indefinitely when the operator is AFK.
# This is the only instruction delivered *before* the first read of launch.md,
# so it must itself prevent that orienting probe (launch.md's own no-explore
# rule can't help until after the file it forbids exploring for has been read).
_LOOP_PROMPT_TEMPLATE = "/loop Read ./launch-{name}.md and run one iteration."


def _initial_prompt(name: str) -> str:
    return _LOOP_PROMPT_TEMPLATE.format(name=name)


def synthesize(mission_dir: Path) -> MissionConfig:
    """Build a MissionConfig for a v9.3 live-REPL mission.

    Six lanes, all claude REPL, role-tiered models, /loop autonomous bootstrap.
    Phases match the v9.0 default shape so the dashboard's phase-strip renders
    correctly out of the box.
    """
    return MissionConfig(
        mission=MissionInfo(
            id=mission_dir.name,
            utc_started=default_v9_0_shape._synthesize_utc_started(mission_dir),
            type="software-engineering",
            description="v9.3 live-REPL template — claude REPL + /loop autonomous per lane",
        ),
        lanes=[
            LaneConfig(
                name="AUDIT",
                short="A",
                role="AUDIT — scrutinize protocol adherence, race conditions, security",
                harness=HarnessBinding(cli="claude", model="claude-opus-4-7"),
                live_repl=True,
                initial_prompt=_initial_prompt("AUDIT"),
            ),
            LaneConfig(
                name="ARCHITECT",
                short="B",
                role="ARCHITECT — design specs, ADRs, integration shapes",
                harness=HarnessBinding(cli="claude", model="claude-opus-4-7"),
                live_repl=True,
                initial_prompt=_initial_prompt("ARCHITECT"),
            ),
            LaneConfig(
                name="BACKEND",
                short="C",
                role="BACKEND — implement server/primitives/adapters in megalodon_ui/",
                harness=HarnessBinding(cli="claude", model="claude-sonnet-4-6"),
                live_repl=True,
                initial_prompt=_initial_prompt("BACKEND"),
            ),
            LaneConfig(
                name="FRONTEND",
                short="D",
                role="FRONTEND — implement UI in ui/static/, wire dashboard forms",
                harness=HarnessBinding(cli="claude", model="claude-sonnet-4-6"),
                live_repl=True,
                initial_prompt=_initial_prompt("FRONTEND"),
            ),
            LaneConfig(
                name="TEST",
                short="E",
                role="TEST — write/run pytest + playwright suites, eliminate skipped/xfail",
                harness=HarnessBinding(cli="claude", model="claude-sonnet-4-6"),
                live_repl=True,
                initial_prompt=_initial_prompt("TEST"),
            ),
            LaneConfig(
                name="META",
                short="F",
                role="META — observe agent behavior, track tick activity, mid/final reports",
                harness=HarnessBinding(cli="claude", model="claude-haiku-4-5-20251001"),
                live_repl=True,
                initial_prompt=_initial_prompt("META"),
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
                r"^(P\d+(\.\d+)?(-[A-F](-to-[A-F])?)?|P\d+-RUN-[A-Z0-9_-]+|REPAIR-[A-Z0-9_-]+|OPERATOR-[A-Z_-]+|S-\d+|TEST-\d+|CHALLENGE-[A-Z0-9_-]+|OA-[A-Z0-9_-]+)$"
            ]
        ),
        orchestrator_pseudo_lane="META",
        task_sections=[
            "PHASE 1 — PLAN",
            "PHASE 2 — BUILD",
            "PHASE 3 — VERIFY",
            "OPERATOR-ACCEPTANCE TASKS",
            "CROSS-LANE / SECONDARY TASK POOL",
        ],
    )
