# LANE-A AUDIT finding — S-NEXT-TICK-VISIBILITY shipped to disk but not to live BE (server not restarted)

- **Agent:** agent-0fa4 (LANE-A AUDIT)
- **Phase:** PHASE-PLAN
- **Discovered:** 2026-05-19T23-28-08Z
- **Severity:** HIGH (feature marked done but not exercising production code path; mis-leads operator + downstream PHASE-VERIFY)

## Summary

Cross-lane task `S-NEXT-TICK-VISIBILITY` was marked `[done: agent-d510 @ 2026-05-19T23:27:21Z]` with the STATUS note "BE+FE+tests+launch files". The disk source files contain a correct end-to-end implementation:

- `megalodon_ui/server.py:721-737` — reads `.fleet/<short>.next_tick.txt` and appends `{name, short, next_tick_utc}` to a `mission_lanes` list, then returns `"mission": {"phase": mission_phase, "lanes": mission_lanes}` at line 743.
- `ui/static/pages/dashboard.js:364-367` — keys `next_tick_utc` by lane name from `mission.lanes[]` (the variable is named `ml` for "mission lane").
- `.fleet/A.next_tick.txt`, `.fleet/C.next_tick.txt`, `.fleet/D.next_tick.txt` — three lanes are already writing the file (LANE-A added by this agent's prior iteration in compliance with the new step 10.5).

**However, the actual `/api/v1/state` response from the running BE does NOT include `mission.lanes` at all.** The end-to-end pipeline is broken because the running uvicorn process is from before the BE code change landed. The operator dashboard cannot render a countdown until someone restarts the server.

## Reproduction

1. Confirm the BE source code includes the new field:
   ```bash
   grep -n "next_tick_utc" megalodon_ui/server.py
   ```
   Yields:
   ```
   megalodon_ui/server.py:726:                next_tick_utc: str | None = None
   megalodon_ui/server.py:730:                        next_tick_utc = tick_path.read_text().strip() or None
   megalodon_ui/server.py:736:                    "next_tick_utc": next_tick_utc,
   ```

2. Confirm the next-tick files exist on disk:
   ```bash
   ls -la .fleet/*.next_tick.txt
   ```
   Yields three files (A, C, D) with content like `2026-05-19T23-27Z`.

3. Fetch live state and look for the field:
   ```bash
   curl -s http://127.0.0.1:8765/api/v1/state | grep -o 'next_tick_utc'
   ```
   Returns **zero** hits in the actual response payload. (The one `next_tick` occurrence in the JSON is inside the *description* field of the `S-NEXT-TICK-VISIBILITY` task itself, not a BE-emitted field.)

4. Confirm the running BE shape proves it's an older code build:
   The current `get_v1_state` in `server.py:738-748` returns:
   ```python
   "mission": {"phase": mission_phase, "lanes": mission_lanes}
   ```
   — i.e. only two keys. But the live response has:
   ```
   "mission": {"phase":"PHASE-PLAN","stuckFlipLock":{...},"history":[...],"events":[...],"id":"...","status":"ACTIVE"}
   ```
   — six keys including ones (`stuckFlipLock`, `history`, `events`, `id`, `status`) that the current source file does NOT emit. **The on-disk source and the running process diverge.** A restart is needed.

5. Independent corroboration of restart-staleness: `mission.stuckFlipLock.lock_age_seconds = 7450.5` (≈2h 4min). The BE has been up for ~2 hours; the `S-NEXT-TICK-VISIBILITY` commit completed ~2 minutes before this audit. The version mismatch is consistent with "BE not restarted since the change."

## Impact

1. **Feature is invisible to the operator.** Every lane card on the dashboard will show no countdown despite three of six lanes already publishing next-tick files.
2. **`S-NEXT-TICK-VISIBILITY` is incorrectly marked done.** A PHASE-VERIFY check against the running system will fail end-to-end. Either the task closure should be reopened, or a follow-up "deploy/restart" task is needed.
3. **Pattern risk for the rest of the mission.** Per the existing `BUG-STATUS-NOT-WRITTEN` task's own note ("Requires server restart for new endpoint AND for agents to read the re-baked launch files"), the fleet is aware that BE changes need restarts — but there is no automatic mechanism to enforce it. Any BE-touching task closed during this session is suspect until the restart-and-verify step is explicit.
4. **The new POST `/api/v1/status/update` endpoint may also be old-code.** My prior iteration successfully POSTed to it and got `"status":"applied"` — that endpoint is therefore older than the next_tick BE change. (Probably present before the 2-hour-old uvicorn started.) Worth verifying in PHASE-VERIFY.

## Root cause

`uvicorn` is not being run with `--reload`, and there is no fleet-level restart trigger when an agent edits `megalodon_ui/*.py`. The protocol invariant (`v9.3: NEVER directly edit shared group docs` ... but source code is per-task / single-owner) implicitly assumes a manual or external restart between code commits and "done" marking. That assumption is not stated in `launch-AUDIT.md` step 5–7 ("Do the work" → "Mark done" → "Append to history"). A "restart-and-verify" step is missing.

## Recommended fixes (ranked by blast radius)

1. **Immediate (one operator action):** restart the uvicorn process. Validate by re-running `curl -s http://127.0.0.1:8765/api/v1/state | grep next_tick_utc` — expect at least 6 hits (one per lane object).

2. **Short-term (launch-template fix):** add an explicit step to every `launch-<LANE>.md`:
   ```
   N. If your task changed code in `megalodon_ui/`, signal the operator via
      `feedback/OPERATOR.md` with a single line "RESTART-REQUESTED: <task-id>" and
      DO NOT mark done until you have re-fetched `/api/v1/state` and confirmed
      the new field/behavior appears in the response.
   ```
   Combine with task-id-stamped restart-acknowledgement messages.

3. **Medium-term (auto-restart):** configure uvicorn `--reload` watching `megalodon_ui/` for the dogfood mission only. The watcher's stat polling cost is negligible compared to the operator-friction of manual restarts. Already non-prod, so the `--reload` mode's slight memory overhead is acceptable.

4. **Long-term (atomic deploy primitive):** add a `/api/v1/admin/reload` POST endpoint (auth-gated) that exec's a graceful uvicorn reload. Make it part of the queue-mutation flow — when an agent closes a code-changing task via `task/done`, the applier checks `git diff` for `megalodon_ui/` changes and enqueues a `RELOAD` intent. The operator dashboard surfaces the pending reload as a banner with "Reload now / Defer to phase-flip" buttons.

## Cross-references

- TASKS.md `S-NEXT-TICK-VISIBILITY` (line 45) — marked done @ 2026-05-19T23:27:21Z
- TASKS.md `BUG-STATUS-NOT-WRITTEN` (line 58) — same class of problem, already acknowledged: "Requires server restart for new endpoint AND for agents to read the re-baked launch files"
- `findings/agent-d510-C-P1-next-tick-visibility-2026-05-19T23-26Z.md` — LANE-C's own finding (worth a re-read to see whether they noted the restart requirement)
- prior LANE-A finding `findings/agent-0fa4-A-P1-server-py-v9-3-endpoints-audit-2026-05-19T23-04-34Z.md` — earlier audit of v9.3 endpoint surface

## Verification step for operator

After restart:
```bash
curl -s http://127.0.0.1:8765/api/v1/state \
  | python3 -c 'import json,sys; m=json.load(sys.stdin)["mission"]; print(json.dumps(m.get("lanes",[]), indent=2))'
```
(I will NOT run this command — `python3` requires operator approval per launch-AUDIT.md permission model. Including as documentation only.)

Expect 6 lane objects, each with `next_tick_utc` either an ISO string or `null`.
