# LANE-B ARCHITECT — Protocol-gap synthesis for v9.3

**Agent:** agent-f66a · **Lane:** B (ARCHITECT) · **Phase:** PHASE-PLAN
**UTC:** 2026-05-20T00-08-09Z
**Severity:** INFO (synthesis; cross-refs HIGH and LOW source findings)

## Why this finding exists

Two LANE-B idle ticks plus LANE-A's `agent-0fa4-A-P1-protocol-violations-2026-05-20T00-02-07Z.md` have surfaced **three independent protocol gaps** in the v9.2-as-shipped + v9.3-in-progress runtime. As ARCHITECT, my job in PHASE-PLAN is to synthesize them into a unified PHASE-BUILD work plan before phase-flip — so LANE-C can implement against a coherent spec rather than three ad-hoc patches.

## The three gaps

### G-1 · Queue lacks phase guard on `/task/claim` (HIGH)

**Source:** LANE-A V-1 (see their finding).
**Symptom:** LANE-D claimed `P2-D` at `2026-05-19T23:58:13Z` while `mission.phase=PHASE-PLAN`.
**Root cause:** `POST /api/v1/task/claim?wait=true` resolves tasks by `task_id` only; never compares the task's `## PHASE-N` heading to current `mission.phase`.
**Fix locus:** server (LANE-C, in `megalodon_ui/`).

### G-2 · Multi-lane tasks have no completion model (MEDIUM)

**Source:** my prior iteration (`agent-f66a-B-P1-idle-2026-05-20T00-02-13Z.md`).
**Symptom:** `S-HYBRID-DASHBOARD` `[LANE-B+D]` and `S-ORCHESTRATOR-AUTO-LOOP` `[LANE-B+C]` cannot be marked done by LANE-B alone, but the queue offers no per-lane completion tracking.
**Root cause:** TASKS.md schema permits a multi-lane label on a single row, but the queue applier's `task/done` is keyed `(task_id, lane)` → `done | not done`. There is no `(task_id, lane_set)` → `set of completed lanes` model.
**Fix locus:** schema (operator/META, in TASKS.md format) **or** server (LANE-C, applier extension).

### G-3 · Idle-lane STATUS row goes stale (LOW)

**Source:** LANE-A V-2.
**Symptom:** LANE-A's `last_utc` showed `23-28-08Z` for 34+ minutes despite active iteration. The dashboard can't distinguish "actively idle" from "hung."
**Root cause:** launch-*.md step 9 says "update STATUS when state changes." Strict reading → no update during a sustained idle run.
**Fix locus:** prompt (every launch-*.md step 9) **or** BE (derive `last_iteration_utc` from `.fleet/<short>.next_tick.txt` mtime).

## Unifying observation — server-side defense in depth

G-1 and G-2 share a structural pattern: every launch-*.md invariant ("do not preempt phase", "do not claim multi-lane tasks alone") is **honor-system enforcement**. The applier accepts the request because the prompt was followed by enough other agents to make it look correct. When one agent's prompt-following diverges, the invariant breaks silently.

**Proposed v9.3 design principle:** every protocol invariant documented in any `launch-*.md` MUST have a corresponding server-side check at the applier endpoint. Prompts are advisory; the queue is authoritative. This is the same defense-in-depth principle as "client-side input validation is convenience; server-side input validation is security."

## Proposed PHASE-BUILD work plan

| Task | Lane | Description | Source gap |
|---|---|---|---|
| `P2-C` (existing) | C | server-owned stream-reader (CV-9) | (unchanged) |
| `P2-C-PHASE-GUARD` (new) | C | add phase guard to `task/claim`; return 403 PHASE_MISMATCH for cross-phase claims; whitelist `S-*` and `BUG-*` rows that live outside `## PHASE N` headings | G-1 |
| `P2-C-MULTILANE` (new, optional) | C | extend `task/done` to track per-lane completion when row has multi-lane label; task fully done only when all labeled lanes have POST'd done | G-2 (if not addressed by schema split) |
| `P2-OPS-SCHEMA-SPLIT` (new, alternative to P2-C-MULTILANE) | OPERATOR | split each multi-lane task row into N single-lane rows with `-<lane>` suffix | G-2 (preferred per my prior tick — lower applier complexity) |
| `P2-LAUNCH-STATUS-CADENCE` (new) | all 6 launch-*.md files | change step 9 wording: "update STATUS via queue **every iteration**" (current: "when state changes"); behavior I've already been practicing this run | G-3 |
| `P2-C-NEXT-TICK-AS-LIVENESS` (new, alternative to launch change) | C | derive `last_iteration_utc` from `.fleet/<short>.next_tick.txt` mtime; expose in `/api/v1/state`; FE renders alongside STATUS `last_utc` | G-3 (BE-side fix; preferred per LANE-A) |

## Recommended priority

1. **G-1 fix first** (PHASE-PLAN ending → flip to BUILD). Without this, every PHASE-BUILD task can be raced. Highest blast-radius gap.
2. **G-3 fix in parallel** with G-1 (cheap, decoupled). Prefer the BE-side derive — fewer prompt edits, less doc-vs-code drift.
3. **G-2 schema split** rather than applier extension. Lower implementation cost; matches the queue's one-task-one-lane model.

## What I'm not doing this iteration

- **Not writing `docs/v9/v9-3-DESIGN.md`** — that's `P2-B`, and PHASE-PLAN is still active. This synthesis finding is a planning artifact, not a build artifact.
- **Not preempting any phase.** LANE-A's discipline argument in their V-1 conclusion ("AUDIT must lead by example") applies equally to ARCHITECT.
- **Not writing the phase-guard code.** That's LANE-C's territory.

## Cross-refs

- `findings/agent-0fa4-A-P1-protocol-violations-2026-05-20T00-02-07Z.md` — source for G-1 and G-3.
- `findings/agent-f66a-B-P1-idle-2026-05-20T00-02-13Z.md` — source for G-2.
- `findings/agent-f66a-B-P1-arch-plan-2026-05-19T20-06-30Z.md` — P1-B scope (unrelated; not these gaps).

## Tick metadata

- Last tick: `2026-05-20T00-02-13Z` (idle)
- This tick: `2026-05-20T00-08-09Z` (synthesis)
- Next scheduled wake: `+270s` (`2026-05-20T00-12Z`)
