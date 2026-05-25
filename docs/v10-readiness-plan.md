# v10 Readiness Plan — making the fleet actually run

**Status:** 2026-05-25. Written after a parallel bug-hunt (6 agents) + first fix wave.
**Goal:** a *barely workable* product — a fleet that, on launch, runs autonomously,
seeds/claims real tasks, produces findings, and progresses phases without an
operator babysitting every keystroke.

---

## UPDATE 2026-05-25 — Architectural pivot: the "governor hook"

A long design session (auto-approver → MCP gateway → PreToolUse hook) reached a
new direction that supersedes the §1b auto-approver and reframes §9:

- **§1b is now solved structurally by a project-committed `PreToolUse` hook** (the
  "governor hook"), not by parsing permission-prompt previews. **Empirically
  validated 2026-05-25** on the live `claude` build: a hook returning
  `{"hookSpecificOutput":{"permissionDecision":"allow"}}` lets a tool run with **no
  `--allowedTools` entry and `permission_mode:default`** (kills the stall), a
  `"deny"` blocks it and feeds the model a reason, the hook receives the **real
  structured command string** on stdin (not a lossy TUI render), and it is
  configured per-project in `.claude/settings.json`. `deny` rules remain an
  un-bypassable backstop.
- **The §1b auto-approver (option A) is ABANDONED.** A GPT-5.5 contrarian review
  returned `spec-should-be-redone`: parsing the watcher's lossy, whitespace-
  collapsed, `Do-you-want`-truncated `command_preview` is unsound (e.g. `rg --pre`,
  `fd -x`, `git diff --ext-diff` execute code; `ls Do you want ; rm x` hides the
  suffix). The governor hook eliminates that whole class by operating on real input.
  See `docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md` (marked
  SUPERSEDED) and `verifications/2026-05-25-contrarian-readonly-auto-approver.md`.
- **Keep the live INTERACTIVE tmux lanes — do NOT pivot to a noninteractive
  orchestrator.** Verified against Anthropic's Help Center: effective **June 15,
  2026**, `claude -p`/Agent-SDK ("programmatic") usage draws from a separate,
  capped monthly credit (Pro $20 / Max5x $100 / Max20x $200, then API rates),
  while **interactive `claude` REPL sessions keep using the flat subscription**.
  The live-lane model is the subsidized bucket; a noninteractive rewrite would move
  all claude work into the metered/capped bucket. The governor hook lets us keep
  interactive billing AND run unattended.
- **MCP is reserved for NEW capabilities, not governance.** A future MCP server
  (megalodon protocol ops; the general A2A `call_agent` cross-CLI delegation tool)
  is an additive layer — separate from the hook, separate plan.
- **`permission_watcher.py` is slated for decommission** in favor of the governor
  hook; the dashboard/narrator move from pane-scraping to reading the hook's
  structured audit log. (Full implementation is the warp plan dated 2026-05-25.)

---

## 1. The core reframe

Most "regressions" were never bugs we re-introduced — the run was **never wired to
self-progress**. The fleet was built assuming a human/orchestrator drives task
seeding and phase flips, and several *mandated* agent commands were structurally
un-runnable. A run therefore stalls in INIT with an empty `TASKS.md` while lanes
heartbeat-idle (or get stuck on permission prompts). Fixing one symptom without
the others looked like "two steps back."

---

## 1b. TOP DESIGN RISK (operator flagged) — agents stall on exploration in the first 5 minutes

> **SUPERSEDED 2026-05-25 — see the "governor hook" pivot above.** Option (A)
> (the preview-parsing auto-approver) is abandoned; this stall is now solved by a
> project `PreToolUse` hook that allow/denies on the real command. The analysis
> below is retained for history.

Observed live: within 5 minutes of launch, multiple lanes ran `find … *.py` /
`find ui/static/` and **stalled on a permission prompt**. This is a *massive red
flag*, and the root cause is structural, not a one-off:

