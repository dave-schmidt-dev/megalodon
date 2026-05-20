# Operator feedback — LANE-D FRONTEND

This file is the operator's async channel to you. Read at the start of EVERY iteration; act on any unprocessed messages (compare timestamps against your prior findings).

---

## 2026-05-19T19:28:00Z — operator (David)

**The v9.0 dashboard is 90% non-functional for the v9.3 dogfood use case.** This is a real, urgent FRONTEND priority — please bump it ahead of P1-D / P2-D and tackle it now.

**The problem**: v9.0 dashboard was designed for the v9.0/v9.1 spawn model (iTerm grid + agents that constantly update STATUS.md/HISTORY.md). In /loop autonomous mode, agents bootstrap, hit permission prompts, claim tasks, write findings — none of that produces the STATUS/HISTORY signal the dashboard renders. As an operator watching this live, I see:

- Lane cards all "idle" even though 4 lanes have active claims
- "Activity (last 60 min)" panel: "no activity yet" all the time
- "Recent HISTORY": empty
- No visibility into what each agent is currently doing, what model, what tokens consumed, when next /loop wake fires
- Permission prompts surface now (good), but nothing else

**What I need** (priority order):

1. **`S-LIVE-ACTIVITY`** (in TASKS.md secondary pool) — per-lane "currently doing" derived from the pipe-pane stream tail. Show: last activity timestamp ("12s ago"), the last meaningful text the agent printed (strip ANSI noise), token usage parsed from the Claude TUI footer (e.g. "ctx: 52k/200k"), active/idle/blocked indicator from stream growth rate.
2. **`S-LANE-CARD-DETAILS`** — default-show model, harness, cadence, current claim, last-tick-ago on each lane card. The "Show details" toggle hides info that should always be visible.
3. **`S-HYBRID-DASHBOARD`** — v9.0 chrome by default with a "View terminal" affordance per lane card that opens the xterm pane on demand (modal/drawer). This is the right design; v9.2 panes-only was wrong.

---

## 2026-05-19T19:34:00Z — operator (David) URGENT pair-up

**Drop everything. Pair with LANE-E TEST (`agent-db2a`) NOW.** The operator is observing live, the dashboard has real bugs. TEST is writing a failing-test queue at `ui/tests/e2e/test_dashboard_live_audit.spec.ts`. Your job: drain that queue. Read `feedback/TEST.md` for full TEST scope.

### Process

1. Read `signals/LANE-E-to-LANE-D-<UTC>.md` (TEST will write it). Each line is one failing test you need to fix.
2. For each failure: open the spec, see the assertion, find the code, fix it, re-run that one spec, then run the full Playwright suite (both chromium + webkit) and confirm 0 regressions.
3. After each fix: write a finding `findings/{{AGENT_ID}}-D-P1-fix-<bug-id>-<UTC>.md` with: bug summary, file + line of fix, before/after diff sketch, test confirming green.
4. Acknowledge each TEST signal by writing `signals/LANE-D-to-LANE-E-<UTC>.md` with the bug-id you closed.

### Specific bugs the operator has observed (start with these even if TEST hasn't written specs yet)

1. **Tab navigation reverts on refresh.** Click Findings → URL becomes `/findings` → reload → URL is back to `/`. Root cause likely in `ui/static/index.html:41`: the auth bootstrap IIFE's `finally` clause unconditionally calls `history.replaceState(null, "", "/")`. Fix: preserve current pathname (`location.pathname`) instead of hardcoding `/`.
2. **Active-tab visual state doesn't update.** Operator sees the tab they clicked doesn't get highlighted. Check `app.js:68 updateNavActive(path)` — verify it's called on every mountPage AND that the `[aria-current="page"]` selector matches the actual nav link DOM (`<a href="/foo">` in `nav.app-nav`).
3. **"Activity (last 60 min)" panel always empty.** It reads `mission.events` but agents in /loop mode don't produce mission events. Either (a) plumb claim-write / finding-write as mission events on the BE, or (b) render a different "activity" feed sourced from `claims.list` + `findings.list` mtimes.
4. **"Recent HISTORY" empty.** Same — agents not writing to HISTORY.md. Either teach them via launch template (file with C+E) or surface findings list as a proxy.

