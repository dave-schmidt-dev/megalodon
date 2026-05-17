# Megalodon v8 changeset

**Author:** agent-34fc (LANE-A, AUDIT)
**Task:** `P3-A`
**Status:** Pass-1 draft @ 2026-05-16T15:51Z + Pass-2 reconciliation @ 2026-05-16T16:02Z (see §G)
**Sources cited as evidence for every edit below:**
- `findings/agent-34fc-A-P2.5-audit-plan-v2-2026-05-16T15-43Z.md` (plan-v2 incorporating ARCHITECT's P2-B→A challenge)
- `findings/agent-34fc-CROSS-S4-okx-archive-mining-2026-05-16T15-51Z.md` (archive evidence + 3 subagent reports)
- `HISTORY.md` entries for SIG-ORCH#1 (15:36Z), SIG-ORCH#2 (15:38Z), SIG-ORCH#3 (15:40Z)
- `.mission-events` 15:49:50Z (cross-lane BLOCKING quorum on canonical-SIGNAL-grammar and non-ASCII-task-IDs)

**Format:** each edit is an explicit `### Edit N` block with `Location`, `Before`, `After`, `Rationale`. Operator applies one-by-one. Severity tags follow `README.md` §"Severity tags" classes.

---

## §A — Edits ordered by severity class (MAJOR first)

### Edit 1 — [MAJOR] Add lineage paragraph naming v7 as a blackboard system

**Location:** `README.md:8-9` (after `**Default cadence:** 3 minutes...` line).

**Before:**
```
**Default cadence:** 3 minutes (configurable in MISSION.md)

---

## How it works
```

**After:**
```
**Default cadence:** 3 minutes (configurable in MISSION.md)

## Lineage

Megalodon is a **blackboard system** in the HEARSAY-II / BB1 tradition:
shared markdown files act as the blackboard, lanes act as opportunistic
knowledge-sources, and the protocol's TIER-1 rules form the control
strategy. Architecturally, Megalodon uses an **exogenous leader** (the
human orchestrator) for coarse-grained decisions (Mission status flips,
BLOCKED resolution, archival) and a **leaderless worker pool** for tick-
level work. We deliberately do NOT propose adding an endogenous worker-
elected leader (Raft / Paxos / bully-algorithm style); the human-in-loop
choice is intentional. Naming this lineage clarifies what the protocol
*is* (blackboard + exogenous-leader) and what it is *not* (consensus-
based replicated state machine).

---

## How it works
```

**Rationale:** Plan-v2 M3 (`agent-34fc-A-P2.5-audit-plan-v2-...md:M3`).
Incorporates ARCHITECT P2-B→A finding #7 (exogenous-leader distinction).
Helps new workers map v7 onto existing literature in seconds; clarifies
what's adoptable from each paradigm (Raft = no; OTP = yes; CRDT = maybe).

---

### Edit 2 — [MAJOR] New TIER-1 sub-clause under RULE 5: DEFER must scan FS first

**Location:** `README.md:76-83` (RULE 5 block).

**Before:**
```
## RULE 5 — ACK-VERIFIED / DISSENT / DEFER (never bare ACK)
When responding to a peer signal, choose one explicitly. Each requires independently reading the cited evidence first.

```
ACK-VERIFIED <sender>: I read <file:line> at <UTC> and confirm <claim>. Updating via RECONSIDERED.
DISSENT <sender>: I read <file:line> at <UTC> and disagree because <reason>. My finding stands.
DEFER <sender>: will address in tick N when I work on <task>. Recording in scratch.
```
```

**After:**
```
## RULE 5 — ACK-VERIFIED / DISSENT / DEFER (never bare ACK)
When responding to a peer signal, choose one explicitly. Each requires independently reading the cited evidence first.

```
ACK-VERIFIED <sender>: I read <file:line> at <UTC> and confirm <claim>. Updating via RECONSIDERED.
DISSENT <sender>: I read <file:line> at <UTC> and disagree because <reason>. My finding stands.
DEFER <sender>: will address in tick N when I work on <task>. Recording in scratch.
```

**Sub-clause (load-bearing): DEFER citing non-existence MUST verify first.** A DEFER that says "claim not yet published" or "no finding exists" MUST first run `ls findings/ | grep <pattern>` AND `ls claims/<task-id>/done` to confirm the absence is real. Hallucinated DEFERs caused a documented 7-tick lag in the okx_case-rebuttal archive (`.archive/2026-05-16T00-17Z--okx_case-rebuttal/findings/agent-95cf-LEGAL-...-LANE-E-CAPSTONE.md:125-131`).
```

**Rationale:** S-4 §1 failure mode A (TIER-1 invariant addition). Single-
run archive evidence but failure is structural (false-negative protocol
move). Closes a non-hallucination loophole consistent with RULE 8.

---

### Edit 3 — [MAJOR] New TIER-2 §Output-format standardization (from SIG-ORCH#3 + BLOCKING quorum)

**Location:** Insert after `README.md:153` (after `## Subagent budget ≤3 parallel per finding`).

**Before:** (no equivalent block exists in v7)

**After (new section):**
```
## Output-format standardization

Heterogeneous finding files degrade synthesis. Workers SHOULD produce findings
that match the canonical template at `findings/_TEMPLATE.md` (ship as part of
the protocol). Required:

**Filename grammar (regex):**
`^agent-[0-9a-f]{4}-[A-F]-P[1-4](\.5)?-[a-z-]+-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}(-\d{2})?Z\.md$`
Or for CROSS / CHALLENGE: substitute `CROSS-S\d+` or `CHALLENGE-<finding-id>`.

**YAML frontmatter (required fields):**
```yaml
---
agent: agent-XXXX
lane: A
task: P1-A
phase: PHASE-PLAN
utc: 2026-05-16T15-32Z   # canonical: YYYY-MM-DDTHH-MM[-SS]Z (dashes in time)
severity: DELTA          # one of BLOCKING / MAJOR / MINOR / NIT / DELTA
finding-type: plan       # plan | challenge | build | verify | cross | capstone
target-lane: F           # required for challenge/verify; null otherwise
artifact: <abs-path>
lineage: v8              # protocol version this finding targets
---
```

**Scratch-vs-finding separation:** `findings/` is for canonical deliverables
only. In-progress scratch goes to `.scratch/<agent-id>/<task-id>.md`. The
v7 convention of `findings/<id>.scratch.md` is deprecated.

**Task-ID character canonicalization (LOAD-BEARING):** All task identifiers
in TASKS.md and `claims/` directories use ASCII `->` only — never Unicode
arrow. Workers MUST claim with the ASCII form (e.g., `mkdir claims/P2-A-to-F`,
not `mkdir claims/P2-A→F`). The cross-lane BLOCKING quorum in this run
(5-source convergence at `.mission-events` 15:49:50Z) confirms Unicode-vs-
ASCII races produced duplicate claims (`claims/P2-B-A` + `claims/P2-B→A`;
`claims/P2-C→B` + `claims/P2-CtoB`).

**Validation tooling:** Ship `validate-finding.py` invoked on write; report
violations to STATUS notes for orchestrator visibility.
```

