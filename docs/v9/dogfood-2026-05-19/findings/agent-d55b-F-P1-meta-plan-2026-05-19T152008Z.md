# META Plan: v9.3 Dogfood Observation Framework

**Agent:** `agent-d55b` (LANE-F)  
**Phase:** PHASE 1 — PLAN  
**Task:** P1-F — Design observation framework  
**Timestamp:** 2026-05-19T15:20:08Z

---

## Summary

This document establishes the observation framework for the v9.3 live dogfood run. The framework tracks **tick activity, claim lifecycle, and idle patterns** across all 6 lanes to identify bottlenecks, failure modes, and task-completion rates. Data is collected passively from git state (claims/, findings/, TASKS.md, STATUS.md) and analyzed at regular checkpoints (P2-F mid-mission report, final OA findings).

---

## Observation Targets

### 1. Tick Activity (per lane, per phase)

**What to track:**
- **Tick count**: How many times did each lane's `/loop` fire?
- **Tick duration**: How long between `/loop` invocation and task completion/idle?
- **Tick frequency**: Self-paced or blocked waiting for events?

**Data source:**
- `claims/<task-id>/owner.txt` creation/deletion timestamps
- `findings/` file timestamps (indicates when a lane finished work)
- File modification time (`stat`) on launch-*.md logs if available

**Observable signal:**
```
Lane A: P1-A claimed @ 15:31, findings written @ 15:42 → 11min tick
Lane A: P1-A claim released @ 15:42, idle until 15:47 → 5min idle gap
Lane A: Next task claimed @ 15:47 → 5min latency after previous release
```

**Threshold alert:** If tick duration > 30min or idle gap > 5min, investigate (blocked, stuck, or slow task).

---

### 2. Time-to-First-Claim (per lane, per phase)

**What to track:**
- Phase-flip timestamp (mission PHASE 1 start)
- First task claimed timestamp per lane
- Delta = **latency to engagement**

**Data source:**
- `MISSION.md` (phase start recorded when operator flips)
- Oldest `claims/<task-id>/owner.txt` per lane per phase (creation time)

**Observable signal:**
```
Phase flip @ 15:30
Lane A first claim @ 15:31 → 1min engagement latency
Lane B first claim @ 15:35 → 5min engagement latency (slower spawn? network?)
Lane F first claim @ 15:20:08 → 0min (meta is synchronous)
```

**Threshold alert:** If time-to-first-claim > 10min, check if lane is stuck at spawn, network, or hardware delay.

---

### 3. Claim Duration (per task)

**What to track:**
- Claim creation → completion (task marked `[done: ...]` in TASKS.md)
- Claim creation → abandonment (task remains open, claim released without completion)

**Data source:**
- `claims/<task-id>/owner.txt` creation time
- Task state in TASKS.md (`[done: ...]` or `[ ]` with no claim)
- Finding file timestamp (usually written before task marked done)

**Observable signal:**
```
P1-A claimed @ 15:31
findings/agent-<X>-A-P1-audit-plan-*.md written @ 15:42
TASKS.md updated P1-A → [done: agent-<X> @ 15:42]
claim released @ 15:42
→ Claim duration: 11min, completion time: 11min
```

**Threshold alert:** If claim held > 60min without task completion, likely abandoned or stuck.

---

### 4. Idle Gaps (per lane)

**What to track:**
- Time between release of claim N and creation of claim N+1
- Reason for idle: (a) waiting for phase flip, (b) no available tasks, (c) backoff (ScheduleWakeup delay), (d) blocked on external event

**Data source:**
- Sequence of `claims/<task-id>/` timestamps
- Task states in TASKS.md at each idle boundary
- ScheduleWakeup reason encoded in findings/agent-<id>-F-P2-meta-mid-*.md (META writes analysis)

**Observable signal:**
```
Lane C releases P1-C @ 15:50
Next available task in PHASE 1: none (P1-A, P1-B already started)
Lane C scheduled wakeup to 15:55 (5min delay per launch-BACKEND.md)
Lane C idle 15:50–15:55 → Reason: waiting for next task (backoff, not blocked)
```

**Threshold alert:** If idle gap > 30min and phase hasn't flipped, investigate task starvation or phase deadlock.

---

### 5. Task Completion Rate & Failure Modes

**What to track:**
- Tasks completed vs. deferred/abandoned per phase
- Completion time distribution (histogram: P1 tasks typically 10–20min?)
- Failure reasons (if a lane writes a finding noting blockers)