1. **Prose constraints don't reliably bind agent tool choice.** `launch.md` Step 0
   *already* said "NEVER use `find`" — agents read it and used `find` anyway. The
   `Glob`/`Grep` guidance added in `ab2494b` makes the rule actionable (names the
   alternative) but is still prose an agent can ignore. It lowers the stall rate;
   it does **not** guarantee zero stalls.
2. **Hardened-against-exploration surface vs. exploration-heavy tasks.** The P1
   tasks are "survey the codebase" — enumeration-heavy — on a tool surface that
   forbids enumeration. The very first action a survey lane takes is the one most
   likely to stall.

**The fix must be structural, not textual.** Options (decide tomorrow):
- **(A) Narrow auto-approver** — auto-approve prompts whose extracted pattern is a
  *read-only inspection* head (`find`/`ls`/`cat`/`head`/`tail`/`wc`/`grep`) with
  **no** dangerous flags (`-exec`, `-delete`, redirects, `;`/`|` to mutating
  commands). Makes benign exploration un-stallable regardless of which tool the
  agent reaches for; writes/network/destructive still gate to the operator.
  Security-reviewed allowlist + flag-denylist. *(permission_watcher.py + a policy)*
- **(B) Pre-baked manifest** — at spawn, hand each lane a generated file/dir
  listing (and a `Glob`/`Grep` cheat-sheet) so it never *needs* to enumerate.
- **(C) Both** — manifest removes most needs; auto-approver covers the rest.

Until one ships, an unattended run will keep stalling in the first minutes.
Recommend **(A)** as the smallest change that actually makes runs autonomous.

## 1c. TOP DESIGN RISK (operator flagged) — zero visibility into the narrator

The only narrator indicator in the UI is a single dot (`online`/`offline`) with a
tooltip. When it went offline tonight there was **no way to tell why** — diagnosis
required SSH-style digging through `ps`/`lsof` to discover an orphan `llama-server`
held the port. An operator has none of that. This is a black box.

**The narrator never reports: ** which state it's in (spawning / model-loading /
ready / degraded-why), the model path, the owned-child PID + whether it's alive,
the bound port + whether a *foreign* process holds it, last-narrate ok/latency/
error, or the consecutive-failure count.

**Fix (tomorrow):**
- **Backend:** add `GET /api/v1/narrator/health` (or extend `/state`) exposing
  `{state, model, port, owned_pid, owned_alive, port_owner_foreign, last_narrate:
  {ok, latency_ms, error}, consecutive_failures, ready}`. `NarratorRuntime`
  already holds most of this; surface it.
- **Frontend:** replace the bare dot with a small narrator status chip/panel —
  state + reason on hover/click (e.g. "offline: port 8085 held by another process",
  "loading model…", "ready · last narrate 420ms"). User-requested.
- This pairs with the **orphan-llama reclaim** fix (§3): reclaim the port *and*
  report when a foreign listener is detected.

---

## 2. Fixed this session (committed)

| Commit | Fix |
|--------|-----|
| `3ff8ef8` | Board reflects STATUS.md lane state when no TASKS.md row backs a lane |
| `d696fd2` | Board horizontal-scrollbar (grid `minmax(0,1fr)` + `min-width:0`) |
| `141ea41` | Narrator self-heals session_id via agent-id correlation; `session_log_dir` path mangling corrected |
| `7f463b3` | Loop heartbeat (`/loop 5m … run one tick`), `chmod +x scripts/*`, queue (history dup / fallback singleton / **tasks-inject CLI**), narrator robustness (`_capture_doc_order` crash, false-ready gate), frontend (store.js crash, SSE) |

Full Python suite: **1003 passed / 34 skipped / 1 xfail**. Board e2e: **51 passed**.

---

## 3. Remaining bugs (triaged, not yet fixed)

### Blocking the autonomous run
- **`tasks-inject` ignores phase sections** — inserts at the end (CROSS-LANE pool)
  instead of the task's phase (`PHASE 1 — PLAN`). Agents look under PHASE 1 and
  miss them. Fix: add `--section`/`--phase` (or infer `P1-*` → "PHASE 1 — PLAN")
  to `tasks_inject` + applier `_apply_tasks_inject`. *(megalodon_ui/queue/queue_client.py:263, applier.py `_apply_tasks_inject`)*
