---
title: Megalodon v9 — Implementation Roadmap
version: 1.2
status: IMPLEMENTATION-READY — Codex contrarian review complete (see "Codex contrarian review applied" section + Document control)
lineage: synthesized from run-2 v8.1-candidate ledger (~47 items) + orchestrator bottleneck analysis + operator architectural directives
utc: 2026-05-16T21:33Z (v1 draft) / 2026-05-16T21:43Z (v1.1 self-contrarian) / 2026-05-16T21:54Z (v1.2 post-Codex synthesis)
codex-review-required: COMPLETE (per QUEUE-DESIGN.md §10; 7 findings, 6 ACCEPT + 1 ACKNOWLEDGE; impulse-tier, no 2nd pass)
implementation-gate: OPEN — begin at Migration plan §3 (helper scripts first)
---

# Megalodon v9 — Implementation Roadmap

## Executive summary

v9 is a **major version** that subsumes the v8.1-candidate stack (~47 items harvested across run-1 + run-2) plus orchestrator-identified bottleneck fixes. It targets three classes of problem run-2 exposed:

1. **Untested contract surface** caused 4 cascading-HEAL cycles (StaticFiles → SPA routes → api-fields → `/api/v1/state` endpoint, each unmasking the next). ~70% of run-2 wall-clock was preventable.
2. **Tool-discipline regression** caused a 17-min "simultaneous-silence" event (workers blocked on permission prompts while operator AFK). Python heredocs and compound bash trigger approval prompts that stall mission.
3. **Observation discipline gaps** caused 5-LANE convergence on a wrong root-cause; only orchestrator ground-truth-override (via SIGNAL) broke the consensus.

v9 introduces 6 marquee changes + 10 architectural improvements + helper-script + spec-grammar updates, all designed to make run-3 reach OPERATOR-ACK in a single PHASE-RUN cycle.

**Scope discipline**: v8.1 was originally planned as a point release; collapsed into v9 per operator directive. v9 is the only protocol version between v8 and v10.

---

## Background — what run-2 taught us

Run-2 ran from 17:30Z to ~21:55Z (deadline). Key data points:

- **Tests**: 23 SKIPS in run-1 → 25 PASS + 1 XFAIL (unit/integration) + 3-13 PASS / 13-3 FAIL (e2e mutations, asymptotic).
- **UI render**: never browser-tested in run-1 → screenshot artifact landed in run-2 (`agent-2e7a-D-P5-RUN-ui-render-2026-05-16T20-10Z.png`, 41 KB, 1280×720, 6 lane rows, 0 console errors via `/static/index.html`).
- **HEAL cycles**: 3 cycles consumed; mission paid for cascading discoveries.
- **CAS contention**: ~79% retry rate at /loop 3m; reduced to ~45% projected at /loop 5m (SIG-ORCH-5 mid-mission cadence change).
- **REPAIRs shipped**: 6 in HEAL-1+2 (BE: SSE, SPA-CATCHALL, STATUS-VIEW; FE: 1-FE, ACTION-PANEL; TEST: FIXTURE-OVERRIDE), 4+ pending in HEAL-3.
- **Protocol self-improvement**: 47 v8.1 candidates harvested; 6 marquee firm; ARCH shipped 3 SPEC-FIRST HEAL addenda (§3-bis SSE, §3-ter SPA, §3-quater api-contract) — the protocol generated its own normative refinements live.

The dominant pattern: **CASCADING-HEAL** (OBS-RUN-9). Each fix unmasked the next gap because the factory `make_app` was built without integration tests exercising the full FE consumer surface. Workers grep'd consumer call-sites individually; missed others systematically.

---

## Marquee changes

### M1. Queue-based write serialization (BLOCKING)

**Reference**: full design in `docs/v9/QUEUE-DESIGN.md`; implementation skeleton in `docs/v9/queue/applier.py` + `docs/v9/queue/queue_client.py`.

**What**: singleton applier daemon drains a queue of write intents against shared-state files (STATUS.md, TASKS.md, HISTORY.md, .mission-events, claim dirs). Per-file fcntl.LOCK_EX. Idempotency via journal.log. Crash recovery via journal replay.

**Why**: SIG-ORCH-1 observed 79-83% CAS retry rate at 6-lane /loop 3m. Queue eliminates contention entirely.

**Reads stay direct** (Option A per operator decision 2026-05-16T21:26Z): plain Read tool for observational reads; `fcntl.LOCK_SH` wrapper (via `queue_client.read_consistent(path)`) for correctness-bearing reads. **No queue-scope expansion for reads.** Linearizability deferred; sequential consistency under write barriers is the actual property workers need.

**Outstanding bugs from META S-8 audit (`findings/agent-9bba-CROSS-S8-queue-design-audit-2026-05-16T19-12Z.md`)**: B1 BLOCKING-UTC handling, B2 MAJOR append-WAL semantics, B3 applier-heartbeat, B4 claim-steal. Must address before code lands.

**Legacy claim migration (per Codex CR-6)**: ship `scripts/migrate_claims_to_owner_txt.py` as part of M1 work. Backfills `owner.txt` for all pre-v9 claim directories using best-effort attribution from STATUS.md history (or `legacy-pre-v9` if unknown). Run once during v8→v9 cutover. Fixtures regenerated with owner.txt as part of v9 fixture refresh. Applier `CLAIM_DIR_DONE` rejection stays strict — clean state via migration, no `legacy_mode` flag.

**Open questions for Codex**: applier failure mode (crash mid-drain); fence intent (option B from read-architecture discussion) for PHASE-FLIP coherence; cross-file ordering when 2 intents target related files (TASKS+HISTORY atomic).

---

### M1.5. Migrate UI mutation endpoints to queue_client (per Codex CR-1)

