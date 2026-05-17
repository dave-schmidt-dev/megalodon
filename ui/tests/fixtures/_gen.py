#!/usr/bin/env python3
"""
Seeded generator for Megalodon test fixtures.

Origin: TEST lane (agent-9265), task S-17 (CROSS), mission
2026-05-16--megalodon-self-improvement. Specified in
findings/agent-9265-E-P1-test-plan-2026-05-16T15-33Z.md (§5) and extended in
findings/agent-9265-E-P2.5-test-plan-v2-2026-05-16T15-44Z.md.

Usage:
    python _gen.py --target fix-medium --seed 42
    python _gen.py --target fix-large  --seed 42
    python _gen.py --target fix-medium-failure-modes --seed 42

Targets:
    fix-medium                   6 lanes,  40 ticks, 12 findings, 2 stale rows,
                                 4 signal exchanges across all 4 phases
    fix-large                    8 lanes, 150 ticks, 60 findings, 1 quorum chain,
                                 1 retroactive recovery, 1 stale-reclaim
    fix-medium-failure-modes     fix-medium + 3 baked failure shapes
                                 (stuck-flip / multi-form claim / HISTORY drift)
"""

import argparse
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil


LANES_6 = ["AUDIT", "ARCHITECT", "BACKEND", "FRONTEND", "TEST", "META"]
LANES_8 = LANES_6 + ["LEGAL", "OBSERVER"]
LANE_CODE = {
    "AUDIT": "A", "ARCHITECT": "B", "BACKEND": "C",
    "FRONTEND": "D", "TEST": "E", "META": "F",
    "LEGAL": "G", "OBSERVER": "H",
}
SEVERITIES = ["BLOCKING", "MAJOR", "MINOR", "NIT", "DELTA"]
PHASES = ["PHASE-PLAN", "PHASE-CHALLENGE", "PHASE-BUILD", "PHASE-VERIFY"]


def agent_id(rng):
    return f"agent-{rng.randrange(0x10000):04x}"


def utc(start, tick_index, cadence_min=3):
    dt = start + timedelta(minutes=tick_index * cadence_min)
    return dt.strftime("%Y-%m-%dT%H:%MZ")


def write_finding(path, lane, agent, task, severity, utc_str, body_lines=4):
    content = [
        "---",
        f"lane: {lane}",
        f"agent: {agent}",
        f"task: {task}",
        f"severity: {severity}",
        f"utc: {utc_str}",
        "artifact: synthetic (fixture)",
        "---",
        "",
        f"# Finding: {task} dummy",
        "",
        "## Summary",
        "",
        "Seeded fixture finding. Body is intentionally minimal.",
        "",
        "## Pass 1 — Fresh-eyes findings",
        "",
    ]
    for i in range(body_lines):
        content.append(f"{i+1}. synthetic claim — `synthetic:{i+1}` — recommend nothing.")
    content += ["", "## Confidence", "", "HIGH (fixture)."]
    path.write_text("\n".join(content) + "\n")


def init_root(root: Path, lanes, mission_id):
    if root.exists():
        shutil.rmtree(root)
    (root / "claims").mkdir(parents=True)
    (root / "findings").mkdir(parents=True)
    (root / ".phase-flip-locks").mkdir(parents=True)
    (root / "MISSION.md").write_text(
        f"# Mission (fixture: {mission_id})\n\n"
        f"**Status:** ACTIVE\n**Lanes:** {len(lanes)} ({', '.join(lanes)})\n"
        f"**Cadence:** 3 min\n\n## Source project\nSynthetic — fixture only.\n"
    )


