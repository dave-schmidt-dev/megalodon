# LANE-E Audit Finding — Tasks / Signals / Mission Page Missing Elements
**Agent:** agent-db2a | **Lane:** LANE-E (TEST) | **Task:** P1-E
**UTC:** 2026-05-20T00-11Z | **Severity:** MAJOR
**Refs:** `ui/static/pages/tasks.js`, `ui/static/pages/signals.js`, `ui/static/pages/mission.js`

## Summary

Five tests fail across tasks, signals, and mission pages on both browsers.
Root cause in each case: missing `data-testid` attributes or missing UI sections.

## Failures

### 1. BUG-TASKS-INJECT-FORM: inject-task form not visible after control mode toggle
**Test:** `inject-task form exists in control mode` (line 141)
**Selector:** `[data-testid="inject-task-form"], form[data-testid*="inject"]`
**Expected:** After clicking `action-toggle-control-mode`, a form appears
**Actual:** No matching element found → timeout ~5s
**Root cause:** The inject-task form may not have a `data-testid="inject-task-form"` attribute, OR the form doesn't appear when control mode is activated from the tasks page (it may only appear from the mission page).

### 2. BUG-SIGNALS-FILTER-BAR: no filter bar on signals page
**Test:** `signals page shows filter chips for sender lanes` (line 174)
**Selector:** `[data-testid="signals-filter-bar"], [aria-label*="filter" i]`
**Expected:** A filter bar element exists on `/signals`
**Actual:** Element not found → assertion fails in ~150ms
**Root cause:** `signals.js` renders a swim-lane chart but no filter bar.

### 3. BUG-MISSION-BODY: `data-testid="mission-body"` / `"mission-content"` absent
**Test:** `mission page shows MISSION.md rendered content` (line 194, 213)
**Selectors:** `[data-testid="mission-body"]`, `[data-testid="mission-content"]`
**Expected:** The rendered MISSION.md content has a `data-testid` for targeting
**Actual:** Element not found → timeout ~8s (page renders content but without the testid)

### 4. BUG-MISSION-HISTORY-TAIL-TESTID: history-tail section absent on mission page
**Test:** `mission page shows recent HISTORY tail section` (line 213)
**Selector:** `[data-testid="history-tail"], [aria-label*="history" i]`
**Expected:** A history section visible on `/mission`
**Actual:** Not found → timeout ~5s
**Note:** `[data-testid="history-tail"]` EXISTS on the dashboard (`/`) and passes there. It is absent from the mission page.

## Tests That Pass (for context)

- Tasks page renders task cards (`data-testid^="task-card-"`) ✓
- Tasks page has phase tab bar with ≥2 tabs ✓
- Signals page renders without JS errors ✓
- Mission page renders content (not "Loading…") ✓
- Mission page exposes orchestrator actions panel ✓

## Recommendations for LANE-D

1. **tasks.js**: Add `data-testid="inject-task-form"` to the inject-task form. Verify the form appears on `/tasks` (not just `/mission`) after control-mode toggle.
2. **signals.js**: Add a filter bar with `data-testid="signals-filter-bar"` and per-sender chips.
3. **mission.js**: Add `data-testid="mission-body"` to the rendered MISSION.md container.
4. **mission.js**: Add `data-testid="history-tail"` to the recent HISTORY section (mirrors dashboard).

All four changes are additive (`data-testid` attrs + minor structural fixes) with no logic changes.