- **No INIT→PHASE-PLAN automation** — phase flips are operator-only by design;
  nothing seeds the first flip. Decide: orchestrator loop vs. auto-flip at scaffold.
  *(server.py:2201, `_state_read.py:77`)*
- **No fully unattended permission path** — only genuinely-novel commands should
  prompt now (chmod fixed `poll.py`), but `find` is still forbidden+unrememberable.
  Steer agents to native `Glob`/`Grep` in `launch.md`; consider an opt-in
  auto-approver gated on `_is_unbounded_tool`. *(harnesses/claude.py:42,150)*

### Spawn / lifecycle (self-heal mitigates narrator impact; resume still broken)
- **Session-id discovery ordering** — polls before the prompt creates the
  transcript; shared projects dir makes the "one new file" heuristic ambiguous.
  Fix: deliver prompt → then discover, correlating by agent-id. *(spawn.py:665)*
- **No background lane-death detection / auto-respawn** — a dead/hung lane is
  never restarted (probe is lazy, only on `/state`). Add a lifespan supervisor
  task. *(server.py:1610, spawn.py:507)*
- **`respawn()` never re-discovers session_id**; `.fleet/<lane>.session.txt` is
  written but never read → restarts lose conversation context. *(spawn.py:507, :310)*
- **`start_all` has no overall timeout**; detached `_deliver_initial_prompt` task
  is unawaited/uncancellable. *(spawn.py:436, :683)*

### Correctness (major)
- `_apply_status_update` enforces no row ownership (any lane can clobber another's
  STATUS row). *(applier.py:504)*
- Orphan `llama-server` on :8085 can serve a stale model (false-ready gate added;
  still no orphan reclaim). *(runtime.py:279)*
- Reconciler treats insert intents as idempotent → re-apply rejects a
  succeeded TASKS_INJECT. *(applier.py:463)*
- `AtomicFile.write` is seek/truncate, not temp+rename → crash can corrupt
  STATUS/TASKS. *(applier.py)*

### Minor
- digest `total_tokens` double-counts cache reads across turns *(digest.py:76)*;
  SSE stale-frame on disconnect + shallow cache snapshot *(server.py:3132, scheduler.py:168)*;
  PermissionWatcher single-slot pending drops stacked prompts *(permission_watcher.py:219)*;
  missing `on_change` wiring *(server.py:1252)*; activity-wall dead `scrollAtTop` +
  pause-cap *(activity_wall.js:558)*; `MISSION.md` lacks the task-assignment matrix
  the launch protocol references.

---

## 4. Design decisions needed (operator/owner call)

1. **Autonomous progression model.** Operator-driven orchestrator loop, OR
   auto-seed PHASE-1 + auto-flip INIT→PHASE-PLAN at scaffold? (Recommend: seed at
   scaffold + an orchestrator `/loop` for later flips; keeps a human gate at
   OPERATOR-ACCEPTANCE.)
2. **Unattended permissions.** Keep human-in-the-loop for novel commands
   (current, safe) vs. an opt-in auto-approver for `_is_unbounded_tool`-bounded
   patterns. (Recommend: keep human gate; widen the static baseline + Glob/Grep
   guidance so mandated commands never prompt.)
3. **Resume vs. fresh on restart.** Wire `--resume` from `.session.txt`, or accept
   fresh re-bootstrap each restart? (Self-heal already recovers the narrator path.)

---

## 5. Clean-restart procedure (operator)

The fleet must be stopped to re-seed cleanly (the applier owns TASKS.md while live).

```bash
cd ~/Documents/Projects/megalodon
M=runs/2026-05-24T22-14Z--v10-prep
# 1. stop server (keep applier or restart both)
kill -TERM $(lsof -nP -iTCP:8765 -sTCP:LISTEN -t | head -1)
# 2. reset mutable mission state, seed PHASE-1 under the right header, flip phase
#    (script TBD — see §3 tasks-inject section fix; until then author TASKS.md directly)
# 3. regenerate config + launch files from the fixed templates
# 4. relaunch
./scripts/launch_fleet.sh --mission-dir $M --spawn --port 8765
```

