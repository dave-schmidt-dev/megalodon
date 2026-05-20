# Finding: HIGH — server-restart trap blocks phase-flip + all shipped fixes

**Agent:** agent-0fa4
**Lane:** A (AUDIT)
**Phase:** PHASE-PLAN
**UTC:** 2026-05-20T00-24-07Z
**Severity:** HIGH (blocks safe operator phase-flip; blocks restart to load ANY shipped fix)
**Verifies (and contradicts):** `agent-f66a-B-P1-phase-plan-closure-ready-2026-05-20T00-20-07Z.md`

---

## TL;DR for the operator

**Do not restart the megalodon_ui server right now without addressing this first.**
The live server (pid 53741) is running a strictly-newer-than-disk version of
`megalodon_ui/server.py`. Multiple endpoints currently being relied on by the
dashboard and by /loop agents (including LANE-A and LANE-B in this very mission)
exist in the running process but **not** on disk. A restart silently drops 8
endpoints, breaking the dashboard's permission panel, per-lane state queries,
mission-event writes, feedback/followup mailbox, and the fleet teardown control.

This also means: **LANE-B's `phase-plan-closure-ready` claim at `00-20-07Z` is
half-right.** The PHASE-PLAN tasks are all `done`, yes — but the *consequence*
of acting on closure (operator presses phase-flip; operator restarts to load
all the PHASE-PLAN fixes that need restart per their own notes) silently breaks
the runtime. The flip itself is safe; the natural follow-on actions are not.

## Evidence

### Live server has the endpoints

Probed at `2026-05-20T00:24Z`:

```
GET  /api/v1/permission_prompts  → 200 OK
GET  /api/v1/lane/A/state        → 200 OK
DELETE /api/v1/fleet (via OPTIONS) → 405 (route exists, OPTIONS not allowed — endpoint registered)
```

### Working-tree server.py does NOT

```
$ grep -n "permission_prompts\|api/v1/lane\|api/v1/mission-event\|api/v1/fleet" megalodon_ui/server.py
(no matches)

$ grep -c "@.*\.post\|@.*\.get\|@.*\.delete" megalodon_ui/server.py
30
```

30 routes on disk; the 8 from C's gap list (below) are all absent.

### LANE-C independently surfaced this

`findings/agent-d510-C-BUG-bug-status-not-written-2026-05-20T00-18-38Z.md` §"Root Cause":

> The live server (pid 53741, started before the dogfood mission commits) had
> these endpoints from a prior `server.py` version. The current working-tree
> `server.py` was missing them.

C added 5 endpoints (`/auth/exchange`, `/status/update`, `/task/claim`,
`/task/done`, `/history/append`) to *match* the live server. C then explicitly
listed the remaining 8 still-missing endpoints:

```
GET  /api/v1/lane/{lane}/state
GET  /api/v1/lane/{lane}/pane-stream
GET  /api/v1/permission_prompts
POST /api/v1/permission_prompts/{lane}/respond
POST /api/v1/lane/{lane}/feedback
POST /api/v1/lane/{lane}/followup
POST /api/v1/mission-event
DELETE /api/v1/fleet
```

C scoped these to "other tasks (S-ORCHESTRATOR-AUTO-LOOP, S-LIVE-ACTIVITY, etc.)"
which is a defensible per-PR scoping decision. But it leaves the **cross-cutting
restart-trap unowned**, which is the AUDIT lane's job to surface.

## Why HIGH severity

### Restart is on the critical path of multiple already-shipped fixes

These fixes were marked `done` in this mission but explicitly noted "requires
server restart":

1. **BUG-PROMPT-FLICKER** (LANE-C `agent-d510` @ `2026-05-19T23:47:48Z`) — adds
   `permission_watcher.PermissionWatcher` with suppression-window. Per
   `agent-d510-C-P1-bug-prompt-flicker-fix-2026-05-19T23-47-27Z.md`: *"Server
   needs a restart to load the new `PermissionWatcher`."* But — restarting also
   drops `POST /api/v1/permission_prompts/{lane}/respond`, which IS the operator's
   approve-button on the permission panel. So restart loads the fix but breaks
   the channel that uses it.

2. **S-NEXT-TICK-VISIBILITY** (LANE-C `agent-d510` @ `2026-05-19T23:27:21Z`) —
   adds per-lane `.fleet/<short>.next_tick.txt` writes + BE exposure on
   `/api/v1/state`. Per my prior finding
   `agent-0fa4-A-P1-next-tick-feature-not-live-server-not-restarted-2026-05-19T23-28-08Z.md`:
   restart needed for `/api/v1/state` to expose the new field. Same restart
   drops the per-lane endpoints the dashboard uses to drive the per-lane cards.

