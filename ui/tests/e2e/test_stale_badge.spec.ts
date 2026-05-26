// test_stale_badge.spec.ts — lane-detail Restart-loop FE coverage.
//
// Runs under the chromium-board / webkit-board projects (MEGALODON_FAKE_SPAWNER=1,
// fix-small fixture, 3 lanes A/B/C).
//
// NOTE (2026-05-24): the grid's stale-BADGE + stale-MODAL tests (formerly cases
// 1/2/3/5) were removed when grid.js was deleted. The summary board surfaces
// staleness as a per-row STALE pill instead — covered by test_board_stale.spec.ts.
// What remains here is the lane-detail toolbar "Restart /loop" flow, which is
// board-independent and still live.
//
// Auth: the restart endpoint is cookie-gated. Authenticate via the auth-exchange
// flow before making any API calls.

import { test, expect } from '@playwright/test';
import { readUiToken, setControlMode } from './_helpers';

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

/**
 * Authenticate via the auth-exchange endpoint and land on the board at /.
 */
async function authenticateAndGotoGrid(page: import('@playwright/test').Page, token: string) {
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/', { timeout: 10_000 });
  await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
}

// ---------------------------------------------------------------------------
// Lane-detail toolbar Restart /loop button
// ---------------------------------------------------------------------------

test.describe('stale badge: lane-detail toolbar Restart /loop', () => {

  test('4: toolbar Restart /loop fires POST after confirm dialog', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    await authenticateAndGotoGrid(page, token);

    // Wave 3 safety: Restart /loop is gated behind Control mode (READ-ONLY is
    // the default). Enable Control mode so the button is actionable.
    // Use setControlMode (not raw .click()) so the server state is confirmed
    // ON regardless of what a prior test may have left in the shared process.
    await setControlMode(page, true);

    // Navigate to /lane/A by clicking the board row for lane A.
    await page.locator('[data-testid="board-row-A"]').click();
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

    // Click the toolbar Restart /loop button → the shared confirm modal opens
    // (Wave 3 replaced window.confirm() with showConfirmModal). Confirm it.
    const restartBtn = page.locator('[data-testid="lane-detail-restart-loop"]');
    await expect(restartBtn).toBeVisible({ timeout: 5_000 });
    await expect(restartBtn).toBeEnabled({ timeout: 5_000 });

    await restartBtn.click();
    await expect(page.locator('[data-testid="confirm-modal"]')).toBeVisible({ timeout: 5_000 });

    const responsePromise = page.waitForResponse('**/restart-loop', { timeout: 8_000 });
    await page.locator('[data-testid="confirm-modal-confirm"]').click();
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
