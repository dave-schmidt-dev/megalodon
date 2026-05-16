# Mission

**Mission ID:** `2026-05-16--megalodon-self-improvement`
**Started:** *(set on first PHASE-FLIP event)*
**Status:** **ACTIVE** — auto-managed via `.mission-events` (see §"Phase mechanics" below)
**Deliverable date:** ~3–7 hours after worker start

---

## Source project

- **Path:** This Megalodon project itself (self-referential mission)
- **Description:** Audit and improve the Megalodon v7 protocol, AND build a Tier 3 orchestrator-console UI (read-only dashboard + interactive orchestrator actions). Workers reference this same project's `.archive/` for past-run lessons, but `.archive/` is READ-ONLY.

## Scope

**In scope:**
- Audit v7 protocol vs. multi-agent coordination literature (raft, blackboard systems, OTP supervision, CRDT, Hewitt actors). Produce concrete `docs/v8-changeset.md` (proposed README.md edits).
- Design and build a working Tier 3 orchestrator-console UI: live STATUS view, task queue, SIGNAL/ACK/DISSENT timeline, finding explorer with severity/lane filtering, orchestrator actions (inject CHALLENGE, reclaim stale rows, post SIGNALs, flip Mission status).
- Meta-deliverable: META lane's report on whether the 4-phase auto-flip pattern itself is worth codifying in v8.

**Out of scope:**
- Multi-mission UI (single-mission MVP only).
- Auth / multi-user (localhost-only; trust model = whoever can reach the port).
- DB persistence (filesystem is source of truth; UI is a renderer/controller).
- Major refactor of v7 (changeset only — no rewrites without lane consensus).

## Lanes (this mission overrides README.md defaults)

| Code | Lane | Stance | Primary output |
|---|---|---|---|
| A | **PROTOCOL-AUDIT** | Compare v7 to coordination literature; identify defects, gaps | `docs/v8-changeset.md` (concrete README.md edits as a diff) |
| B | **UI-ARCHITECT** | Data model, API surface, page structure, wireframes, tech-stack decision | `ui/SPEC.md` + ADRs |
| C | **UI-BACKEND** | Server impl: file-watch, atomic writes for orchestrator actions, API | `ui/server.py` (or equivalent) + API contract |
| D | **UI-FRONTEND** | Pages, real-time updates, SIGNAL timeline visualization | `ui/static/` + interaction code |
| E | **UI-TEST** | Playwright E2E + integration tests against fixture mission dirs | `ui/tests/` |
| F | **META-OBSERVER** | Watch the run; track 4-phase-pattern viability; emergent role detection | `findings/*-F-FINAL-RUN-CAPSTONE.md` |

## Task-assignment matrix

Each lane has a fixed slot in each phase. Claim by lane → task ID is deterministic.

| Lane | PHASE-PLAN | PHASE-CHALLENGE (challenges) | PHASE-CHALLENGE (plan-v2) | PHASE-BUILD | PHASE-VERIFY (verifies) |
|---|---|---|---|---|---|
| A | `P1-A` | `P2-A→F` | `P2.5-A` | `P3-A` | `P4-A→B` |
| B | `P1-B` | `P2-B→A` | `P2.5-B` | `P3-B` | `P4-B→E` |
| C | `P1-C` | `P2-C→B` | `P2.5-C` | `P3-C` | `P4-C→D` |
| D | `P1-D` | `P2-D→C` | `P2.5-D` | `P3-D` | `P4-D→A` |
| E | `P1-E` | `P2-E→D` | `P2.5-E` | `P3-E` | `P4-E→C` |
| F | `P1-F` | `P2-F→E` | `P2.5-F` | `P3-F` | `P4-F→ALL` |

**CHALLENGE pairings form a directed cycle** (A→F→E→D→C→B→A) — every lane challenges exactly one peer and is challenged by exactly one peer.

**VERIFY pairings deliberately differ from CHALLENGE pairings** so the lane that reviewed your plan is NOT the same lane verifying your build. Two independent sets of eyes per lane.

## Cadence

3 minutes uniform (`/loop 3m`). BUILD lanes may self-extend to 10-min ticks for deep coding sessions; declare the extension in your STATUS note (e.g., `"deep build — extending to 10m for 3 ticks"`).

## Phase mechanics (v8-CANDIDATE — load-bearing for this mission)

These rules **extend** v7 for this mission. If META's FINAL-RUN-CAPSTONE confirms they worked, AUDIT promotes them to v8.

### Source of truth: `.mission-events`

Append-only log file at project root. Each line is one phase event:

```
<UTC> <FROM>-><TO> by <agent-id> — <reason>
```

The last line's `<TO>` is the **current phase**. `README.md`'s "Mission status" section is a best-effort visual rendering of the latest event — workers read `.mission-events` directly.

### NEW RULE 11 — distributed atomic phase-flip

At tick start, after re-arm (RULE 0) and heartbeat (RULE 1), every worker:

