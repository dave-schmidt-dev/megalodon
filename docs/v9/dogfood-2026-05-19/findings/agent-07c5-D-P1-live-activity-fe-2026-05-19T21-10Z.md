# S-LIVE-ACTIVITY — FE side implemented — LANE-D agent-07c5

- **Lane:** LANE-D (FRONTEND)
- **Agent:** `agent-07c5`
- **Task:** `S-LIVE-ACTIVITY` (FE portion)
- **Phase:** PHASE 1 — PLAN
- **UTC:** 2026-05-19T21-10Z

## Summary

Implemented the frontend side of `S-LIVE-ACTIVITY`. The dashboard now polls per-lane activity summaries and renders them in expanded lane card drawers. BE endpoint is not yet implemented (signal sent to LANE-C), so the feature is present but silent until LANE-C delivers `GET /api/v1/lane/<short>/activity_summary`.

## What was implemented

### `ui/static/js/store.js`

- Added `activitySummaries: {}` slice to `initialState()`. Keyed by lane name; values are `{last_activity_utc, last_text, token_ctx, status}` from the BE endpoint.

### `ui/static/pages/dashboard.js`

**New helpers:**
- `parseTokenCtx(tokenCtx)` — parses `"52k/200k"` or `"52000/200000"` into `[used, total]` numbers. Returns `null` if unparseable.
- `fetchActivitySummary(short)` — fetches `GET /api/v1/lane/<short>/activity_summary`. Returns `null` on 404 or network error (graceful when BE not yet implemented).
- `pollActivitySummaries(configByLane)` — iterates all lanes with `short` codes; updates `store.activitySummaries` keyed by lane name.

**`renderLaneCard` additions:**
- Added `activitySummary` as 5th parameter.
- In the expanded drawer: renders an activity section when `activitySummary` is non-null:
  - `[data-testid="activity-status"]` — badge showing `active` / `idle` / `blocked`
  - `[data-testid="activity-last-tick"]` — relative age of last activity (e.g. "12s ago")
  - `[data-testid="activity-last-text"]` — "Currently: <last meaningful text>" truncated to 80 chars
  - `[data-testid="activity-token-bar"]` — progress bar + `Xk / Yk` label parsed from `token_ctx`

**`renderLaneGrid`** — reads `activitySummaries` from store; passes per-lane summary to `renderLaneCard`.

**`render()` additions:**
- `pollActivitySummaries(configByLane)` called on initial render.
- `setInterval(() => pollActivitySummaries(configByLane), 15_000)` for 15s refresh.
- `store.subscribe("activitySummaries", ...)` to re-render grid when data arrives.
- `clearInterval(activityTimer)` in cleanup.

## Test results

```
468 passed, 34 skipped, 3 xfailed, 7 failed (pre-existing tmux socket failures)
```

No regressions vs. previous iteration.

## What's still needed

The activity section only renders when the BE returns a 200 response. Until LANE-C implements `GET /api/v1/lane/<short>/activity_summary`, the expanded drawer shows the existing task/notes content but no activity summary section. This is the correct graceful behavior.

Signal to LANE-C was written at `signals/LANE-D-to-LANE-C-2026-05-19T20-30Z.md`.

## data-testid summary for BE integration testing

Once BE implements the endpoint, these testids should be verifiable:
- `[data-testid="activity-status"]` — inside expanded `[data-testid="lane-drawer-<LANE>"]`
- `[data-testid="activity-last-tick"]` — relative last-activity age
- `[data-testid="activity-last-text"]` — last stream text
- `[data-testid="activity-token-bar"]` — token progress bar (only when `token_ctx` present)
