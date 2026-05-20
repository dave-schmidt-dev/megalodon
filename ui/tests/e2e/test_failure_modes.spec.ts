// Failure-mode E2E tests, run against fix-medium-failure-modes.
// Test IDs T-FX-FAILMODE-a/b/c from P2.5-E §"Updated test inventory".
// Source: META CHALLENGE-4 (fixture corpus missing failure-mode shapes).

import { test, expect } from '@playwright/test';

// baseURL is set by the project config (chromium-failure-modes / webkit-failure-modes
// in playwright.config.ts) to point at the fix-medium-failure-modes fixture on
// its dedicated port. Do NOT override here — a `test.use({ baseURL })` would
// silently steal these specs onto the wrong fixture.

test.describe('Failure-mode UI surfacing', () => {

  test('T-FX-FAILMODE-a — stuck-phase-flip warning panel appears', async ({ page }) => {
    await page.goto('/mission');
    // Per P2.5-E recommendation: UI must surface a warning when a lock dir
    // exists but .mission-events doesn't reflect the implied next phase.
    const warning = page.locator('[data-testid="warning-stuck-phase-flip"]');
    await expect(warning).toBeVisible();
    await expect(warning).toContainText(/PHASE-PLAN.*PHASE-CHALLENGE/);
  });

  test('T-FX-FAILMODE-a — recovery action is offered (post v8 changeset)', async ({ page }) => {
    await page.goto('/mission');
    const action = page.locator('[data-testid="action-complete-stuck-flip"]');
    // This may be hidden in v7 (no recovery support yet); v8 surfaces it.
    // For now, assert it's present in the DOM even if disabled.
    await expect(action).toBeAttached();
  });

  test('T-FX-FAILMODE-b — multi-form claim collision listed in non-canonical panel', async ({ page }) => {
    await page.goto('/tasks');
    // fix-medium-failure-modes has both `claims/P2-C-to-B/` and `claims/P2-C→B/`.
    // The UI should de-duplicate by canonical form and surface the duplicate.
    const panel = page.locator('[data-testid="panel-non-canonical-claims"]');
    await expect(panel).toBeVisible();
    await expect(panel).toContainText('P2-C→B');
  });

  test('T-FX-FAILMODE-c — HISTORY drift entries flagged with warning glyph', async ({ page }) => {
    await page.goto('/mission');  // assumes mission page renders HISTORY tail
    const driftEntries = page.locator('[data-testid^="history-entry-"][data-drift="true"]');
    // fix-medium-failure-modes injects 3 drift lines.
    await expect(driftEntries).toHaveCount(3);
  });

});