**What**: the queue serializes shared-state writes from worker lanes, but the UI server itself also writes shared state directly. Without migrating UI endpoints, two write paths persist and CAS contention survives.

Specific endpoints requiring migration:
- `megalodon_ui/server.py:324-337` — TASKS.md direct write → `queue_client.tasks_bracket()`
- `megalodon_ui/server.py:367-400` — STATUS.md direct write → `queue_client.status_update()`
- `megalodon_ui/server.py:537-565` — README.md + TASKS.md direct write → `queue_client.tasks_bracket()` + new `queue_client.readme_update()` intent type
- `ui/server.py:1178-1215` — STATUS.md + TASKS.md + claim-dir mutations → covered by M1.6 unification (legacy deprecated)

**Why**: Codex CR-1 caught this — without M1.5, queue migration is partial; CAS contention and lost-update races survive. M1.5 is **prerequisite** for M1 to actually deliver write serialization.

**Implementation**: ~80 LOC across the 4 endpoints. Each endpoint's direct file-write replaced with queue_client call + idempotency_key derived from request UUID. Returns 202 Accepted with intent-status URL (instead of 200 + committed body).

**Open questions for Codex 2nd-pass**: should UI POST endpoints become async (returning 202 with poll URL) or stay synchronous (blocking on queue apply)? What's the operator-facing latency cost?

### M1.6. Backend unification — factory canonical (per Codex CR-2)

**What**: deprecate `ui/server.py` legacy entry; factory `megalodon_ui` becomes canonical backend. All response shapes follow factory grammar.

**Why**: Codex CR-2 caught the dual-target problem: `ui/server.py:927-936` returns `{tasks: [...]}`; `megalodon_ui/server.py:435-439` returns `{phases: [...]}`. MISSION.md:20 validates against legacy; e2e validates against factory. Contract scan against either alone is insufficient.

**Implementation**:
- Rewrite `ui/server.py` as thin shim: `from megalodon_ui import make_app; uvicorn.run(make_app(...))`. Preserves operator habit of `python ui/server.py` invocation.
- All API endpoints, response shapes, SSE event types defined ONLY in factory. Legacy file contains zero business logic.
- MISSION.md run-3 updated to validate against `python -m megalodon_ui` OR the shim — both invocations exercise factory code.
- `docs/v9/api-contract.md` (see M2) describes ONLY factory grammar; legacy shapes are out-of-scope post-v9.

**Open questions for Codex 2nd-pass**: rollback strategy if factory has undiscovered regressions vs legacy at v9 cutover? Should we keep legacy as feature-flagged fallback for 1 release?

### M2. PRE-VERIFY contract scan in P3

**What**: before any PHASE-VERIFY claim closes, run a contract scan that exercises every FE→BE call:
- Parse `*.js` for `fetch(/api/...)` / `apiGet()` / `apiPost()` invocations
- Assert each route exists in BE factory (HTTP 200 + correct content-type)
- Assert response shape contains every field FE consumes (parsed from `.field` access patterns)
- Assert StaticFiles mount + SPA route catch-all exist
- Fail P3 if any contract surface untested

**Why**: 4 cascading-HEAL cycles in run-2 = 4 untested contract surfaces (StaticFiles, SPA routes, staleness fields, /api/v1/state endpoint). Each cost ~10-15 min HEAL. Contract scan catches all 4 in 30 seconds at P3 close.

**Implementation** (revised per Codex CR-3 + CR-7): `scripts/contract_scan.py` runs in P3 verify step. ~80 LOC. **Source-of-truth document approach** — no JS AST parsing, no new dependencies:

1. **`docs/v9/api-contract.md`** is THE single source of truth, enumerating all routes + response shapes + their FE consumer functions. Lives in the repo; treated as a normative spec (TIER-1, like SPEC-v2).
2. **BE schema-validates at startup**: factory `make_app()` reads `api-contract.md` and asserts every declared route is registered + every response handler returns the declared shape (via Pydantic schema or equivalent). Startup fails if mismatch.
3. **FE runtime instrumentation**: in P3 test mode, install a `fetch()` wrapper that logs every URL hit during a smoke-test page render. Cross-check against `api-contract.md`: any fetch to undocumented URL → contract scan FAIL.
4. **Helper indirection caught transparently**: `postAction(url, body)` at `mission.js:121-128` calls `fetch(url)`; the wrapper intercepts the actual fetch, so helper indirection doesn't hide URLs from instrumentation. Codex CR-3 motivated this exact substitution.

Outputs JSON `{contracts: [{endpoint, status, source: contract_md|runtime}], undocumented_fetches: [...], schema_mismatches: [...], pass: bool}`. Any non-empty `undocumented_fetches` or `schema_mismatches` blocks P3 close.

**Open questions for Codex 2nd-pass**: ownership of `api-contract.md` (who updates it during BE changes — BE lane, ARCH lane, dedicated PR review?); how to handle legitimate dynamic-path patterns like `/api/v1/findings/${id}` (declare path-with-params in contract doc; runtime wrapper normalizes paths to template before comparison).

### M3. Helper scripts + grammar update (ban heredocs, ban compound bash)

**What**: ship 3 single-pattern Python scripts that workers invoke directly:

1. `scripts/atomic_close.py --task <id> --lane <X> --notes "..."` — does RULE-10 four-step (touch done + TASKS bracket + HISTORY append + STATUS update) with proper fcntl locking on shared files.
2. `scripts/poll.py [--brief|--full]` — canonical state read: `.mission-events` tail + STATUS row counts + claims/ summary + recent findings list. Returns one JSON document for the agent to parse.
3. `scripts/run_e2e.sh` — canonical playwright invocation with proper env vars + config path + redirect. Avoids `cd && cmd` compound that triggered TEST's 21:22Z permission prompt.

