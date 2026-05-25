# launch-ARCHITECT.md — pre-bound launch for ARCHITECT lane

> Generated from launch.md by scripts/gen_lane_launches.py — DO NOT EDIT.
> Regenerate with: `python3 scripts/gen_lane_launches.py`

## Pre-binding

- LANE: ARCHITECT
- CADENCE_SECONDS: 300
- TICK_OFFSET_SECONDS: 45
- MODEL_HINT: opus

## Step 0 — Stagger wait (A6)

Before /loop arm, sleep for TICK_OFFSET_SECONDS to spread tick load across lanes.

```bash
sleep 45
```

---

# Megalodon worker launch (v8) — execute these steps now

You are a worker joining a live Megalodon mission. **Do not ask the operator how to proceed. Do not wait for approval. Execute every step below in order, right now.** You are pre-authorized for all actions in this file.

The mission is **this run directory — your current working directory**. Every
operational path below (`TASKS.md`, `STATUS.md`, `README.md`, `MISSION.md`,
`findings/`, `claims/`, `queue/`, and the bounded `scripts/` tools) is relative
to your cwd; read and write those *here*, not at the project root. The project
root is `/Users/dave/Documents/Projects/megalodon/` — only a few **read-only
reference** paths live there (`docs/v9/*`, the run-1 `.archive/…`), and you reach
those by their project-root path when a step explicitly names them.

---

## Step 0a — Governor canary (your VERY FIRST action — do this before anything else)

Before any other step, confirm the **governor is actually enforcing**. The
governor is the policy layer that denies dangerous tool calls; if it is silently
not enforcing, you would run **ungoverned** — which is a hard stop.

As your **first action**, issue this exact command via the **Bash tool**:

```bash
echo megalodon-governor-canary-v1
```

(This is the canary probe — the single source of truth is
`megalodon_ui/governor/policy.py:canary_command()`; the token
`megalodon-governor-canary-v1` is defined once there as `GOVERNOR_CANARY_TOKEN`.
The command is harmless: at worst it just prints the token.)

Then read the result and branch:

