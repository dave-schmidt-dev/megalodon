// Failure-mode E2E tests, run against fix-medium-failure-modes.
// Test IDs T-FX-FAILMODE-a/b/c from P2.5-E §"Updated test inventory".
// Source: META CHALLENGE-4 (fixture corpus missing failure-mode shapes).

import { test, expect } from '@playwright/test';
import { gotoAuthed } from './_helpers';

// baseURL is set by the project config (chromium-failure-modes / webkit-failure-modes
// in playwright.config.ts) to point at the fix-medium-failure-modes fixture on
// its dedicated port. Do NOT override here — a `test.use({ baseURL })` would
// silently steal these specs onto the wrong fixture.
//
// The deny-by-default auth gate requires the mui_session cookie on the mission /
// tasks data fetches, so every (active) test authenticates first (gotoAuthed)
// before navigating to its surface; an unauthenticated load renders an empty
// page and the failure-mode panels never appear.
//
// DEPRECATED-UI NOTE (FAILMODE-a × 2, FAILMODE-c): these three asserted
// affordances of the PRE-v9.4 dashboard — the stuck-phase-flip warning panel
// (`warning-stuck-phase-flip`), its recovery action (`action-complete-stuck-flip`),
// and HISTORY drift glyphs (`history-entry-*[data-drift]`). The v9.4 dashboard
// rebuild (commit b1c867d) deleted the old dashboard.js and the new mission
// page (ui/static/pages/mission.js) renders none of these testids — they exist
// nowhere in ui/static/ today. That rebuild's own commit message flags these as
// "pre-existing v9.3-era failures that test the deprecated dashboard". They are
// NOT an auth regression and NOT a product bug — they assert intentionally
// dropped UI. They are skipped (not deleted) so the dropped coverage stays
// visible/recoverable should the recovery affordances ever be re-introduced on
// the board. (Mirrors the "deleted rather than re-introduce a dropped feature"
// precedent in test_status_view.spec.ts.) FAILMODE-b stays active: its panel
// (`panel-non-canonical-claims`) exists in the live tasks page and passes once
// authenticated.

test.describe('Failure-mode UI surfacing', () => {

  test.skip('T-FX-FAILMODE-a — stuck-phase-flip warning panel appears', async ({ page }, testInfo) => {
    await gotoAuthed(page, testInfo, '/mission');
    // Per P2.5-E recommendation: UI must surface a warning when a lock dir
    // exists but .mission-events doesn't reflect the implied next phase.
    // DEPRECATED: `warning-stuck-phase-flip` was removed in the v9.4 rebuild.
    const warning = page.locator('[data-testid="warning-stuck-phase-flip"]');
    await expect(warning).toBeVisible();
    await expect(warning).toContainText(/PHASE-PLAN.*PHASE-CHALLENGE/);
  });

  test.skip('T-FX-FAILMODE-a — recovery action is offered (post v8 changeset)', async ({ page }, testInfo) => {
    await gotoAuthed(page, testInfo, '/mission');
    // DEPRECATED: `action-complete-stuck-flip` was removed in the v9.4 rebuild.
    const action = page.locator('[data-testid="action-complete-stuck-flip"]');
    await expect(action).toBeAttached();
  });

  test('T-FX-FAILMODE-b — multi-form claim collision listed in non-canonical panel', async ({ page }, testInfo) => {
    await gotoAuthed(page, testInfo, '/tasks');
    // fix-medium-failure-modes has both `claims/P2-C-to-B/` and `claims/P2-C→B/`.
    // The UI should de-duplicate by canonical form and surface the duplicate.
    const panel = page.locator('[data-testid="panel-non-canonical-claims"]');
    await expect(panel).toBeVisible();
    await expect(panel).toContainText('P2-C→B');
  });

  test.skip('T-FX-FAILMODE-c — HISTORY drift entries flagged with warning glyph', async ({ page }, testInfo) => {
    await gotoAuthed(page, testInfo, '/mission');  // pre-v9.4 mission page rendered a HISTORY tail
    // DEPRECATED: `history-entry-*[data-drift]` was removed in the v9.4 rebuild;
    // the new mission page renders mission-event rows, not drift-flagged history.
    const driftEntries = page.locator('[data-testid^="history-entry-"][data-drift="true"]');
    // fix-medium-failure-modes injects 3 drift lines.
    await expect(driftEntries).toHaveCount(3);
  });

});
