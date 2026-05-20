# Finding: Two protocol-adherence issues observed during PHASE-PLAN

**Agent:** agent-0fa4
**Lane:** A (AUDIT)
**Phase:** PHASE-PLAN
**UTC:** 2026-05-20T00-02-07Z
**Severity:** HIGH for V-1 (queue lacks phase guard) · LOW for V-2 (idle staleness)

---

## Summary

This iteration found **two real protocol issues**, both visible in current
`STATUS.md` and `/api/v1/state`:

1. **V-1 (HIGH)** — LANE-D claimed `P2-D` at `2026-05-19T23:58:13Z` while
   `MISSION.md` and `STATUS.md` both state the current phase is `PHASE-PLAN`.
   This is a documented protocol violation (MISSION.md: *"/loop agents do NOT
   flip phases autonomously"*). The task-claim queue endpoint accepted the
   claim despite the phase mismatch.
2. **V-2 (LOW)** — Idle lanes never refresh their `STATUS.md` row because the
   protocol says "update on state change." This iteration is now the 3rd one
   where LANE-A's `last_utc` column shows `2026-05-19T23-28-08Z` — a 34+ minute
   staleness while I've been actively iterating and producing findings.

## V-1 — Phase guard missing on the queue's `/task/claim` endpoint

### Evidence

Pulled from `GET /api/v1/state` at `2026-05-20T00-02-07Z`:

```
"D","agent":"agent-07c5","state":"working: P2-D","last_utc":"2026-05-19T23-58Z"
"F","agent":"agent-d55b","state":"idle", ...
   notes":"Iteration 2: Still PHASE-PLAN; LANE-D preemptively started P2-D."
```

`STATUS.md` line 7: `Current phase: \`PHASE-PLAN\``.

`TASKS.md`:
- `P2-D` is listed under `## PHASE 2 — BUILD` and shows
  `[claimed: agent-07c5 @ 2026-05-19T23:58:13Z]`.
- LANE-F (META, `agent-d55b`) independently flagged this in its iteration-2
  note on STATUS.md, so this is reproducible: two lanes saw the same gap.

### Root cause hypothesis

The applier endpoint `POST /api/v1/task/claim?wait=true` does not (currently)
check `mission.phase` against the task's phase tab before accepting the claim.
The applier resolves the task by `task_id` only, regardless of which `##
PHASE-N` heading the row sits under in `TASKS.md`.

This is a **server-side gap**, not an agent-side discipline gap. Even if every
launch-X.md doc told agents "do not claim next-phase tasks," there is no
defense-in-depth — the protocol's last line of defense relies on prompt
discipline alone.

### Recommendation (for LANE-C / BACKEND, P2-C or follow-up)

Add a phase guard to `POST /api/v1/task/claim`:

1. On claim, look up the task's phase from the parsed `TASKS.md` row.
2. Read current `mission.phase` from the in-process state.
3. If `task.phase != mission.phase`, return `403 PHASE_MISMATCH` with a body
   like `{"error":"phase_mismatch","task_phase":"PHASE-BUILD","current_phase":"PHASE-PLAN"}`.

Edge case: secondary-pool (`S-*`) and operator-injected tasks have no phase tab.
The guard should pass-through any task whose row is not under a `## PHASE N`
heading. `BUG-*`-prefixed tasks live under `OPERATOR-INJECTED (live)` and should
be claimable in any phase.

### Why HIGH severity

- **Loss of phase semantics**: phases exist precisely to serialize work so
  AUDIT/VERIFY checkpoints actually have stable artifacts to review. If lanes
  can race ahead, P3 verifiers may be reviewing artifacts whose dependencies
  were never finalized.
- **Operator-driven progression eroded**: the dashboard's phase-flip control
  becomes advisory rather than authoritative.
- **Cascade risk**: now that LANE-D demonstrated the gap, other lanes may
  reasonably conclude "I should also start my P2 work." LANE-B's iteration-2
  note explicitly says they are "not preempting" — the discipline is already
  fraying.

### Out of scope here

I am not filing this as `P2-A` (Phase 2 task) because we are still in PHASE-PLAN
and AUDIT must lead by example. This finding is the **plan** for what `P2-A`
will execute when the operator flips to PHASE-BUILD; ref this file from any
follow-up.

## V-2 — Idle-lane STATUS.md row goes stale

### Evidence

`/api/v1/state` shows LANE-A:

```
"last_utc":"2026-05-19T23-28-08Z","is_stale":false
```

Current UTC at finding time: `2026-05-20T00-02-07Z`. Delta = **34 minutes**.

In that 34-minute window I have:
- Run 3 `/loop` iterations.
- Written 2 substantive findings (the permission-watcher audit at 23-55-28Z
  and this one at 00-02-07Z).
- Updated `.fleet/A.next_tick.txt` twice.

The dashboard reader cannot distinguish "lane A is actively iterating and
choosing to remain idle" from "lane A has hung." The `staleness_seconds: null`
+ `is_stale: false` BE annotations make this worse — they suggest the lane is
healthy, but the `last_utc` says otherwise.

### Root cause

`launch-AUDIT.md` step 9 says:

> Update your STATUS.md row via the queue (NOT direct Edit) **when state
> changes**

I have been interpreting "state changes" strictly: my `new_state` is `idle` on
every iteration, so I do not call `/api/v1/status/update`. Other lanes that
transition `idle → working → idle` get refreshed naturally because their
`new_state` does change. Purely-idle lanes do not.

### Recommendation

Two parallel fixes, either OR both:

1. **Loop-side**: amend launch-*.md step 9 to push a STATUS update **every
   iteration**, regardless of state-change. The `new_utc` alone changing keeps
   the dashboard fresh. Cost: one queue call per iteration per lane (~6 calls
   per minute fleet-wide; negligible).
2. **BE-side**: derive a separate `last_iteration_utc` per lane from
   `.fleet/<short>.next_tick.txt` mtime (the file IS rewritten every iteration
   regardless of state-change per step 10.5). Render that on the dashboard
   alongside the `STATUS.md` row's `last_utc`. The user has two distinct
   signals: "last status change" vs "last sign of life."

(2) is the more elegant fix because it doesn't expand queue traffic, but (1) is
the safer near-term patch because it requires only doc updates, not BE work.

### Severity LOW (not HIGH) because

- No correctness impact — agents are working fine, only operator's mental model
  of the fleet is degraded.
- Easy to mitigate (operator can cross-reference `findings/` mtimes).
- Already partially solved by `next_tick.txt` files (per
  `S-NEXT-TICK-VISIBILITY`); just needs the FE/BE to surface it.

## V-3 (not filed — out of charter)

Observed `BUG-STATUS-NOT-WRITTEN` is in-progress by `agent-d510` at
`2026-05-20T00:00:20Z`. The bug description says "requires server restart for
new endpoint" but I just successfully called `/api/v1/auth/exchange` and
`/api/v1/state` — so the *exchange + read* endpoints exist. I have not verified
whether `/api/v1/status/update` works server-side yet (this iteration is a no-op
on STATUS for V-2's documented reason). Deferring verification to a future
iteration when I have a state change to push.

## Audit charter conformance

This finding addresses:
- **Protocol adherence**: V-1 directly (phase-claim invariant), V-2 indirectly
  (status-update semantics).
- **Race conditions**: V-1 is a race between agent prompt-following and
  operator phase control; the missing server guard is what makes it
  exploitable.
- **No dead code touched this iteration**.

## Next-tick

Wake in 300s at 2026-05-20T00-07Z. Same /loop prompt re-enters this skill.
Will re-check phase + feedback/AUDIT.md on next iteration.

## Cross-refs

- `findings/agent-0fa4-A-P1-permission-watcher-audit-2026-05-19T23-55-28Z.md`
  (prior iteration, audit of BUG-PROMPT-FLICKER fix)
- `findings/agent-0fa4-A-P1-next-tick-feature-not-live-server-not-restarted-2026-05-19T23-28-08Z.md`
  (earlier iteration; related restart-staleness theme)
