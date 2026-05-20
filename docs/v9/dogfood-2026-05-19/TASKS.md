# Tasks — v9.3 Dogfood (live REPL + /loop)

Format: `- [ ] [LANE-X] <task-id> — <description>`

States: `[ ]` open · `[claimed: <agent-id> @ <UTC>]` · `[done: <agent-id> @ <UTC>]`

Claim via `mkdir claims/<task-id>/` per v9 protocol. Use ASCII task IDs only.

---

## PHASE 1 — PLAN

- [done: agent-0fa4 @ 2026-05-19T19-20-52Z] [LANE-A] `P1-A` — AUDIT plan: scan v9.2 codebase (`megalodon_ui/`, `ui/static/`, `scripts/tests/`) for protocol violations, race conditions, security issues, and dead code. Identify top-3 highest-risk findings. Output: `findings/agent-0fa4-A-P1-audit-plan-2026-05-19T19-20-52Z.md`.
- [done: agent-f66a @ 2026-05-19T20-06-30Z] [LANE-B] `P1-B` — ARCHITECT plan: design v9.3 spec for (a) generalizing `live_repl` to non-Claude adapters, (b) external loop driver pattern for CLIs lacking `/loop`. Reference `docs/v9/v9-2-ROADMAP.md` deferrals. Output: `findings/agent-f66a-B-P1-arch-plan-2026-05-19T20-06-30Z.md`.
- [done: agent-d510 @ 2026-05-19T20-10-00Z] [LANE-C] `P1-C` — BACKEND plan: read v9.2 Task 1.6 CV-9 deferral (server-owned stream-reader). Plan implementation: file path, integration with FleetSpawner, fallback to existing tail. Output: `findings/<agent>-C-P1-backend-plan-<UTC>.md`.
- [done: agent-07c5 @ 2026-05-19T20-48Z] [LANE-D] `P1-D` — FRONTEND plan: read the 4 deferred Playwright specs (`test_fe_phase_navigator_custom_config`, `test_fe_renders_with_custom_3_lane_config`, plus 2 others under `chromium-v92-dashboard`). Plan how to wire the fake-spawner fixture to activate them. Output: `findings/<agent>-D-P1-frontend-plan-<UTC>.md`.
- [done: agent-db2a @ 2026-05-20T00:14:17Z] [LANE-E] `P1-E` — TEST plan: enumerate all currently-skipped and xfailed tests (`pytest --collect-only -q | grep -E 'SKIP|XFAIL'`). Categorize as (a) reactivatable, (b) genuinely-blocked, (c) stale. Plan reactivation order. Output: `findings/<agent>-E-P1-test-plan-<UTC>.md`.
- [done: agent-d55b @ 2026-05-19T15:20:08Z] [LANE-F] `P1-F` — META plan: design observation framework for this dogfood run. Track tick activity per lane, time-to-first-claim, claim duration, idle gaps. Output: `findings/<agent>-F-P1-meta-plan-<UTC>.md`.

## PHASE 2 — BUILD

- [ ] [LANE-A] `P2-A` — AUDIT executes top-3 findings from P1-A: file specific bug reports or design ADRs. Output: `findings/<agent>-A-P2-audit-build-<UTC>.md` referencing each issue.
- [ ] [LANE-B] `P2-B` — ARCHITECT writes `docs/v9/v9-3-DESIGN.md` covering live_repl generalization + external loop driver pattern. Include adapter Protocol change proposal.
- [done: agent-d510 @ 2026-05-20T00:57:10Z] [LANE-C] `P2-C` — BACKEND implements server-owned stream-reader (CV-9). Add `megalodon_ui/stream_reader.py` or equivalent. Add unit + integration tests. Verify existing tail-based tests still pass.
- [claimed: agent-07c5 @ 2026-05-19T23:58:13Z] [LANE-D] `P2-D` — FRONTEND wires the 4 deferred Playwright specs. Adjust `ui/tests/e2e/playwright.config.ts` if needed. All 4 must pass on both chromium and webkit projects.
- [ ] [LANE-E] `P2-E` — TEST writes integration test for the new `live_repl` path: spawn a lane with `live_repl=true` and `initial_prompt="echo hello"`, verify pipe-pane output contains "hello" within 10s. Mark as `@pytest.mark.isolated` (real-tmux).
- [ ] [LANE-F] `P2-F` — META writes mid-mission report: per-lane tick counts, dominant failure modes, surprising behaviors. Output: `findings/<agent>-F-P2-meta-mid-<UTC>.md`.

