# LANE-E Audit Finding — Missing Dashboard Panels + Mission Tooltip Gaps
**Agent:** agent-db2a | **Lane:** LANE-E (TEST) | **Task:** P1-E
**UTC:** 2026-05-20T00-11Z | **Severity:** MAJOR (panels) / MINOR (tooltips)
**Refs:** `ui/static/pages/dashboard.js`, `ui/static/pages/mission.js`

## Summary

Two missing dashboard panels (expected, not yet implemented) plus tooltip gaps
on the mission page. Also one cross-browser regression in `store.js`.

## Failures

### 1. MISSING-FEATURE: active claims panel (both browsers)
**Test:** `active claims panel renders with data-testid per claim` (line 260)
**Selector:** `[data-testid^="active-claim-"]`
**Expected:** Dashboard shows live claim rows from the `claims/` directory
**Actual:** No such element — panel not implemented
**Status:** EXPECTED failure (per operator spec, not yet built)

### 2. MISSING-FEATURE: permission prompts panel (both browsers)
**Test:** `permission prompts panel renders when prompts are pending` (line 270)
**Selector:** `[data-testid="permission-prompts-panel"]`
**Expected:** Dashboard shows pending permission prompts
**Actual:** No such element — panel not implemented
**Status:** EXPECTED failure (per operator spec, not yet built)

### 3. BUG-TOOLTIPS-MISSION-PAGE: mission page controls lack title= attributes
These tests fail on BOTH browsers (expected fail per S-TOOLTIPS spec):
- `phase flip submit button has title attribute` (line 415) → `[data-testid="action-submit-flip-mission"]` visible but no `title=`
- `phase flip target buttons have title attributes` (line 425) → `[data-testid^="flip-target-"]` visible but no `title=`
- `reclaim button in stale panel has title attribute` (line 438) → `[data-testid="confirm-reclaim"]` not found or no `title=`

LANE-D's S-TOOLTIPS fix added tooltips to dashboard controls but missed mission page buttons.

### 4. BUG-STORE-PHASE-CHROMIUM: store.set("mission") subscriber notification (chromium only)
**Test:** `phase strip updates when store.set("mission", ...) is called directly` (line 345)
**Result:** ✘ FAIL on chromium-default, ✓ PASS on webkit-default
**Root cause:** The `store.js` fix for BUG-PHASE-INDICATOR-STUCK works in webkit but not chromium.
The test calls `store.set("mission", {...})` via `page.evaluate` and expects the phase-strip subscriber to fire.
In chromium, the subscriber is either not registered at eval time or the nested-key notification doesn't fire.
This cross-browser gap suggests a timing issue: the module import in `page.evaluate` may resolve differently between engines.

## Positive Findings (Expected Failures That Now Pass)

These tests were marked `[MISSING-FEATURE]` but PASS on both browsers — confirming LANE-D's work:
- Lane cards show model/harness by default ✓ (S-LANE-CARD-DETAILS done)
- Lane cards show last-tick-ago by default ✓ (S-LANE-CARD-DETAILS done)
- Phase strip segments have title attributes ✓ (S-TOOLTIPS done for header)
- Control-mode toggle has title attribute ✓
- Lane card state badge has title attribute ✓
- Lane card show-details toggle has title attribute ✓
- Phase strip reactive to hydration (both browsers) ✓ (BUG-PHASE-INDICATOR-STUCK fixed)
- auth IIFE path preservation test ✓ (already fixed in index.html)

## Recommendations

1. **LANE-D dashboard.js**: Implement active-claims panel (iterate `claims/` list from `/api/v1/state.status.lanes[*].claim_id`).
2. **LANE-D dashboard.js**: Implement permission-prompts panel (requires new `/api/v1/permission_prompts` endpoint from S-ORCHESTRATOR-AUTO-LOOP design).
3. **LANE-D mission.js**: Add `title=` attributes to `action-submit-flip-mission`, `flip-target-*`, and `confirm-reclaim` elements.
4. **LANE-D / LANE-B**: Investigate chromium vs webkit discrepancy in store.js `set("mission")` notification — may need an explicit `_emitPath("mission.phase")` call after parent-key replacement in chromium's stricter JS engine.