**Rationale:** Plan-v2 M2 (output-format standardization from SIG-ORCH#3 +
non-ASCII-task-IDs BLOCKING quorum at `.mission-events:15:49:50Z`). Two
independent quorum events (canonical-SIGNAL-grammar M2/C8/META; non-ASCII-
task-IDs 5-source CH-2) confirm this is structural. SIG-ORCH#3 text at
`HISTORY.md` 15:40Z is the primary citation.

---

### Edit 4 — [MAJOR] File-collision concurrency architecture — META-deferred placeholder

**Location:** Insert after `README.md:174` (end of TIER-2 section, before `# TIER 3` header).

**Before:** (no block exists)

**After (new section):**
```
## File-collision concurrency architecture (META-deferred)

The orchestrator surfaced this as SIG-ORCH#2 mid-run (`HISTORY.md` 15:38Z):
shared-mutable `STATUS.md` / `TASKS.md` / `HISTORY.md` produce read-modify-
write races at 6-worker concurrency. Four candidate fixes were proposed
(file-per-lane status / append-only event log / `mkdir` write lock / CRDT
row merges). Selection deferred to META's FINAL-RUN-CAPSTONE evidence
(Dim-5: race / stale / recovery events). AUDIT does NOT prescribe a winner
in v8; the v8.x point release after META's verdict will codify the
selected mechanism.
```

**Rationale:** Plan-v2 M1 (META-deferred placeholder per SIG-ORCH#2
explicit framing: *"META: assess in capstone, do NOT prescribe."*).
Strengthened by three-source convergence: SIG-ORCH#2 + my plan-v2 M2 +
ARCHITECT P1-B §6 hazard 1 + BACKEND C8 (cited in `.mission-events`
15:49:50Z BLOCKING quorum). The placeholder is itself the correct v8
move; AUDIT chooses the wrong mechanism if it picks now.

---

## §B — Edits ordered by severity class (MINOR)

### Edit 5 — [MINOR] TIER-2 §"Triage signals" REVERSAL (from S-4 failure mode C)

**Location:** `README.md:148-149`.

**Before:**
```
## Triage signals; don't always preempt
On tick start, check for SIGNALs addressed to you. Respond within 2 ticks (use DEFER if mid-task). Don't context-switch immediately — finish what you're doing first unless the signal explicitly affects your current task.
```

**After:**
```
## Triage signals; preempt-or-DEFER, don't drift
On tick start, check for SIGNALs addressed to you. Respond within 2 ticks. If the signal does not affect your current task, **DEFER explicitly** in a STATUS note (`DEFER <sender>: will address tick N`) and continue your work. If the signal *does* affect your current task, **preempt** the work — even mid-task — and re-claim or re-scope when done. The okx_case run produced 3 documented instances of un-DEFER'd signals lingering >2 ticks under v7's "finish own work first" guidance (`.archive/.../findings/agent-95cf-LEGAL-...-LANE-E-CAPSTONE.md:123`); the load-bearing failure mode is silent drift, not interruption cost.
```

**Rationale:** S-4 §1 failure mode C. Archive shows the current rule
*produced* the failure mode; flipping it removes the predictable trap.
Note: this is a TIER-2 *revision*, not addition — the section already
exists and gets reframed.

---

### Edit 6 — [MINOR] TIER-2 LANE-X-PEER-REVIEWER promotion to default

**Location:** `README.md:117-120` (Lane CAPSTONE on primary drain block).

**Before:**
```
## Lane CAPSTONE on primary drain
When your lane's task queue empties, produce a CAPSTONE rollup: `findings/<your-id>-LANE-X-CAPSTONE-<UTC>.md`. Rolls up your lane's findings + cross-lane convergence + delivery-team recommendations.

**Equivalent:** If you took the GLOBAL-PEER-REVIEWER role, your `PEER-REVIEW-LOG.md` counts as your CAPSTONE — don't double-write.
```

**After:**
```
## Lane CAPSTONE on primary drain + LANE-X-PEER-REVIEWER role

When your lane's task queue empties AND your lane has produced 3+ findings, you MAY self-assign the role `LANE-X-PEER-REVIEWER` (where X is your lane code, e.g., `LANE-A-PEER-REVIEWER`). This role:

1. **Produces** the lane CAPSTONE: `findings/<your-id>-LANE-X-CAPSTONE-<UTC>.md`. Rolls up your lane's findings + cross-lane convergence + delivery-team recommendations.
2. **Reviews** all peer findings from your own lane and writes RECONSIDERED notes on any that the synthesis sharpens or contradicts.
3. **Coordinates** with the GLOBAL-PEER-REVIEWER (if claimed) — the global role writes `PEER-REVIEW-LOG.md`, which catalogs *consensus across lanes*; the lane role catalogs *consensus within lane*.

**Constraint (enforce, don't just state):** ONE LANE-CAPSTONE per drained lane. The first worker in a lane to self-assign owns the role for the run. Late arrivers who duplicate the work waste effort and produce competing capstones (okx_case archive: 3 parallel capstones in the drain window — `.archive/.../findings/agent-41fa-LANE-C-CAPSTONE-...md:13,96-98`).

**Equivalent:** If you took the GLOBAL-PEER-REVIEWER role, your `PEER-REVIEW-LOG.md` counts as your CAPSTONE — don't double-write.
```

**Rationale:** S-4 §2 emergent-roles + §1 failure mode E. 4-of-5 organic
adoption in okx_case archive with the lane's own self-codification
proposal (`PEER-REVIEW-LOG.md:797` of archive). Strongest emergent-role
evidence available; promoting to TIER-2 with explicit one-per-lane
constraint addresses both the role utility AND the proliferation race.

---

### Edit 7 — [MINOR] TIER-2 CHALLENGE self-conflict declaration

**Location:** `README.md:133-135` (CHALLENGE role block).

**Before:**
```
## CHALLENGE role (devil's advocate)
For any finding with 3+ lane convergence, any worker may self-assign a CHALLENGE task: `[ ] [CHALLENGE-<finding-id>] Construct the strongest argument that the consensus is wrong.` Claim via mkdir. Output: `findings/<your-id>-CHALLENGE-<finding-id>-<UTC>.md`. CHALLENGEs typically produce DELTA-class refinements, not overturns — that's the design goal.
```

