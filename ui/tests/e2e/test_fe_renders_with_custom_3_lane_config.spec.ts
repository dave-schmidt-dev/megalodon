// E2E test: FE renders correctly with a custom 3-lane config.
//
// Fixture: scripts/tests/fixtures/configs/minimal_3_lane/.mission-config.yaml
//   Defines lanes: ALPHA, BETA, GAMMA (3 lanes only, no standard 6-lane set).
//
// This test verifies P3.2 milestone: pages consume loadConfig() and render
// lane cards matching the server-returned /api/v1/config lanes array, rather
// than the hardcoded fallback list.
//
// SKIP marker: this test requires booting the server against the minimal_3_lane
// fixture (not the standard fix-medium fixture used by the default project).
// The orchestrator validates this test by running:
//   MEGALODON_MISSION_DIR=<path>/scripts/tests/fixtures/configs/minimal_3_lane \
//   npx playwright test test_fe_renders_with_custom_3_lane_config.spec.ts
//
// When run in the standard CI project (chromium-default), the test is skipped.

import { test, expect } from '@playwright/test';

// Detect if the server is configured with the 3-lane fixture by checking the
// /api/v1/config response. If we can't confirm, skip gracefully.
async function isThreeLaneFixture(page: import('@playwright/test').Page): Promise<boolean> {
  try {
    const resp = await page.request.get('/api/v1/config');
    if (!resp.ok()) return false;
    const data = await resp.json();
    const lanes: { name: string }[] = data.lanes || [];
    const names = lanes.map((l: { name: string } | string) =>
      typeof l === 'string' ? l : l.name,
    );
    return (
      names.length === 3 &&
      names.includes('ALPHA') &&
      names.includes('BETA') &&
      names.includes('GAMMA')
    );
  } catch {
    return false;
  }
}

test.describe('FE renders with custom 3-lane config (P3.2 milestone)', () => {
  test(
    'dashboard shows exactly 3 lane cards: ALPHA, BETA, GAMMA',
    async ({ page }) => {
      await page.goto('/');

      // If the server is not running against the minimal_3_lane fixture, skip.
      const is3Lane = await isThreeLaneFixture(page);
      test.skip(!is3Lane, 'Requires server booted against minimal_3_lane fixture — orchestrator validates');

      // Wait for the loading skeleton to resolve and real cards to appear.
      // The skeleton is replaced by lane-row-* elements after loadConfig() resolves.
      await expect(page.locator('[data-testid^="lane-row-"]')).toHaveCount(3, { timeout: 10_000 });

      // Assert the three specific lane cards are present.
      await expect(page.locator('[data-testid="lane-row-ALPHA"]')).toBeVisible();
      await expect(page.locator('[data-testid="lane-row-BETA"]')).toBeVisible();
      await expect(page.locator('[data-testid="lane-row-GAMMA"]')).toBeVisible();

      // Assert the standard 6-lane cards are NOT present (migration proof).
      await expect(page.locator('[data-testid="lane-row-AUDIT"]')).toHaveCount(0);
      await expect(page.locator('[data-testid="lane-row-ARCHITECT"]')).toHaveCount(0);
    },
  );

  test(
    'loading skeleton is visible before config resolves',
    async ({ page }) => {
      const is3Lane = await (async () => {
        await page.goto('/');
        return isThreeLaneFixture(page);
      })();
      test.skip(!is3Lane, 'Requires server booted against minimal_3_lane fixture — orchestrator validates');

      // Navigate fresh and immediately check for the skeleton div.
      // We intercept the config fetch to delay it, making the skeleton observable.
      await page.route('/api/v1/config', async (route) => {
        await new Promise((resolve) => setTimeout(resolve, 300));
        await route.continue();
      });

      await page.goto('/');
      // Skeleton should be briefly visible.
      const skeleton = page.locator('.loading-skeleton');
      await expect(skeleton).toBeVisible({ timeout: 500 });
      // After delay, skeleton resolves and lane cards appear.
      await expect(page.locator('[data-testid^="lane-row-"]')).toHaveCount(3, { timeout: 10_000 });
    },
  );
});
