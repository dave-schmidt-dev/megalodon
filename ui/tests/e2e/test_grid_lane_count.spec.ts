// test_grid_lane_count.spec.ts — Task 1.5: pages/grid.js lane count test.
//
// Boots a 3-lane mission using the fix-small fixture (.mission-config.yaml
// declares LANE-A/A, LANE-B/B, LANE-C/C). Navigates to / and asserts that
// exactly 3 panes are rendered via [data-pane-lane="<short>"] attributes.
//
// Runs under chromium-grid project (NON_MUTATION_DEFAULT_ENV + fix-small
// fixture, port 8769). No fake spawner; SSE connections to pane-stream will
// receive 401 (auth-gated), but the wrapper DOM is still created — the test
// only checks structural presence, not terminal output.

import { test, expect } from '@playwright/test';

test.describe('grid page: lane count matches mission config', () => {

  test('renders exactly 3 panes for a 3-lane mission (fix-small)', async ({ page }) => {
    await page.goto('/');
    // Wait for the grid page to replace the loading skeleton.
    await expect(page.locator('[data-testid="grid-page"]')).toBeVisible({ timeout: 10_000 });

    // The grid must contain exactly one pane per lane.
    const allPanes = page.locator('[data-pane-lane]');
    await expect(allPanes).toHaveCount(3, { timeout: 5_000 });

    // Verify each expected short code is present (not 6-lane default).
    await expect(page.locator('[data-pane-lane="A"]')).toHaveCount(1);
    await expect(page.locator('[data-pane-lane="B"]')).toHaveCount(1);
    await expect(page.locator('[data-pane-lane="C"]')).toHaveCount(1);

    // Confirm no lane-D pane leaks in (would indicate default 6-lane fallback).
    await expect(page.locator('[data-pane-lane="D"]')).toHaveCount(0);
  });

  test('pane count equals /api/v1/config lanes length', async ({ page }) => {
    // Belt-and-suspenders: compare DOM count against config endpoint.
    const configResp = await page.request.get('/api/v1/config');
    expect(configResp.status()).toBe(200);
    const config = await configResp.json();
    const laneCount: number = config.lanes.length;
    expect(laneCount).toBe(3);

    await page.goto('/');
    await expect(page.locator('[data-testid="grid-page"]')).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('[data-pane-lane]')).toHaveCount(laneCount, { timeout: 5_000 });
  });

});
