# Dogfood run — 2026-05-19

Megalodon eating itself: a 6-lane Claude REPL fleet (`/loop` autonomous mode) used Megalodon v9.3 to improve Megalodon v9.3. Run lasted ~6 hours (15:30 EDT spawn → 21:11 EDT shutdown).

This directory archives the entire run: every finding, every inter-lane signal, every operator feedback note, the queue applier log, the mission's terminal state, and the rough code drafts agents produced. **Nothing in this directory is active source** — it's evidence for v9.4 planning.

## What was the mission?

Improve Megalodon itself by exercising v9.3 features (`live_repl` mode, server-owned tmux spawn, queue applier, permission watcher, operator feedback queue) in a real multi-agent context. Surface bugs that only appear under sustained multi-lane load.

## Lane assignments

| Lane | Role | Model | Agent ID |
|------|------|-------|----------|
| A | AUDIT | opus | agent-0fa4 |
| B | ARCHITECT | opus | agent-f66a |
| C | BACKEND | sonnet | agent-d510 |
| D | FRONTEND | sonnet | agent-07c5 |
| E | TEST | sonnet | agent-db2a |
| F | META | haiku | agent-d55b |

## What's here

| Path | Count | Purpose |
|------|------:|---------|
| `findings/` | 120 | Per-agent finding files (one per iteration outcome) — the primary research output |
| `signals/` | 2 | Inter-lane messages (LANE-X → LANE-Y) — protocol exercise |
| `feedback/` | 4 | Operator-to-lane notes dropped via the feedback queue |
| `launches/` | 6 | Per-lane `/loop` launch templates as baked at spawn time |
| `agent-designs/` | 2 | Design docs agents produced (`v9-3-HYBRID-DASHBOARD.md`, `v9-3-ORCHESTRATOR-AUTO-LOOP.md`) |
| `agent-code-drafts/` | 12 | Code agents wrote that DIVERGED from what shipped (their attempts at server.py, dashboard.js, etc., plus new files like `stream_reader.py`, `terminal_modal.js`) — preserved as inputs for v9.4 |
| `.fleet/queue-applier.log` | 105 lines | The queue applier's intent-by-intent audit trail |
| `MISSION.md`, `STATUS.md`, `TASKS.md`, `HISTORY.md` | — | Final mission state at shutdown |
| `.mission-config.yaml` | — | The config that drove the spawn |

## What shipped to active source (separately, in v9.3 commits)

The orchestrator's session of bug fixes — distinct from agent drafts — landed in active source under `megalodon_ui/` and `ui/static/`. Highlights:

- `megalodon_ui/server.py` — in-process queue applier, `?wait=true` sync endpoints, no-cache headers for `/static/`, queue-applier UTC formatter
- `megalodon_ui/permission_watcher.py` — TAIL_BYTES bumped 4KB→32KB, `CLEAR_SUPPRESSION_SECONDS` re-flash guard
- `megalodon_ui/queue/applier.py` — UTC-correct asctime via `Formatter.converter = time.gmtime`
- `megalodon_ui/harnesses/claude.py` — `live_repl` mode + narrow allowlist (read-only shell + claims primitives + `pytest`/`uv run --with pytest*`/`npx playwright`)
- `ui/static/js/app.js` — `mountPage` race guard via `_mountSeq` + awaited async render
- `ui/static/pages/dashboard.js` — lane-key fix (short↔long mismatch causing empty lane cards)
- `scripts/check_megalodon_workers.sh` — operator-tick snapshot with stale-lane + pending-aware detection

## Top failure modes observed (v9.4 input)

These recurred under load and are the foundation for v9.4's UI rebuild:

1. **No per-agent line-by-line visibility** — operator could not see what each agent was doing in real time. Findings, signals, and STATUS updates were lagging proxies. Watching a lane's `.fleet/<X>.stream.log` via `tail -c | python3 ANSI-strip` was the only way to debug.
2. **Approval friction stalled the fleet** — agents writing slightly-different but always-safe compound bash (`for/done` polling, `||` fallbacks, `cmd | head -N`, single-quoted curls, absolute-path `ls`) triggered fresh prompts every iteration. Operator clicked Approve ~50+ times per hour at peak.
3. **Permission prompts buried by thinking-dots** — Sonnet 4.6 thinking output filled the watcher's 4KB tail in ~30s, scrolling actual permission prompts out of view. One lane was invisibly blocked for 195 minutes.
4. **Stale-lane false positives** — lanes producing real work via the applier (not findings) appeared "stale" because the detector only checked finding mtimes. Also a 4-hour timezone skew because applier logger emitted local time labeled `Z`.
5. **UI snap-back to dashboard** — async page render's return value was a Promise, not the cleanup function. The `typeof cleanup === "function"` guard always failed; setIntervals + store subscriptions leaked across every navigation. Dashboard's slow `await loadConfig()` would paint over whatever tab the operator had clicked.
6. **Browser cache served stale JS** — no `Cache-Control: no-store` on `/static/`; Safari served stale `app.js` for hours after fixes shipped.
7. **Lane-key short/long mismatch** — BE emitted `lane:"A"`, FE looked up by `"AUDIT"` (config name). Every lane card silently fell through to the synthesized placeholder, looking "empty" while the BE delivered perfect data.
8. **Hardcoded auth cookies** — LANE-C never executed the documented `auth/exchange` cookie flow; looped 5+ times with placeholder cookie strings.
9. **`/loop` schedule died silently** — LANE-D's `ScheduleWakeup` chain broke at some point; REPL alive but no future ticks scheduled. Recovery: `tmux send-keys '/loop <prompt>'` to re-arm.
10. **No "open this lane's terminal" affordance** — operator could only `tmux attach` from a terminal, losing dashboard context.

## How to use this archive when planning v9.4

1. Read `findings/` chronologically (filenames sort by UTC) to see how the fleet's understanding evolved
2. Read `agent-designs/` for the ARCHITECT and AUDIT lanes' own v9.4 proposals
3. Read `agent-code-drafts/` to cherry-pick useful prototypes (`terminal_modal.js`, `stream_reader.py` are most promising)
4. Read `feedback/*.md` for the operator's in-flight directives — they were the ground truth for what the operator wanted
5. Cross-reference `.fleet/queue-applier.log` with HISTORY.md to reconstruct the timeline of any specific bug

The 10 failure modes above are the seeds for v9.4's spec. Solve them, prove the solutions with playwright, and v9.4 is shippable.
