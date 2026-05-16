# Megalodon Coordination Protocol

A blackboard multi-agent coordination protocol for parallel review, audit, synthesis, and similar deep-work missions across multiple Claude sessions.

**Version:** v7
**Last updated:** 2026-05-16
**Default cadence:** 3 minutes (configurable in MISSION.md)

---

## How it works

5+ Claude sessions run `/loop` in this directory. Each reads `README.md` (the protocol) and `MISSION.md` (the current mission) on every tick. They self-organize via shared markdown files:

- `STATUS.md` — heartbeat board (one row per lane)
- `TASKS.md` — work queue with mkdir-based atomic claims
- `claims/<task-id>/` — atomic claim directories (POSIX-atomic; source of truth)
- `findings/` — workers' outputs (one file per finding)
- `HISTORY.md` — append-only completion log
- `MISSION.md` — mission-specific scope (edit per deployment)

The orchestrator (you, or a dedicated Claude session) sets Mission status, pushes new tasks, watches progress. Workers self-organize within the protocol's rules.

---

## How to deploy

1. **Edit `MISSION.md`** to define your mission (scope, lanes, source project, deliverable, optional cadence override).
2. **Seed `TASKS.md`** with lane-tagged tasks (`[ ] [LANE-X] <id> — <description>`).
3. **Set Mission status to ACTIVE** in this README's `## Mission status` section.
4. **Start 5+ Claude sessions** in this directory with `/loop 3m` (or your chosen cadence).
5. **Watch STATUS.md / HISTORY.md / findings/** — workers self-organize from here.
6. **At end:** orchestrator sets Mission status to DRAINING → wait one cycle → COMPLETE → run archive process.

---

## Mission status

**Current: PHASE-PLAN (mission active — see `.mission-events` for authoritative phase)**

For phase-gated missions (see MISSION.md §"Phase mechanics"), the source of truth is the append-only `.mission-events` log. This section is a best-effort visual rendering of the latest event — workers read `.mission-events` directly per RULE 11.

Possible status values:
- **IDLE** — no active mission (template state)
- **ACTIVE** — claim and work on tasks normally (single-phase missions)
- **PHASE-PLAN / PHASE-CHALLENGE / PHASE-BUILD / PHASE-VERIFY** — phase-gated mission; stance per phase defined in MISSION.md
- **DRAINING** — finish current task, write CAPSTONE if your lane drained, then idle. Do NOT claim new tasks.
- **COMPLETE** — write final HISTORY entry with session totals; heartbeat last time; halt loop

Workers re-read this section AND `.mission-events` on every tick BEFORE claiming.

---

# TIER 1 — Load-bearing rules (mandatory, non-negotiable)

These cannot be skipped. They are the protocol's atomic-correctness guarantees.

## RULE 0 — Keep the loop alive
Re-arm your next wakeup before any work each tick.
- **Dynamic mode:** `ScheduleWakeup({delaySeconds: 180, prompt: "<<autonomous-loop-dynamic>>", reason: "megalodon next tick"})`
- **Cron mode** (`/loop 3m`): runtime re-arms automatically. Calling ScheduleWakeup anyway is harmless.

## RULE 1 — Heartbeat every tick
Update your STATUS row's `Last UTC` every tick, even mid-task. A worker stale >15 min is presumed dead and reclaimed.

## RULE 2 — Atomic claim by mkdir
`mkdir claims/<task-id>` is the lock. Exit 0 = you own it. Exit nonzero = pick another. TASKS.md is informational; `claims/` is authoritative.

## RULE 3 — Hybrid review stance (Pass-1 / Pass-2)
- **Pass 1 (FRESH EYES):** form your view from the artifact alone. Do NOT read prior verifications or peer findings. This is the load-bearing safeguard against anchoring.
- **Pass 2 (RECONCILE):** read prior verifications + peer findings. Add `## Reconciliation` section: Concordance, Missed by me, Novel to me, Disagreements. Use RECONSIDERED notes — never rewrite originals, append.

## RULE 4 — SIGNALs must cite evidence
When signaling another worker via STATUS notes, you MUST cite evidence (`path:line` or `path:section`). Unsourced claims are invalid; recipients ignore them.

## RULE 5 — ACK-VERIFIED / DISSENT / DEFER (never bare ACK)
When responding to a peer signal, choose one explicitly. Each requires independently reading the cited evidence first.

```
ACK-VERIFIED <sender>: I read <file:line> at <UTC> and confirm <claim>. Updating via RECONSIDERED.
DISSENT <sender>: I read <file:line> at <UTC> and disagree because <reason>. My finding stands.
DEFER <sender>: will address in tick N when I work on <task>. Recording in scratch.
```

## RULE 6 — Stale-row reclamation
Each tick, scan STATUS for rows with State ≠ idle/PEER-REVIEWER AND Last UTC >15 min old.
- **Retroactive recovery:** if a finding file exists matching their working task-id, recover (touch done, mark TASKS done, append RECOVERY HISTORY entry).
- **Otherwise reclaim:** set State to STALE-RECLAIMED, release the lock (`rm -rf claims/<id>`; reset TASKS bracket to `[ ]`).

## RULE 7 — Source project is read-only
Workers may read anything under the source project (see MISSION.md). Writes are forbidden anywhere outside `<PROJECT_ROOT>/` (this Megalodon directory). No `git`, no build scripts, no DuckDB writes, no package installs. If a tool would modify the source project, do not run it.

## RULE 8 — No hallucination
Every assertion in a finding cites `path:line` or `path:section`. If you cannot verify, write `UNVERIFIED — reason: ...` and continue. Do not guess.

## RULE 9 — Synthesis stays with you
You may dispatch subagents (Explore, general-purpose, code-reviewer) for sub-questions. Never delegate the synthesis. The finding is yours.

## RULE 10 — Atomic completion block
When marking a task done, do all four in one tick:
1. `touch claims/<task-id>/done`
2. Mark TASKS bracket: `[done: <agent-id> @ <UTC>] [LANE-X]`
3. Append HISTORY: `<UTC> | <agent-id> | <LANE> | <task-id> | <finding-filename> | <severity>`
4. Update STATUS row to `idle | <UTC> | <task-id> done — <summary>`

Splitting these across ticks creates stale-detection false-positives.

---

# TIER 2 — Strong defaults (opt-out with rationale in STATUS notes)

These are the recommended defaults. Override only with explicit rationale.

## Cadence: 3 minutes
Default `*/3 * * * *` cron or `delaySeconds: 180` dynamic. Stays inside the 5-min prompt cache TTL. Drop to 10-15m for DRAINING phases; 20-30m for idle/monitoring. Avoid 5m (worst cache economics — sits exactly on the cache boundary). MISSION.md may override.

## Lane CAPSTONE on primary drain
When your lane's task queue empties, produce a CAPSTONE rollup: `findings/<your-id>-LANE-X-CAPSTONE-<UTC>.md`. Rolls up your lane's findings + cross-lane convergence + delivery-team recommendations.

**Equivalent:** If you took the GLOBAL-PEER-REVIEWER role, your `PEER-REVIEW-LOG.md` counts as your CAPSTONE — don't double-write.

## Mandatory scratch for multi-tick work
Before exiting any tick on unfinished work, write in-progress state to `findings/<your-id>-<task-id>.scratch.md`. Resumption reads scratch. Recommended even for context-persistent sessions to survive compaction.

## GLOBAL-PEER-REVIEWER (one slot, first-claim)
First lane to drain may set State to `PEER-REVIEWER`. Mandate:
- Read all new findings each tick
- Verify peer signals (post nudges if signals go unresponded >2 ticks)
- Track cross-lane convergence in `findings/PEER-REVIEW-LOG.md` (append-only)
- Surface quorum-escalation candidates
- Self-assign CHALLENGE on highest-converged findings
- RECONSIDERED on own prior findings as new info warrants

## CHALLENGE role (devil's advocate)
For any finding with 3+ lane convergence, any worker may self-assign a CHALLENGE task: `[ ] [CHALLENGE-<finding-id>] Construct the strongest argument that the consensus is wrong.` Claim via mkdir. Output: `findings/<your-id>-CHALLENGE-<finding-id>-<UTC>.md`. CHALLENGEs typically produce DELTA-class refinements, not overturns — that's the design goal.

## Severity escalation
- **MINOR → MAJOR:** 1 peer's Pass-1 independent finding on same artifact.
- **MAJOR → BLOCKING:** 2+ INDEPENDENT lanes' Pass-1 findings. ACK-VERIFIED responses do NOT count toward quorum.
- **Single-source BLOCKING allowed but flag as `SINGLE-LANE-BLOCKING — awaits independent confirmation`.** Don't gate on quorum; let the operational-class BLOCKINGs (build artifacts, placeholders) settle without waiting for corroboration.

## BLOCKING is final unless CHALLENGED within 2 ticks
BLOCKING claims settle automatically. CHALLENGE is opt-in. If filed within 2 ticks, BLOCKING is provisional until resolved.

## Scan findings/+claims/ at tick start
Proactive new-CHALLENGE and new-finding detection. Adds ~1 sec per tick; catches signals up to 2 ticks faster.

## Triage signals; don't always preempt
On tick start, check for SIGNALs addressed to you. Respond within 2 ticks (use DEFER if mid-task). Don't context-switch immediately — finish what you're doing first unless the signal explicitly affects your current task.

## Subagent budget ≤3 parallel per finding
For decomposable tasks, dispatch up to 3 subagents in parallel. Brief them with absolute paths and the read-only/write-scoped constraints from RULE 7. Cap response length (`"report in under 300 words"`).

## Worker self-introduction
First tick: generate ID via `python -c "import secrets; print('agent-'+secrets.token_hex(2))"`. Cache it. Reuse every tick.

## YAML frontmatter on findings
Use YAML frontmatter for finding metadata to enable Obsidian Dataview / Bases queries:
```yaml
---
lane: LOGIC
agent: agent-3ff8
task: L1
severity: MAJOR
utc: 2026-05-16T00:56Z
---
```

## RECONSIDERED preserves audit trail
Never rewrite a finding. Append RECONSIDERED notes with new evidence and reasoning. The trail is the deliverable.

## Quorum survives RECONSIDER unless explicitly revoked
If a RECONSIDER refines a finding but the core claim holds, quorum stands. If a RECONSIDER explicitly revokes the original, quorum point is voided.

---

# TIER 3 — Observed patterns (informational, from past runs)

These behaviors emerged in past runs. Documented for awareness — not encoded as rules. Workers may use these as priors but should derive from current evidence.

## Asymmetric collaboration styles
Different lane stances produce different styles. Forensic-evidence lanes (LEGAL, LOGIC) heavily cross-reference. Synthesis lanes (PROSE-as-PEER-REVIEWER) catch what task-driven workers miss. Independent verification lanes (SQL, MATH) tend to work solo until publishing.

## Cross-lane meta-patterns
Findings can converge into meta-patterns (e.g., "wrong-direction bridge across multiple findings", "scope-disclaim pattern across multiple sections"). Pattern-level findings can't be patched piecemeal — they signal systemic issues.

## Emergent role inventions
Workers may invent new roles. In the okx_case run: LOGIC invented LANE-PEER-REVIEWER (lane-local capstone synthesis) which spread to 3+ lanes within 3 ticks. Don't suppress — observe; codify in v8+ if useful.

## Forward pointers in STATUS notes
Workers may signal "tick N: <task> next" to let others align. Cheap coordination without explicit RPC.

## "Mea culpa" audit-trail discipline
When a worker misses a signal, the corrected behavior is to add an explicit acknowledgment in STATUS notes (e.g., "ACK LOGIC tick-N correction"). Don't hide errors — log them for the trail.

## Reproduction-as-concession defect class
When a witness/author reproduces opposing-party data exactly, this STRENGTHENS the opposing case — the rebuttal must acknowledge the empirical concession.

## Tool diversity emerges
Workers may invent tools beyond protocol defaults (e.g., SQL agent wrote its own Python diff harness rather than using DuckDB CLI alone). Don't constrain unless it conflicts with hard rules.

## Defect taxonomy by layer
Across past runs, defects partition into: arithmetic / methodology-in-scope / citation-chain / provenance / pipeline-determinism / inferential-direction / inferential-class / definitional-drift / header-content / operational-artifact. Useful for cross-finding aggregation.

## CHALLENGEs tend to produce refinement, not overturn
4-of-4 CHALLENGEs in the okx_case run ended as DELTA-class modified-consensus. Plan for this — CHALLENGE work is high-leverage even when it doesn't change the headline severity.

---

## Verifier-report format

```markdown
---
lane: <LOGIC | PROSE | SQL | MATH | LEGAL | ...>
agent: <agent-id>
task: <task-id>
severity: <BLOCKING | MAJOR | MINOR | NIT | DELTA>
utc: <timestamp>
artifact: <absolute path(s)>
---

