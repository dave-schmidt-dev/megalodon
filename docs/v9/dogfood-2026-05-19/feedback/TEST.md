# Operator feedback — LANE-E TEST

This file is the operator's async channel to you. Read at the start of EVERY iteration; act on any unprocessed messages (compare timestamps to your prior findings).

---

## 2026-05-19T19:34:00Z — operator (David)

**URGENT — drop your current P1-E plan task; pair with FRONTEND (LANE-D `agent-07c5`) NOW.** Megalodon is running live, the dashboard UI is severely broken, and we need adversarial Playwright coverage of every page + button immediately so FRONTEND has a failing-test queue to drive fixes against.

### What you should do this iteration

1. **Read `feedback/FRONTEND.md`** — same operator complaint, full context. Don't duplicate; cooperate.

2. **Write a new Playwright suite** at `ui/tests/e2e/test_dashboard_live_audit.spec.ts` that exercises every page + every interactive control in the v9.0 chrome. At minimum:
   - **Navigation tab integrity** — click every nav link in `nav.app-nav` (Dashboard, Tasks, Findings, Signals, Mission); assert the URL pathname matches the link's href AFTER click; assert `a[aria-current="page"]` matches the clicked link; assert the page content corresponds. **Then refresh the page (`page.reload()`) and assert the tab state survived** — the operator reports tabs revert to Dashboard after refresh.
   - **Findings page** — assert findings load, are clickable, content renders. Operator reports the page is non-functional.
   - **Tasks page** — assert TASKS.md content renders, phase tabs work, inject-task form submits.
   - **Signals page** — assert signals render.
   - **Mission page** — assert MISSION.md, orchestrator actions, HISTORY tail render.
   - **Active claims panel** (new, top of dashboard) — assert claims render with `data-testid="active-claim-<task-id>"` when claims exist.
   - **Permission prompt panel** (new, top of dashboard) — assert prompts render when prompts pending.
   - Cover BOTH `chromium-default` and `webkit-default` projects (operator uses Safari).

3. **Don't use the existing fix-medium fixture** — it doesn't reflect the live mission state. Either spawn a real mini-fixture in the test (use `/tmp/test-live-audit-X/`) OR run against the live mission dir (read-only assertions only).

4. **Run the suite. Every failure is a bug for FRONTEND.** File one finding per category of failure: `findings/{{AGENT_ID}}-E-P1-dashboard-audit-<topic>-<UTC>.md`. Include: spec name, what failed, exact selector, expected vs actual, screenshot path if Playwright captured one.

5. **Coordinate with FRONTEND via `signals/`**: write a brief `signals/LANE-E-to-LANE-D-<UTC>.md` listing the failing-test queue with one-line summaries + finding pointers. FRONTEND reads this each iteration, picks one, fixes it, marks the signal acknowledged.

6. **Don't claim P1-E** until this dashboard audit is in flight. Reframe P1-E as: "TEST plan + dashboard live audit (under operator-priority override)." Mark it done only after the suite exists and at least 3 failures are documented.

### Known bugs (start here)

- `ui/static/index.html:41` — the auth bootstrap IIFE's `finally` clause unconditionally `history.replaceState(null, "", "/")` on every page load that had a token in the hash. This drops the user's current path on hard-refresh. Repro: load `localhost:8765/findings#t=<token>` → URL becomes `/`. Should be: replaceState to current pathname, preserving the navigation target.
- Active-tab visual indicator may not update on refresh — `updateNavActive(path)` is called from `mountPage` but the path may not match what's in the nav links if there's any URL mismatch.
- "Activity (last 60 min)" panel shows "no activity yet" even when 4 lanes have active claims. The panel reads `mission.events` which only contains PHASE-FLIP/RECLAIM events, not lane work. This is a design bug but file it.
- "Recent HISTORY" panel similarly silent because no HISTORY.md entries are being written by agents yet.

### Process

- Use Playwright tools (Read/Write/Edit + Bash for `npx playwright test`). The `Bash(npx:*)` invocation will trigger an operator approval — that's fine, operator will approve.
- Run tests with `./scripts/run_e2e.sh test_dashboard_live_audit.spec.ts` for both engines.
- Don't break existing 56 Playwright tests. Run the full suite at the end and verify all pass.
- Write findings with concrete file refs + line numbers. Operator will read them.

**Don't reply via chat / REPL** — write a finding acknowledging this message + listing your plan. Then GO.

## 2026-05-19T19:55:49Z — orchestrator (auto-monitor)

You've held your claim for 33+ minutes with no finding written. The pipe-pane stream shows you're actively thinking, not blocked on a prompt. Two possibilities:

1. **Heavy iteration** — fine, but please write an intermediate checkpoint finding (`findings/<agent-id>-<LANE>-P1-checkpoint-2026-05-19T19:55:49Z.md`) describing what you've done so far + what's left, so the operator has visibility.
2. **Blocked silently** — if you're spinning on something (e.g. trying a tool that prompts and then bouncing off, deep nested research, etc.), STOP, release the claim, write a finding describing what blocked you, then re-claim with a narrower scope.

Either way: respond by writing a checkpoint finding within your next iteration. The operator is watching.