3. **BUG-STATUS-NOT-WRITTEN** (LANE-C `agent-d510` @ `2026-05-20T00:18:38Z`,
   just now) — adds the 5 queue-proxy endpoints. Restart needed (and is the
   *primary motivator* for restarting in the first place). Same restart drops
   the 8 unrelated endpoints.

So **every restart-required fix is in the same trap**: restart loads the fix
AND simultaneously regresses 8 endpoints. There is no clean restart path.

### Operator phase-flip likely co-occurs with restart

Operators commonly batch "flip to PHASE-BUILD" with "restart to load
everything shipped in PHASE-PLAN". Even if the flip itself doesn't require a
restart (it's a queue write to `mission.phase`), the natural workflow is "flip
+ refresh". B's closure finding does not warn against this.

### Dashboard and /loop agents are both affected

- Dashboard breakage: permission panel, lane-card per-lane drilldown, signals,
  fleet-teardown button, history/feedback/followup interactions all break.
- /loop agents using the queue-proxy endpoints (LANE-A, LANE-B, LANE-D, LANE-F
  per recent STATUS history) are fine — those 5 endpoints are now in disk.
- /loop agents using `feedback/<LANE>.md` write-back via API would break, but
  no agent is doing that yet. Lower-priority concern.

## Why this slipped past LANE-B's closure assessment

B's `phase-plan-closure-ready` finding cross-checks **task completeness**
(every P1-* is `done`; every P2-* has a stable design dep). It does **not**
cross-check **runtime continuity** (will the current runtime state survive
phase-flip + likely-co-occurring restart). This is a real blind spot in the
closure model — completeness ≠ deployability.

This is not a flaw in B's analysis. It's a gap in **what "phase closure"
formally means**. AUDIT recommends extending the closure assessment template:

> A phase is closed when (1) every task is `done` AND (2) the runtime state of
> the system can survive the standard operator transition (restart, phase-flip,
> dashboard refresh) without regression of previously-shipped functionality.

## Recommendations

### Immediate (before any restart or phase-flip)

**R-1.** Operator: do not restart server until R-2 lands. Use the existing
running pid 53741 as-is.

**R-2.** Add a `P1-C-RESTART-PARITY` task (PHASE-PLAN, retro-injected) for
LANE-C: backfill the 8 missing endpoints from the live server into working-tree
`server.py`. Source of truth = live behavior. Suggested process:

- For each endpoint, capture the live request/response shape via curl probes.
- Reimplement in `_register_routes` with the same body/auth/error contract.
- Add minimal integration tests (one happy-path per endpoint) to lock the
  shape before the next restart.

**R-3.** After R-2 lands, operator restart can safely load:
- BUG-PROMPT-FLICKER fix
- S-NEXT-TICK-VISIBILITY exposure
- BUG-STATUS-NOT-WRITTEN endpoints (already loaded via R-2 work)
- All 8 backfilled endpoints

This unblocks PHASE-FLIP → PHASE-BUILD safely.

### Process improvement

**R-4.** Add a "live-vs-disk parity check" to the AUDIT charter for every
mission. The check: enumerate routes in running server (via OpenAPI dump or
exhaustive curl probe), diff against working-tree route registrations, file
HIGH-severity finding on any divergence. This is the kind of drift that
silently accumulates across long-running dogfood sessions.

**R-5.** Extend B's phase-closure template per my §"Why this slipped" — add
the runtime-continuity check as a required step.

## Out of scope here

- **Not** filing the per-endpoint backfill work as P1-C-RESTART-PARITY myself —
  AUDIT identifies, doesn't claim BE tasks. Recommending operator inject the
  task.
- **Not** running an exhaustive endpoint probe to enumerate every divergence —
  trust C's gap list as the authoritative diff for now; R-4 makes this a
  recurring AUDIT check.
- **Not** advocating to delay phase-flip beyond R-2. R-2 is small (8 endpoints,
  likely ~30-60 min for LANE-C) and unblocks everything else.

## Cross-refs

- `findings/agent-d510-C-BUG-bug-status-not-written-2026-05-20T00-18-38Z.md` (source: 8-endpoint gap list)
- `findings/agent-f66a-B-P1-phase-plan-closure-ready-2026-05-20T00-20-07Z.md` (contradicted: "phase-flip is safe")
- `findings/agent-0fa4-A-P1-next-tick-feature-not-live-server-not-restarted-2026-05-19T23-28-08Z.md` (prior: restart-staleness theme)
- `findings/agent-d510-C-P1-bug-prompt-flicker-fix-2026-05-19T23-47-27Z.md` (depends on `/permission_prompts/{lane}/respond`)

## Next-tick

Wake in 300s at 2026-05-20T00-29Z. Will re-check whether operator has acted on
R-1/R-2 or whether a phase-flip lands first.
