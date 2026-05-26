// test_board_reauth_nonblocking.spec.ts — R1 (BLOCKING regression).
//
// The re-auth affordance must NOT brick the SPA. Previously showReauthModal()
// called <dialog>.showModal(), which renders a top-layer ::backdrop that
// intercepts EVERY pointer event — so a mid-session 401 (transient blip, or the
// tightened auth gate) froze navigation and clicks board-wide and turned the
// chromium-default navigation specs red.
//
// This spec proves the fix: a mid-session 401 surfaces a NON-MODAL re-auth
// prompt, the prompt is dismissible (Escape / Dismiss), and crucially the rest
// of the UI stays clickable — the operator can still navigate while it's up.
//
// Runs under chromium-board / webkit-board (fix-small, MEGALODON_FAKE_SPAWNER=1
// → a valid ui.token, 3 lanes A/B/C, real nav).

import { test, expect, Page, TestInfo } from '@playwright/test';
import { readUiToken } from './_helpers';

async function authAndGotoBoard(page: Page, testInfo: TestInfo): Promise<void> {
  const token = readUiToken(testInfo);
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/', { timeout: 10_000 });
  await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
}

test.describe('R1: re-auth prompt is non-blocking', () => {
  test('a mid-session 401 surfaces re-auth WITHOUT blocking navigation', async ({ page }, testInfo) => {
    await authAndGotoBoard(page, testInfo);

    // Healthy board: no modal.
    await expect(page.locator('[data-testid="reauth-modal"]')).toHaveCount(0);

    // Force a mid-session 401 on a gated fetch, then re-mount the board to
    // trigger the gated narrative fetch → 401 → re-auth prompt.
    await page.route('**/api/v1/narrative', (route) =>
      route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'authentication required' }),
      }),
    );
    await page.evaluate(() => {
      history.pushState({}, '', '/approval-rules');
      window.dispatchEvent(new PopStateEvent('popstate', { state: {} }));
      history.pushState({}, '', '/');
      window.dispatchEvent(new PopStateEvent('popstate', { state: {} }));
    });

    // The re-auth prompt appears and is visible.
    const modal = page.locator('[data-testid="reauth-modal"]');
    await expect(modal).toBeVisible({ timeout: 8_000 });

    // CRUX OF R1: the rest of the UI is still clickable. The prompt is NON-MODAL
    // (dialog.show(), not showModal()), so there is no top-layer ::backdrop
    // swallowing pointer events. Click a nav link and prove navigation works.
    // Stop the 401 first so the destination can render normally.
    await page.unroute('**/api/v1/narrative');

    // A navigation click must succeed even with the prompt open. The findings
    // nav link is present on every page chrome.
    await page.locator('[data-testid="nav-findings"]').click();
    await expect(page).toHaveURL(/\/findings$/, { timeout: 5_000 });
    await expect(page.locator('[data-testid="findings-page"]')).toBeVisible({ timeout: 8_000 });

    // The prompt is dismissible via the explicit Dismiss button.
    // (It may still be open after navigation — non-modal prompts persist.)
    if (await modal.isVisible()) {
      await page.locator('[data-testid="reauth-dismiss"]').click();
      await expect(modal).toBeHidden({ timeout: 5_000 });
    }

    // And we can navigate again afterwards — UI fully usable.
    await page.locator('[data-testid="nav-signals"]').click();
    await expect(page).toHaveURL(/\/signals$/, { timeout: 5_000 });
    await expect(page.locator('[data-testid="signals-page"]')).toBeVisible({ timeout: 8_000 });
  });

  test('Escape dismisses the non-modal re-auth prompt', async ({ page }, testInfo) => {
    await authAndGotoBoard(page, testInfo);

    await page.route('**/api/v1/narrative', (route) =>
      route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'authentication required' }),
      }),
    );
    await page.evaluate(() => {
      history.pushState({}, '', '/approval-rules');
      window.dispatchEvent(new PopStateEvent('popstate', { state: {} }));
      history.pushState({}, '', '/');
      window.dispatchEvent(new PopStateEvent('popstate', { state: {} }));
    });

    const modal = page.locator('[data-testid="reauth-modal"]');
    await expect(modal).toBeVisible({ timeout: 8_000 });

    await page.unroute('**/api/v1/narrative');
    await page.keyboard.press('Escape');
    await expect(modal).toBeHidden({ timeout: 5_000 });
  });
});
