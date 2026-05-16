# Megalodon Self-Improvement Mission — Design Spec

**Mission ID:** `2026-05-16--megalodon-self-improvement`
**Brainstormed:** 2026-05-16
**Status:** approved by operator; ready for deployment

---

## 1. Goal

Use the v7 Megalodon protocol — with a new phase-gated workflow extension — to
(a) audit v7 itself against multi-agent coordination best practices, and
(b) design + build a working Tier 3 orchestrator-console UI.

**Tier 3 UI:** read-only dashboard *plus* interactive orchestrator actions
(inject CHALLENGE, reclaim stale rows, post SIGNALs, flip Mission status).

**Hands-off:** the operator deploys 6 worker sessions and walks away.
The run produces artifacts in 3–7 hours.

## 2. Workflow shape

Four explicit phases, gated by automatic transitions:

| Phase | Stance |
|---|---|
| `PHASE-PLAN` | Each agent produces a plan for their area. Pass-1 fresh eyes — do NOT read other lanes' plans. |
| `PHASE-CHALLENGE` | Each agent adversarially reviews one peer's plan (directed cycle); each plan author publishes a Plan v2 incorporating or rebutting feedback. |
| `PHASE-BUILD` | Agents claim implementation chunks per consolidated plans. BACKEND publishes stub API early so FRONTEND parallelizes. |
| `PHASE-VERIFY` | Each agent verifies a different peer's build output. **No self-verification.** |

Phase transitions are automatic via distributed atomic-flip (see §4).

## 3. Lane composition (6 agents)

| Code | Lane | Owns |
|---|---|---|
| A | PROTOCOL-AUDIT | v7→v8 changeset; literature comparison |
| B | UI-ARCHITECT | data model, API surface, spec + ADRs |
| C | UI-BACKEND | server (Python/FastAPI assumed), file-watch, atomic-write strategy |
| D | UI-FRONTEND | pages, real-time updates (SSE/poll/WS), SIGNAL timeline |
| E | UI-TEST | Playwright E2E + integration tests against fixture mission dirs |
| F | META-OBSERVER | run-meta-analysis; 4-phase-pattern viability report |

**CHALLENGE pairings (directed cycle):** A→F, F→E, E→D, D→C, C→B, B→A
**VERIFY pairings (rotated):** A→B, B→E, E→C, C→D, D→A, F→ALL

The two pairings deliberately differ — two independent sets of eyes on each lane's output, separated in time.

## 4. Cadence + automatic phase transitions (v8 candidate)

**Cadence:** 3 min uniform; BUILD lanes may self-extend to 10 min for deep coding sessions, declared in STATUS note.

**Source of truth: `.mission-events`** (append-only log; last line wins).

**New RULE 11 (atomic phase-flip):** at tick start, after re-arm + heartbeat, every worker reads `.mission-events`, checks current-phase completion (all `P<N>-*` tasks done, no lane actively mid-task), and if true tries `mkdir .phase-flip-locks/<from>-to-<to>`. Winner appends the flip event, updates README.md Mission status, and continues.

**Watchdog (new TIER 2 default):** if no phase flip occurs for 30 min despite some tasks being done, the next worker injects a `[STALL-INVESTIGATION]` task and SIGNALs all lanes.

**BLOCKED vetoes auto-flip:** any lane in `BLOCKED` state freezes phase transitions. Requires human intervention.

**Halt:** when phase = `COMPLETE`, workers tick 3 more times to confirm, then stop calling `ScheduleWakeup`.

## 5. Tasks

- **24 primary tasks** organized into 4 phase-batches (6 per phase: P1-* / P2-* / P3-* / P4-*). Plus 6 plan-v2 reconciliation tasks (P2.5-*) inside PHASE-CHALLENGE.
- **~18 secondary tasks** in a pool, claimable by drained lanes — split across audit-extensions (raft/OTP/blackboard literature), UI-extensions (mobile, themes, exports), meta-extensions (subagent dispatch patterns, comparison to V-model/OODA), and docs/infrastructure.

Full enumeration in `TASKS.md`.

## 6. Deployment

```bash
# In 6 separate terminal windows, from this project directory:
claude "Join the Megalodon mission as a worker. Read README.md, MISSION.md, and TASKS.md. Self-claim a lane in STATUS.md per RULE 2. Then /loop 3m"
```

Each session self-claims an unclaimed lane row, generates an agent-ID, and begins ticking. `PHASE-PLAN` is initial state. Auto-flip cascades through `CHALLENGE → BUILD → VERIFY → DRAINING → COMPLETE`.

## 7. Expected artifacts on return

```
findings/                            # 30+ primary findings + capstones
docs/v8-changeset.md                 # AUDIT lane's proposed README.md edits
ui/                                  # working orchestrator console (FE + BE)
ui/tests/                            # Playwright + integration suite
.mission-events                      # full phase timeline (audit trail)
HISTORY.md                           # appended throughout the run
findings/*-F-FINAL-RUN-CAPSTONE.md   # META's retrospective on the 4-phase pattern
```

Workers cannot `git commit` — the `.claude/settings.json` deny list blocks it. Operator reviews, decides what is keep-worthy, and commits manually after the run.

## 8. Hard constraints

- Workers cannot `git`, `curl`, `wget`, `npm`, `pip`, `brew`, `sudo`, `chmod`, `chown`, `ssh`, `scp` (existing `.claude/settings.json` deny list).
- `.archive/` is READ-ONLY (existing deny).
- No self-verification in PHASE-VERIFY (encoded in pairing matrix).
- Source project IS this project — no additional Read-allow paths needed.

## 9. Failure modes & manual intervention

| Symptom | Resolution |
|---|---|
| Lane goes BLOCKED | Auto-flip freezes; operator investigates STATUS note, resolves, lane clears BLOCKED |
| Phase stalls 30+ min | Watchdog injects STALL-INVESTIGATION task; whichever lane claims it diagnoses |
| Worker dies mid-task | Stale-reclaim per RULE 6; finding preserved via scratch file if it exists |
| Auto-flip race | mkdir-atomic guarantees exactly one flipper; losers proceed normally |

## 10. v8 candidates produced by this mission

If the run succeeds and META's FINAL-RUN-CAPSTONE confirms each, AUDIT's `docs/v8-changeset.md` should propose:

1. Phase-gated mission workflow (PHASE-PLAN / CHALLENGE / BUILD / VERIFY) as a TIER-1 pattern for implementation-class missions.
2. `.mission-events` append-only log as a new protocol file.
3. RULE 11 (distributed atomic phase-flip via mkdir).
4. TIER-2 watchdog rule (30-min stall threshold injects STALL-INVESTIGATION).
5. BLOCKED-vetoes-auto-flip semantics.
6. Per-phase cadence flexibility (BUILD lanes may self-extend).

If any of those don't work as designed, META documents the failure mode and AUDIT proposes a modified version or rejection.
