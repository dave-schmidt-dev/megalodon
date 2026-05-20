# v9.3 Dogfood — live run state (recovery doc for orchestrator)

**Purpose:** If the orchestrator's conversation context has been compacted, read this file first to re-orient. The cron job + on-disk artifacts contain the live state; this doc tells you how to read them.

**Last updated:** 2026-05-19T21:06Z (immediately before /compact).

---

## What is running

A 6-lane Megalodon dogfood mission. Each lane = a tmux session running `claude` REPL in `--live_repl` mode, bootstrapped via /loop autonomous iteration.

**Mission dir:** `/Users/dave/Documents/Projects/megalodon-fleet/` (git branch `fleet/dogfood-2026-05-19`).
**Server:** `http://localhost:8765/` (use `localhost`, not `127.0.0.1`, to bypass Safari module cache).
**Auth:** token at `<mission>/.fleet/ui.token`. URL hash form: `http://localhost:8765/#t=<token>`.
**Tmux socket:** `<mission>/.fleet/tmux.sock`. Sessions: `lane-A` through `lane-F`.

## Active cron job

Job ID `d906ed88` fires every 5 minutes with prompt `check the megalodon workers`. Session-only; auto-expires in 7 days. Cancel with `CronDelete`.

When the cron fires, the orchestrator's response should:

1. Run the snapshot script (single approved invocation):
   ```bash
   bash scripts/check_megalodon_workers.sh /Users/dave/Documents/Projects/megalodon-fleet 8765
   ```
2. Auto-approve any pending prompts that are read-only inspection (ls / find / grep / cat / head / tail / wc / echo / Monitor / fswatch). Defer to operator for python3 / network / compound shells.
3. Render the lane table per `docs/v9/v9-3-ORCHESTRATOR-TICK.md` (always 6 rows, columns Lane / Agent (model) / Task).
4. Optional bullets above the close — only when something is worth surfacing.

## Codified specs

- `docs/v9/v9-3-ORCHESTRATOR-TICK.md` — exact table format the orchestrator must produce each tick.
- `docs/v9/v9-3-PERMISSION-MODEL.md` — what `--allowedTools` auto-approves at spawn vs what surfaces to operator.
- `scripts/check_megalodon_workers.sh` — single-file snapshot script (operator can `/permissions allow Bash(scripts/check_megalodon_workers.sh:*)` to silence per-tick prompts).
- `<mission>/launch-<NAME>.md` — per-lane /loop instruction file (read by agent at start of each iteration). Six files, agent-ids pre-baked at spawn.

## Lanes + pre-baked agent-ids

| Lane | Role | Model | Agent-id |
|---|---|---|---|
| A | AUDIT | opus | agent-0fa4 |
| B | ARCHITECT | opus | agent-f66a |
| C | BACKEND | sonnet | agent-d510 |
| D | FRONTEND | sonnet | agent-07c5 |
| E | TEST | sonnet | agent-db2a |
| F | META | haiku | agent-d55b |

Agent-ids live in `<mission>/launch-<NAME>.md` (substituted from `{{AGENT_ID}}` placeholder by `FleetSpawner._bake_agent_id_in_launch_file` at spawn time). Survive server restart; do NOT regenerate.

## v9 protocol surface (for re-orientation)

- **Claims**: `<mission>/claims/<task-id>/owner.txt` = lane holding that task. `mkdir claims/<task-id>` is the atomic mutex; `rm -rf claims/<task-id>` releases.
- **Findings**: `<mission>/findings/<agent-id>-<lane-short>-<phase-short>-<topic>-<UTC>.md` = work output. Includes idle notes when an agent had no claimable work.
- **Signals**: `<mission>/signals/LANE-<X>-to-LANE-<Y>-<UTC>.md` = inter-lane coordination message.
- **Feedback**: `<mission>/feedback/<LANE-NAME>.md` = operator (or orchestrator) async message to a lane. Read by agent at start of each iteration.
- **TASKS.md**: per-phase task list. `[ ]` open, `[done: <agent-id> @ <UTC>]` complete.

## Live dashboards / endpoints

- Dashboard: `http://localhost:8765/#t=<token-from-ui.token>` (v9.0 chrome mode; v9.2 panes are off because operator wanted orchestration view).
- `GET /api/v1/permission_prompts` → pending approval prompts.
- `POST /api/v1/permission_prompts/<lane>/respond {action: approve|approve_remember|deny}` → respond.
  - **NOTE:** `approve_remember` action requires server restart to be live (BE source has it; running server may still be on prior version). Verify with a single test call before relying on it.
- `GET /api/v1/state` → mission state (claims, tasks, findings, mission events).
- `DELETE /api/v1/fleet` → destructive teardown.
- `python -m megalodon_ui.shutdown --mission-dir <path>` → graceful shutdown CLI.

## Operator-facing context (you, David)

Operator uses Safari. Operator interrupts the orchestrator with messages or with `check the megalodon workers` (cron fires same prompt). Operator has been frustrated by:

1. v9.0 dashboard non-functional for /loop mode (`S-LIVE-ACTIVITY` in TASKS pool — LANE-D is working on it).
2. Tab navigation reverting on refresh (`index.html:41` auth IIFE bug — LANE-D claims to have fixed in worktree).
3. Per-tick approval friction (fixed: see `scripts/check_megalodon_workers.sh`).
4. Lack of per-lane terminal drilldown (workaround: `tmux -S <sock> attach -t lane-<X>` from operator's own terminal; Ctrl-B D to detach).
5. Agents getting prompted for read-only project ops (fixed: widened `claude.py` allowlist for next launch; requires server restart to apply).

## Known stuck state right now (as of 21:06Z)

- **LANE-E TEST `agent-db2a`** has held `P1-E` for 53+ minutes with no progress. Operator may force-release by `rm -rf claims/P1-E`. If LANE-E still spins without producing findings after release, kill `lane-E` tmux session and the spawner will skip-reattach it on next launch_fleet but not auto-respawn (would need restart).
- All other 5 lanes are healthy. PHASE-PLAN is 6 of 7 tasks done. Once P1-E completes, operator should phase-flip to PHASE-BUILD via the dashboard's Mission tab orchestrator-actions.

## Continuity contract

After /compact:
1. The cron `d906ed88` keeps firing every 5 min with prompt "check the megalodon workers" — this re-enters the orchestrator without operator action.
2. The orchestrator reads THIS file first to re-orient.
3. The orchestrator runs the script + auto-approves safe prompts + renders the table per spec.
4. If anything looks wrong (e.g. server isn't on port 8765, mission dir missing), ALERT the operator via PushNotification BEFORE attempting recovery.

## To stop the loop

```
CronDelete({jobId: "d906ed88"})
```

Or operator invokes `/loop` again with empty args / closes the session.