**After:**
```
## CHALLENGE role (devil's advocate)
For any finding with 3+ lane convergence, any worker may self-assign a CHALLENGE task: `[ ] [CHALLENGE-<finding-id>] Construct the strongest argument that the consensus is wrong.` Claim via mkdir. Output: `findings/<your-id>-CHALLENGE-<finding-id>-<UTC>.md`. CHALLENGEs typically produce DELTA-class refinements, not overturns — that's the design goal.

**Conflict-of-interest declaration:** The CHALLENGE author SHOULD NOT have been a contributor to the targeted quorum. If unavoidable (e.g., when the challenger is the only idle lane), the CHALLENGE's frontmatter MUST include `conflict: self-quorum-member — <which findings>` and the author MUST frame the challenge against their own prior contribution explicitly. Undisclosed self-conflict appeared in the okx_case archive (`.archive/.../findings/agent-3ff8-CHALLENGE-hetzner-blocking-...md:105`).
```

**Rationale:** S-4 §1 failure mode D. Single-run archive evidence with
explicit author self-disclosure of the issue. Codifies a transparency
requirement that the archive's author already volunteered manually.

---

### Edit 8 — [MINOR] Bootstrap-ergonomics — `launch.md` at repo root

**Location:** Repo-structure recommendation (not a single README edit; affects multiple files).

**Before:** Operator bootstrap requires typing this six times:
```
claude "Join the Megalodon mission as a worker. Read README.md, MISSION.md, and TASKS.md. Generate your agent-ID, self-claim the first unclaimed lane in STATUS.md, then /loop 3m"
```

**After:** Ship `launch.md` at repo root. Operator bootstrap becomes:
```
claude "read launch.md"
```

`launch.md` content:
```markdown
# Megalodon — worker launch

1. Read these three in order: `README.md` (protocol), `MISSION.md` (this mission), `TASKS.md` (work queue).
2. Generate your agent ID: `python3 -c "import secrets; print('agent-'+secrets.token_hex(2))"`. Cache it; reuse every tick.
3. Self-claim the first unclaimed row in `STATUS.md` (atomically — race resolves on UTC, earliest wins).
4. Start the tick loop: `/loop 3m` (or whatever MISSION.md specifies under "Cadence").
5. On each tick: heartbeat, read `.mission-events` for current phase, scan stale rows + signals, claim phase task, work it, RULE-10 atomic completion.

For phase-gated missions (this is one), the current phase is the last line of `.mission-events`.
```

Also update `README.md` "How to deploy" section step 4 to reference `launch.md`.

**Rationale:** SIG-ORCH#1 (`HISTORY.md` 15:36Z), explicitly addressed to
AUDIT. Operator-friction observation; one-line affordance. Plan-v2 m2.

---

### Edit 9 — [MINOR] Restart-strategy taxonomy for stale-row reclamation

**Location:** `README.md:85-89` (RULE 6 block).

**Before:**
```
## RULE 6 — Stale-row reclamation
Each tick, scan STATUS for rows with State ≠ idle/PEER-REVIEWER AND Last UTC >15 min old.
- **Retroactive recovery:** if a finding file exists matching their working task-id, recover (touch done, mark TASKS done, append RECOVERY HISTORY entry).
- **Otherwise reclaim:** set State to STALE-RECLAIMED, release the lock (`rm -rf claims/<id>`; reset TASKS bracket to `[ ]`).
```

**After:**
```
## RULE 6 — Stale-row reclamation (one-for-one restart)
Each tick, scan STATUS for rows with State ≠ idle/PEER-REVIEWER AND Last UTC >15 min old. v7 uses **one-for-one restart** (only the dead worker's lane is recycled; peer lanes continue). Other OTP-supervision strategies (one-for-all, rest-for-one) are NOT supported in v7 / v8; if a future mission needs them, it's a protocol extension.

- **Retroactive recovery:** if a finding file exists matching their working task-id, recover (touch done, mark TASKS done, append RECOVERY HISTORY entry).
- **Otherwise reclaim:** set State to STALE-RECLAIMED, release the lock (`rm -rf claims/<id>`; reset TASKS bracket to `[ ]`).
```

**Rationale:** Plan-v2 m1. Documents the implicit choice; helps auditors
comparing v7 to OTP supervision know what is in/out of scope.

---

## §C — Edits ordered by severity class (DELTA / TIER-3 / observation-only)

### Edit 10 — [DELTA] Defect taxonomy refactor (TIER-3 observed-patterns)

**Location:** `README.md:202-203` (defect taxonomy line).

**Before:**
```
## Defect taxonomy by layer
Across past runs, defects partition into: arithmetic / methodology-in-scope / citation-chain / provenance / pipeline-determinism / inferential-direction / inferential-class / definitional-drift / header-content / operational-artifact. Useful for cross-finding aggregation.
```

**After:**
```
## Defect taxonomy by layer
Across past runs, defects partition into 13 categories (10 keep + 3 added):

**Kept (v7 → v8):** arithmetic / methodology-in-scope / citation-chain / provenance / pipeline-determinism / inferential-class / definitional-drift / header-content / operational-artifact.

**Split (v8):** `inferential-direction` (v7) split into **`wrong-direction-bridge`** (evidence runs the opposite way the body presents it) and **`direction-asymmetry`** (different epistemic treatment of the same evidence class across sister findings). Justification: v7's `inferential-direction` was the single dominant category in okx_case (~10+ hits, ~40-50% of MAJOR-or-worse findings); under-resolution hurt cross-finding aggregation.

**Added (v8):**
- **`scope-expansion`** — sub-population claims pleaded as platform-wide; small-N treated as large-N.
- **`strawman-substitution`** — rebuttal targets a substituted paraphrase of the opposing claim rather than the actual claim.
- **`framing-symmetry`** — rebuttal addresses one branch of a multi-branch claim without acknowledging the unaddressed branches.

Useful for cross-finding aggregation.
```

**Rationale:** S-4 §3 defect-taxonomy survey. Single-run evidence but
the categories appeared organically in 8-10 finding files; v8 documents
what's already happening empirically.

---

### Edit 11 — [DELTA] TIER-3 observation: all-BLOCKED operator-attention pattern

**Location:** Insert after `README.md:206` (end of "CHALLENGEs tend to produce refinement, not overturn" block).

**Before:** (no block)

**After (new TIER-3 entry):**
```
## All-BLOCKED operator-attention pattern
When all 6 lanes simultaneously set State to BLOCKED, the protocol's auto-flip mechanism (MISSION.md phase mechanics) freezes by design. The orchestrator's response time becomes critical, but the protocol provides no in-band escalation to surface this — the operator must be watching. UI/dashboard implementations SHOULD surface all-BLOCKED state prominently (e.g., dedicated banner, audio cue) so operators don't miss it. This is a UI affordance, not a protocol mechanic — adding an in-protocol auto-mechanism would defeat the deliberate human-in-loop choice of `MISSION.md` BLOCKED-vetoes-auto-flip.
```

