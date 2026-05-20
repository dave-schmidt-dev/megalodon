# v9.3 — Orchestrator tick format

**Status:** Operator-codified 2026-05-19. Applies to any session where the orchestrator (Claude Code) is /loop'd to monitor a live Megalodon fleet.

## What this covers

When the orchestrator is running a recurring `check the megalodon workers`-style loop (or any equivalent fleet-monitoring cron / ScheduleWakeup), each tick MUST end with a per-lane status table so the operator can scan state at a glance without re-reading the prose.

## The table

Render at the bottom of every tick response. Markdown, 3 columns:

| Lane | Agent (model) | Task |
|---|---|---|
| `<SHORT> <ROLE>` | `<agent-id>` `(<model-tier>)` | one of the cell rules below |

### Cell rules — `Task` column

- **Has an active claim** (a `claims/<task-id>/owner.txt` matches this lane's agent-id):
  - Render the task-id in **bold**, plus age in minutes:
    `**P1-E** (11m, dashboard audit)`
  - The trailing parenthetical is optional context (current activity / sub-step). Keep under 40 chars.

- **No active claim** (idle):
  - Render the most recent `[done: <this-agent-id> @ ...]` entry in TASKS.md:
    `idle (last: P1-A done)`
  - If the agent has never completed a task in this session:
    `idle (no tasks completed yet)`

### Cell rules — `Agent (model)` column

Format: `<agent-id> (<tier>)` where tier is `opus` | `sonnet` | `haiku`. Use the short tier alias, not the full model id (no `claude-opus-4-7` — just `opus`).

### Cell rules — `Lane` column

Format: `<short> <ROLE>` — e.g. `A AUDIT`, `B ARCHITECT`, `C BACKEND`, `D FRONTEND`, `E TEST`, `F META`. Always 6 rows (one per configured lane), in short-code order.

## Optional extras (only when something needs operator attention)

After the table, add one to three bullet lines above the routine `Next cron at HH:MM` close. Only include bullets when SOMETHING is worth surfacing — never spam routine "everything is fine."

Examples worth a bullet:

- A lane has held a claim > 10 minutes with no checkpoint finding.
- A lane has produced no findings across multiple ticks.
- A new pending permission prompt with an `<unknown command>` parser fallback.
- A phase appears to be done (all tasks in current phase `[done:]`) — prompts operator to phase-flip.
- A finding contains an explicit action request to the operator.

## What NOT to include

- Don't dump the raw bash output of `wc`/`ls`/curl pipelines (the table replaces it).
- Don't include "no prompts pending" if there are none — silence is the signal.
- Don't include findings count, signals count, stream sizes — those go in the optional bullets only if they matter THIS tick.
- Don't include a "summary" prose paragraph — the table IS the summary.

## Source of data

- **Claim → lane mapping**: read `claims/<task-id>/owner.txt`; cross-reference agent-id against pre-baked launch files (`launch-<NAME>.md`).
- **Last-done lookup**: grep `TASKS.md` for `[done: <agent-id> @ ...]` and pick the latest by UTC.
- **Current activity (optional cell parenthetical)**: derived from the lane's pending permission prompt if any, or from the most-recent finding's filename (e.g. `dashboard-audit-ack` → "dashboard audit"). Keep it tight.

## Why this format

The operator is observing live and needs scan-speed visibility. A six-row table fits in one viewport, encodes the 80% question ("who is doing what right now?"), and avoids burying the answer in prose. Findings counts and stream sizes are second-order — surface them only when an exception fires.

## Future

When `S-ORCHESTRATOR-AUTO-LOOP` (TASKS.md secondary pool) ships, this table will be auto-rendered by the orchestrator wrapper script rather than reconstructed each tick from shell pipelines. The format spec above is the wire contract that endpoint should produce.