Operator allowlists `python3 scripts/atomic_close.py *` + `python3 scripts/poll.py *` + `./scripts/run_e2e.sh *` once. **Scripts internally validate ALL args against strict whitelist patterns** (per self-contrarian OW-5 fix + Codex CR-4 regex broadening) — protects against arbitrary-arg exploitation of the wildcard allowlist:
- `--task` must match `^(P\d+(\.\d+)?(-[A-F](-to-[A-F])?)?|P\d+-RUN-[A-Z0-9_-]+|REPAIR-[A-Z0-9_-]+|OPERATOR-[A-Z_-]+|S-\d+)$` — covers `P1-A`, `P2.5-B`, `P2-A-to-F`, `P5-RUN-MUTATIONS-E2E`, `REPAIR-MUTATIONS-E2E-3-ACTION-PANEL`, `OPERATOR-ACCEPTANCE-REQUEST`, `S-8` (per Codex CR-4 inventory grep of `TASKS.md`)
- `--lane` must match `^(AUDIT|ARCHITECT|BACKEND|FRONTEND|TEST|META)$`
- `--notes` is the only free-form arg; length-capped at 2000 chars; no shell-metacharacters except `_,.-/()`
- Reject any other arg pattern with exit code 2 + stderr explanation. Workers never reinvent.

**Playwright config cleanup (per Codex CR-5, ACKNOWLEDGE-class)**: `ui/tests/e2e/playwright.config.ts:45-58` uses `cd /abs/path && uv run ...` in webServer config. This runs inside Playwright's subprocess.spawn — NOT subject to Claude Code worker permission gating, so it didn't cause run-2's silence event. But unhygienic. Clean up as part of M3 code-quality work: switch to `--config` flag + env-var cwd pattern. Not blocking; ship M3 helper scripts first.

**launch.md §5 grammar update** (mandatory):
- For RULE-10 close: use `scripts/atomic_close.py` or Edit/Write tool calls. **NEVER** `python3 <<'PYEOF'` heredocs. (Heredocs for non-CAS atomic-close are over-engineered for lane-prefixed REPAIRs.)
- For polling: use `scripts/poll.py` or parallel single-purpose tool calls. **NEVER** `cmd1 && cmd2 && for ...; do ...; done` compounds. (Triggers permission gate when operator AFK; causes simultaneous-silence pattern.)
- For Python+fcntl: reserve ONLY for cross-lane CAS writes where parallel writers race the same row (mostly: STATUS heartbeats during contended phase-flip, .mission-events appends during flip-win race). Lane-prefixed REPAIRs have zero race risk → Edit suffices.

**Why**: SIG-ORCH-6 (filed 21:21Z) revealed the "MULTI-LANE-SIMULTANEOUS-SILENCE" event was tool-prompt-blocking, not cron-infra. AUDIT iterated 4 false-positive root cause classifications before operator ground-truth override broke the consensus.

**Open questions for Codex**: should `atomic_close.py` be the only sanctioned RULE-10 path (forbid Edit-based closes) or coexist? How to enforce launch.md grammar — pre-commit hook? CI lint? Trust-on-honor with peer audit?

### M4. Shared constants registry

**What**: single source of truth for IDs/keys/event-names referenced by both FE and BE:
- `megalodon_ui/constants.py` — Python constants exported by BE
- `ui/static/js/constants.js` — JS constants imported by FE (generated from Python at build time, or fetched at runtime via `/api/v1/config`)

Includes: localStorage keys (`CONTROL_MODE_KEY`), data-testid prefixes, SSE event names, API path constants, RULE-1 stale threshold (900s), default ports.

**Why**: REPAIR-3 root cause in run-2 was a 28-char string disagreement at `store.js:17` — `CONTROL_MODE_KEY` was `'megalodon-control-mode'`, tests expected `'controlMode'`. 6 e2e tests failed because of one constant. Constants registry prevents this entire class.

**Implementation**: ~30 LOC of Python constants + 30 LOC of generated JS (or runtime fetch). Build-time codegen via `scripts/gen_js_constants.py`.

**Open questions for Codex**: build-time codegen vs runtime fetch (codegen catches errors earlier but adds build step); whether constants should also include CSS color tokens (probably yes — UI consistency); migration path for existing 50+ string literals scattered across `ui/static/js/*.js`.

### M5. PRE-CLASSIFY INVARIANTS as mandatory launch.md grammar (Edit-42 codification)

**What**: add explicit observation discipline to launch.md §6:
- **Liveness check before classifying any artifact**: `stat -f "%m %z" <path>` + size growing across 2 ticks → "in-flight, do not classify yet"
- **Wait for completion signals**: `done` marker file OR mtime stable >60s OR finding-written-with-frontmatter
- **PRE-CLASSIFY checklist** (per META-OBS-18, AUDIT 4-LANE convergence, 2026-05-16T20:30Z+):
  - (a) liveness check
  - (b) baseline-invariants check (does this match known patterns?)
  - (c) uniformity check (if N items fail same way, suspect upstream invariant not per-item bug)
  - (d) lane-bias check (am I over-attributing to my lane's known classification bias?)
- **Three cause classes** (per META-OBS-34 refinement): INFRASTRUCTURE-FAILURE, BEHAVIORAL, APPLICATION-LAYER-DISCIPLINE. Most consensus errors come from misattributing application-layer-discipline as infrastructure or behavioral.
- **Convergence-can-be-wrong caveat** (META-OBS-35): N-LANE consensus is necessary but not sufficient for empirical-fact claims. Normative-protocol claims (cycle-semantics) are more reliable than empirical-fact claims (cron infra failed).

**Why**: AUDIT alone hit observation-misclassification 4 times in run-2. 5-LANE convergence reached wrong root-cause (cron-infra) and required operator SIG override.

**Open questions for Codex**: should observation discipline be enforced by tooling (auto-warn if reading a file with recent mtime) or trust-on-honor; whether the 3 cause classes are exhaustive or need extension.

### M6. INTENT-DECLARED → INTENT-EXPIRED timeout + cross-lane reclaim path

**What**: 
- When a lane declares intent to claim a REPAIR (e.g., "BE will claim REPAIR-5 on next tick"), the declaration is timestamped in STATUS row.
- If 12 minutes pass without actual claim materialization (no `mkdir claims/X/` + no TASKS bracket flip), the intent EXPIRES.
- After expiry: peers (per task-assignment matrix) may claim freely without RULE-6 ceremony.
- HEAL stale-row ESCALATE-VIA-SIGNAL path (META-OBS-31): when a HEAL REPAIR's owning lane is silent past stale threshold, observer lane files a SIGNAL to operator (not auto-reclaim, since observer lanes can't do code work).

