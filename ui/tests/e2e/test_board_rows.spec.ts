// test_board_rows.spec.ts — Task 3.5a: board row structure + navigation.
//
// Replaces the deleted grid-specific specs (test_grid_lane_count /
// test_grid_click_navigates), porting their structural guarantees to the
// summary board which is now the default route at /.
//
// Boots a 3-lane mission using the fix-small fixture (.mission-config.yaml
// declares LANE-A/A, LANE-B/B, LANE-C/C). Navigates to / and asserts that
// exactly 3 board rows are rendered (one per config lane) and that clicking a
// row navigates to /lane/<short>, with back-navigation restoring the board.
//
// Runs under chromium-board project (GRID_SMOKE_ENV + fix-small fixture, port
// 8769). The rich narrative/drawer/banner behaviour is covered by Task 3.5b;
// this spec is the row/navigation structural contract only.

import { test, expect } from '@playwright/test';
import { establishSession, readUiToken } from './_helpers';

// Authenticate via the hash-token exchange and land on the board. The board's
// narrative / lanes/stale / narrative-stream requests are session-gated; an
// unauthenticated load now (correctly) surfaces the re-auth modal which would
// overlay and block row clicks. (P0 frontend audit: bugs #1/#2.)
async function authAndGotoBoard(
  page: import('@playwright/test').Page,
  testInfo: import('@playwright/test').TestInfo,
): Promise<void> {
  const token = readUiToken(testInfo);
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/', { timeout: 10_000 });
  await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
}

test.describe('board page: row count matches mission config', () => {

  test('renders exactly 3 rows for a 3-lane mission (fix-small)', async ({ page }, testInfo) => {
    // The board's config/narrative fetches are session-gated; authenticate
    // first (hash-token exchange) so the rows actually render.
    await authAndGotoBoard(page, testInfo);
    // Wait for the board page to replace the loading skeleton.
    await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });

    // The board must contain exactly one row per lane.
    const allRows = page.locator('[data-testid^="board-row-"]');
    await expect(allRows).toHaveCount(3, { timeout: 5_000 });

    // Verify each expected short code is present (not the 6-lane default).
    await expect(page.locator('[data-testid="board-row-A"]')).toHaveCount(1);
    await expect(page.locator('[data-testid="board-row-B"]')).toHaveCount(1);
    await expect(page.locator('[data-testid="board-row-C"]')).toHaveCount(1);

    // Confirm no lane-D row leaks in (would indicate default 6-lane fallback).
    await expect(page.locator('[data-testid="board-row-D"]')).toHaveCount(0);
  });

  test('row count equals /api/v1/config lanes length', async ({ page }, testInfo) => {
    // /api/v1/config is session-gated (deny-by-default). Establish the session
    // cookie in the browser context first; page.request shares that cookie jar,
    // so the direct GET below carries it and returns 200 instead of 401.
    await establishSession(page, testInfo);

    // Belt-and-suspenders: compare DOM count against config endpoint.
    const configResp = await page.request.get('/api/v1/config');
    expect(configResp.status()).toBe(200);
    const config = await configResp.json();
    const laneCount: number = config.lanes.length;
    expect(laneCount).toBe(3);

    await page.goto('/');
    await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('[data-testid^="board-row-"]')).toHaveCount(laneCount, { timeout: 5_000 });
  });

});

test.describe('board page: clicking a row navigates to /lane/<short>', () => {

  test('click row A changes URL to /lane/A', async ({ page }, testInfo) => {
    await authAndGotoBoard(page, testInfo);
    await expect(page.locator('[data-testid="board-row-A"]')).toBeVisible({ timeout: 5_000 });

    await page.locator('[data-testid="board-row-A"]').click();

    await expect(page).toHaveURL(/\/lane\/A$/, { timeout: 5_000 });
  });

  test('click row B changes URL to /lane/B', async ({ page }, testInfo) => {
    await authAndGotoBoard(page, testInfo);
    await expect(page.locator('[data-testid="board-row-B"]')).toBeVisible({ timeout: 5_000 });

    await page.locator('[data-testid="board-row-B"]').click();

    await expect(page).toHaveURL(/\/lane\/B$/, { timeout: 5_000 });
  });

  test('going back from a lane restores the board at /', async ({ page }, testInfo) => {
    await authAndGotoBoard(page, testInfo);

    // Navigate to a lane.
    await page.locator('[data-testid="board-row-A"]').click();
    await expect(page).toHaveURL(/\/lane\/A$/, { timeout: 5_000 });

    // Go back — browser history should restore the board at /.
    await page.goBack();
    // Match URL ending in the root path (trailing slash after the port).
    await expect(page).toHaveURL(/:\d+\/$/, { timeout: 5_000 });
    // Board should re-mount (popstate fires mountPage).
    await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 8_000 });
  });

});