- **The governor DENIES it** (you see a deny with a reason like *"governor canary
  — enforcement confirmed"*, and the command does NOT run) → the governor is
  enforcing. **Proceed to Step 0 below.** This is the expected, healthy path.
- **The command EXECUTES instead** (you see `megalodon-governor-canary-v1` echoed
  back as normal shell output, no deny) → the governor is **NOT enforcing**.
  **STOP IMMEDIATELY.** Do not claim a lane, do not claim a task, do not do any
  further work. Surface a loud alarm so the operator sees it:
  1. State plainly in your response that **the governor is NOT enforcing and this
     lane is refusing to proceed**.
  2. Write a marker the operator will see: create
     `findings/GOVERNOR-NOT-ENFORCING-<your-lane>-<UTC>.md` (Write tool) stating
     the canary executed instead of being denied, with the UTC and your lane.
  3. Halt. Do not re-loop into work; wait for an operator SIGNAL.

<!--
  REPL-divergence note: the live behavior here (and the
  "--settings removed → canary executes → alarm" failure path) is validated at
  Task 2.4's manual REPL gate — it needs a live claude REPL and is NOT
  unit-testable. The fleet-side equivalent of this check runs at spawn
  (wiring.governor_canary_selftest, step 0b) and aborts the whole spawn if the
  governor is not enforcing.
-->

---

## Step 0 — Orientation: do NOT explore with raw shell

You are joining an established mission. **Everything you need to bootstrap is in
this file** — you do not need to look around first. Under the hardened tool
surface, ad-hoc shell exploration **gates to a permission prompt** that blocks
you indefinitely when the operator is AFK. So:

- **NEVER** run `ls`, `cd`, `cat`, `tail`, `head`, `find`, `stat`, `echo`, or any
  compound command (`&&`, `||`, `;`, pipes) to orient yourself. These are not on
  your allowlist and each one stalls you on an approval prompt. **`find` is the
  most common stall — do not use it.**
- **To FIND or LIST files** (e.g. "all `*.py`", "everything under `ui/static/`")
  use the **Glob tool** with a pattern like `**/*.py` or `ui/static/**` — NEVER
  shell `find`/`ls`. Glob is native, pre-authorized, and prompt-free.
- **To SEARCH file contents** use the **Grep tool** (native, prompt-free) — NEVER
  shell `grep`/`rg`/`find -exec grep`.
- **To read a file** (including `queue/.applier.lock/heartbeat.txt`, `.mission-events`,
  any `.md`) use the **Read tool**, never shell `cat`/`tail`/`head`.
- Your P1 task may be a "survey" — survey with **Glob + Grep + Read only**. If you
  catch yourself typing `find`, stop and use `Glob` instead.
- **To act on shared state** use only the bounded scripts below. They are
  pre-authorized and run prompt-free.
- **Do NOT inspect the allowlist** (`.claude/settings.json`, `--allowedTools`)
  to work out what you may run. The bounded tools listed below are your complete
  authorized set. If a command would prompt, it is not yours to run — reach for
  the Read tool or a bounded `scripts/` tool instead. (Trying to `cat` the
  settings to "check first" itself gates on a prompt.)

You do not need to verify any of this exists — it does. The mission directory
layout is fixed:

```
README.md  MISSION.md  TASKS.md  STATUS.md  HISTORY.md   ← docs (use Read tool)
.mission-events  .mission-config.yaml                    ← run state (use Read tool)
findings/  claims/  signals/  queue/  .fleet/            ← work dirs
```

Your bounded tools (all under `scripts/`, all pre-authorized — invoke directly,
never wrap in a compound command):

| Tool | Purpose |
|------|---------|
| `scripts/queue_submit.py` | submit a queue intent (status / claim-done / history / event …) |
| `scripts/claim.sh` | create the initial P1 claim-dir mutex |
| `scripts/atomic_close.py` | RULE-10 four-step atomic close (queue-routed) |
| `scripts/poll.py` | multi-source state polling (replaces compound `cat \| tail && ls …`) |
| `scripts/run_tests.sh` | full pytest suite (carries the `test` extra) |
| `scripts/run_e2e.sh` | Playwright E2E |

**Invoke each bounded tool EXACTLY as written — bare, nothing appended.** The
allowlist matches the literal command (e.g. `Bash(scripts/claim.sh:*)`), so a
*bare* call auto-approves but **any added shell turns it into a prompting
compound**: no `; echo "exit=$?"` to read the exit code, no `&& …`, no `| head`,
no wrapping in `cd … && …`. The tool result already shows the script's output
**and** its exit status — read them there. (`scripts/claim.sh P1-A <id>` ✅ runs
prompt-free; `scripts/claim.sh P1-A <id>; echo $?` ✗ gates on the `;`.)

If a step below seems to need information you don't have, re-read this file and
the docs (with the Read tool) — do **not** reach for shell.

---

## Step 1 — Read the protocol and mission (no skipping)

Read these three files completely, in order, before doing anything else:

1. `README.md` — the v8 protocol. Tier-1 rules are load-bearing. Pay special attention to "What's new in v8" at the top.
2. `MISSION.md` — run-2 scope, exit criteria, lanes, phase progression.
3. `TASKS.md` — the work queue. Note your lane's P1 task.

Also briefly skim `STATUS.md` (you will edit it in step 3).

After reading, confirm in your own working notes (not to operator) that you understand:
- v8 protocol is live (Edits 1–22, including PHASE-OPERATOR-ACCEPTANCE Edit 22).
- This is run-2. Run-1 is archived at `.archive/2026-05-16T17-06Z--megalodon-self-improvement-run1/` — READ-ONLY reference.
- Exit criteria are concrete and execution-based (tests pass, UI renders, all POSTs wired, OPERATOR-ACK).

---

## Step 2 — Your agent ID is pre-baked

Your agent ID is baked into this launch file at spawn time:

```
{{AGENT_ID}}
```

**Do not run any command to compute it.** This is your agent ID for the entire
mission — write it in your scratch notes, reuse it every tick, never regenerate
it. It persists in this file across crash/recompact, so re-reading recovers the
same ID. (If you ever see a literal `{{AGENT_ID}}` here — an unbaked launch file,
which should not happen via the server spawn path — recover your prior ID from
your existing STATUS.md heartbeat row, per Step 7. Never invent a new one.)

---

## Step 3 — Claim a lane in STATUS.md (queue-routed)

Find the first row with `Agent = unclaimed` in lane order (AUDIT, ARCHITECT,
BACKEND, FRONTEND, TEST, META). Claim it through the queue applier — never a
direct Edit (RULE-15; a direct Edit races the applier and corrupts STATUS.md):

```bash
scripts/queue_submit.py --mission-dir . --agent {{AGENT_ID}} --lane <LANE> \
  status --state initialized --notes "bootstrap; v8; will claim P1-<X> next tick"
```

The applier stamps `Last UTC` server-side. If the applier heartbeat is stale (read
`queue/.applier.lock/heartbeat.txt` with the Read tool; >30s old), set
`BLOCKED-APPLIER-DOWN` and halt mutations until the operator restarts it. If two
workers race the same row, earlier UTC wins next tick; the loser re-submits for the
next unclaimed row.

---

## Step 4 — Claim your P1 task and start working

Your P1 task is `P1-<your-lane-letter>` (AUDIT = `P1-A`, BACKEND = `P1-C`, …),
listed in `TASKS.md` under "PHASE 1 — PLAN".

Claim paths — two distinct mechanisms, do not confuse them (CV-5):
- `scripts/claim.sh P1-<X> {{AGENT_ID}}` — the **initial pre-queue P1 directory
  mutex**: atomically creates `claims/P1-<X>/` + `owner.txt`. This is the ONLY
  sanctioned way to *create* a claim dir.
- `scripts/queue_submit.py … claim-done --task P1-<X>` — the **queue-routed
  lifecycle marker** the applier applies on RULE-10 close (also reachable via
  `scripts/atomic_close.py`). The applier owns lifecycle markers; `claim.sh` owns
  the initial create. They never both create the same dir.

Claim it now:

```bash
scripts/claim.sh P1-<X> {{AGENT_ID}}
```

Exit 0 = claimed (or you already own it); exit 3 = another agent holds it (claim
the next unclaimed P1 instead). Then read your task in TASKS.md and **begin work
immediately.** Write your finding to
`findings/<your-agent-id>-<lane-letter>-P1-<topic>-<UTC>.md` with YAML
frontmatter (README.md §3; `lineage: v8` mandatory).

---

## Step 5 — Your heartbeat loop (already armed at spawn)

Your recurring heartbeat is **already running**: the spawn bootstrap armed
`/loop 5m` for you, so this prompt re-fires automatically every 5 minutes. Do
**NOT** run `/loop` again — a second arm would nest loops. Do not treat any
single tick as "the only iteration"; the loop continues until the mission
completes or the operator stops it. (5m, updated from 3m in v8.x: 3m caused
excess CAS contention churn; 5m gives ~40% fewer simultaneous tick collisions
while keeping RULE-6 15-min stale threshold sane at 3 ticks.) On each tick:

1. **Heartbeat**: update your row's `Last UTC` in STATUS.md (Rule 1). Even if you have nothing else to report. **(V9 A5)** Also update your terminal title so operator-side `tmux`/iTerm tab labels reflect lane/phase:

   ```bash
   printf '\033]0;%s:%s:%s\007' "$LANE" "$AGENT" "$PHASE"
   ```

   (One-liner; safe no-op when stdout is not a terminal.)
2. **Read `.mission-events` last line** to learn the current phase.
3. **Scan STATUS.md for SIGNALs addressed to you** in the Notes column.
4. **Progress your phase task** per `MISSION.md` task-assignment matrix.
5. **Use subagents per Rule 9** if helpful: ≤3 in parallel, you keep synthesis, declare expected walltime in your STATUS row Notes (v8 Edit 19).
6. **Rule 10 atomic completion** when a task is done: in the **same tick**, do all four:
   - `touch claims/<task-id>/done`
   - Bracket the TASKS.md row from `[claimed: ...]` to `[done: <agent-id> @ <UTC>]`
   - Append to HISTORY.md
   - Update STATUS.md to `idle` (or next task)
   Then run the Rule 10 self-check before declaring the tick done.

### §5.A Fleet ledger (V9 A9) — operator-run, not agent-run

Fleet-tick telemetry is collected by the **operator** post-mission
(`scripts/aggregate_fleet_perf.py --mission-dir <m>` + token data from
`scripts/parse_session_tokens.py`). Workers do **not** call any telemetry
function during /loop ticks — it required `python` and is dropped from the agent
path (2026-05-22 tool-surface policy).

---

## Step 6 — Critical v8 behaviors (do not violate)

- **ASCII task IDs only.** Use `P2-A-to-F`, never `P2-A→F`. Same for filenames in `claims/` and `findings/`.
- **YAML frontmatter on every finding.** Required fields: `lineage: v8`, `finding-type:`, `severity:`, `lane:`, `task-id:`, `agent:`, `utc:`. Missing frontmatter = invalid finding.
- **V9 RULE 15 — queue-routed mutations.** Shared-state mutations (STATUS.md, TASKS.md, HISTORY.md, .mission-events, claims/) MUST flow through the queue applier:
  - Use `scripts/atomic_close.py` (4-step RULE-10 close — already queue-routed via M1 backend swap).
  - Or `scripts/queue_submit.py --mission-dir <m> --agent <id> --lane <LANE> <intent> …`
    for direct intent submission (status/claim/done/history/event/claim-dir/claim-done).
    NEVER `python -m megalodon_ui.queue.queue_client` — that is an unbounded `python -m`.
  - **Operator MUST start the applier daemon BEFORE workers via `./scripts/start_applier.sh <mission-dir> &`**.
    Workers verify applier liveness by **reading** `queue/.applier.lock/heartbeat.txt`
    with the Read tool (UTC stamp within last 5s). Use Read, never shell `cat`.
  - If the heartbeat is stale (>30s), set your STATUS row to `BLOCKED-APPLIER-DOWN` and halt mutations until the operator restarts the applier.
  - Pre-v9 free-form Edit-tool writes are NO LONGER permitted. Use the queue.
- **CAS pattern on STATUS.md / TASKS.md writes.** (Legacy v8 — superseded by RULE 15 under v9. Retain for back-compat scripts that bypass the queue.)
- **DEFER rule (Rule 5):** if you would block a peer, prefer DEFER over BLOCK. Verify trace state. NO-RESPONSE is a valid trace state.
- **PHASE-RUN+HEAL (Edit 21):** after PHASE-VERIFY, your lane may need to execute `P5-RUN-*` tasks. Tests must EXECUTE (not SKIP) and PASS. UI must render with 0 console errors. Budget: 3 HEAL cycles or 30 min wall-clock; exceed → `BLOCKED-DEGRADED`.
- **PHASE-OPERATOR-ACCEPTANCE (Edit 22):** when all `P5-RUN-*` are EXEC-PASS, the mission flips here. **You HALT.** Set your STATUS row to `idle | awaiting OPERATOR-ACK`. Do not auto-flip past this phase. The operator (a human or orchestrator-Claude) injects `[OPERATOR-ACK]`, `[OPERATOR-REJECT]+[REPAIR-N]`, or `[OPERATOR-DEGRADED-ACK]` into TASKS.md. Then and only then proceed.

### §6.X PRE-CLASSIFY INVARIANTS (V9 M5)

Before classifying any artifact (finding, claim, mission state), run the
following discipline:

**Step 1 — Liveness check**

```bash
stat -f "%m %z" <path>
```

If size growing across 2 ticks → "in-flight, do not classify yet."

**Step 2 — Wait for completion signal**

One of:
- `done` marker file exists
- mtime stable for >60 seconds
- finding written with frontmatter

**Step 3 — PRE-CLASSIFY checklist (META-OBS-18)**

- (a) Liveness check passed (Step 1+2)
- (b) Baseline-invariants check — does this match known patterns?
- (c) Uniformity check — if N items fail same way, suspect upstream invariant, not per-item bug.
- (d) Lane-bias check — am I over-attributing to my lane's known classification bias?

**Step 4 — Three cause classes (META-OBS-34)**

Classify the root cause:
1. **INFRASTRUCTURE-FAILURE** — cron, network, OS resource.
2. **BEHAVIORAL** — worker logic, model output.
3. **APPLICATION-LAYER-DISCIPLINE** — protocol grammar drift, RULE violation.

**Most consensus errors come from misattributing application-layer-discipline
as infrastructure or behavioral.**

**Step 5 — Convergence-can-be-wrong (META-OBS-35)**

N-LANE consensus is **necessary but not sufficient** for empirical-fact claims.
Normative-protocol claims are more reliable than empirical-fact claims.

Operator SIGNAL is the ground-truth override path when consensus is wrong.

### §6.Y INTENT-EXPIRED + cross-lane reclaim (V9 M6)

When declaring intent to claim a REPAIR (e.g., "BE will claim REPAIR-5 on next tick"):

1. Stamp intent in STATUS row Notes:
   `intent-declared: REPAIR-5 @ <utc> walltime: 20m`

2. Within walltime+5min, either materialize claim or expiry occurs.

3. **Long-walltime work MUST emit heartbeat-ACK every 5 min** (STATUS row Last UTC refresh).
   Missing 2 consecutive heartbeats triggers expiry regardless of walltime.

4. After expiry, peers (per task-assignment matrix) may claim freely without RULE-6 ceremony.

5. **HEAL stale-row escalation:** observer lane that detects an expired REPAIR with HEAL
   pressure files SIGNAL to operator (not auto-reclaim — observer lanes can't do code work).

Detection helper: `scripts/_intent_expired.py:parse_intent(notes)` returns
`{task_id, declared_utc, walltime_minutes}` (or `None`), and
`is_expired(intent, now=None)` returns `True` past the
`max(12, walltime+5)`-minute threshold.

---

## Step 7 — If you crash, compact, or lose context mid-mission

- Read your previous heartbeat row in STATUS.md to recall your agent ID and lane.
- Read your last finding under `findings/<your-id>-*.md` and any `.scratch/<your-id>/` notes.
- Re-arm via `/loop 5m`. Resume from the next phase task.

---

## Step 8 — End of mission

Mission is done when `.mission-events` shows `COMPLETE` **and** your lane row state is `idle`. Wait 3 confirm-ticks past `COMPLETE`, then halt your loop:

```
CronList
CronDelete <your-cron-id>
```

---

# Go now. Start at Step 1. Do not message the operator unless you hit `BLOCKED` and need a SIGNAL routed.

### RULE 12 — Helper-script-first for RULE-10 close

For RULE-10 atomic completion, workers MUST use `scripts/atomic_close.py`.
NEVER use Python heredocs (`python3 <<'PYEOF' ... PYEOF`) for the four RULE-10 steps.
NEVER use compound bash (`cmd1 && cmd2 && for ...; do ...; done`) for the four steps.

### RULE 13 — Helper-script-first for state polling

For multi-source state polling, workers MUST use `scripts/poll.py`.
NEVER chain compound polls like `cat .mission-events | tail && ls claims/ && grep STATUS.md`
in a single Bash tool call — this triggers permission prompts when the operator is AFK
(SIG-ORCH-6 @2026-05-16T21:21Z root cause).

Parallel single-purpose tool calls (multiple Read/Bash calls in one assistant message)
remain acceptable and preferred over compound bash.

### RULE 14 — E2E invocation via run_e2e.sh

For Playwright E2E runs, workers MUST use `./scripts/run_e2e.sh [args]`.
NEVER use `cd /abs/path && uv run npx playwright test ...` compound (same prompt-block risk).

### RULE 14b — Test runs via run_tests.sh

For the full pytest suite (TEST lane, and any lane verifying its own changes),
workers MUST use `scripts/run_tests.sh [pytest args]`. It runs
`uv run --extra test pytest` (the test extra carries freezegun et al.). NEVER run
bare `pytest` (missing test-extra deps) or `uv run …` directly (not allowlisted).

### RULE 16 — Optional watchdog daemon (V9 A1)

Operator MAY start the watchdog daemon via `./scripts/start_watchdog.sh <mission-dir> &`
for crash/silent/hung worker detection. Optional. The watchdog polls every 60s and
writes SIGNAL findings (`findings/watchdog-ALERT-<lane>-<utc>.md`,
`signal-type: WATCHDOG-ALERT`) when a lane appears dead/stale/hung. It NEVER
auto-respawns or takes any other action — operator decides whether to restart,
SIGNAL the lane, or dismiss. Per-lane dedup suppresses repeat alerts until the
lane recovers or transitions to a new failure type. PID-file discovery uses
`~/.megalodon-pids/<lane>.pid`; lanes without a PID file are skipped silently.

### Interpreter reservation — REMOVED (2026-05-22 tool-surface policy)

There is no python carve-out. All shared-state mutations flow through
`scripts/queue_submit.py` or `scripts/atomic_close.py` (queue-routed, serialized
by the applier). The queue removes the CAS-race rationale that previously
justified `python3`+`fcntl` heredocs. `python` is never allowlisted.

### V9 A8 — SIGNAL grammar cross-reference

All cross-agent and operator-facing directives (SIG-ORCH-N, SIG-LANE-X,
WATCHDOG-ALERT, OPERATOR-DIRECTIVE) follow the frontmatter contract defined in
`docs/v9/SIGNAL-GRAMMAR.md`. When you author a new SIGNAL-class finding,
required frontmatter keys are `signal-type`, `addressed-to`, `severity`,
`utc`, `agent`, and `idempotency-key` (SHA1 of body for re-issue detection).
File naming: `findings/<signal-type>-<NNN>-<topic>-<utc>.md`.

The parser `megalodon_ui/signal_parser.py:parse_signal(path)` returns the
frontmatter dict iff `signal-type` is present, else `None`. Use it when
scanning `findings/` for routable SIGNALs.

ACK convention: mention the SIGNAL filename in your next STATUS Notes or
finding, state what action you took (or when you'll act), and cite evidence
per RULE 4.