### Constraints

- The BE already exposes `claims.list` and `findings.list` in `/api/v1/state`. Use them.
- For stream-tail activity surfacing, you'll need a new BE endpoint — coordinate with BACKEND (LANE-C `agent-d510`) via `signals/LANE-D-to-LANE-C-<UTC>.md`. Endpoint shape: `GET /api/v1/lane/<short>/activity_summary` → `{last_activity_utc, last_text, token_ctx, status}`. BACKEND owns the parser; you consume.
- Test on BOTH chromium and webkit (operator uses Safari).
- Don't break the existing 56 Playwright tests or 653 pytests.

### Don't

- Don't reply via chat / REPL.
- Don't claim tasks outside [LANE-D].
- Don't flip phases.
- Don't push to remote.

GO. Operator is watching live.

## 2026-05-19T19:55:49Z — orchestrator (auto-monitor)

You've held your claim for 33+ minutes with no finding written. The pipe-pane stream shows you're actively thinking, not blocked on a prompt. Two possibilities:

1. **Heavy iteration** — fine, but please write an intermediate checkpoint finding (`findings/<agent-id>-<LANE>-P1-checkpoint-2026-05-19T19:55:49Z.md`) describing what you've done so far + what's left, so the operator has visibility.
2. **Blocked silently** — if you're spinning on something (e.g. trying a tool that prompts and then bouncing off, deep nested research, etc.), STOP, release the claim, write a finding describing what blocked you, then re-claim with a narrower scope.

Either way: respond by writing a checkpoint finding within your next iteration. The operator is watching.

## 2026-05-19T21:25:00Z — operator (David)

**Tooltips everywhere.** The UI has zero tooltips. Every interactive control needs a `title=` (HTML tooltip) or a custom hover popover explaining:

- What this button / control does
- What state it expects to be in to be valid
- What it will affect (which lane, which file, which phase)

Specific cases that surfaced this:
1. **Phase strip** (INIT / PLAN / CHALLENGE / BUILD / VERIFY / RUN / HEAL / OP-A / DRAIN / COM) — hover should show "PHASE-PLAN: lanes write their plan for the work. Synchronization barrier — no lane advances to BUILD until operator phase-flips." Etc. for each phase.
2. **Phase-flip control** (orchestrator-actions panel) — explain that flipping is operator-driven, takes a from/to pair, affects all 6 lanes immediately, locked by a `.phase-flip-locks/` directory while in progress.
3. **Reclaim button** on stale-lane rows — "Forces ownership of the stale lane back to ORCHESTRATOR. The agent will be told 'STALE-RECLAIMED' on next tick. Use when a lane is hung > 10 min."
4. **Permission banner Approve / Approve&remember / Deny** — already has a title on Approve&remember, generalize the pattern.
5. **Active claims panel rows** — hover should show full task description (currently truncated to task-id).
6. **Lane card status pill** (idle/working/blocked) — hover should show "Idle since X; last task: Y" or "Working on Z; claimed N min ago".
7. **Read-only / Control toggle** — explain what control mode unlocks.

**Implementation note**: Use HTML `title=` attribute for simple cases (browser-native tooltip, no JS needed). For complex multi-line explanations, use a small CSS `:hover` popover or a tiny custom component. Don't pull in a heavy library — base.css can handle this.

**Test**: Playwright suite should add `assert(await el.getAttribute('title') != null)` for every interactive control. Coverage tier — every button, every clickable thing.

GO. Don't let this become "I'll do it later" — every dashboard PR you ship from now on should add tooltips for any control it touches.
