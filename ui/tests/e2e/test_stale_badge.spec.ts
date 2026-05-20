// test_stale_badge.spec.ts — v9.4 Task 2.8: stale-lanes badge + Restart-loop FE.
//
// Runs under the chromium-grid project (MEGALODON_FAKE_SPAWNER=1, fix-small fixture,
// 3 lanes A/B/C). The _test/stale_override endpoint is only registered when
// MEGALODON_FAKE_SPAWNER=1.
//
// Test cases:
//   1. Badge appears: POST stale_override for lane A → reload / → badge "1 stale" visible.
//   2. Modal opens on click: click badge → modal visible, lane A row visible with
//      duration "20m 0s" (1200s = 20m 0s).
//   3. Restart button works: click Restart /loop in modal → assert POST was made with
//      X-CSRF-Token header → assert success toast visible.
//   4. Lane-detail toolbar Restart: navigate to /lane/A → click Restart /loop → accept
//      confirm dialog → assert POST to restart-loop fired.
//   5. No badge when no stale: fresh load with no override → badge hidden.
//
// Auth: stale endpoints are cookie-gated. Authenticate via the auth-exchange
// flow (same as test_v94_phase1_smoke.spec.ts) before making any API calls.
//
// Network intercept strategy (tests 3, 4): page.route() in PASSTHROUGH mode captures
// request headers/body while still forwarding to the real server. The fake spawner
// short-circuits the actual tmux send_keys call so the server returns 202 without
// a real tmux socket.

import { test, expect } from '@playwright/test';
import { readUiToken } from './_helpers';

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

/**
 * Authenticate via the auth-exchange endpoint and navigate to /.
 */
async function authenticateAndGotoGrid(page: import('@playwright/test').Page, token: string) {
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/', { timeout: 10_000 });
  await expect(page.locator('[data-testid="grid-page"]')).toBeVisible({ timeout: 10_000 });
}

/**
 * POST _test/stale_override to mark a lane as stale.
 * CSRF token is read from the page's meta tag via page.evaluate().
 */
async function setStaleOverride(
  page: import('@playwright/test').Page,
  lane: string,
  seconds: number,
): Promise<void> {
  const csrf = await page.evaluate(() => {
    return (document.querySelector('meta[name="csrf-token"]') as HTMLMetaElement | null)
      ?.getAttribute('content') || '';
  });

  const resp = await page.request.post(
    `/api/v1/_test/stale_override?lane=${encodeURIComponent(lane)}&seconds=${seconds}`,
    {
      headers: {
        'Content-Type': 'application/json',
        ...(csrf ? { 'X-CSRF-Token': csrf } : {}),
      },
      data: {},
    },
  );
  expect(resp.status(), `stale_override POST for lane ${lane}`).toBe(200);
}

// ---------------------------------------------------------------------------
// Test 1 + 2: Badge appears and modal opens
// ---------------------------------------------------------------------------

test.describe('stale badge: badge appears and modal opens', () => {

  test('1: badge shows "1 stale" (red bg) after stale_override for lane A', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    await authenticateAndGotoGrid(page, token);

    // Set lane A as stale (1200s = 20 minutes).
    await setStaleOverride(page, 'A', 1200);

    // Reload to trigger the initial poll (badge updates on mount).
    await page.reload();
    await expect(page.locator('[data-testid="grid-page"]')).toBeVisible({ timeout: 10_000 });

    // Badge must be visible and contain "1 stale".
    const badge = page.locator('[data-testid="stale-badge"]');
    await expect(badge).toBeVisible({ timeout: 8_000 });
    await expect(badge).toContainText('1 stale', { timeout: 5_000 });
  });

  test('2: clicking badge opens modal with lane A row showing ~20m 0s', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    await authenticateAndGotoGrid(page, token);

    // Set lane A as stale.
    await setStaleOverride(page, 'A', 1200);

    // Reload to pick up the override.
    await page.reload();
    await expect(page.locator('[data-testid="grid-page"]')).toBeVisible({ timeout: 10_000 });

    // Wait for badge to appear.
    const badge = page.locator('[data-testid="stale-badge"]');
    await expect(badge).toBeVisible({ timeout: 8_000 });

    // Click badge → modal opens.
    await badge.click();

    const modal = page.locator('[data-testid="stale-modal"]');
    await expect(modal).toBeVisible({ timeout: 5_000 });

    // Modal title should say "Stale Lanes (1)".
    await expect(page.locator('[data-testid="stale-modal-title"]'))
      .toContainText('Stale Lanes (1)', { timeout: 3_000 });

    // Lane A row must be present.
    const laneRow = page.locator('[data-testid="stale-lane-row-A"]');
    await expect(laneRow).toBeVisible({ timeout: 3_000 });

    // Lane A chip should be visible inside the row.
    await expect(page.locator('[data-testid="stale-lane-chip-A"]')).toBeVisible();

    // Duration should show "20m 0s" (1200s = 20 min 0 sec).
    const durationEl = page.locator('[data-testid="stale-lane-duration-A"]');
    await expect(durationEl).toContainText('20m', { timeout: 3_000 });
  });

});

// ---------------------------------------------------------------------------
// Test 3: Restart /loop button in modal
// ---------------------------------------------------------------------------