# Finding: <short title>

## Summary
<2–4 sentences>

## Pass 1 — Fresh-eyes findings
<numbered: claim + evidence (path:line) + impact + recommended action>

## Pass 2 — Reconciliation with prior verifications and peer findings
- Concordance: ...
- Missed by me: ...
- Novel to me: ...
- Disagreements: ...

## Inter-agent signals received and responses
<list: signal source | claim | evidence | my response | UTC>

## Out-of-lane observations
<brief; do not investigate>

## Subagents dispatched
<list: subagent_type | purpose | one-line outcome>

## Confidence
<HIGH | MEDIUM | LOW>, one-sentence justification.
```

## Severity tags

- **BLOCKING** — ship-stopper. Tribunal/audit-credibility damage if shipped as-is.
- **MAJOR** — substantive defect; fix before delivery.
- **MINOR** — improvement; fix if cheap.
- **NIT** — cosmetic.
- **DELTA** — difference of opinion; documented for the record.

Be conservative with BLOCKING. Reserve for issues a competent adversary would weaponize.

---

## Communication

- **STATUS.md row** — live state (heartbeat, working/idle, lane)
- **STATUS.md Notes column** — short signals to orchestrator OR peers (with evidence per RULE 4)
- **A finding with severity DELTA** — protocol/design questions, scope concerns, proposals
- **BLOCKED state** — orchestrator intervention required

The orchestrator reads STATUS.md every cadence interval.

---

## Permission management

Workers run autonomously when `.claude/settings.json` defines a Bash allowlist + deny list. Auto-accept edits (Shift+Tab in Claude Code) covers file ops; the allowlist covers Bash. Both are needed for fully prompt-free operation.

**First-time setup (after cloning):**
```bash
cp .claude/settings.example.json .claude/settings.json
# Replace every <PROJECT_ROOT> in .claude/settings.json with the absolute path to this directory.
# Claude Code permission rules don't support variables — absolute paths only.
```

`.claude/settings.json` is gitignored (per-user / per-machine config); commit changes to `.claude/settings.example.json` if you want the template updated.

---

## End-of-run process

When mission deliverables are complete:

1. Orchestrator sets Mission status to **DRAINING** in this README
2. Wait one cron cycle for workers to acknowledge and complete in-flight work
3. Orchestrator sets Mission status to **COMPLETE**
4. Workers write final HISTORY entries with session totals
5. Orchestrator runs archive:
   - `cp -R findings claims STATUS.md TASKS.md HISTORY.md README.md` → `.archive/<UTC>--<mission-slug>/`
   - Replace STATUS / TASKS / HISTORY / findings / claims with empty templates
   - Update `.archive/INDEX.md`
6. User deletes crons (`CronDelete <id>` for each) or closes worker sessions

---

## Protocol changelog

- **v7 (2026-05-16):** Tiered structure (load-bearing rules / strong defaults / observed patterns); MISSION.md split; per-mission lanes/cadence; 3m default; emergent role recognition (LANE-CAPSTONE, GLOBAL-PEER-REVIEWER); CHALLENGE refinement protocol; YAML frontmatter; lessons from 42-observation review of okx_case run.
- **v6 (2026-05-15 21:24 EDT):** PEER-REVIEWER role generalized.
- **v5 (2026-05-15 21:17 EDT):** Inter-agent communication (SIGNAL / ACK-VERIFIED / DISSENT / DEFER); severity quorum; CHALLENGE role.
- **v4 (2026-05-15 20:42 EDT):** Mandatory heartbeat (RULE 1); atomic completion ordering; retroactive completion recovery.
- **v3 (2026-05-15 20:37 EDT):** Atomic mkdir claim; stale-row reclamation; Mission status; pre-claim duplicate check; mandatory scratch; early-proceed in race resolution.
- **v2 (2026-05-15 20:22 EDT):** Auto lane assignment; Rule 0 loop-keepalive.
- **v1 (2026-05-15 20:17 EDT):** Initial protocol; manual lane assignment.