def gen_medium(root: Path, rng, lanes=LANES_6, n_findings=12, with_failure_modes=False):
    start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    mission_id = "fix-medium-failure-modes" if with_failure_modes else "fix-medium"
    init_root(root, lanes, mission_id)

    # Spec-mandated severity mix from P1-E §5
    severity_mix = ["BLOCKING", "MAJOR", "MAJOR", "MAJOR",
                    "MINOR", "MINOR", "MINOR", "MINOR", "MINOR",
                    "NIT", "NIT", "DELTA"]
    assert len(severity_mix) == n_findings, "severity mix must equal n_findings"

    agents = {lane: agent_id(rng) for lane in lanes}

    # Phase events — 4 phases, evenly distributed across 40 ticks
    events = [(start, "INIT", PHASES[0], "orchestrator", "mission start")]
    phase_boundaries = [10, 20, 30]  # tick indices
    for i, b in enumerate(phase_boundaries):
        events.append((
            start + timedelta(minutes=b * 3),
            PHASES[i], PHASES[i + 1],
            rng.choice(list(agents.values())),
            f"all P{i+1} done",
        ))
    (root / ".mission-events").write_text(
        "\n".join(
            f"{e[0].strftime('%Y-%m-%dT%H:%M:%SZ')} {e[1]}->{e[2]} by {e[3]} — {e[4]}"
            for e in events
        ) + "\n"
    )

    # Findings spread across phases (3 per phase)
    findings_per_phase = [n_findings // 4] * 4
    for extra in range(n_findings - sum(findings_per_phase)):
        findings_per_phase[extra] += 1

    sev_iter = iter(severity_mix)
    history_lines = []
    for phase_idx, count in enumerate(findings_per_phase):
        base_tick = phase_idx * 10 + 2
        for i in range(count):
            lane = rng.choice(lanes)
            agent = agents[lane]
            code = LANE_CODE[lane]
            task = f"P{phase_idx+1}-{code}"
            sev = next(sev_iter)
            tick = base_tick + i
            ts = utc(start, tick)
            fname = f"{agent}-{code}-{task}-{ts.replace(':', '-')}.md"
            path = root / "findings" / fname
            write_finding(path, lane, agent, task, sev, ts)
            history_lines.append(f"{ts} | {agent} | LANE-{code} | {task} | findings/{fname} | {sev}")
            # claim dir + done marker
            claim_dir = root / "claims" / task
            claim_dir.mkdir(exist_ok=True)
            (claim_dir / "done").touch()

    (root / "HISTORY.md").write_text(
        "# History\n\n" + "\n".join(history_lines) + "\n"
    )

    # STATUS — 2 stale rows >15min, plus signal exchanges
    now_tick = 38  # near end of run
    now_utc = utc(start, now_tick)
    rows = []
    for i, lane in enumerate(lanes):
        if i < 2:
            # stale rows: Last UTC 20 min behind now
            stale_utc = utc(start, now_tick - 7)  # 21 min stale
            state = f"working: P4-{LANE_CODE[lane]}"
            note = "no heartbeat — stale candidate for RULE 6 reclaim"
        elif i == 2:
            state = "working: P4-" + LANE_CODE[lane]
            stale_utc = now_utc
            note = (
                f"SIG-FROM-LANE-{LANE_CODE[lanes[3]]}: please verify finding-X@line5 "
                f"(evidence: findings/{agents[lanes[3]]}-{LANE_CODE[lanes[3]]}-P3-{LANE_CODE[lanes[3]]}-...md:5)"
            )
        elif i == 3:
            state = "idle"
            stale_utc = now_utc
            note = (
                f"ACK-VERIFIED LANE-{LANE_CODE[lanes[2]]}: read at "
                f"{now_utc} — confirm. RECONSIDERED in scratch."
            )
        elif i == 4:
            state = "idle"
            stale_utc = now_utc
            note = (
                f"DISSENT LANE-{LANE_CODE[lanes[5]]}: read at {now_utc} — "
                "disagree because cite missing. Finding stands."
            )
        else:
            state = "idle"
            stale_utc = now_utc
            note = (
                f"DEFER LANE-{LANE_CODE[lanes[4]]}: will address in tick 40 "
                "during my P4 work."
            )
        rows.append(f"| {lane:<9} | {agents[lane]} | {state} | {stale_utc} | {note} |")

    (root / "STATUS.md").write_text(
        "# Status board\n\n"
        "| Lane | Agent | State | Last UTC | Notes |\n"
        "|---|---|---|---|---|\n"
        + "\n".join(rows) + "\n"
    )

    # TASKS — minimal, just the 4 phases × 6 lanes shape
    task_lines = ["# Tasks\n"]
    for phase_idx in range(4):
        task_lines.append(f"\n## PHASE {phase_idx+1}\n")
        for lane in lanes:
            code = LANE_CODE[lane]
            tid = f"P{phase_idx+1}-{code}"
            done = (root / "claims" / tid / "done").exists()
            agent = agents[lane]
            if done:
                task_lines.append(
                    f"- [done: {agent} @ {utc(start, phase_idx*10 + 2)}] "
                    f"[LANE-{code}] `{tid}` — dummy phase-{phase_idx+1} task for {lane}"
                )
            else:
                task_lines.append(
                    f"- [ ] [LANE-{code}] `{tid}` — dummy phase-{phase_idx+1} task for {lane}"
                )
    (root / "TASKS.md").write_text("\n".join(task_lines) + "\n")

    if with_failure_modes:
        bake_failure_modes(root, agents, lanes, start)


def bake_failure_modes(root, agents, lanes, start):
    """CHALLENGE-4 from META P2-F→E: three pathological shapes."""

    # Shape-A: stuck phase-flip. Lock exists, .mission-events doesn't reflect flip.
    (root / ".phase-flip-locks" / "PHASE-PLAN-to-PHASE-CHALLENGE").mkdir(exist_ok=True)
    me = root / ".mission-events"
    text = me.read_text().splitlines()
    text = [line for line in text if "PHASE-CHALLENGE" not in line.split(" by ")[0]]
    me.write_text("\n".join(text) + "\n")
    # backdate lock age to 5 min before "now" (tick 38)
    stuck_age_utc = (start + timedelta(minutes=33 * 3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    (root / ".phase-flip-locks" / "PHASE-PLAN-to-PHASE-CHALLENGE" / ".stuck-at").write_text(stuck_age_utc)

    # Shape-B: two claim dirs for same logical task.
    canon = root / "claims" / "P2-C-to-B"
    noncanon = root / "claims" / "P2-C→B"
    canon.mkdir(exist_ok=True)
    noncanon.mkdir(exist_ok=True)
    (canon / "done").touch()
    (noncanon / "done").touch()
    # only one finding exists for the canonical form
    fname = f"{agents['BACKEND']}-C-P2-C-to-B-{utc(start, 12).replace(':', '-')}.md"
    write_finding(
        root / "findings" / fname,
        "BACKEND", agents["BACKEND"], "P2-C-to-B", "MAJOR", utc(start, 12),
    )

    # Shape-C: HISTORY format drift. Inject 3 spelling variants.
    drift_lines = [
        f"{utc(start, 11)} | {agents['META']} | F | DRIFT-1 | findings/dummy-drift-1.md | NIT",
        f"{utc(start, 12)} | {agents['FRONTEND']} | FRONTEND | DRIFT-2 | findings/dummy-drift-2.md | NIT",
        f"{utc(start, 13)} | {agents['AUDIT']} | LANE-A | DRIFT-3 | findings/dummy-drift-3.md | NIT",
    ]
    h = root / "HISTORY.md"
    h.write_text(h.read_text().rstrip() + "\n" + "\n".join(drift_lines) + "\n")


def gen_large(root: Path, rng, n_ticks=150, n_findings=60):
    """8 lanes, 150 ticks, 60 findings, with quorum + recovery + stale-reclaim."""
    init_root(root, LANES_8, "fix-large")
    start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    agents = {lane: agent_id(rng) for lane in LANES_8}

    # Phase events — same 4-phase structure stretched to 150 ticks
    events = [(start, "INIT", PHASES[0], "orchestrator", "mission start")]
    boundaries = [37, 74, 112]
    for i, b in enumerate(boundaries):
        events.append((
            start + timedelta(minutes=b * 3),
            PHASES[i], PHASES[i + 1],
            rng.choice(list(agents.values())),
            f"all P{i+1} done",
        ))
    (root / ".mission-events").write_text(
        "\n".join(
            f"{e[0].strftime('%Y-%m-%dT%H:%M:%SZ')} {e[1]}->{e[2]} by {e[3]} — {e[4]}"
            for e in events
        ) + "\n"
    )

    # Findings — even spread across phases
    per_phase = [n_findings // 4] * 4
    sev_choices = ["BLOCKING"] * 2 + ["MAJOR"] * 12 + ["MINOR"] * 28 + ["NIT"] * 12 + ["DELTA"] * 6
    rng.shuffle(sev_choices)
    sev_iter = iter(sev_choices)

    history_lines = []
    for phase_idx, count in enumerate(per_phase):
        base = phase_idx * 37 + 5
        for i in range(count):
            lane = rng.choice(LANES_8)
            agent = agents[lane]
            code = LANE_CODE[lane]
            tid = f"P{phase_idx+1}-{code}-{i:02d}"
            sev = next(sev_iter)
            tick = base + i
            ts = utc(start, tick)
            fname = f"{agent}-{code}-{tid}-{ts.replace(':', '-')}.md"
            write_finding(root / "findings" / fname, lane, agent, tid, sev, ts)
            history_lines.append(f"{ts} | {agent} | LANE-{code} | {tid} | findings/{fname} | {sev}")
            claim_dir = root / "claims" / tid
            claim_dir.mkdir(exist_ok=True)
            (claim_dir / "done").touch()

    # Quorum chain: 3 findings on same artifact path
    quorum_artifact = "synthetic-shared-artifact.py:42"
    for i, lane in enumerate(LANES_8[:3]):
        agent = agents[lane]
        code = LANE_CODE[lane]
        tid = f"QUORUM-{code}"
        ts = utc(start, 80 + i)
        fname = f"{agent}-{code}-{tid}-{ts.replace(':', '-')}.md"
        path = root / "findings" / fname
        write_finding(path, lane, agent, tid, "MAJOR", ts)
        # rewrite to cite the shared artifact
        text = path.read_text().replace("artifact: synthetic (fixture)", f"artifact: {quorum_artifact}")
        path.write_text(text)
        history_lines.append(f"{ts} | {agent} | LANE-{code} | {tid} | findings/{fname} | MAJOR")
        claim_dir = root / "claims" / tid
        claim_dir.mkdir(exist_ok=True)
        (claim_dir / "done").touch()

    # Retroactive recovery case: finding exists, no claim done marker initially,
    # then recovered. Encode by leaving an UNRECOVERED claim dir + a recovery
    # HISTORY entry referencing it.
    recovery_tid = "RECOVERY-X"
    (root / "claims" / recovery_tid).mkdir(exist_ok=True)
    (root / "claims" / recovery_tid / "done").touch()
    rec_agent = agents["LEGAL"]
    rec_ts = utc(start, 95)
    rec_fname = f"{rec_agent}-G-{recovery_tid}-{rec_ts.replace(':', '-')}.md"
    write_finding(root / "findings" / rec_fname, "LEGAL", rec_agent, recovery_tid, "MAJOR", rec_ts)
    history_lines.append(f"{rec_ts} | {rec_agent} | LANE-G | {recovery_tid} | findings/{rec_fname} | MAJOR")
    history_lines.append(f"{utc(start, 96)} | agent-recv | RECOVERY | {recovery_tid} | findings/{rec_fname} | RECOVERY — retroactive done-marker added")

    (root / "HISTORY.md").write_text("# History\n\n" + "\n".join(history_lines) + "\n")

    # STATUS — 1 stale-reclaimed lane + 7 working/idle
    now_utc = utc(start, n_ticks - 1)
    rows = []
    for i, lane in enumerate(LANES_8):
        agent = agents[lane]
        if i == 0:
            state = "STALE-RECLAIMED"
            row_utc = utc(start, n_ticks - 20)
            note = "reclaimed at tick 130 — original agent unresponsive >15min"
        else:
            state = "idle"
            row_utc = now_utc
            note = "drained"
        rows.append(f"| {lane:<10} | {agent} | {state} | {row_utc} | {note} |")

    (root / "STATUS.md").write_text(
        "# Status board\n\n"
        "| Lane | Agent | State | Last UTC | Notes |\n"
        "|---|---|---|---|---|\n"
        + "\n".join(rows) + "\n"
    )

    # TASKS — abbreviated
    (root / "TASKS.md").write_text(
        "# Tasks\n\n"
        "(Generated fixture — see HISTORY.md and claims/ for full state.)\n"
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--target", required=True, choices=[
        "fix-medium", "fix-large", "fix-medium-failure-modes",
    ])
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", type=Path, default=Path(__file__).parent)
    args = p.parse_args()

    rng = random.Random(args.seed)
    target_root = args.out / args.target

    if args.target == "fix-medium":
        gen_medium(target_root, rng)
    elif args.target == "fix-medium-failure-modes":
        gen_medium(target_root, rng, with_failure_modes=True)
    elif args.target == "fix-large":
        gen_large(target_root, rng)

    print(f"Generated {args.target} at {target_root}")


if __name__ == "__main__":
    main()
