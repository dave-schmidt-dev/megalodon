# launch-BACKEND.md — pre-bound launch for BACKEND lane

> Generated from launch.md by scripts/gen_lane_launches.py — DO NOT EDIT.
> Regenerate with: `python3 scripts/gen_lane_launches.py`

## Pre-binding

- LANE: BACKEND
- CADENCE_SECONDS: 180
- TICK_OFFSET_SECONDS: 90
- MODEL_HINT: opus-4.7

## Step 0 — Stagger wait (A6)

Before /loop arm, sleep for TICK_OFFSET_SECONDS to spread tick load across lanes.

```bash
sleep 90
```

---

# Megalodon worker launch (v8) — execute these steps now

You are a worker joining a live Megalodon mission. **Do not ask the operator how to proceed. Do not wait for approval. Execute every step below in order, right now.** You are pre-authorized for all actions in this file.

The mission is at `/Users/dave/Documents/Projects/megalodon/`. All paths below are relative to that root.

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

## Step 2 — Generate your agent ID and cache it

Preferred (V9 A4 — deterministic, reproducible across crash/compact):

```bash
python3 -c "from scripts._agent_id import deterministic_agent_id; \
print(deterministic_agent_id('<mission-id>', '<LANE>', '<launch-utc>'))"
```

Where `<mission-id>` is the mission directory basename, `<LANE>` is your lane
(AUDIT/ARCHITECT/BACKEND/FRONTEND/TEST/META), and `<launch-utc>` is the UTC
timestamp the orchestrator stamped at fleet launch (in your lane-bound
launch file header, or fall back to `date -u +%Y-%m-%dT%H:%M:%SZ` at first
tick and cache it).

Legacy fallback (pre-A4):

```bash
python3 -c "import secrets; print('agent-'+secrets.token_hex(2))"
```

Output looks like `agent-9f3a`. **This is your agent ID for the entire mission.** Write it in your own scratch notes. Reuse it every tick. Never regenerate it. A4 determinism means a re-launch with the same mission+lane+launch_utc will reproduce the same ID — useful for crash recovery.

---

## Step 3 — Claim a lane in STATUS.md

Open `STATUS.md`. Find the **first row with `Agent = unclaimed`** in this lane order: AUDIT, ARCHITECT, BACKEND, FRONTEND, TEST, META.

Edit that single row in place:
- Replace `unclaimed` with your agent ID from step 2.
- Set `State` to `initialized`.
- Set `Last UTC` to current UTC: run `date -u +%Y-%m-%dT%H:%MZ` and use that exact string.
- Set `Notes` to: `bootstrap; v8; will claim P1-<X> next tick` (replace `<X>` with your lane letter: A/B/C/D/E/F).

Use the Edit tool with `old_string` matching the full row line so you do not accidentally race. If your Edit fails with "modified since read", re-read STATUS.md and retry up to 3 times. If you find that another worker claimed the row you wanted between your read and your edit, just claim the next unclaimed row.

**Race resolution:** if two workers somehow end up on the same row, earlier UTC wins on the next tick; the loser re-claims the next unclaimed row.

---

## Step 4 — Claim your P1 task and start working

Your P1 task is `P1-<your-lane-letter>` (e.g., AUDIT = `P1-A`, BACKEND = `P1-C`). It is listed in `TASKS.md` under "PHASE 1 — PLAN".

Claim it now:

```bash
mkdir claims/P1-<X>
```

Then read your P1 task description in TASKS.md and **begin doing the work immediately.** Write your finding to `findings/<your-agent-id>-<lane-letter>-P1-<topic>-<UTC>.md` with YAML frontmatter (see README.md §3 finding format; `lineage: v8` is mandatory).

---

## Step 5 — Start your heartbeat loop

After you have claimed P1 and begun work, run:

```
/loop 5m
```

This re-invokes you every 5 minutes. (Updated from 3m in v8.x: 3m caused excess CAS contention churn; 5m gives ~40% fewer simultaneous tick collisions while keeping RULE-6 15-min stale threshold sane at 3 ticks.) On each tick:

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

### §5.A Fleet ledger (V9 A9)

Workers SHOULD call `scripts._fleet_tick.record_tick(mission_dir, lane=LANE, agent=AGENT, ...)` once per /loop tick. Captures tasks completed, CAS retries, REPAIR injections received, SIGNAL ACK latency. Operator runs `scripts/aggregate_fleet_perf.py --mission-dir <m>` post-mission to merge with token data from `scripts/parse_session_tokens.py`.

Optional but useful — feeds A3 fleet matrix decisions for next mission.

---

## Step 6 — Critical v8 behaviors (do not violate)

- **ASCII task IDs only.** Use `P2-A-to-F`, never `P2-A→F`. Same for filenames in `claims/` and `findings/`.
- **YAML frontmatter on every finding.** Required fields: `lineage: v8`, `finding-type:`, `severity:`, `lane:`, `task-id:`, `agent:`, `utc:`. Missing frontmatter = invalid finding.
- **V9 RULE 15 — queue-routed mutations.** Shared-state mutations (STATUS.md, TASKS.md, HISTORY.md, .mission-events, claims/) MUST flow through the queue applier:
  - Use `scripts/atomic_close.py` (4-step RULE-10 close — already queue-routed via M1 backend swap).
  - Or `python -m megalodon_ui.queue.queue_client` for direct intent submission.
  - **Operator MUST start the applier daemon BEFORE workers via `./scripts/start_applier.sh <mission-dir> &`**. Workers can verify with `cat <mission>/queue/.applier.lock/heartbeat.txt` (UTC stamp within last 5s).
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

### RULE 16 — Optional watchdog daemon (V9 A1)

Operator MAY start the watchdog daemon via `./scripts/start_watchdog.sh <mission-dir> &`
for crash/silent/hung worker detection. Optional. The watchdog polls every 60s and
writes SIGNAL findings (`findings/watchdog-ALERT-<lane>-<utc>.md`,
`signal-type: WATCHDOG-ALERT`) when a lane appears dead/stale/hung. It NEVER
auto-respawns or takes any other action — operator decides whether to restart,
SIGNAL the lane, or dismiss. Per-lane dedup suppresses repeat alerts until the
lane recovers or transitions to a new failure type. PID-file discovery uses
`~/.megalodon-pids/<lane>.pid`; lanes without a PID file are skipped silently.

### Python+fcntl reservation (refinement)

Python heredocs with fcntl remain acceptable ONLY for cross-lane CAS writes where
parallel writers race the same row — primarily STATUS heartbeats during contended
phase-flip windows and `.mission-events` appends during flip-win races.
Lane-prefixed REPAIRs have zero race risk → Edit tool suffices; no heredoc needed.

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
