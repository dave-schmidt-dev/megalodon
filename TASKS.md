# Tasks

Format: `[ ] [LANE-X] <task-id> — <description>`

States: `[ ]` open · `[claimed: <agent-id> @ <UTC>]` claimed · `[done: <agent-id> @ <UTC>]` complete

Claim via `mkdir claims/<task-id>` per RULE 2; TASKS.md is informational.

Task IDs encode phase and lane per MISSION.md task-assignment matrix.

---

## PHASE 1 — PLAN (claim by lane; Pass-1 fresh eyes; do NOT read other lanes' plans)

- [ ] [LANE-A] `P1-A` — AUDIT plan: methodology, literature checklist (raft, blackboard, OTP, CRDT, Hewitt actors, CSP), v8 changeset skeleton. Output: `findings/<agent-id>-A-P1-audit-plan-<UTC>.md`
- [ ] [LANE-B] `P1-B` — ARCHITECT plan: data model for protocol state, API surface sketch, page structure, wireframes, tech-stack recommendation (Python/FastAPI assumed; recommend otherwise if warranted). Output: `findings/<agent-id>-B-P1-arch-plan-<UTC>.md`
- [ ] [LANE-C] `P1-C` — BACKEND plan: server architecture (file-watch vs poll vs hybrid), atomic-write strategy for orchestrator actions, API contract draft. Output: `findings/<agent-id>-C-P1-backend-plan-<UTC>.md`
- [ ] [LANE-D] `P1-D` — FRONTEND plan: page-by-page interaction design, real-time update strategy (SSE/poll/WS), SIGNAL-timeline visual design. Output: `findings/<agent-id>-D-P1-frontend-plan-<UTC>.md`
- [ ] [LANE-E] `P1-E` — TEST plan: test strategy, Playwright coverage matrix, fixture mission-dir setup. Output: `findings/<agent-id>-E-P1-test-plan-<UTC>.md`
- [ ] [LANE-F] `P1-F` — META plan: observation framework — signals indicating phase-gating works/fails, data-collection plan, capstone scope. Output: `findings/<agent-id>-F-P1-meta-plan-<UTC>.md`

## PHASE 2 — CHALLENGE (directed cycle A→F→E→D→C→B→A)

- [ ] [LANE-A] `P2-A→F` — AUDIT challenges META plan. Find blind spots in observation methodology. Output: `findings/<agent-id>-A-P2-challenge-of-meta-<UTC>.md`
- [ ] [LANE-F] `P2-F→E` — META challenges TEST plan. Phase-gating itself testable? Output: `findings/<agent-id>-F-P2-challenge-of-test-<UTC>.md`
- [ ] [LANE-E] `P2-E→D` — TEST challenges FRONTEND plan. Testability holes, race conditions. Output: `findings/<agent-id>-E-P2-challenge-of-frontend-<UTC>.md`
- [ ] [LANE-D] `P2-D→C` — FRONTEND challenges BACKEND plan. FE-unfriendly API shapes, missing endpoints. Output: `findings/<agent-id>-D-P2-challenge-of-backend-<UTC>.md`
- [ ] [LANE-C] `P2-C→B` — BACKEND challenges ARCHITECT plan. Spec gaps, concurrency hazards. Output: `findings/<agent-id>-C-P2-challenge-of-architect-<UTC>.md`
- [ ] [LANE-B] `P2-B→A` — ARCHITECT challenges AUDIT plan. Over-reach beyond v7 evidence base. Output: `findings/<agent-id>-B-P2-challenge-of-audit-<UTC>.md`

### PHASE 2.5 — Plan-v2 reconciliation (each plan author incorporates or rebuts their challenge)

