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

Run this exact command:

```bash
python3 -c "import secrets; print('agent-'+secrets.token_hex(2))"
```

Output looks like `agent-9f3a`. **This is your agent ID for the entire mission.** Write it in your own scratch notes. Reuse it every tick. Never regenerate it.

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

1. **Heartbeat**: update your row's `Last UTC` in STATUS.md (Rule 1). Even if you have nothing else to report.
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

---

## Step 6 — Critical v8 behaviors (do not violate)

- **ASCII task IDs only.** Use `P2-A-to-F`, never `P2-A→F`. Same for filenames in `claims/` and `findings/`.
- **YAML frontmatter on every finding.** Required fields: `lineage: v8`, `finding-type:`, `severity:`, `lane:`, `task-id:`, `agent:`, `utc:`. Missing frontmatter = invalid finding.
- **CAS pattern on STATUS.md / TASKS.md writes.** Hash file before edit, re-hash before commit, retry up to 3 times if changed. Alphabetical lock order if you must touch multiple shared files.
- **DEFER rule (Rule 5):** if you would block a peer, prefer DEFER over BLOCK. Verify trace state. NO-RESPONSE is a valid trace state.
- **PHASE-RUN+HEAL (Edit 21):** after PHASE-VERIFY, your lane may need to execute `P5-RUN-*` tasks. Tests must EXECUTE (not SKIP) and PASS. UI must render with 0 console errors. Budget: 3 HEAL cycles or 30 min wall-clock; exceed → `BLOCKED-DEGRADED`.
- **PHASE-OPERATOR-ACCEPTANCE (Edit 22):** when all `P5-RUN-*` are EXEC-PASS, the mission flips here. **You HALT.** Set your STATUS row to `idle | awaiting OPERATOR-ACK`. Do not auto-flip past this phase. The operator (a human or orchestrator-Claude) injects `[OPERATOR-ACK]`, `[OPERATOR-REJECT]+[REPAIR-N]`, or `[OPERATOR-DEGRADED-ACK]` into TASKS.md. Then and only then proceed.

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

### Python+fcntl reservation (refinement)

Python heredocs with fcntl remain acceptable ONLY for cross-lane CAS writes where
parallel writers race the same row — primarily STATUS heartbeats during contended
phase-flip windows and `.mission-events` appends during flip-win races.
Lane-prefixed REPAIRs have zero race risk → Edit tool suffices; no heredoc needed.
