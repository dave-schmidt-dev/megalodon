# S-LANE-CARD-DETAILS testid fixes + DESIGN-BUG test inversions — LANE-D agent-07c5

- **Lane:** LANE-D (FRONTEND)
- **Agent:** `agent-07c5`
- **Task:** secondary (post-P1-D cleanup)
- **Phase:** PHASE 1 — PLAN
- **UTC:** 2026-05-19T21-05Z

## Summary

After reading `test_dashboard_live_audit.spec.ts` more carefully, found that the S-LANE-CARD-DETAILS implementation was missing two `data-testid` attributes the spec explicitly tests, and two `[DESIGN-BUG]` tests needed to be inverted now that the bugs are fixed.

## Changes

### `ui/static/pages/dashboard.js`

1. `renderLaneCard`: added `"data-testid": "lane-model"` to the model text span.
2. `renderLaneCard`: renamed `"data-testid": "last-utc"` → `"data-testid": "lane-last-tick"` on the staleness span (better scoped name, consistent with the `lane-` prefix convention).

### `ui/tests/e2e/test_dashboard_live_audit.spec.ts`

3. Inverted `[DESIGN-BUG: events-always-empty]` test: was asserting `toContainText('no activity yet')`; now asserts `not.toContainText('no activity yet')` since the sparkline now shows findings/claims as proxy.
4. Inverted `[DESIGN-BUG: history-always-empty]` test: same pattern, now asserts `not.toContainText('no HISTORY entries yet')`.
5. Removed `[MISSING-FEATURE]` and `// FAIL EXPECTED` prefixes from both S-LANE-CARD-DETAILS tests (these tests should now pass).

### `ui/tests/e2e/test_status_view.spec.ts`

6. Updated selector `[data-testid="last-utc"]` → `[data-testid="lane-last-tick"]` to match the renamed attribute.

## Remaining P2-D work (after phase flip to BUILD)

- Create fixture YAML files (`minimal_custom_phases/`, `minimal_3_lane/`)
- Add playwright projects for custom-config + v92-dashboard
- Implement active-claims panel (`[data-testid^="active-claim-"]`)
- Implement permission-prompts panel (`[data-testid="permission-prompts-panel"]`)