**Rationale:** Plan-v2 Δ1 (UI affordance recommendation hand-off, NOT a
protocol mechanic — would contradict MISSION.md:99-101 per ARCHITECT
P2-B→A finding #2).

---

### Edit 12 — [DELTA] Verifier-report frontmatter: add `lineage:` field

**Location:** `README.md:213-221` (Verifier-report format YAML block).

**Before:**
```
---
lane: <LOGIC | PROSE | SQL | MATH | LEGAL | ...>
agent: <agent-id>
task: <task-id>
severity: <BLOCKING | MAJOR | MINOR | NIT | DELTA>
utc: <timestamp>
artifact: <absolute path(s)>
---
```

**After:**
```
---
lane: <LOGIC | PROSE | SQL | MATH | LEGAL | ...>
agent: <agent-id>
task: <task-id>
severity: <BLOCKING | MAJOR | MINOR | NIT | DELTA>
utc: <timestamp>
artifact: <absolute path(s)>
lineage: v8                   # protocol version this finding targets
finding-type: plan|challenge|build|verify|cross|capstone
---
```

**Rationale:** Plan-v2 m3. Allows future audits to trace which protocol
version a finding was written against. Folds into Edit 3 (output-format
standardization) frontmatter spec.

---

## §D — Cross-cutting protocol promotions

### Promotion bar (TIER-1 / TIER-2 / TIER-3)

Recommend adding the following TIER-promotion bar to `README.md` near
the top of TIER-1 section (`README.md:54-56`) so future auditors apply
the same evidence bar I had to apply (and missed in P1-A):

- **TIER-1:** ≥3 prior-run convergent observations AND invariant-class
  (atomic correctness, evidence discipline, completion atomicity).
- **TIER-2:** ≥2 runs OR strong single-run evidence + explicit opt-out
  path documented in the rule.
- **TIER-3:** single-run observation, informational only.

**Rationale:** Caught by ARCHITECT P2-B→A finding #1 against my P1-A
slot #2 (would have promoted a tactic-class rule to TIER-1 on 18-minute
evidence). Codifying the bar prevents recurrence.

---

## §E — Items deliberately NOT in this changeset

These were considered and rejected with rationale:

1. **Deadlock detector for all-BLOCKED.** Contradicts `MISSION.md:99-101`
   explicit human-in-loop choice. Replaced by Edit 11 (UI affordance).
   Rejected per ARCHITECT P2-B→A finding #2.
2. **TIER-1 mandate for `flock`-based file mutation.** Tactic-class, not
   invariant-class; pre-empts SIG-ORCH#2 META-deferral. Replaced by
   Edit 4 (META-deferred placeholder). Rejected per ARCHITECT P2-B→A
   finding #1 and #3.
3. **`LANE-X-CHALLENGE` prefix convention.** Only 2 archive lanes used
   it; single-run evidence, observation-only. Stays TIER-3 if surfaced;
   no edit warranted yet.
4. **Endogenous leader election (Raft/Paxos).** Out-of-scope; v7's
   exogenous-leader + leaderless-worker-pool design is intentional.
   See Edit 1 lineage paragraph.

---

## §G — Pass-2 reconciliation (tick 8, 2026-05-16T16:02Z)

Pass-2 work executed via one general-purpose subagent (RULE 9 synthesis
retained by me) reading the 5 peer plan-v2s + META P3-F mid-mission +
META CROSS S-13 challenge-outcomes. Findings:

### Concordance

- **Edit 3** is strongly concurrent. 5-source BLOCKING quorum on
  canonical-SIGNAL-grammar / non-ASCII-task-IDs confirmed at
  `findings/agent-aa79-B-P2.5-arch-plan-v2-...:§10:124-130`. Sources:
  BACKEND C3, ARCHITECT P1-B out-of-lane, FRONTEND ghost-claim
  observation, META CH-2 (=`findings/agent-5f87-F-P2-challenge-of-
  test-2026-05-16T15-40Z.md:74-113`), SIG-ORCH#3. META P3-F:135-141
  extends to 6 sources by counting SIG-ORCH#2 separately.
- **Edit 8** (`launch.md`) is passively concurrent — all four non-AUDIT
  lanes' "Inter-agent signals" tables defer SIG-ORCH#1 to AUDIT.
- **Edit 11** (all-BLOCKED UI affordance) is weakly concurrent. META
  P3-F:117-129 reports **zero BLOCKED states observed** in this run and
  recommends DEFER-MORE-RUNS on Dim-4. The Edit stands but urgency is
  reduced; severity stays DELTA.
- **Edit 12** (YAML `lineage:` + `finding-type:` fields) is folded by
  reference into Edit 3; BACKEND :§Δ4 (132-153) types the LaneRow shape
  and META P2.5-F:280-285 explicitly directs schema gap to my changeset.

### Missed by me — new Edits added below (Edits 13-18)

Pass-2 surfaced six items I should have included. Added as Edit 13-18.

### Novel to me

- META P3-F's quantitative S-13 numbers (47/50 ACK rate, 0% DISSENT, 6%
  QUORUM-ESCALATION) replace the okx_case "4-of-4 DELTA" line in
  `README.md:206`. Folded into Edit 13 RULE 5 expansion.
- FE+BE convergence on SSE event-envelope `id: <int>` monotonic field.
  Folded into Edit 17.

### Disagreements

None. No edit was contradicted in a way that requires DISSENT under
RULE 5. The closest case is **Edit 4** (file-collision META-deferral),
which is now superseded by ARCHITECT and BACKEND committing to specific
mechanisms. I'm RECONSIDERING Edit 4 below (not dropping it) to preserve
the audit trail per `README.md:170`.

### RECONSIDERED — Edit 4 (file-collision concurrency architecture)

**Original (Pass-1):** META-deferred placeholder; AUDIT does NOT prescribe.

**Pass-2 evidence that changes the conclusion:**
- **ARCHITECT P2.5-B §A:54-78** commits to `content-hash CAS` as PRIMARY
  mechanism (compare-and-swap on file hash before write; reject on
  mismatch and retry).
- **BACKEND P2.5-C §Δ5:156-169** commits to `alphabetical-absolute-path
  lock-order` as secondary mechanism for multi-file atomic operations.
- **META P3-F §Dim-5:187-205** treats this as `PROMOTE-WITH-CHANGES`,
  not `DEFER-MORE-RUNS` — META has the data, the verdict is in.

**RECONSIDERED conclusion:** Edit 4 should commit to the ARCH+BE+META
converged mechanism rather than defer. Original placeholder text remains
in §A above as the audit-trail record; the operational v8 edit is below
as **Edit 4-bis**.

---

### Edit 4-bis — [MAJOR] (RECONSIDERED from Edit 4) Adopt CAS + lock-order for shared mutable files

**Location:** Same as Edit 4 (after `README.md:174`).

**Before:** v7 has no concurrency-architecture rule for shared markdown.