**Data source:**
- Count of `[done: ...]` entries in TASKS.md per phase
- Findings files with "blocked", "deferred", or "next steps" notes
- Manual scan: do P1 findings recommend reordering PHASE 2 tasks?

**Observable signal:**
```
PHASE 1 complete:
- P1-A: done in 11min ✓
- P1-B: done in 22min ✓
- P1-C: done in 18min ✓
- P1-D: stuck (Playwright fixture issue), deferred, idle 45min
- P1-E: done in 12min ✓
- P1-F: done in 5min ✓

→ 5/6 lanes complete. Lane D blocklist -> ARCHITECT mitigates in P2 design.
```

---

### 6. Cross-Lane Signals & Dependencies

**What to track:**
- Do findings from one lane influence another lane's PHASE 2 task order?
- Does a blocked lane (e.g., FRONTEND waiting on ARCHITECT design) cause cascading idle?
- Recommendation flow: P1 findings → P2 task adjustments

**Data source:**
- Findings files (grep for "blocked by", "depends on", "see Lane-X")
- Manual correlation: if Lane X completes before Lane Y, does Y's task become possible?

**Observable signal:**
```
P1-B finding recommends deferring live_repl generalization to v9.4
→ P2-B task scope reduced
→ LANE-C (backend) waiting on P2-B design can start parallel work
→ Phase 2 completes faster due to parallelism unlock
```

---

## Instrumentation Strategy

### Passive collection (no code changes):
1. **Snapshot TASKS.md & STATUS.md** at regular intervals (every P2-F tick) → track claim state deltas
2. **Scan findings/** directory → extract completion times, blocker notes, recommendations
3. **Compute metrics** from file timestamps and git state

### Active collection (META writes to findings/):
1. **P2-F mid-mission report** (`findings/agent-d55b-F-P2-meta-mid-*.md`):
   - Per-lane: tick count, idle duration, tasks completed, dominant failure mode
   - Phase-level: overall completion %, critical path, top blockers
   
2. **OA findings** (if requested):
   - Recommendations for v9.4 run structure
   - Identify lanes that would benefit from different model/cadence (e.g., slower model for long-running analysis?)

---

## Metrics Dashboard (conceptual)

```
PHASE 1 — PLAN SUMMARY
====================
Elapsed:        15:20–16:05 (45 min total)
Active lanes:   6/6
Completed:      5/6 tasks
Avg tick time:  12 min
Avg idle gap:   3 min

Per-lane snapshot:
┌─ LANE-A (AUDIT)     │ ticks: 1 │ claimed: 11m │ done ✓
├─ LANE-B (ARCHITECT) │ ticks: 1 │ claimed: 22m │ done ✓
├─ LANE-C (BACKEND)   │ ticks: 1 │ claimed: 18m │ done ✓
├─ LANE-D (FRONTEND)  │ ticks: 3 │ claimed: 45m │ blocked [Playwright v92]
├─ LANE-E (TEST)      │ ticks: 1 │ claimed: 12m │ done ✓
└─ LANE-F (META)      │ ticks: 1 │ claimed:  5m │ done ✓

Critical path: LANE-D (Playwright fixture blocker) → PHASE 2 design
Next phase unblocks: LANE-B design fixes LANE-D fixture in P2
```

---

## Evidence & Next Steps

**Framework is now ready for:**
1. **P2-F iteration**: Collect tick activity, idle patterns, task completion data → write `agent-d55b-F-P2-meta-mid-*.md`
2. **Continuous observation**: Each META tick snapshots TASKS/STATUS and updates findings directory
3. **OA analysis**: Final report with recommendations for future run structure

**Key files to monitor:**
- `claims/` — source of truth for active work
- `findings/` — output and blocker tracking
- `TASKS.md` — completion state
- `STATUS.md` — lane state summary

**No code changes required.** Observation is passive (file timestamps, git state).

---

## Recommendations for P2

1. **Per-lane telemetry**: If lanes write `launch-*.md` logs with tick timestamps, mirror metrics to `findings/` for easier tracking.
2. **Idle reason propagation**: When ScheduleWakeup is called with a reason, log it to a metrics file so META can correlate idle gaps to specific blockers.
3. **Phase-flip signal**: Ensure MISSION.md timestamp is updated when operator flips phases, so time-to-first-claim is precise.

---

**Plan complete. Ready for PHASE 1 execution.**