- [ ] [LANE-A] `P2.5-A` — AUDIT writes plan-v2 incorporating ARCHITECT's challenge feedback. Output: `findings/<agent-id>-A-P2.5-audit-plan-v2-<UTC>.md`
- [ ] [LANE-B] `P2.5-B` — ARCHITECT writes plan-v2 incorporating BACKEND's challenge feedback. Output: `findings/<agent-id>-B-P2.5-arch-plan-v2-<UTC>.md`
- [ ] [LANE-C] `P2.5-C` — BACKEND writes plan-v2 incorporating FRONTEND's challenge feedback. Output: `findings/<agent-id>-C-P2.5-backend-plan-v2-<UTC>.md`
- [ ] [LANE-D] `P2.5-D` — FRONTEND writes plan-v2 incorporating TEST's challenge feedback. Output: `findings/<agent-id>-D-P2.5-frontend-plan-v2-<UTC>.md`
- [ ] [LANE-E] `P2.5-E` — TEST writes plan-v2 incorporating META's challenge feedback. Output: `findings/<agent-id>-E-P2.5-test-plan-v2-<UTC>.md`
- [ ] [LANE-F] `P2.5-F` — META writes plan-v2 incorporating AUDIT's challenge feedback. Output: `findings/<agent-id>-F-P2.5-meta-plan-v2-<UTC>.md`

## PHASE 3 — BUILD (claim by lane; implement per plan-v2)

- [ ] [LANE-A] `P3-A` — AUDIT writes `docs/v8-changeset.md` — concrete proposed edits to README.md / MISSION.md as a diff
- [ ] [LANE-B] `P3-B` — ARCHITECT writes final `ui/SPEC.md` + Architecture Decision Records in `ui/adrs/`
- [ ] [LANE-C] `P3-C` — BACKEND builds server. **Publish stub API in tick 1-2** so FRONTEND can integrate in parallel. Output: `ui/server.py` (or chosen stack) + `ui/api-contract.md`
- [ ] [LANE-D] `P3-D` — FRONTEND builds the UI integrating against BACKEND's stub then real API. Output: `ui/static/` + integration code
- [ ] [LANE-E] `P3-E` — TEST builds Playwright E2E suite + integration tests against fixture mission dirs. Output: `ui/tests/`
- [ ] [LANE-F] `P3-F` — META writes mid-mission report: how the 4-phase pattern is going; tick-by-tick emergence. Output: `findings/<agent-id>-F-P3-mid-mission-meta-<UTC>.md`

## PHASE 4 — VERIFY (rotated pairings; no self-verification)

- [ ] [LANE-A] `P4-A→B` — AUDIT verifies ARCHITECT spec for protocol-fidelity. Does UI honor v7 (and proposed v8) semantics? Output: `findings/<agent-id>-A-P4-verify-of-architect-<UTC>.md`
- [ ] [LANE-B] `P4-B→E` — ARCHITECT verifies TEST coverage maps to spec acceptance criteria. Output: `findings/<agent-id>-B-P4-verify-of-test-<UTC>.md`
- [ ] [LANE-E] `P4-E→C` — TEST verifies BACKEND code against test specifications (review BE code). Output: `findings/<agent-id>-E-P4-verify-of-backend-<UTC>.md`
- [ ] [LANE-C] `P4-C→D` — BACKEND verifies FRONTEND consumes API correctly under failure modes. Output: `findings/<agent-id>-C-P4-verify-of-frontend-<UTC>.md`
- [ ] [LANE-D] `P4-D→A` — FRONTEND verifies AUDIT changeset accurately reflects observed protocol behavior. Output: `findings/<agent-id>-D-P4-verify-of-audit-<UTC>.md`
- [ ] [LANE-F] `P4-F→ALL` — META verifies the whole run. Did phases gate cleanly? Were CHALLENGE pairings independent? Final 4-phase-viability capstone. Output: `findings/<agent-id>-F-FINAL-RUN-CAPSTONE-<UTC>.md`

---

## CHALLENGE TASKS

(workers may self-assign CHALLENGEs on 3+ lane converged findings per TIER 2; orchestrator may inject)

## CROSS-LANE / SECONDARY TASK POOL

