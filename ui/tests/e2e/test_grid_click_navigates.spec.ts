// test_grid_click_navigates.spec.ts — Task 1.5: grid pane click navigation.
//
// Verifies that clicking a lane pane on the grid page navigates the browser
// URL to /lane/<short>. lane_detail.js does not exist yet (Task 1.6), so
// the router will attempt to load it and fail — the test only asserts URL
// change + router dispatch, not successful page render.
//
// Runs under chromium-grid project (NON_MUTATION_DEFAULT_ENV + fix-small
// fixture). Lane A short code is used as the click target.

import { test, expect } from '@playwright/test';

test.describe('grid page: clicking a pane navigates to /lane/<short>', () => {

  test('click pane A changes URL to /lane/A and triggers router', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('[data-testid="grid-page"]')).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('[data-pane-lane="A"]')).toBeVisible({ timeout: 5_000 });

    // Collect any console errors (router failure is expected if lane_detail.js absent).
    const consoleErrors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });

    // Click the pane wrapper for lane A.
    await page.locator('[data-pane-lane="A"]').click();

    // Primary assertion: URL must change to /lane/A.
    await expect(page).toHaveURL(/\/lane\/A$/, { timeout: 5_000 });
  });

  test('click pane B changes URL to /lane/B', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('[data-testid="grid-page"]')).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('[data-pane-lane="B"]')).toBeVisible({ timeout: 5_000 });

    await page.locator('[data-pane-lane="B"]').click();

    await expect(page).toHaveURL(/\/lane\/B$/, { timeout: 5_000 });
  });

  test('clicking back navigates back to grid page', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('[data-testid="grid-page"]')).toBeVisible({ timeout: 10_000 });

    // Navigate to a lane.
    await page.locator('[data-pane-lane="A"]').click();
    await expect(page).toHaveURL(/\/lane\/A$/, { timeout: 5_000 });

    // Go back — browser history should restore the grid at /.
    await page.goBack();
    // Match URL ending in the root path: the full URL looks like http://host:port/
    // Use a regex that matches the trailing slash after the port (not /lane/...).
    await expect(page).toHaveURL(/:\d+\/$/, { timeout: 5_000 });
    // Grid should re-mount (popstate fires mountPage).
    await expect(page.locator('[data-testid="grid-page"]')).toBeVisible({ timeout: 8_000 });
  });

});