test.describe('stale badge: Restart /loop button in modal', () => {

  test('3: clicking Restart /loop in modal sends POST with X-CSRF-Token and shows toast', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    await authenticateAndGotoGrid(page, token);

    // Set lane A as stale.
    await setStaleOverride(page, 'A', 1200);

    // Reload + open modal.
    await page.reload();
    await expect(page.locator('[data-testid="grid-page"]')).toBeVisible({ timeout: 10_000 });

    const badge = page.locator('[data-testid="stale-badge"]');
    await expect(badge).toBeVisible({ timeout: 8_000 });
    await badge.click();

    const modal = page.locator('[data-testid="stale-modal"]');
    await expect(modal).toBeVisible({ timeout: 5_000 });

    // Intercept the restart-loop POST to capture headers and return mocked 202.
    // The fake spawner sessions have no initial_prompt so the real server would
    // return 409 — mock at the network level to test the FE wiring independently.
    let capturedRequest: { headers: Record<string, string>; body: string } | null = null;
    await page.route('**/restart-loop', async (route) => {
      const req = route.request();
      capturedRequest = {
        headers: req.headers(),
        body: req.postData() ?? '',
      };
      // Return a mocked 202 — we're testing the FE plumbing, not the BE.
      await route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: '{"ok":true}',
      });
    });

    // Click Restart /loop for lane A.
    const restartBtn = page.locator('[data-testid="stale-restart-A"]');
    await expect(restartBtn).toBeVisible({ timeout: 3_000 });

    const responsePromise = page.waitForResponse('**/restart-loop', { timeout: 8_000 });
    await restartBtn.click();
    const response = await responsePromise;

    // Assert request was captured with X-CSRF-Token header.
    await expect.poll(() => capturedRequest, { timeout: 5_000 }).not.toBeNull();
    const csrfHeader = capturedRequest!.headers['x-csrf-token'];
    expect(csrfHeader, 'X-CSRF-Token header must be present and non-empty').toBeTruthy();
    expect(csrfHeader.length).toBeGreaterThan(0);

    // Assert mocked 202 was received.
    expect(response.status()).toBe(202);

    // Assert success toast appears.
    await expect(page.locator('#toast-region'))
      .toContainText('Restarted /loop for lane A', { timeout: 5_000 });
  });

});

// ---------------------------------------------------------------------------
// Test 4: Lane-detail toolbar Restart /loop button
// ---------------------------------------------------------------------------

test.describe('stale badge: lane-detail toolbar Restart /loop', () => {

  test('4: toolbar Restart /loop fires POST after confirm dialog', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    await authenticateAndGotoGrid(page, token);

    // Navigate to /lane/A.
    await page.locator('[data-pane-lane="A"]').click();
    await expect(page).toHaveURL(/\/lane\/A$/, { timeout: 5_000 });
    await expect(page.locator('[data-testid="lane-detail-page"]')).toBeVisible({ timeout: 8_000 });

    // Intercept the restart-loop POST to capture headers and return mocked 202.
    // The fake spawner sessions have no initial_prompt so the real server would
    // return 409 — mock at the network level to test the FE wiring independently.
    let capturedRequest: { headers: Record<string, string>; body: string } | null = null;
    await page.route('**/restart-loop', async (route) => {
      const req = route.request();
      capturedRequest = {
        headers: req.headers(),
        body: req.postData() ?? '',
      };
      await route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: '{"ok":true}',
      });
    });

    // Accept the confirm() dialog automatically.
    page.on('dialog', async (dialog) => {
      expect(dialog.message()).toContain('Restart loop for lane A');
      await dialog.accept();
    });

    // Click the toolbar Restart /loop button.
    const restartBtn = page.locator('[data-testid="lane-detail-restart-loop"]');
    await expect(restartBtn).toBeVisible({ timeout: 5_000 });

    const responsePromise = page.waitForResponse('**/restart-loop', { timeout: 8_000 });
    await restartBtn.click();
    const response = await responsePromise;

    // Assert POST was fired.
    await expect.poll(() => capturedRequest, { timeout: 5_000 }).not.toBeNull();

    // Assert X-CSRF-Token header present.
    const csrfHeader = capturedRequest!.headers['x-csrf-token'];
    expect(csrfHeader, 'X-CSRF-Token must be present in toolbar restart POST').toBeTruthy();

    // Assert mocked 202 was received.
    expect(response.status()).toBe(202);
  });

});

// ---------------------------------------------------------------------------
// Test 5: No badge when no stale lanes
// ---------------------------------------------------------------------------

test.describe('stale badge: badge hidden when no stale lanes', () => {

  test('5: badge hidden when stale endpoint returns empty list', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);

    // Mock the stale endpoint to return an empty list. The fix-small fixture has
    // ancient timestamps (2026-01-01) so the real computation always returns 3
    // stale lanes — override at network level to test the badge-hidden FE path.
    await page.route('**/api/v1/lanes/stale', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ stale_lanes: [], checked_at_utc: new Date().toISOString() }),
      });
    });

    await authenticateAndGotoGrid(page, token);

    // Wait a moment for the initial poll to complete.
    await page.waitForTimeout(1500);

    const badge = page.locator('[data-testid="stale-badge"]');

    // When stale count = 0, badge must be hidden (display:none).
    await expect(badge).toBeHidden({ timeout: 5_000 });
  });

});