**After (new TIER-1 sub-block):**
```
## File-collision concurrency architecture (TIER-1)

For files mutated in place by multiple workers (`STATUS.md`, `TASKS.md`,
`HISTORY.md`), workers MUST use a two-mechanism discipline:

1. **Content-hash CAS (compare-and-swap):** before write, hash the
   file. Hold the hash. Re-read the file just before commit. If the
   hash changed, retry the read-modify-write cycle (max 3 retries
   before logging UNVERIFIED). This is the PRIMARY collision-prevention
   mechanism per ARCHITECT P2.5-B §A.

2. **Alphabetical absolute-path lock-order** when modifying multiple
   files in one logical operation: acquire `flock` on files in
   `sorted(absolute_paths)` order. Release in reverse. Prevents
   classic lock-order deadlock per BACKEND P2.5-C §Δ5.

`HISTORY.md` MAY be appended without CAS (append-only operations are
naturally race-tolerant). Append-only event logs (per Edit 13 below)
are the preferred future direction.
```

**Rationale:** Pass-2 evidence supersedes Pass-1 placeholder. Three-lane
convergence (ARCH PRIMARY + BE SECONDARY + META PROMOTE-WITH-CHANGES
verdict on Dim-5). v8 has enough evidence to codify; deferring to a
future v8.x release would lose the momentum.

---

### Edit 13 — [MAJOR] RULE 5 expansion: NO-RESPONSE as fourth option + quantitative TIER-3 update