(claimable by any drained lane; tag `[CROSS]`; secondary tasks are optional polish/depth — don't claim before your primary lane work is done for the current phase)

### Audit-extensions

- [ ] [CROSS] `S-1` — Compare v7 to Raft consensus semantics. What does Raft solve that v7 doesn't? Output: `findings/<agent-id>-CROSS-S1-raft-comparison-<UTC>.md`
- [ ] [CROSS] `S-2` — Compare v7 to Erlang OTP supervision trees. Apply supervisor/worker thinking to lane lifecycle. Output: `findings/<agent-id>-CROSS-S2-otp-comparison-<UTC>.md`
- [ ] [CROSS] `S-3` — Compare v7 to Hewitt actor model + CSP. Message-passing primitives we should adopt. Output: `findings/<agent-id>-CROSS-S3-actor-csp-comparison-<UTC>.md`
- [ ] [CROSS] `S-4` — Parse `.archive/2026-05-16T00-17Z--okx_case-rebuttal/findings/`. Extract failure modes and emergent patterns not yet codified. Output: `findings/<agent-id>-CROSS-S4-okx-archive-mining-<UTC>.md`
- [ ] [CROSS] `S-5` — Compare v7's `mkdir`-atomic claim to Chubby / Zookeeper ephemeral locks. Output: `findings/<agent-id>-CROSS-S5-distributed-lock-comparison-<UTC>.md`

### UI-extensions

- [ ] [CROSS] `S-6` — Mobile-responsive layout spec. Output: `ui/adrs/S6-mobile-spec.md`
- [ ] [CROSS] `S-7` — Dark/light theme toggle (dark-default). Output: `ui/static/themes/`
- [ ] [CROSS] `S-8` — Export-run-as-static-HTML for archival. Output: `ui/tools/export-run.py`
- [ ] [CROSS] `S-9` — Multi-mission selector (foundation for cross-mission view). Output: `ui/adrs/S9-multi-mission-spec.md`
- [ ] [CROSS] `S-10` — Keyboard shortcuts for orchestrator actions. Output: `ui/static/shortcuts.js` + docs

### Meta-extensions

- [ ] [CROSS] `S-11` — Track subagent dispatch patterns across lanes (which phases dispatch more?). Output: `findings/<agent-id>-CROSS-S11-subagent-patterns-<UTC>.md`
- [ ] [CROSS] `S-12` — Compare 4-phase pattern to V-model verification, Boyd's OODA loop, agile sprint structures. Output: `findings/<agent-id>-CROSS-S12-4-phase-vs-models-<UTC>.md`
- [ ] [CROSS] `S-13` — Track CHALLENGE→consensus convergence rate — DELTA-refinement vs overturn vs no-change. Output: `findings/<agent-id>-CROSS-S13-challenge-outcomes-<UTC>.md`

### Docs & infrastructure

- [ ] [CROSS] `S-14` — Update README.md to reflect v8 changes IF AUDIT's changeset is accepted (do NOT commit; produce a `docs/README.md-v8-preview.md` for operator review). Output: `docs/README.md-v8-preview.md`
- [ ] [CROSS] `S-15` — Write UI deployment instructions (how to start server, point at mission dir). Output: `ui/README.md`
- [ ] [CROSS] `S-16` — Propose updates to `.claude/settings.example.json` for any new bash commands UI needs at runtime. Output: `findings/<agent-id>-CROSS-S16-settings-deltas-<UTC>.md` (DO NOT directly edit settings.example.json — propose only)
- [ ] [CROSS] `S-17` — Create fixture mission directories (small / medium / large) for testing. Output: `ui/tests/fixtures/`
- [ ] [CROSS] `S-18` — Devil's-advocate CHALLENGE on the consensus plan as a whole (claimable in PHASE-CHALLENGE+1 by any lane). Output: `findings/<agent-id>-CROSS-S18-meta-challenge-<UTC>.md`
