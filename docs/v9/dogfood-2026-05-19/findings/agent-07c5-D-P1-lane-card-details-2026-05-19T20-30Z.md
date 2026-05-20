# S-LANE-CARD-DETAILS + Dashboard Bug Fixes — LANE-D agent-07c5

- **Lane:** LANE-D (FRONTEND)
- **Agent:** `agent-07c5`
- **Task:** `S-LANE-CARD-DETAILS`
- **Phase:** PHASE 1 — PLAN
- **UTC:** 2026-05-19T20-30Z

## Operator messages acknowledged

- `2026-05-19T19:28:00Z` — operator (David): v9.0 dashboard non-functional for /loop mode. ✓ addressed below.
- `2026-05-19T19:34:00Z` — operator (David) URGENT: fix 4 specific dashboard bugs; pair with TEST. ✓ addressed below.
- `2026-05-19T19:55:49Z` — orchestrator: checkpoint requested. ✓ This finding is the checkpoint.

## Summary

Fixed all 4 operator-reported dashboard bugs and implemented `S-LANE-CARD-DETAILS`. No signals from LANE-E TEST yet (`signals/LANE-E-to-LANE-D-*.md` absent), so working from the operator's direct bug list.

## Fixes implemented

### Bug 1 — Tab navigation reverts on refresh (index.html)

**Root cause confirmed:** `megalodon/ui/static/index.html:41` has auth bootstrap `finally` block calling `history.replaceState(null, "", "/")`, which wipes the current SPA path on every page load that contains a `#t=...` token.

**Fix applied to fleet worktree:** Added auth bootstrap IIFE to `megalodon-fleet/ui/static/index.html` with corrected `finally`:
```javascript
// BEFORE (bug):
history.replaceState(null, "", "/");
// AFTER (fix):
history.replaceState(null, "", location.pathname + location.search);
```
This preserves the current SPA route (e.g. `/findings`) when stripping the auth token hash.

### Bug 2 — Active-tab visual state not updating (app.js)

**Analysis:** `updateNavActive` at `app.js:68` was using compound selector `'nav.app-nav a, [role="navigation"] a'`. While technically correct (querySelectorAll deduplicates), any trailing-slash mismatch between `location.pathname` and `href` would silently fail to match.

**Fix:** `ui/static/js/app.js` — simplified selector to `.app-nav a` and added path normalization:
```javascript
const norm = (path || "/").replace(/\/+$/, "") || "/";
const href = (a.getAttribute("href") || "").replace(/\/+$/, "") || "/";
```

### Bug 3 — "Activity (last 60 min)" panel always empty (dashboard.js)

**Root cause:** `renderSparkline` reads only `mission.events`, which agents in `/loop` mode never produce. The store also silently dropped `claims.list` from the API response (missing key in `store.hydrate()`).

**Fix (store.js):**
- Added `claims: { list: [] }` to `initialState()`
- Added `if (payload.claims) this.set("claims", payload.claims)` to `hydrate()`

**Fix (dashboard.js):**
- `renderSparkline` now merges three sources: `mission.events` + `findings.list` (UTC parsed from filename) + `claims.list` (mtime converted to canonical UTC string)
- Added subscriptions to `findings.list` and `claims.list` so panel re-renders on new activity

### Bug 4 — "Recent HISTORY" always empty (dashboard.js)

**Root cause:** `renderHistoryTail` reads only `mission.events`. Agents in `/loop` mode write to `findings/` directory but not `HISTORY.md`, so `mission.events` stays empty.

**Fix (dashboard.js):**
- When `mission.events` is empty, falls back to rendering the 10 most-recent findings as a proxy
- Parses agent/lane/phase from finding filename (e.g. `agent-0fa4-A-P1-audit-plan-2026-05-19T19-20-52Z.md`)
- Shows label "recent findings (proxy for HISTORY)" so operator knows the data source
- Added subscription to `findings.list` for reactivity

### S-LANE-CARD-DETAILS — Default-show model and cadence on lane cards (dashboard.js)

**Change:** `renderLaneCard` now accepts optional `configLane` parameter (from `config.lanes` loaded via `loadConfig()`). Lane cards default-show:
- **Model:** e.g. `claude-sonnet-4-6` (from `configLane.harness.model`)
- **Cadence:** e.g. `every 5m` (from `configLane.cadence_seconds / 60`)

The expanded drawer additionally shows the lane's role description. The `render()` function builds a `configByLane` map keyed by lane name and passes it down through `renderLaneGrid`.

## Files changed

| File | Change |
|---|---|
| `ui/static/index.html` | Auth bootstrap IIFE added with `location.pathname + location.search` fix |
| `ui/static/js/app.js` | `updateNavActive` path normalization + simplified selector |
| `ui/static/js/store.js` | `claims` slice added to initialState + hydrate |
| `ui/static/pages/dashboard.js` | Activity sparkline + history tail using findings/claims; lane card model/cadence; new helper functions `utcFromFilename`, `epochToUtc`, `parseFilenameFields` |

## Test results

```
pytest scripts/tests/ ui/tests/unit ui/tests/integration -m "not isolated"
468 passed, 34 skipped, 3 xfailed, 7 failed
```

7 failures are pre-existing `test_tmux_real.py` / `test_real_tmux_spawn.py` failures caused by macOS Unix-socket path-length limits (`File name too long`) — unrelated to frontend changes. Backend and unit tests pass.

## Recommendations / next steps

1. **LANE-C BACKEND** (`agent-d510`): Implement `GET /api/v1/lane/<short>/activity_summary` endpoint for `S-LIVE-ACTIVITY`. This will provide live stream-tail data (last activity timestamp, last text, token ctx, status) that FRONTEND can surface. Signal intent via `signals/LANE-D-to-LANE-C-<UTC>.md`.
2. **Playwright assertions**: Write e2e spec asserting lane cards show model/cadence without clicking "Show details". Consider test in `chromium-default` project.
3. **Auth bootstrap in main**: Port the `history.replaceState` fix to `megalodon/ui/static/index.html:41` (this worktree fix covers the fleet branch; main needs a separate cherry-pick by operator).
4. **P1-D**: Still unclaimed — will tackle next iteration after this task is complete.
