# Tasks — v94-ui-dogfood

Format: `[ ] [LANE-X] <task-id> — <description>`
States: `[ ]` open · `[claimed: <agent-id> @ <UTC>]` · `[done: <agent-id> @ <UTC>]`

Mission: harden the v9.4 dashboard + clear the v9.x backlog + scope v10-refactor
groundwork, **while** each lane mechanically proves its dashboard surface reflects
disk truth. See MISSION.md for the dual charter.

---

## Surface-ownership matrix (visibility charter)

Each lane owns ONE primary dashboard surface and runs a mechanical disk-vs-UI
assertion (a script, not a judgment) for it every tick; files a finding on divergence.
Data fidelity only — interaction fidelity is the harness/Playwright/operator's job.

| Lane | Model | Surface | Component / endpoint | Disk source of truth |
|---|---|---|---|---|
| A AUDIT | opus | Findings / activity wall + stale-lane badge | `activity_wall.js`, `stale_modal.js`, `/api/v1/lanes/stale` | `findings/*.md`, lane mtimes |
| B ARCHITECT | opus | Plans / mission view | mission+plan view | `MISSION.md`, plan docs |
| C BACKEND | sonnet | Approval rules + approve→respawn→suppression | `approval_rules.js`, `spawn.py` | `.fleet/approval-rules.json` |
| D FRONTEND | sonnet | Active-agents grid + terminal panes | `grid.js`, `terminal_pane.js` | `.fleet/lane-state`, pane bytes |
| E TEST | sonnet | Tasks + inject/restart-loop round-trip | `tasks.js`, `lane_detail.js` | this `TASKS.md`, inject log |
| F META | haiku | Communication / signals | `signals.js` | `signals/*`, `claims/*` |

---

## PHASE 1 — PLAN

- [ ] [LANE-A] A-P1 — Write `findings/`-backed disk-vs-UI assertion script for the activity wall + stale badge; record baseline (current findings count, stale state) before any mutation.
- [ ] [LANE-B] B-P1 — Capture the v10-refactor groundwork scope as a plan doc; define what "performance established" means as the v10 entry gate.
- [ ] [LANE-C] C-P1 — Inventory the v9.x backlog (CR-4, WR-3, CV-8 + the 9 run-2 residuals); assign each item a lane and a primary-work task id below.
- [ ] [LANE-D] D-P1 — Write the grid + terminal-pane disk-vs-UI assertion (lane state on disk == grid cells; pane byte stream == terminal_pane render).
- [ ] [LANE-E] E-P1 — Write the tasks-surface assertion (this TASKS.md == tasks.js render) and a round-trip probe for inject/restart.
- [ ] [LANE-F] F-P1 — Write the signals/communication assertion (`signals/` + `claims/` on disk == signals.js render).

## PHASE 2 — BUILD

Primary-work pool (claim into here; one lane per item):
- [ ] [LANE-?] BUILD-FM1 — Harden failure-mode #1 (can't-see-agents): terminal pane time-to-visibility < 60s under load; add error/empty state.
- [ ] [LANE-?] BUILD-FM2 — Harden failure-mode #2 (approval friction): approve-and-remember suppression path; target operator clicks/hr < 10.
- [ ] [LANE-?] BUILD-FM3 — Harden failure-mode #3 (buried/blocked lane): dashboard surfaces blocked state < 60s (the 195-min invisible-block class).
- [ ] [LANE-?] BUILD-FM4 — Harden failure-mode #4 (stale-lane false/miss): stale badge correctness on the right lane; clears on recovery.
- [ ] [LANE-?] BUILD-CR4 — Backlog CR-4 (carry the spec text into the task when claimed).
- [ ] [LANE-?] BUILD-WR3 — Backlog WR-3.
- [ ] [LANE-?] BUILD-CV8 — Backlog CV-8.
- [ ] [LANE-?] BUILD-RES — Run-2 residuals (9 items; split as claimed).

Visibility charter (continuous, every tick — not a one-shot task):
- [ ] [LANE-A] A-VIS — Run A-P1 assertion every tick; file finding on divergence.
- [ ] [LANE-B] B-VIS — Run plan/mission assertion every tick; file finding on divergence.
- [ ] [LANE-C] C-VIS — Run approval-rules assertion every tick; file finding on divergence.
- [ ] [LANE-D] D-VIS — Run grid+pane assertion every tick; file finding on divergence.
- [ ] [LANE-E] E-VIS — Run tasks assertion every tick; file finding on divergence.
- [ ] [LANE-F] F-VIS — Run signals assertion every tick; file finding on divergence.

## PHASE 3 — VERIFY

- [ ] [LANE-E] V-HARNESS — Stimulus harness PASS: `run_stale_lane_check` + `run_signal_fidelity_check` against the live server.
- [ ] [LANE-E] V-E2E — `ui/tests/e2e/visibility.spec.ts` green (snap-back, tab-highlight, activity-wall fidelity, empty-state).
- [ ] [LANE-A] V-NODIV — Zero unresolved divergence findings open at close.
- [ ] [LANE-F] V-SOAK — ≥2h soak alongside the harness shows no latency drift in signal/activity propagation.

## OPERATOR-ACCEPTANCE TASKS

- [ ] [OP] OP-HEARTBEAT — Confirm all 6 lanes logged ≥2 STATUS heartbeats within 10 min of spawn (preflight check 4, live).
- [ ] [OP] OP-CLAIMS — Confirm the 4 original T4.3 qualitative claims do not recur during soak (corroboration, not the gate).
- [ ] [OP] OP-CLOSE — Write terminal `.mission-events` token (COMPLETE | DEGRADED-CLOSE) then `archive_run.sh`.

## CROSS-LANE / SECONDARY TASK POOL

- [ ] [ANY] X-DOCS — As surfaces change, keep `docs/v9/api-contract.md` and the dashboard section of README in sync (T5.1 finalizes after close).
- [ ] [ANY] X-V10 — Drop v10-groundwork notes into `findings/` tagged `v10:` for B to fold into the plan doc.
