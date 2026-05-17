// Failure-mode E2E tests, run against fix-medium-failure-modes.
// Test IDs T-FX-FAILMODE-a/b/c from P2.5-E §"Updated test inventory".
// Source: META CHALLENGE-4 (fixture corpus missing failure-mode shapes).

import { test, expect } from '@playwright/test';

// Point the server at the failure-modes fixture for this whole suite.
test.use({
  baseURL: process.env.MEGALODON_UI_URL || 'http://127.0.0.1:8765',
});

test.describe('Failure-mode UI surfacing', () => {

  test.beforeAll(async () => {
    // Operator pre-step: server must be launched with MEGALODON_MISSION_DIR
    // pointing at fix-medium-failure-modes. The webServer.env in
    // playwright.config.ts can be overridden via env var when running this
    // suite specifically.
  });

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