**Why**: run-2 had a 17-22 min gap between "I'll claim immediately" (BE 20:38Z, FE 20:51Z) and actual claims (21:14Z, 21:15Z). Caused HEAL-2 budget pressure and the simultaneous-silence event.

**Implementation**: STATUS row `Notes` gets optional `intent-declared: <task-id> @ <utc> walltime: <Nm>` field. Expiry threshold = `max(12 min, declared-walltime + 5 min slack)` per self-contrarian OW-3 fix. If worker declared `walltime: 20m` for the work, expiry only triggers at 25 min. **Workers MUST emit periodic heartbeat-ACK every 5 min during long-walltime work** (updates STATUS row `Last UTC` even if no other progress to report); missing 2 consecutive heartbeats triggers expiry regardless of walltime. Atomic claim by any eligible lane (per task-assignment matrix) after expiry. ~30 LOC change to RULE-6 reclaim logic.

**Open questions for Codex**: how to enforce heartbeat-ACK on long-walltime work (trust-on-honor vs watchdog-injected reminder)? Cross-lane vs same-lane reclaim — who has priority? Behavior when walltime estimate was wildly wrong (e.g., declared 5min, actually 60min — should peers reclaim or wait)?

---

## Architectural improvements (operator-directed)

### A1. Watchdog daemon for autonomous worker respawn

**What**: a long-running watchdog process polls each worker's STATUS row + JSONL session log. On detection of (a) crashed session, (b) silent past N min, (c) context-near-limit, the watchdog respawns the worker session (spawns `claude` with `read launch.md` prompt) per launch.md §7.

**Why**: workers can't self-compact (Claude Code CLI restriction — `/compact` is operator-only); but can crash or stall. Watchdog automates recovery so operator doesn't have to babysit terminals.

**Scope (v9, per self-contrarian OW-4)**: ship crash + silent detection only. **Context-window monitoring deferred to v10** — requires Claude Code telemetry exposure that doesn't currently exist. Three signals trigger watchdog action:
- (a) Process death: `pgrep` doesn't find the Claude Code session.
- (b) STATUS row mtime stale: lane's `Last UTC` is `>= max(15 min, /loop cadence × 3)` ago.
- (c) JSONL session log mtime stale: no writes to `~/.claude/projects/<path>/<session>.jsonl` for `>= 5 min` despite STATUS row being newer (worker may have hung mid-tool-call).

Action: write a SIGNAL finding to `findings/watchdog-ALERT-<utc>.md` addressed to operator. Watchdog does NOT auto-respawn (too risky — could spawn during legitimate deep-thinking). Operator decides whether to respawn manually.

**Implementation**: ~120 LOC Python daemon. Maintains `.scratch/watchdog/state.json` with per-lane health. Polls every 60 seconds.

**Open questions for Codex**: should watchdog be best-effort or strict (kill-and-respawn on stale-detect)? How to distinguish "deep-thinking" (legitimate tool-call latency) from "stuck" without context-window data? Should watchdog observe SIGNAL findings count (rising rate may indicate prompt-blocking)?

### A2. Per-lane launch files with pre-bound model

**What**: instead of 6 workers all reading `launch.md` and self-claiming a lane, ship 6 lane-specific launch files: `launch-AUDIT.md`, `launch-ARCHITECT.md`, etc. Each pre-binds the agent to its lane + model choice + cadence + role-specific guidance.

**Why**: operator pre-flight (selected model/provider based on capability+cost per user 2026-05-16T19:48Z discussion) becomes part of the launch. Eliminates self-claim race condition. Customizes prompt to lane's specific role (observer vs builder vs tester).

**Implementation**: 6 markdown files. Plus a `scripts/launch_fleet.sh` that opens 6 terminals with `claude --model X "read launch-LANE.md"` invocations. ~50 LOC of shell + 6×100 lines markdown.

**Open questions for Codex**: how to handle a lane crashing (do we re-launch with same file?); how to introduce a 7th observer lane (CROSS) — does that need its own launch file too?

### A3. Fleet matrix (cross-provider load balancing)

**What**: orchestrator pre-flight picks model/provider per lane based on capability + cost. Per-lane fleet ledger tracks performance. Run-to-run feedback adjusts assignments.

**Constraint** (per operator 2026-05-16T19:50Z): "Claude is highest usage available, with Codex second, and Gemini 3rd but has 24h cooldowns on usage." Mistral and Cursor for lower-tier tasks.

**Implementation**: `docs/v9/fleet-matrix.md` lists capability+cost per provider+model; `scripts/fleet_select.py` outputs lane→model mapping; per-lane launch files read this. Performance ledger at `.fleet-ledger/<utc>--run-N/<lane>-perf.json` tracks tokens-spent + tasks-completed + REPAIR-injections.