---

## 6. Roadmap to "workable"

- **M1 (done):** stop the bleeding — board truth, narrator, loop heartbeat, perms,
  queue/FE crashes. ✅
- **M2 (next):** `tasks-inject` section targeting + a `reset_and_seed` operator
  script + INIT→PHASE-PLAN; verify one full PLAN phase runs autonomously end-to-end.
- **M3:** spawn-lifecycle hardening — discovery ordering, lane-death supervisor,
  resume from `.session.txt`.
- **M4:** correctness sweep — STATUS owner enforcement, AtomicFile temp+rename,
  reconciler insert handling, orphan llama reclaim.
- **M5:** polish — token accounting, SSE teardown, PermissionWatcher multi-prompt,
  MISSION.md matrix, docs.

---

## 8. Tomorrow's sweep — valuable directions to find more

The 6-agent hunt (§3) covered the static/code layer well. The highest-value
*next* finds are **integration/runtime** bugs (only visible during a live
multi-tick run) and **subsystems not yet deeply audited**.

**A. Integration / runtime (start a fresh run, watch ≥30 min, then audit):**
- Does the `find`→`Glob` fix actually hold? Watch tick 2+ for any remaining
  permission stalls; log every command that still prompts.
- Multi-tick loop stability: do lanes keep ticking past tick 1 for 30+ min, or
  drift/stop? Does `/loop 5m` re-fire reliably for all 6?
- Narrator over time: does it recover + stay green; latency/timeouts under load;
  token-count accuracy on long transcripts.
- Concurrent-claim races: 6 lanes claiming/closing through the queue under real
  load — duplicate applies, lost updates, STATUS clobbering (§3 owner-enforcement).
- Phase progression: can the fleet actually move PLAN→BUILD→VERIFY (who flips,
  does the dashboard reflect it, do the phase-source defaults agree)?
- Stale-lane detection + restart-loop button: trigger a stuck lane, verify
  detection + recovery.

**B. Subsystems not deeply hunted this round:**
- Watchdog (`scripts/start_watchdog.sh`, stream-log size signal).
- Auth/cookie/CSRF surface + the token-exchange/paste-recovery flow.
- SSE backpressure / fan-out under many subscribers (queue overflow drop).
- Activity-wall 6-source merge correctness + pause/cap behavior under load.
- Phase-flip-locks mechanism + the orchestrator-tick design
  (`docs/v9/v9-3-ORCHESTRATOR-TICK.md`).
- Non-claude harness adapters (codex/gemini/copilot/cursor/vibe) — mostly
  untested in anger.
- `preflight` REPL + `new_run.sh` scaffold (does a fresh run seed correctly now?).

**C. Cross-cutting issues already spotted, not yet fixed:**
- Phase-source inconsistency: `server.py:2201` defaults INIT vs
  `_state_read.py:75` defaults PHASE-PLAN — pick one source of truth.
- `tasks-inject` has no phase-section targeting (rows land in CROSS-LANE).
- `AtomicFile.write` is seek/truncate not temp+rename (crash can corrupt files).
- Orphan-llama reclaim (bit us live every restart).

**How to run the sweep:** fan out read-only investigation agents per subsystem
(as in §3), but this time seed each with the live transcripts/logs from a 30-min
run so they audit *observed behavior*, not just code.

---

## 9. Strategic direction: a custom MCP server for agent tooling (operator-proposed)

**Idea:** replace the current "bounded `scripts/*` + CLI.md + custom launch
prompts + Bash allowlist" approach with a **custom MCP server** the lanes connect
to, exposing the fleet's capabilities as typed MCP tools — and, longer-term, a
tool to invoke *other* CLIs (codex/gemini/…) as delegated subagents.