## PHASE 3 — VERIFY

- [ ] [LANE-A] `P3-A-to-B` — AUDIT verifies ARCHITECT's v9-3-DESIGN.md is consistent with shipped v9.2 contracts.
- [ ] [LANE-B] `P3-B-to-C` — ARCHITECT verifies BACKEND's stream-reader matches the CV-9 design.
- [ ] [LANE-C] `P3-C-to-D` — BACKEND verifies FRONTEND's 4 reactivated specs actually exercise the right endpoints (no fake-green).
- [ ] [LANE-D] `P3-D-to-E` — FRONTEND verifies TEST's new live_repl integration test runs against the real adapter (not a mock).
- [ ] [LANE-E] `P3-E-to-F` — TEST verifies META's mid-mission report is grounded in actual claim/event data.
- [ ] [LANE-F] `P3-F-to-A` — META verifies AUDIT's findings are reproducible (re-run their test/scan commands).

## OPERATOR-ACCEPTANCE TASKS

- [ ] [ORCHESTRATOR] `OA-FINAL` — David reviews all findings + code changes, runs full test suite, signs off. Set MISSION.md status to COMPLETE.

## CROSS-LANE / SECONDARY TASK POOL

- [ ] [LANE-B+D] `S-HYBRID-DASHBOARD` — Design and implement hybrid v9.3 dashboard. v9.2 panes-only mode loses orchestration visibility; v9.0 chrome-only loses live REPL drilldown. Operator wants both: v9.0 chrome by default (lane cards w/ model + cadence + current task + next-tick ETA, TASKS list, STATUS table, claims, recent HISTORY, signals between lanes, activity feed), with a "View terminal" affordance per lane card that opens the xterm pane as a modal/drawer. ARCHITECT writes design, FRONTEND implements. Output: `docs/v9/v9-3-HYBRID-DASHBOARD.md` + working UI.
- [done: agent-d510 @ 2026-05-19T23:27:21Z] [LANE-C] `S-NEXT-TICK-VISIBILITY` — Agents in /loop mode call `ScheduleWakeup(delaySeconds, prompt)` but the scheduled wakeup time isn't visible to the operator. Implement: per-lane `.fleet/<short>.next_tick.txt` file written by the agent each iteration; BE exposes `mission.lanes[i].next_tick_utc` in `/api/v1/state`; FE renders countdown in lane card. Output: spec + BE + FE + test.
- [done: agent-07c5 @ 2026-05-19T20-30Z] [LANE-D] `S-LANE-CARD-DETAILS` — v9.0 lane cards hide model/harness/cadence behind "Show details". Default-show: model, harness, cadence, current task, last-tick-ago. Output: ui/static/pages/mission.js change + Playwright assertion.
- [ ] [LANE-C+D] `S-LIVE-ACTIVITY` — v9.0 dashboard has no visibility into agent activity between mission events (PHASE-FLIP / RECLAIM / etc). Bootstrap, thinking, blocked-on-prompt, token usage — all invisible. Add: BE endpoint exposing per-lane stream tail summary (last activity timestamp, last meaningful text, token-context parsed from Claude TUI footer); FE renders "Currently: ..." + "Last activity: 12s ago" + token-usage bar in expanded lane card. Stream signal comes from existing pipe-pane logs, no new infrastructure needed.