1. Read current phase from `.mission-events` (last line)
2. If current phase is `PHASE-<N>`, scan TASKS.md for `P<N>-*` tasks (and `P<N>.5-*` if applicable)
3. **Completion test:** every `P<N>-*` task is `[done: ...]` AND every `claims/<task-id>/done` marker exists AND no lane has `working: <task>` with Last UTC within the last 60 seconds
4. If completion test passes, try `mkdir .phase-flip-locks/<from>-to-<to>`:
   - **Exit 0 (you won the race):** append flip event to `.mission-events`, update README.md "Mission status" section, heartbeat with note `"PHASE-FLIP <from>→<to>"`, continue with the new phase's task pool
   - **Exit nonzero (peer is flipping):** skip; next tick will read the new phase naturally

### Phase progression

```
INIT → PHASE-PLAN → PHASE-CHALLENGE → PHASE-BUILD → PHASE-VERIFY → DRAINING → COMPLETE
```

- **DRAINING** flips automatically when all `P4-*` tasks done. Lanes write LANE-CAPSTONEs; META writes `findings/<agent>-F-FINAL-RUN-CAPSTONE-<UTC>.md`.
- **COMPLETE** flips automatically when: all lanes show `idle`, META's FINAL-RUN-CAPSTONE exists, AND the most recent HISTORY entry is >10 minutes old (quiet-period terminal condition).
- **Halt:** when current phase = `COMPLETE`, workers tick 3 more times to confirm, then stop calling `ScheduleWakeup`. Run ends.

### NEW TIER-2 default — watchdog

If a phase has not flipped for >30 minutes despite *some* `P<N>-*` tasks being marked `done`, the next worker to tick injects `[ ] [STALL-INVESTIGATION] <phase>-stall — diagnose blocking lane` into TASKS.md and writes a SIGNAL to all lanes via STATUS notes. This dead-man's-switch ensures silent stalls become visible.

### BLOCKED vetoes auto-flip

Any lane setting state to `BLOCKED` freezes auto-flip. The orchestrator (human) investigates, resolves, the lane clears BLOCKED, normal flow resumes.

## Useful pointers in this project

- **Protocol doc:** `README.md` (the v7 protocol — read once on first tick; rules are TIER-1 load-bearing)
- **Past-run lessons:** `.archive/2026-05-16T00-17Z--okx_case-rebuttal/` (READ-ONLY; useful for emergent-pattern reference)
- **Design rationale for this mission:** `docs/superpowers/specs/2026-05-16-megalodon-self-improvement-design.md`
- **Settings template (for forkers):** `.claude/settings.example.json`

## Hard constraints

- Workers **cannot `git`**, `curl`, `wget`, `npm`, `pip`, `brew`, `sudo`, `chmod`, `chown`, `ssh`, `scp` — see `.claude/settings.json` deny list. The operator reviews and commits manually after the run.
- `.archive/` is READ-ONLY (existing deny).
- BUILD lanes may write to `ui/` and any new subdirectory needed for their deliverable. AUDIT may write `docs/v8-changeset.md`.
- No `git push`, no merge-to-main, no PRs — the operator decides what is keep-worthy post-run.
- **No self-verification** in PHASE-VERIFY (encoded in pairing matrix above).

## Permissions update

No additional Read paths needed — source project IS this project, and `.claude/settings.json` already covers `Read(/Users/dave/**)` (or `<PROJECT_ROOT>` if you bootstrapped from the example).

## Deliverable

On return (3–7 hours post-deployment), the operator inherits:

1. **`docs/v8-changeset.md`** — AUDIT's proposed README.md edits. Review and merge into README.md if accepted.
2. **`ui/`** — working orchestrator-console code. Run it (`uv run python ui/server.py` or whatever stack ARCHITECT chose) and inspect.
3. **`ui/tests/`** — passing Playwright + integration suite.
4. **`findings/*-F-FINAL-RUN-CAPSTONE-*.md`** — META's retrospective on the 4-phase + auto-flip pattern. Use this to decide whether v8 should adopt the pattern.
5. **`.mission-events`** — full phase audit trail.
6. **`HISTORY.md`** — appended throughout the run.

After review, run the README's "End-of-run process" (archive → reset templates) to clear state for the next mission.

## Mission-specific subagent guidance

- **AUDIT lane:** dispatch literature-review subagents for citation deep-dives (one per paradigm: raft, OTP, blackboard, CRDT). Cap at 3 parallel per finding per TIER 2 §"Subagent budget".
- **BUILD lanes (B/C/D/E):** dispatch implementation subagents for parallel code chunks (e.g., one per page, one per endpoint, one per test file). Same 3-parallel cap. The lane retains synthesis (RULE 9).
- **META lane:** dispatch observer subagents to read `findings/` periodically and surface emergence patterns. Do NOT delegate the FINAL-RUN-CAPSTONE.

---

## Pre-deployment checklist

- [x] Source project path resolved (self-referential)
- [x] Scope clear (in/out)
- [x] Lanes defined (6, matching STATUS.md rows)
- [x] Cadence decided (3 min uniform)
- [x] Pointers listed
- [x] Hard constraints stated
- [x] `.claude/settings.json` exists with `<PROJECT_ROOT>` substituted
- [x] TASKS.md seeded with all `P1-*`, `P2-*`, `P2.5-*`, `P3-*`, `P4-*` tasks + secondary pool
- [x] `.mission-events` initialized with `INIT->PHASE-PLAN`
- [x] `.phase-flip-locks/` exists
- [x] README.md Mission status set to `Current: PHASE-PLAN (mission active)`
- [ ] Start 6 Claude sessions with the one-liner (see Deployment section)
