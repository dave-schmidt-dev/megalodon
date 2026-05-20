# Finding: BUG-HISTORY-UNREADABLE — enriched findings rendering

**Agent:** agent-07c5 | **Lane:** D FRONTEND | **Phase:** PHASE-PLAN
**Task:** BUG-HISTORY-UNREADABLE
**UTC:** 2026-05-19T23-21Z
**Severity:** INFO

## Problem

The "Recent HISTORY" panel rendered opaque rows: `2026-05-19T21-36Z | agent-d55b | F | P1`. The operator had no way to know what happened in each finding. No topic slug, no severity badge, no click-to-open.

## Root cause

`parseFilenameFields` in `dashboard.js` stopped parsing after `phase` (index 3) and ignored `parts.slice(4)` which contains the human-readable topic slug. The rendered output also used the pipe-separated raw format with no interactive affordance.

## Fix — `ui/static/pages/dashboard.js`

### 1. `parseFilenameFields` now extracts `topic`

```diff
-  const phase = parts[3] || "—";
-  return { agentId, laneShort, phase };
+  const phase = parts[3] || "—";
+  const topic = parts.slice(4).join(" ") || "—";
+  return { agentId, laneShort, phase, topic };
```

`agent-07c5-D-P1-bug-phase-indicator-stuck-2026-05-19T23-04Z.md`
→ topic = `"bug phase indicator stuck"` (was invisible before).

### 2. `LANE_SHORT_TO_NAME` mapping added

Maps `D → FRONTEND`, `C → BACKEND`, etc. so lane chips show proper color from `base.css` lane-chip classes.

### 3. `severityFromTopic` helper added

Returns MAJOR (bug/error/fail/broken/stuck/critical), DELTA (idle/scratch/checkpoint), or NIT (default) — mapped to existing `severity-badge` CSS classes.

### 4. Enriched finding row layout

Each row now renders:
- Top row: colored lane chip + **bold topic text** + severity badge
- Meta row: `agentId · phase · utc` at 60% opacity

Clicking a row navigates to `/findings` for full detail. `tabindex="0"` and `onkeydown` added for keyboard accessibility.

### 5. Header renamed

"Recent HISTORY" → "Recent Findings" (the data is findings, not HISTORY.md content).

## Evidence

Python test suite: **479 passed, 0 new failures** (8 pre-existing failures: 7 tmux integration tests requiring live sessions, 1 constants drift from LANE-C's API_LANE_ACTIVITY addition without JS regen).

Playwright e2e: blocked by pre-existing socket-path-too-long issue (mission path > 100 bytes for uvicorn socket). Not caused by this change. Tests that check `[data-testid="history-tail"]` visibility are unaffected (element still renders with same testid).

## Files changed

- `ui/static/pages/dashboard.js` — parseFilenameFields, severityFromTopic, LANE_SHORT_TO_NAME, renderHistoryTail, card header

## Operator feedback acknowledged

- `2026-05-19T19:34:00Z` — pair-up with LANE-E / fix specific bugs (addressed multiple iterations)
- `2026-05-19T21:25:00Z` — S-TOOLTIPS-EVERYWHERE (done in previous iteration)
- `2026-05-19T19:28:00Z` — S-LIVE-ACTIVITY / S-LANE-CARD-DETAILS (done in previous iterations)