**Open questions for Codex**: how to handle quota exhaustion mid-mission (Gemini 24h cooldown); fallback chain if primary provider down; whether all lanes always need the same model class (probably not — observer lanes can be Sonnet, builders Opus).

### A4. Deterministic agent-IDs

**What**: replace `secrets.token_hex(2)` random IDs with deterministic hash of (mission-id, lane, launch-utc). Same lane in same mission always gets same ID, even across crash+respawn.

**Why**: simpler audit trail; recovery (per launch.md §7) doesn't need to re-derive ID from old STATUS row; deterministic agent → reproducible debugging.

**Implementation**: 5-LOC change in launch.md step 2.

**Open questions for Codex**: what if same lane respawns twice in one mission (crashed agent recovered + new agent joined)? Suffix with attempt counter?

### A5. Terminal title via ANSI escape (option 3 per operator 2026-05-16T19:14Z)

**What**: workers print `\033]0;<lane>:<agent-id>:<phase>\007` at each heartbeat. Terminal title shows lane + agent + current phase at a glance.

**Implementation**: 1 line in launch.md §5 heartbeat step. ~3 LOC if wrapped in helper.

**Open questions for Codex**: should phase be live-updated or only on phase-flip? Compatibility with non-iTerm/Terminal.app harnesses (e.g., Linux gnome-terminal — should work, but verify).

### A6. Lane tick-offset staggering (45s gaps)

**What**: instead of all 6 lanes ticking simultaneously, stagger by 45s: AUDIT @0s, ARCHITECT @45s, BACKEND @90s, FRONTEND @135s, TEST @180s, META @225s. Reduces aligned-tick contention.

**Why**: SIG-ORCH-5 cadence change reduced contention 40%; further 40% projected from staggering.

**Held for v9** (per operator 2026-05-16T19:08Z) pending Codex/external review.

**Implementation**: per-lane launch file specifies stagger offset; first `/loop` tick fires after sleep N seconds.

**Open questions for Codex**: should staggering interact with /loop cadence (e.g., 45s offset only sensible if cadence ≥ 270s)? How to re-stagger after a lane crashes and respawns mid-mission?

### A7. Per-lane configurable cadence

**What**: AUDIT/META observer lanes might run /loop 7m (lower-frequency observation); BACKEND/FRONTEND/TEST might run /loop 3m during PHASE-BUILD active development; PHASE-RUN might want /loop 1m during execution. Static one-size cadence (5m) is suboptimal.

**Why**: SIG-ORCH-5 surfaced the principle. Run-2 ran all lanes at /loop 5m which over-paced observers and under-paced runners.

**Implementation**: launch.md §5 takes cadence per-lane-per-phase from `.scratch/cadence-matrix.json` (orchestrator-set during pre-flight). Workers re-arm /loop on phase-flip.

**Open questions for Codex**: who's authoritative — pre-flight matrix or live operator SIGNAL? Behavior when cadence change arrives mid-tick.

### A8. Mid-mission SIGNAL grammar

**What**: codify the SIGNAL convention. Run-2 had SIG-ORCH-1 through SIG-ORCH-6 evolving organically. v9 should specify SIGNAL frontmatter (severity, addressed-to, expected-ACK-pattern, related-findings), SIGNAL routing (operator → workers, worker → operator, peer → peer), and SIGNAL idempotency (re-issued SIGNALs should not double-process).

**Implementation**: `docs/v9/SIGNAL-GRAMMAR.md` ~100 lines. Plus minor `launch.md` §6 reference.

**Open questions for Codex**: should SIGNALs go through the queue (option B fence-like) or stay as filesystem findings? How does this interact with INTENT-EXPIRED?

### A9. Fleet performance ledger

**What** (per self-contrarian OW-6 — split worker-observable vs operator-observable):

**Worker-side ledger** (workers append `.fleet-ledger/<utc>--<lane>-tick-<N>.json` at each tick): tasks completed, walltime per task, CAS retry count, REPAIR injections received, SIGNAL ACK latency. All metrics the worker can observe from inside its own session.

**Operator-side ledger** (post-mission via `scripts/parse_session_tokens.py`): tokens spent, model used, session duration, cost estimate. Parsed from `~/.claude/projects/<path>/<session-id>.jsonl` files which Claude Code writes externally. Workers do NOT track tokens themselves — they have no API to read their own token count.

Aggregator merges both into `runs/<mission-id>/fleet-perf.json` post-mission.

**Why**: informs A3 fleet matrix decisions; identifies degraded providers; supports operator cost tracking.

**Implementation**: ~80 LOC worker-side + ~100 LOC operator-side JSONL parser. Post-mission aggregator ~50 LOC.

**Open questions for Codex**: should JSONL parser run as part of mission archival or as on-demand operator command? Whether to expose ledger live (real-time dashboard) or aggregate at mission-end.

### A10. Subagent-routed heavy reasoning

**What**: when a worker hits a heavy-reasoning task (e.g., META synthesis, ARCH spec drafting, AUDIT cross-lane verification), dispatch via RULE-9 subagent rather than doing inline. Keeps main context clean.

