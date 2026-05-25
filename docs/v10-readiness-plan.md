# v10 Readiness Plan — making the fleet actually run

**Status:** 2026-05-25. Written after a parallel bug-hunt (6 agents) + first fix wave.
**Goal:** a *barely workable* product — a fleet that, on launch, runs autonomously,
seeds/claims real tasks, produces findings, and progresses phases without an
operator babysitting every keystroke.

---

## 1. The core reframe

Most "regressions" were never bugs we re-introduced — the run was **never wired to
self-progress**. The fleet was built assuming a human/orchestrator drives task
seeding and phase flips, and several *mandated* agent commands were structurally
un-runnable. A run therefore stalls in INIT with an empty `TASKS.md` while lanes
heartbeat-idle (or get stuck on permission prompts). Fixing one symptom without
the others looked like "two steps back."

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

## 7. Session checkpoint (2026-05-25 ~03:25Z — pause/compact point)

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