**Why this is the right shape (it directly solves §1b):** MCP tools are
authorized as a unit (`mcp__megalodon__*`) and do **not** go through Claude Code's
per-command Bash permission prompt. So agents call structured, pre-authorized
tools instead of raw shell — the `find`/`python3` stall class disappears, and we
stop depending on prose (launch.md/CLI.md) to constrain tool choice. It also
replaces brittle allowlist-pattern matching (the `python3 scripts/poll.py`
deadlock, the `Bash(scripts/x:*)` head-matching fragility) with real typed tools
the agent discovers via MCP.

**Candidate tools:** `submit_intent`, `claim_task`, `close_task`, `poll_state`,
`read_findings`, `survey_files`(glob/grep wrapper), `append_history` — i.e. the
v9 protocol surface as MCP tools. Plus a guarded `run_cli(adapter, args)` for
cross-CLI delegation (this one needs its own allowlist/sandbox or it just moves
the unbounded-exec risk down a layer — design carefully; **Security > all**).

**Open questions / spike scope (tomorrow):**
- stdio vs HTTP MCP; how each lane's `claude` is launched with `--mcp-config`
  (wire into `spawn.py` / mission config).
- Map the existing `scripts/*` + queue intents to MCP tools 1:1 first (lowest
  risk), measure whether the permission stalls vanish, THEN consider `run_cli`.
- How this relates to the harness *adapters* (those spawn lanes; this is lanes
  *calling out*) — keep the two concerns separate.
- Does it subsume the §1b auto-approver? Likely yes for the protocol surface;
  raw exploration would still need either the MCP `survey_files` tool or the
  auto-approver.
- Effort: moderate. Build as a design spike + a thin MCP server mapping the queue
  client, prove the stall-elimination on one lane, then expand.

This is a promising v10 architecture bet; recommend a scoped spike tomorrow
before committing to it wholesale.

---

## 7. Session checkpoint (2026-05-25 ~03:30Z — STOPPED for the night)

**Commits this session:** `3ff8ef8`, `d696fd2`, `141ea41`, `7f463b3`, `c4f13aa`,
`ab2494b`. Full Python suite green (1003 passed); board e2e 51 passed.

**Live run state (`runs/2026-05-24T22-14Z--v10-prep`, server on :8765):**
- Clean-restarted with all fixes. Mission flipped to **PHASE-PLAN**; `TASKS.md`
  seeded `P1-A..P1-F` under PHASE 1. Standalone applier PID 74540 alive.
- Lanes re-spawned fresh (new agent-ids). META `working:P1-F`, AUDIT/ARCHITECT/
  BACKEND claimed/initialized. **FRONTEND/TEST + others parked on `find` permission
  prompts** (the exact stall — fixed by `ab2494b`, effective when lanes re-read
  the regenerated launch-*.md on their next 5-min tick; the 4 currently-parked
  prompts need a one-time approve OR will clear when the tick re-fires).
- Narrator: an orphan `llama-server` held :8085 (from a prior server) so the new
  owned child couldn't bind → readiness gate correctly kept it offline. Killed the
  orphan; new owned child (was PID 93525) is loading the model. `narrator_ok` should
  climb as lanes gain transcripts+STATUS rows. UI overflow: fixed (1440==1440).

**Immediate next steps on resume:**
1. Verify the next tick: lanes use Glob/Grep (no `find` prompts), claim remaining
   P1 tasks, produce findings → board RUNNING, not IDLE.
2. Confirm narrator dot goes green (model finished loading + lanes narratable).
3. **New high-priority code fixes surfaced live (add to M3/M4):**
   - **Orphan-llama reclaim** — `NarratorRuntime` must kill/await a stale listener
     on its port before spawning (this bit us every restart). *(runtime.py:279)*
   - **Narrator UI visibility** — the only indicator is a tiny dot; add a panel
     (online/offline, model, last-narrate ok/latency) so operators can see narrator
     health. (user-requested)
   - **Permission autonomy decision** — Glob/Grep guidance handles survey stalls,
     but a narrow auto-approver for read-only inspection (find/ls/grep w/o
     -exec/-delete/redirects) would make runs truly unattended. Security call.
4. Then: `tasks-inject` phase-section targeting (M2), spawn discovery ordering +
   lane-death supervisor (M3).