**Why**: discussed as replacement for self-compact (which agents can't do). Subagents return concise summaries; main worker stays focused on lane work.

**Implementation**: launch.md §6 update specifying when to use subagent vs inline. No code changes — workers already have subagent capability.

**Open questions for Codex**: criteria for "heavy" — token threshold, time threshold, complexity threshold? Risk that subagent dispatch itself becomes a new cascading discipline.

---

## Minor v8.1 candidates (deferred to appendix)

The ~32 minor candidates from the v8.1-candidate ledger that don't merit marquee status. Will be folded into v9 launch.md, queue, or grammar updates without dedicated sections. Highlights:

- OBS-RUN-13 phantom-claims-dir hygiene (validate claim dir names against regex)
- META-OBS-19 RULE-10 surface load-bearing analysis (TASKS+HISTORY load-bearing; mkdir+done local-convenience)
- META-OBS-22 phantom claim-dir validation
- META-OBS-29 conditional REPAIR injection grammar
- META-OBS-30 mid-cycle REPAIR re-ownership (LANE-X→LANE-Y transfer)
- META-OBS-32 lane-bias-awareness in observation
- ARCH OBS-RUN-11 SPEC-FIRST HEAL (formalize ARCHITECT mid-HEAL SPEC addenda)
- OBS-RUN-15c naming-collision resolution (lane-prefix observation IDs)
- TEST OBS ALL-OR-NOTHING pass/fail count obscures HEAL progress (need per-step assertion-progression metric)
- Edit-43 load-bearing-minimum (TASKS+HISTORY only) for single-lane-prefix HEAL claims
- Edit-21 explicit cycle-semantics (verify=cycle-N, residuals=cycle-N+1, AGGREGATE budget, 3-cycle cap on residual cycles only)
- Edit-21 HEAL budget-extension protocol for verify-phase walltime

Full ledger: `findings/*v8.1-candidate*.md` and individual finding files with `severity: MAJOR` or `severity: TIER-1`.

---

## Migration plan: v8 → v9

**Approach**: clean cut, not gradual. v8 is archived; v9 ships as new launch.md + new docs/ + new scripts/.

**Step 1**: Codex contrarian review of this roadmap (this gate is BLOCKING). **COMPLETE @2026-05-16T21:54Z** — see "Codex contrarian review applied" section below; full synthesis at `~/Documents/Projects/.plans/megalodon/v9-roadmap-2026-05-16-synthesis.md`.

**Step 2**: address Codex blockers. Update roadmap. Possibly second Codex review if blockers are substantive. **COMPLETE** — 6 ACCEPT findings applied inline (M1.5, M1.6, M2 revision, M3 regex broadening, M1 migration script); 1 ACKNOWLEDGE noted in M3. No 2nd pass required (impulse tier, no blockers escalated).

**Step 3** (NEXT — implementation begins here): implement in dependency order:
- (3a) Helper scripts (`atomic_close.py`, `poll.py`, `run_e2e.sh`) — operationally cheap, immediately useful
- (3b) Shared constants registry — unblocks contract scan
- (3c) Contract scan — unblocks reliable P3 close
- (3d) Queue + applier + queue_client — addresses CAS contention; addresses META S-8 bugs B1-B4
- (3e) Watchdog daemon — addresses worker recovery
- (3f) Per-lane launch files + fleet matrix + deterministic IDs + terminal titles — pre-flight machinery
- (3g) Lane tick-offset staggering + per-lane cadence — performance optimization, ship last
- (3h) PRE-CLASSIFY INVARIANTS launch.md grammar — discipline codification
- (3i) INTENT-EXPIRED timeout — coordination grammar
- (3j) Fleet performance ledger — observability
- (3k) SIGNAL grammar doc

**Step 4**: migration test — synthetic mission against fix-medium fixture (existing). Must hit OPERATOR-ACK in single PHASE-RUN cycle.

**Step 5**: run-3 — real "make-it-work" mission with v9 protocol. Targets cleanup of run-2 residuals (e2e fixes that didn't land + 7 missing legacy /api/v1/* GETs).

**Step 6**: BIG documentation update (per operator directive 2026-05-16T20:45Z). README.md v8→v9 with new rules. HISTORY.md major entry. Update launch.md fully.

**Estimated implementation time**: 8-12 hours of focused orchestrator work across all marquee + architectural items, assuming Codex review yields no major redesigns.

---

## Out-of-scope for v9

To keep v9 reviewable in finite time:

- **Multi-mission orchestration**: v9 is single-mission. Cross-mission state (e.g., run-2 archive → run-3 ingest) handled by file system, not new protocol.
- **Web UI for orchestrator**: orchestrator stays CLI/SIGNAL-based.
- **Distributed across machines**: v9 assumes single-host. Multi-host queue + watchdog deferred to v10.
- **Provider failover automation**: A3 fleet matrix is **pre-flight** assignment only; mid-mission provider switch deferred.
- **Beyond 6 lanes**: lane count stays 6 (AUDIT/ARCH/BE/FE/TEST/META). 7th observer/CROSS lane discussed but not in v9 scope.
- **Migrating the 7 missing legacy /api/v1/* GETs** that BE discovered but didn't fix in run-2 — that's a run-3 concrete task, not a v9 protocol item.
- **Self-compact / agent context reset** — Claude Code CLI doesn't support this; v9 routes around it via watchdog respawn (A1) + subagent reasoning (A10).

---

## Pre-implementation gate (MANDATORY) — SATISFIED

Per `docs/v9/QUEUE-DESIGN.md` §10 and operator directive 2026-05-16T19:02Z:

> A **contrarian review by Codex** (different provider, different training distribution) must complete before any code lands. This applies to the entire v9 plan, not just the queue.

**Status**: SATISFIED @2026-05-16T21:54Z. See "Codex contrarian review applied" section below for findings + applied changes. Review artifacts at `~/Documents/Projects/.plans/megalodon/v9-roadmap-2026-05-16-review-contrarian.json` (raw) and `…-synthesis.md` (decisions). Implementation gate is OPEN.

The six original Codex challenge prompts (preserved for traceability):

1. Challenge the architecture: is queue really BLOCKING-required? Or is fcntl alone sufficient?
2. Find missing edge cases: what happens when applier crashes mid-drain? What if 2 workers race on the same intent idempotency_key?
3. Identify over-engineering: is per-lane configurable cadence (A7) necessary, or premature?
4. Identify under-specification: is M6 INTENT-EXPIRED 12-min threshold right? What about for slow tasks like UI-RENDER?
5. Question priorities: should A1 watchdog ship before M1 queue (since watchdog catches more bugs in practice)?
6. Identify hidden assumptions: does M2 contract scan assume FE is the only consumer? What about external tooling (CI, observability)?

**Codex review output landed at** `~/Documents/Projects/.plans/megalodon/v9-roadmap-2026-05-16-review-contrarian.json` (not `docs/v9/CONTRARIAN-REVIEW-CODEX.md` as originally specified — synthesis is the canonical decision record).

---

## v8.1 candidate ledger reference

Full audit trail of the 47 candidates is distributed across:
- `findings/orchestrator-SIGNAL-001..006*.md` — orchestrator-filed SIGNALs
- `findings/agent-*-v8.1-candidate-*.md` — AUDIT lane synthesis
- `findings/agent-9bba-*-meta-*.md` — META lane synthesis
- `findings/agent-9bba-CROSS-S8-queue-design-audit-2026-05-16T19-12Z.md` — META queue design audit (4 bugs B1-B4 + 4 gaps)
- HISTORY.md `HEAL-1-SPEC-ADDENDUM-*` entries — ARCHITECT SPEC-v2 §3-bis/§3-ter/§3-quater
- Mission STATUS.md row notes (run-2) — distributed via lane heartbeats

After Codex review and v9 implementation, archive run-2 to `.archive/<utc>--megalodon-run2-make-it-work/` and reference findings from that archive.

---

## Self-contrarian review applied

Orchestrator self-pass (Phase 1.5 per `~/.agent/prompts/plan.md`) ran adversarial review against this draft before dispatching Codex. Findings classified per `~/.agent/prompts/contrarian.md` taxonomy: Obviously Wrong (OW) / Probably Wrong (PW) / Worth Reconsidering (WR).

**Total findings**: 18 (6 OW + 6 PW + 6 WR). Fixed inline: 5 OW + 0 PW + 0 WR. Self-corrected during pass: 1 OW reclassified to WR. Remaining 12 deferred to Codex external review.

### Fixed inline (5 OW)

- **OW-2**: M2 contract scan regex won't catch dynamic URLs (template literals). **Fixed**: switched to AST parser (`acorn`) + runtime fetch-wrapper instrumentation as complementary methods. Added `dynamic_unresolved` count threshold to P3 close gate.
- **OW-3**: M6 INTENT-EXPIRED 12-min threshold lacks rationale + risks false-expiry for long-walltime work. **Fixed**: threshold = `max(12 min, declared-walltime + 5 min slack)`. Workers MUST emit periodic heartbeat-ACK every 5 min during long work; missing 2 consecutive triggers expiry regardless of walltime.
- **OW-4**: A1 Watchdog depends on unresolved context-window visibility (operator open question 21:13Z). **Fixed**: dropped context-window monitoring from v9 scope (deferred to v10). Watchdog ships with crash + silent + JSONL-mtime detection only. Watchdog files SIGNAL findings; does NOT auto-respawn (too risky).
- **OW-5**: M3 helper script allowlist `python3 scripts/atomic_close.py *` has wildcard exploit risk. **Fixed**: scripts internally validate ALL args against strict regex whitelist (`--task`, `--lane`, `--notes`). Reject non-conforming args with exit code 2.
- **OW-6**: A9 Fleet ledger tokens — workers can't see own token count from Claude Code CLI. **Fixed**: split into worker-observable metrics (tasks/walltime/CAS-retries/SIGNAL-ACK-latency) + operator-side post-mission JSONL parser (`scripts/parse_session_tokens.py`). Workers no longer track what they can't read.

### Self-corrected during pass (1 OW → WR)

- **OW-1 (retracted, reclassified to WR)**: A6 lane tick-offset staggering math. Initially flagged as "45s × 6 = 270s ≈ /loop 5m = no contention reduction." Re-derived during review: stagger spreads ticks across 270s of each 300s cycle (AUDIT@T, ARCH@T+45, ..., META@T+225, AUDIT@T+300) — no 2 lanes within 45s of each other. Math actually works. Reclassified to WR: optimization may be unnecessary if /loop independent schedulers are already naturally staggered, but the math itself is correct. Codex should evaluate whether explicit staggering is needed vs implicit drift from independent schedulers.

### Deferred to Codex (12 secondary)

PW-1 through PW-6:
- PW-1: M1 Queue BLOCKING but META S-8 audit's 4 bugs (B1 BLOCKING-UTC, B2 MAJOR append-WAL, B3 applier-heartbeat, B4 claim-steal) not addressed in this roadmap. Roadmap references them as TODOs but doesn't resolve.
- PW-2: `docs/v9/queue/applier.py` + `queue_client.py` already drafted before this Codex review. Did the orchestrator jump the gate the operator set in QUEUE-DESIGN.md §10? If so, what's the rollback path?
- PW-3: M4 codegen-vs-runtime decision not made (constants registry). Both options listed; no recommendation.
- PW-4: A2 6 launch files × ~100 lines = ~600 lines of largely-duplicated content. Maintenance overhead vs benefit?
- PW-5: A3 static fleet assignment may not capture cost optimization opportunities a dynamic mid-mission switch would. Acknowledged out-of-scope but not weighed.
- PW-6: M5 PRE-CLASSIFY trust-on-honor regressed in run-2 (workers ACK'd discipline then immediately violated). Is launch.md grammar sufficient or does it need tooling enforcement?

WR-1 through WR-6:
- WR-1: M2 source-of-truth `docs/v9/api-contract.md` ownership unclear (who writes it, who maintains it).
- WR-2: A7 per-lane cadence × A6 staggering interaction unspecified.
- WR-3: A8 SIGNAL grammar may be documentation-for-documentation; SIG-ORCH-1..6 worked fine ad-hoc.
- WR-4: A10 subagent dispatch criteria undefined (when is reasoning "heavy enough" to dispatch?).
- WR-5: Migration plan cutover point ambiguous (clean cut from v8 to v9 but no concrete trigger).
- WR-6: 8-12 hour implementation estimate likely optimistic for 16 architectural items.

### Self-pass calibration

- 5 OW fixes / 6 OW findings = 83% inline-fix rate (high; OW are supposed to be most fixable). Acceptable.
- 1 OW retracted during pass = healthy self-correction; doesn't indicate over-finding.
- 12 PW + WR deferred = appropriate; Codex external view is what these need.
- No findings dismissed as IRRELEVANT (all kept for either inline-fix or Codex deferral).

Codex prompt will include this full self-pass summary in its "Already Caught" section — Codex should focus on what we missed, not re-derive these.

---

## Codex contrarian review applied

Codex contrarian review (impulse tier per `~/.agent/prompts/plan.md`) ran 2026-05-16T21:50Z — 2026-05-16T21:54Z. Model: `gpt-5.5` with `model_reasoning_effort=xhigh`. Confidence: high. Output at `~/Documents/Projects/.plans/megalodon/v9-roadmap-2026-05-16-review-contrarian.json`. Full synthesis at `~/Documents/Projects/.plans/megalodon/v9-roadmap-2026-05-16-synthesis.md`.

**Findings**: 7 total (3 high + 4 medium). All with file:line evidence.

**Decisions**: 6 ACCEPT + 1 ACKNOWLEDGE + 0 REJECT + 0 ESCALATE.

### Findings + applied changes

- **CR-1 (high)** Two write paths — UI mutation endpoints write shared state directly, bypassing queue. **APPLIED**: added M1.5 "Migrate UI mutation endpoints to queue_client" with specific endpoint targets (`megalodon_ui/server.py:324-337` TASKS, `:367-400` STATUS, `:537-565` README+TASKS; `ui/server.py:1178-1215` covered by M1.6).
- **CR-2 (high)** Two server surfaces with divergent shapes — legacy `{tasks: [...]}` vs factory `{phases: [...]}`. **APPLIED**: added M1.6 "Backend unification — factory canonical." Legacy rewritten as thin shim around `make_app()`. All response shapes in factory. `api-contract.md` describes factory only.
- **CR-3 (high)** Contract scan misses `postAction(url, body)` helper indirection. **APPLIED**: revised M2. Dropped AST parsing as primary method. Source-of-truth document (`docs/v9/api-contract.md`) + runtime fetch-wrapper instrumentation. Runtime wrapper catches helper indirection transparently.
- **CR-4 (high)** Whitelist regex excludes valid task IDs (P2-A-to-F, P5-RUN-*, OPERATOR-ACCEPTANCE-REQUEST, S-*). **APPLIED**: broadened regex in M3 to `^(P\d+(\.\d+)?(-[A-F](-to-[A-F])?)?|P\d+-RUN-[A-Z0-9_-]+|REPAIR-[A-Z0-9_-]+|OPERATOR-[A-Z_-]+|S-\d+)$`.
- **CR-5 (medium) — ACKNOWLEDGE**: Playwright config `cd && uv run` is unhygienic but runs in Playwright-internal subprocess (not worker tool gate). Code-quality note added to M3; not blocking.
- **CR-6 (medium)** Applier rejects CLAIM_DIR_DONE when owner.txt absent; legacy claims lack it. **APPLIED**: added `scripts/migrate_claims_to_owner_txt.py` to M1 implementation. Backfills owner.txt for pre-v9 claims. Strict runtime check preserved.
- **CR-7 (medium)** Acorn AST adds unpinned JS dependency to no-manifest project. **APPLIED**: dovetails with CR-3 substitution. AST dropped entirely. Source-of-truth document + runtime instrumentation. Zero new dependencies.

### Calibration

86% ACCEPT rate is high but principled: V9-ROADMAP authored from conceptual evidence; Codex's grounded review surfaced genuine blind spots in mechanism (M2) + scope (M1). The 15 of 16 marquee/architectural items not flagged is a meaningful signal that core design is sound. CR-5 was reclassified from initial ACCEPT to ACKNOWLEDGE on stricter re-review (the blocking-class concern doesn't actually apply to Playwright-internal subprocess spawning).

No escalations to operator. No 2nd Codex review required (impulse tier complete). Implementation can proceed.

---

## Document control

- **Author**: orchestrator-Claude (run-2)
- **Date**: 2026-05-16T21:33Z (v1) / 2026-05-16T21:43Z (v1.1 post-self-contrarian) / 2026-05-16T21:54Z (v1.2 post-Codex)
- **Status**: POST-CODEX-REVIEW. Implementation can proceed per Migration plan §3.
- **Self-pass result**: 5 OW fixed inline, 1 OW retracted, 12 PW+WR deferred to Codex.
- **Codex review result**: 7 findings (3 high + 4 medium), 6 ACCEPT + 1 ACKNOWLEDGE + 0 REJECT + 0 ESCALATE. All accepted findings applied inline above.
- **Owner of implementation**: orchestrator-Claude (post-Codex, can begin).
- **Plan artifacts** (per `plan.md` Phase 5):
  - Plan: `docs/v9/V9-ROADMAP.md` (this file; canonical) + `~/Documents/Projects/.plans/megalodon/v9-roadmap-2026-05-16.md` (mirror for cross-project metrics)
  - Review JSON: `~/Documents/Projects/.plans/megalodon/v9-roadmap-2026-05-16-review-contrarian.json`
  - Synthesis: `~/Documents/Projects/.plans/megalodon/v9-roadmap-2026-05-16-synthesis.md`
  - Task breakdown: deferred to start of implementation phase