- [ ] [LANE-B+C] `S-ORCHESTRATOR-AUTO-LOOP` — At fleet startup, launch a 7th Claude Code session (or out-of-band) that is /loop'd to monitor the fleet on the operator's behalf. Each iteration: GET /api/v1/permission_prompts and auto-approve a curated safe-list (ls / find / grep / cat / head / tail / wc + the v9 protocol primitives); detect stuck claims (held > 10 min with no finding) and write to feedback/<LANE>.md asking what's blocking; PushNotification to operator on significant events (phase boundaries, completed tasks, stuck lanes). Operator's role becomes high-stakes-decisions only. ARCHITECT writes the protocol contract; BACKEND implements the wrapper script + safelist policy.

(Operator can also inject tasks during the run via the dashboard's inject-task control.)


## OPERATOR-INJECTED (live)

- [done: agent-07c5 @ 2026-05-19T22:21:17Z] [LANE-D] `S-TOOLTIPS-EVERYWHERE` — UI has zero tooltips on interactive controls (phase strip, phase-flip control, reclaim button, permission banner buttons, active-claims panel rows, lane card status pills, read-only/control toggle). Operator can't discover what any button does without trying it. Add HTML `title=` attributes (simple cases) and CSS `:hover` popovers (complex multi-line). Every interactive control must explain: what it does, what state expected, what it affects. Playwright assertion: every interactive control has a non-empty `title` or aria-describedby. See `feedback/FRONTEND.md` (2026-05-19T21:25:00Z) for the full spec.
- [done: agent-d510 @ 2026-05-19T23:47:48Z] [LANE-C] `BUG-PROMPT-FLICKER` — After operator approves a permission prompt, the prompt disappears from the panel, RE-APPEARS for ~1s, then goes away for good. Root cause: BE permission_watcher.clear_lane() sets pending=None, but the next 1s scan reads the lane stream tail and the prompt marker is still in the rendered TUI text (Claude REPL has not yet cleared the prompt block after receiving the "1" keystroke). Watcher re-detects + re-adds. Fix: add a suppression window (~5s) per lane after clear_lane() is called externally; skip detection during the window. Add to megalodon_ui/permission_watcher.py PermissionWatcher. Add unit test asserting no re-detection during the suppression window even if stream tail still contains marker.
- [done: agent-d510 @ 2026-05-20T00:19:08Z] [LANE-C] `BUG-STATUS-NOT-WRITTEN` — Lane cards show "—" for state, "no notes", "task: —" even for lanes with active claims (e.g. LANE-D agent-07c5 on S-TOOLTIPS). Root cause: agents claim via mkdir but never write to STATUS.md, so dashboard has nothing to render. Fix: launch template tells agents to call POST /api/v1/status/update (new in v9.3) on every claim/release/state-change. Requires server restart for new endpoint AND for agents to read the re-baked launch files.
- [done: agent-07c5 @ 2026-05-19T23:22:27Z] [LANE-D] `BUG-HISTORY-UNREADABLE` — Recent HISTORY panel shows `2026-05-19T21-36Z | agent-d55b | F | P1` — operator cannot tell what happened. Need: human-readable summary including finding-topic (parsed from filename slug), task-id (full), severity badge, click-to-open finding. Also: rename "Recent HISTORY" header (the data is findings-list-as-proxy per Ds note, not actual HISTORY.md). Fix: enrich list rendering in dashboard.js renderHistoryTail. Operator quote: "so opaque as to be useless."
- [done: agent-07c5 @ 2026-05-19T23:10:38Z] [LANE-D] `BUG-PHASE-INDICATOR-STUCK` — Phase strip in dashboard header still highlights INIT even after operator phase-flipped to PHASE-PLAN ~30 min ago. BE confirms mission.phase=PHASE-PLAN via /api/v1/state. FE store.subscribe("mission.phase") not firing on hydrate. Suspect: store.hydrate() does store.set("mission", payload.mission) but the nested-key subscriber for "mission.phase" doesnt fire on parent-object replacement. Fix: explicitly emit "mission.phase" subscriber notification when mission obj changes AND .phase differs. Add Playwright regression.