**Location:** `README.md:76-83` (RULE 5 block; extends Edit 2's sub-clause).

**Before:** v7 RULE 5 has three responses (ACK-VERIFIED / DISSENT / DEFER).

**After (added fourth option + TIER-3 swap):**
```
**Fourth response: NO-RESPONSE.** If a signal goes unanswered for >2
ticks AND no DEFER was issued, the recipient's lane is flagged as
NO-RESPONSE in the META capstone. NO-RESPONSE is a protocol-defective
state, not a fourth-choice peer can pick — it's the trace left by
inaction. Quorum on a finding survives unrelated NO-RESPONSE traces but
the trace IS captured.
```

Also update `README.md:206` ("CHALLENGEs tend to produce refinement,
not overturn") to reflect this run's actual numbers per META S-13
`:232-243`:

```
**Before:** "4-of-4 CHALLENGEs in the okx_case run ended as DELTA-class
modified-consensus."

**After:** "Empirical rates across runs: okx_case 4-of-4 DELTA; this run
47/50 ACK-VERIFIED (94%), 0% DISSENT, 6% QUORUM-ESCALATION. CHALLENGE
refinement is the consistent outcome; overturns are rare."
```

**Rationale:** META S-13 :217-220 + :232-243 (3-source quorum: TEST P2.5-E
CHALLENGE-1 + META P2-F→E CH-3 + META P3-F live observation).

---

### Edit 14 — [MAJOR] RULE 11 step 4a: stuck-flip detection and recovery

**Location:** Insert after `MISSION.md:74-83` (NEW RULE 11 block) — note
this edit targets MISSION.md template, applies to phase-gated missions.

**Before:** RULE 11 has 4 steps. Step 4 says: "mkdir lock won → append
event, update README, heartbeat, continue with new phase."

**After (insert as step 4a):**
```
**Step 4a — stuck-flip recovery.** If a worker holds the
`.phase-flip-locks/<from>-to-<to>` directory but the `.mission-events`
append did not complete within 60 seconds (visible as: lock exists AND
the most-recent event still shows the OLD phase), the next worker to
tick scans for this condition. On detection:
1. Verify the lock-holder's STATUS.md Last UTC > 60s old.
2. `rm -rf .phase-flip-locks/<from>-to-<to>` (release the lock).
3. Re-run RULE 11 from step 1.

This handles the "lock-held-before-event-appended" window that META
P3-F :71-77 observed live in this run.
```

**Rationale:** Pass-2 surfaced TEST P2.5-E CHALLENGE-1 (`:65-73`) +
META P2-F→E CH-3 + META P3-F mid-mission. 3-source convergence; quorum
sufficient for TIER-1 step-4a addition. The live observation of the
window in this run (META P3-F :71-77) is the deciding evidence.

---

### Edit 15 — [MINOR] DISSENT-rate watchdog (anti-absorption-theater)

**Location:** TIER-2 default; insert after `README.md:139-143` (Severity
escalation + BLOCKING-finality block).

**Before:** v7 has no mechanism flagging "ACK-VERIFIED but not actually
incorporated."

**After (new TIER-2 default):**
```
## DISSENT-rate watchdog (anti-absorption-theater)

In PHASE-VERIFY (or equivalent post-build verification), if a verifier
finds that a P3 BUILD did NOT incorporate a P2.5 CHALLENGE that was
ACK-VERIFIED in the corresponding plan-v2, the verifier flags this as
a DELTA-class finding on the plan-v2 author. The discipline:
**100% ACK-VERIFIED with 0% actual incorporation is "absorption
theater" — the protocol cannot reward agreement without follow-through.**
```

**Rationale:** META S-13 :245-250. Directly addresses the failure mode
where lanes ACK-VERIFIED challenges to look collegial but never
actually changed their build. Codifies a verification check that closes
the loop.

---

### Edit 16 — [MINOR] Plan-v2 tabular outcome format

**Location:** Verifier-report format block (`README.md:213-247`); extends
Edit 12.

**Before:** v7's verifier-report format leaves plan-v2 structure to author
discretion (ARCH used Concordance table; TEST used per-CHALLENGE subsections).

**After (mandate uniform row format for plan-v2 reconciliation):**
```
For plan-v2 (P2.5) findings reconciling a peer challenge, the
Reconciliation section MUST use the following per-finding table:

| Challenger finding | My response | Action taken | Citation |
|---|---|---|---|
| `<challenger>-F#` | ACK-VERIFIED | RECONSIDERED <slot> | <path:line> |
| `<challenger>-F#` | DEFER (tick N) | <action> | <path:line> |
| `<challenger>-F#` | DISSENT | finding stands | <path:line> |
| `<challenger>-F#` | NO-RESPONSE | (defective) | n/a |

Heterogeneous plan-v2 structures hurt META's capstone aggregation —
META S-13 :222-230 quantifies the cost.
```

**Rationale:** META S-13 :222-230 + cross-lane format drift observed in
this run (5 lanes used 4 different structures).

---

### Edit 17 — [MINOR] SSE event-envelope `id: <int>` monotonic field

**Location:** Extends Edit 3 (output-format standardization) §"Canonical
SIGNAL grammar."

**Before:** Edit 3 specifies YAML frontmatter + filename regex + task-ID
canonicalization but doesn't address streaming-event envelope.

**After (added to Edit 3's canonical-grammar section):**
```
**SSE event-envelope:** if the UI (or any future event-emitter)
publishes a Server-Sent-Events stream over `events/`, each event MUST
include a monotonically-increasing `id: <int>` field. Consumers use the
`Last-Event-ID` header for replay. FE and BE plan-v2s (FRONTEND C2
:51-72, BACKEND Δ4) commit to this independently.
```

**Rationale:** FRONTEND P2.5-D §C2 (`:51-72`) + BACKEND P2.5-C §Δ4
two-lane convergence. Crosses the canonical-SIGNAL-grammar BLOCKING
quorum (so M2/C8/META + FE/BE = 5-source convergence on event-envelope
discipline).

---

### Edit 18 — [DELTA] HISTORY.md `<LANE>` field shape canonicalization

**Location:** `HISTORY.md` header (`README.md:5` references the format).

**Before:** Format spec is `<UTC> | <agent-id> | <LANE> | <task-id> |
<finding-filename> | <severity>` — but workers used `A`, `LANE-A`, and
`AUDIT` interchangeably (visible in HISTORY this run; 3 formats observed
in 14 entries per META P3-F :168-176).

**After (canonical-shape clause):**
```
The `<LANE>` field MUST use the matrix form from MISSION.md task-
assignment table — single letter (`A` / `B` / `C` / ...) for lane code
OR the full lane name (`AUDIT` / `ARCHITECT` / ...). Pick one form per
mission in `MISSION.md`'s deployment block; workers MUST use that form
consistently. Mixing forms hurts MTV (mean-time-to-verify) for
aggregated HISTORY queries.
```

**Rationale:** TEST P2.5-E :247-258 + META P3-F :168-176. Single-mission
single-format choice; cheap to enforce, expensive to retrofit.

---

## §H — Deployment Affordances (SIG-ORCH #1 + #3 + #4 cluster)

Per SIG-ORCH#4 (`HISTORY.md` 15:55Z) explicit bundling guidance:
**SIG #1 (launch.md) + SIG #3 (output formats) + SIG #4 (permission
minimization) form a single coherent v8 theme: pre-kickoff orchestration
hygiene.** I am clustering them here as a single deployment-time concern
even though individual edits live in §A/§B above. Operator applying the
changeset should treat these as one feature, not three.

**Member edits:** Edit 3 (output-format std), Edit 8 (launch.md),
Edit 12 (YAML lineage field), Edit 16 (plan-v2 tabular format).

**Additional pre-kickoff items from SIG-ORCH#4 (`HISTORY.md` 15:55Z):**

1. **Directory tree pre-generation.** Before workers spawn, `mkdir -p`
   all dirs derivable from TASKS.md lane-task matrix. For this run:
   `ui/{adrs,static/{css,js,pages,themes},tests/{unit,integration,e2e,
   fixtures/{small,medium,medium-failure-modes,large}},tools}`,
   `.scratch/`, `events/`, `docs/`.
2. **Settings.json allowlist broad enough.** `<PROJECT_ROOT>/**` Edit
   allow + enumerated safe Bash commands. Deny list unchanged
   (no git, no curl/wget/npm/pip/brew/sudo).
3. **Path-discipline enforcement.** Workers writing `.scratch.md` files
   to `findings/` is a violation; v8 should flag via `validate-finding.py`
   (Edit 3).

**Rationale:** Live evidence from this run — 4 `.scratch.md` files (mine
included) ended up in `findings/` because `.scratch/` didn't exist at
kickoff; orchestrator post-hoc-created at 15:55Z but mid-run renames
hurt the audit trail. Pre-generation eliminates the friction.

---

## §F — (was) Pass-2 reconciliation plan — superseded by §G above

(Confidence statement moved to §I below.)

---

## §I — Pass-3 reconciliation: FRONTEND P4-D→A verification (DRAINING phase, 16:37Z)

FRONTEND's P4-D→A verification (`findings/agent-1371-D-P4-verify-of-audit-
2026-05-16T16-33Z.md`) verified 16 of 18 Edits as well-supported and
surfaced 5 issues. Per RULE 5 ACK-VERIFIED discipline, I read each issue's
cited evidence at 16:37Z and respond below. All five issues are accepted.

### RECONSIDERED — Edit 4-bis (TIER-1 → TIER-2 demotion)

**FE Issue 1 (MAJOR):** Edit 4-bis is labeled TIER-1 but §D Promotion bar
(my own) requires ≥3-run + invariant-class. CAS + lock-order is 1-run +
mechanism-class.

**ACK-VERIFIED — I read `docs/v8-changeset.md:419-433` (§D) and
`:529-549` (Edit 4-bis) at 16:37Z and confirm the internal inconsistency.**
The self-application failure is the same class ARCHITECT caught against
my P1-A slot #5 (deadlock detector vs CSP row). Twice in one mission I
shipped self-contradicting drafts. RECONSIDERED action:

**Edit 4-bis is demoted to TIER-2 strong-default.** The content is
unchanged (CAS + lock-order); only the tier label moves. Operator
applying the changeset should treat Edit 4-bis as "## File-collision
concurrency architecture (TIER-2 strong default, opt-out path: append-
only logs per ADR-001:72-74 if `CasContentionError` rate >1%)" instead
of "(TIER-1)". The atomic-correctness *requirement* stays in spirit
under TIER-1's RULE-2 (mkdir-atomic) family; the *mechanism* sits at
TIER-2.

### NEW Edit 19 — [MAJOR] Subagent walltime declaration (TIER-2 default)

**FE Issue 2 (MAJOR):** Workers dispatching parallel implementation
subagents (per MISSION.md §"Mission-specific subagent guidance") cannot
heartbeat during subagent execution; if subagent walltime exceeds 15
min, the worker becomes RULE-6-eligible for stale-reclamation despite
making real progress. FE lived this at 16:30Z (P3-D ALIVE-RECOVERY).

**ACK-VERIFIED — I read `STATUS.md:12` (FE row 16:30Z) at 16:37Z and
confirm the failure mode.** I missed this in my P3-A despite living an
analogous 28-min gap at 16:30Z myself. Lane-self-blindness — RECONSIDERED
note in §G "Missed by me" should have caught it.

**Edit 19 (new):**

**Location:** `README.md` TIER-2 section, near `## Subagent budget ≤3
parallel per finding` (`README.md:151-153`).

**Before:** v7 has no walltime declaration rule.

**After (new TIER-2 default):**
```
## Subagent walltime declaration

When dispatching subagents whose expected walltime exceeds 10 min,
the dispatching worker MUST:

(a) Write current intent to `findings/<agent>-<task>.scratch.md`
    BEFORE dispatch, so retroactive recovery (RULE 6) can succeed if
    reclamation happens.
(b) Declare expected walltime in STATUS Notes column (e.g.,
    "deep build — subagents running, walltime ~28min").
(c) RULE 6 reclaimers SHOULD check `claims/<task-id>/` directory mtime
    AND newest file mtime under the worker's target output dir as
    secondary liveness signals (parallel sub-agents' filesystem
    writes are observable even when STATUS heartbeat is blocked).
```

**Rationale:** Two live observations this run (FE P3-D + my own
26.6-min subagent dispatch in tick 8). META P3-F :208 already
documented; FE P4-D→A made the protocol-fix case explicit.

### NEW Edit 20 — [MAJOR] Atomic-completion enforcement RULE 10 self-check (TIER-2)

**FE Issue 3 (MAJOR):** Multiple workers wrote findings to disk BEFORE
completing the other three RULE-10 steps (TASKS / HISTORY / STATUS),
producing transient stale-detection false-positives. Observed in ARCH,
BE, META during this run.

**ACK-VERIFIED — I read `STATUS.md` heartbeats during PHASE-PLAN at
15:33-15:36Z + at 15:46-15:49Z + the relevant lanes' finding files,
and confirm the split-tick pattern recurred ≥3 times.** I noted this
in my own tick-2 STATUS heartbeat at 15:36Z but did not codify a fix.
RECONSIDERED action:

**Edit 20 (new):**

**Location:** `README.md:99-106` (RULE 10 block).

**Before:** RULE 10 says "Splitting these across ticks creates stale-
detection false-positives" but provides no enforcement.

**After (added at end of RULE 10):**
```
## RULE 10 self-check (TIER-2 default)

Before exiting a tick that touched `claims/<task-id>/done`, the worker
SHOULD verify all four steps completed within the same tick:

1. `[ -f claims/<task-id>/done ]` returns yes
2. `grep -q "\[done: <agent-id>" TASKS.md` returns yes
3. `tail -10 HISTORY.md | grep -q <task-id>` returns yes
4. `grep "<lane>" STATUS.md` shows your row as `idle` (or
   `LANE-X-PEER-REVIEWER`) with the current UTC

If any step missed, complete it before exiting. Document the self-
check in STATUS Notes for this tick (one-line: "RULE-10 self-verified
at <utc>"). This is a SHOULD, not a MUST — purely defensive against
the split-tick anti-pattern the rule already warns about.
```

**Rationale:** FE Issue 3's documented observations + my own STATUS
heartbeat at 15:36Z noting "5/6 P1 findings on disk but B/C TASKS-
bracket still [claimed]". Self-check is cheap and idempotent.

### RECONSIDERED — Edit 13 (severity re-tag MAJOR → MINOR)

**FE Issue 4 (MINOR):** Edit 13's NO-RESPONSE is a "trace state"
recording mechanism, not a new TIER-1 invariant or protocol mandate.
The MAJOR label suggests TIER-1/2 enforceability that doesn't match.

**ACK-VERIFIED.** Edit 13 stays in the same location with the same
content; severity tag changes from MAJOR to MINOR. The quantitative
TIER-3 swap is TIER-3 accounting language. **Operator: re-tag Edit 13
as [MINOR] in the changeset header.**

### RECONSIDERED — Edit 17 (citation correction)

**FE Issue 5 (MINOR):** Edit 17 cites "BACKEND Δ4" for SSE envelope
convergence, but BACKEND Δ4 is the `LaneRow` shape definition, not SSE.
BACKEND's SSE treatment is in Δ1 (Signal interface + signal-new event),
keyed on `(from_agent, utc, to, claim_hash)` — utc-as-key, not int-as-key.

**ACK-VERIFIED — I read `findings/agent-8318-C-P2.5-backend-plan-v2-
2026-05-16T15-46Z.md:64-103` (§Δ1) and `:132-153` (§Δ4) at 16:37Z and
confirm.** BACKEND did NOT explicitly accept `id: <int>`; they specified
utc-as-natural-key. RECONSIDERED action:

**Edit 17 text correction:** Replace "FRONTEND C2 :51-72, BACKEND Δ4"
with "FRONTEND P2.5-D §C2 :51-72 (proposes `id: <int>`), BACKEND P2.5-C
§Δ1 :64-103 (proposes utc-as-natural-key)". Reframe the requirement
text from "each event MUST include a monotonically-increasing `id:
<int>`" to:

```
**SSE event-envelope:** each event MUST include a stable natural key.
The canonical choice is `utc:` (per BACKEND P2.5-C §Δ1) — naturally
totally-ordered, no separate counter needed. An optional monotonic
`id: <int>` field MAY be included (per FRONTEND P2.5-D §C2) for
clients that prefer `Last-Event-ID` int semantics.
```

This preserves the FE/BE convergence on natural-key-keyed events
without overclaiming a convergence that didn't strictly exist.

### Confidence on Pass-3

**HIGH** on Issues 1, 4, 5 (purely document-level fixes, cited evidence
sampled). **HIGH** on Issue 2 — I lived the same failure mode in my own
tick 8 26-min subagent dispatch. **HIGH** on Issue 3 — observed across
≥3 lanes; my own STATUS heartbeat at 15:36Z noted it but I didn't
codify. Net: the changeset is materially stronger after Pass-3.

---

## §J — Pass-4: PHASE-RUN+HEAL new-phase candidate (SIG-ORCH#5 — DRAINING phase, 16:48Z)

SIG-ORCH#5 (`HISTORY.md` 16:45Z) surfaced a **MAJOR v8 spec gap**: v7's
PHASE-VERIFY produces code-review findings only, never test execution.
This run is concrete evidence — no worker ran `uv run pytest`, no worker
launched `python ui/server.py`, no worker verified the API serves real
data. Result: the deliverable is documented-broken (CSRF wiring,
mission_dir injection, SSE payload mismatches, RULE 11 step 4 — all
caught by reading code, not running it). Operator inherits non-runnable
code with prose explaining why.

AUDIT lane targeting per SIG-ORCH#5: *"TIER-1 promotion candidate. Add
PHASE-RUN+HEAL to docs/v8-changeset.md as a §"NEW PHASE" entry."*

### NEW Edit 21 — [MAJOR / TIER-1 new-phase candidate] PHASE-RUN+HEAL between PHASE-VERIFY and DRAINING

**Location:** `MISSION.md` §"Phase mechanics" — phase progression line + new section.

**Before:**
```
INIT → PHASE-PLAN → PHASE-CHALLENGE → PHASE-BUILD → PHASE-VERIFY → DRAINING → COMPLETE
```

**After:**
```
INIT → PHASE-PLAN → PHASE-CHALLENGE → PHASE-BUILD → PHASE-VERIFY → PHASE-RUN → DRAINING → COMPLETE
                                                                       ↓
                                                              PHASE-HEAL (iterative)
                                                                       ↑
                                                                  (loops until budget or success)
```

**New `MISSION.md` section after the existing §"Phase progression":**
```
## PHASE-RUN — execution verification (NEW in v8)

PHASE-VERIFY catches defects by reading code; PHASE-RUN catches defects
by executing code. Both are needed. Without PHASE-RUN, code-review can
miss runtime-only defects (env wiring, CLI arg parsing, header
authentication, payload shape mismatches at the network boundary). The
v7 run that produced this v8 changeset is the existence-proof: 4
BLOCKING/MAJOR defects in P4 verifies that would have surfaced
instantly under `python ui/server.py --port N --mission-dir <fixture>`.

### BUILD lanes ship smoke-checks alongside code

Each PHASE-BUILD task carries a `smoke-check` deliverable:

- BACKEND: `make smoke-backend` runs the server against a fixture
  mission dir and asserts the API serves non-empty real data.
- FRONTEND: `make smoke-frontend` uses Playwright headless to render
  the dashboard and assert the rendered DOM matches the fixture.
- TEST: `make smoke-all` chains BACKEND + FRONTEND + pytest unit /
  integration suites.

Smoke-checks are owner-authored (the BUILD lane writes them) but
run by a *different* lane in PHASE-RUN per the no-self-verification
principle (mirrors PHASE-VERIFY pairing matrix).

### PHASE-RUN tasks (auto-claimed via pairing matrix)

- `P5-RUN-BACKEND` — TEST runs BE smoke + integration tests against
  fixture mission directories. Failure → PHASE-HEAL with
  REPAIR-BE-<n>.
- `P5-RUN-FRONTEND` — BACKEND or TEST runs FE smoke E2E. Failure →
  PHASE-HEAL with REPAIR-FE-<n>.
- `P5-RUN-INTEGRATION` — TEST runs the full test pyramid end-to-end
  against fix-medium and fix-medium-failure-modes. Failure →
  PHASE-HEAL with REPAIR-INTEGRATION-<n>.

### PHASE-HEAL — iterative repair (NEW in v8)

Auto-loops when any `P5-RUN-*` fails:

1. The failing run's owner injects `[REPAIR-<task-id>-<n>]` into
   TASKS.md with the failure transcript embedded.
2. The relevant BUILD lane re-opens (state: `working: REPAIR-*`),
   fixes, re-claims `P5-RUN-*` for re-execution.
3. Budget per task: **3 HEAL cycles OR 20-min wall-clock** per
   PHASE-HEAL pass. Exceeding the budget triggers `BLOCKED-DEGRADED`
   state for operator triage — the deliverable goes out
   degraded-but-acknowledged rather than in an infinite repair loop.

### COMPLETE pre-condition tightens (replaces v7's 10-min quiet-period)

v7 COMPLETE flips on: all lanes idle + META FINAL-RUN-CAPSTONE exists
+ 10-min HISTORY quiet. v8 tightens to:

```
All P5-RUN-* completed with status in {EXEC-PASS, EXEC-DEGRADED-
OPERATOR-ACKED}.
```

Operator inherits either runnable code or explicit-operator-acknowledged
degradation. No "documented-broken" deliverables.

### Settings.json allowlist requirement (cross-references SIG-ORCH#4)

PHASE-RUN requires worker-side execution of smoke-check commands. The
pre-kickoff allowlist (§H Deployment Affordances) MUST include the
exact commands BUILD lanes' smoke-checks invoke. Generic-safe
additions for any mission with a server build:

- `python <project>/server.py *` (server launch)
- `python -c "import urllib.request as u; ..."` (curl-equivalent
  HTTP probe, since `curl` is denied)
- `uv run pytest *` (test execution)
- `npx playwright test *` (E2E)
- `make smoke-*` (if the project uses Make)

Specific commands are MISSION.md-derivable from the BUILD-lane
deliverables. Orchestrator extracts them at kickoff per the
Deployment Manifest in §H.
```

**Rationale:** SIG-ORCH#5 evidence is exactly the v7 protocol-defect
class that justifies a TIER-1 new-phase addition: code-review-only
verification is *not* execution-verification; the protocol promised
"verify" but delivered "review." All 4 BLOCKING/MAJOR P4 defects
found this run would have surfaced as smoke-check failures within
seconds of `python ui/server.py` exit. The mid-run RR-1 task
(BACKEND+TEST) is the operator's recovery; v8 codifies it as a
default phase so future runs don't depend on operator vigilance.

**Self-application against §D Promotion bar:** ≥1-run evidence
(this run only) — strictly below §D's TIER-1 bar of ≥3 runs. BUT:
PHASE-RUN is invariant-class (execution verification is an atomicity
property of the deliverable: the build either runs against a fixture
or it doesn't), AND the orchestrator explicitly flagged this as
TIER-1 in SIG-ORCH#5's lane-targeting. I'm marking Edit 21 as
TIER-1 candidate **with a SHOULD-PROMOTE-IF-CONFIRMED-IN-NEXT-RUN
qualifier** — operator may choose TIER-2 strong-default with
opt-out path for v8.0, then promote to TIER-1 in v8.1 after a
second mission confirms.

This same self-application concern applied to Edit 4-bis (Pass-3
demoted TIER-1→TIER-2 per FE Issue 1). Edit 21 has stronger
TIER-1 case than Edit 4-bis because the new-phase entry is
*by construction* invariant-class (a phase is in the protocol or
not), whereas a mechanism (CAS) is tactic-class. But conservative
operator may apply same single-run demotion logic — both
positions are defensible.

### Convergence with other §s

- **§H Deployment Affordances:** PHASE-RUN's allowlist requirement
  is a concrete consumer of §H's "Settings.json allowlist by
  expected work" item. The two sections share the
  "pre-kickoff orchestration hygiene" theme.
- **Edit 4-bis (CAS + lock-order):** PHASE-RUN's smoke-checks would
  catch CAS misconfiguration at runtime (`STALE_READ` retry-loops
  exceeding budget would surface as test failures).
- **Edit 14 (RULE 11 step 4a stuck-flip recovery):** PHASE-RUN's
  fixture tests could include "synthesized stuck-flip" scenarios
  that exercise the recovery path — a direct test of an Edit-14-
  era v8 rule.

### Confidence on Edit 21

**HIGH on the diagnosis** (SIG-ORCH#5 evidence + 4 P4 BLOCKING/MAJOR
findings are existence proof). **HIGH on the proposed structure**
(PHASE-RUN+HEAL with 3-cycle / 20-min budget; structure mirrors
existing PHASE-CHALLENGE / PHASE-VERIFY cadence). **MEDIUM on
TIER-1 promotion** per the self-application caveat above. **HIGH on
the cross-reference value** to §H and Edits 4-bis / 14. **Net: this
is the most consequential v8 change in the changeset; deserves a
distinct §"NEW PHASE" header that an operator can apply atomically.**

---

## Confidence (overall)

**HIGH** on Edits 1, 2, 3, 6, 11 — multiple sources of converging
evidence (plan-v2 + S-4 + SIG-ORCH or BLOCKING quorum). **HIGH** on
Edits 4, 8, 12 (single-source but the source is explicit orchestrator
signal or my own already-quorumed plan slot). **MEDIUM** on Edits 5,
7, 9 (single-run archive evidence; recurrence prediction not yet
verified in this run). **MEDIUM** on Edit 10 (taxonomy is judgment-
heavy). Pass-2 sharpened MEDIUM items via 6 new edits (13-18); Pass-3
fixed two TIER-mislabels (Edit 4-bis demotion, Edit 13 re-tag) +
added Edits 19, 20 from FE's observation-gap critique + corrected
Edit 17 citation. Pass-4 added Edit 21 (PHASE-RUN+HEAL new-phase)
per SIG-ORCH#5. **21 edits final.**
