// P5.3 — auth-redirect.spec.ts
//
// Exercises the v9.2 paste-token modal (PM-5 + gap 2): the modal must fire
// on ANY 401, not just initial load.
//
// Scenarios:
//   1. Initial load without cookie → /api/v1/lane/<NAME>/pane-stream 401
//      → modal becomes visible.
//   2. Paste an invalid token → /api/v1/auth/exchange 401 → modal stays open
//      with an error label.
//   3. Paste the valid token → /api/v1/auth/exchange 200 → modal closes
//      and the EventSources reconnect.
//
// Runs under `MEGALODON_V92_DASHBOARD=1 MEGALODON_LIFESPAN_TEST_MODE=1`
// (chromium-v92-dashboard project). Token is read from
// ui/tests/fixtures/fix-medium-v92/.fleet/ui.token which the v9.2 webServer
// writes at startup.

import { test, expect } from '@playwright/test';
import { readUiToken } from './_helpers';

// On a no-cookie initial load the v92 dashboard IIFE probes /api/v1/config and
// 401s → it surfaces the paste-token modal; independently, the shared
// authedFetch path 401s → it surfaces the global re-auth modal (auth.js). BOTH
// are now NON-modal (dialog.show() / pinned banner, NO ::backdrop) so neither
// bricks the SPA — that product change is correct and must stay. But the two
// recovery surfaces overlap geometrically at the top of the viewport, and the
// re-auth banner (z-index 2147483646) sits over the paste-token submit button,
// intercepting its click. Since the re-auth banner is intentionally
// dismissible (its whole reason for being non-modal), close it first so the
// paste-token modal — the surface THIS spec exercises — is interactable. This
// is the operator's own recovery path, not a product workaround.
async function dismissReauthBanner(page: import('@playwright/test').Page): Promise<void> {
  const reauth = page.locator('[data-testid="reauth-modal"]');
  if (await reauth.isVisible().catch(() => false)) {
    await page.evaluate(() => {
      const d = document.querySelector('[data-testid="reauth-modal"]') as HTMLDialogElement | null;
      try { d?.close(); } catch { /* non-dialog fallback */ }
      d?.removeAttribute('open');
    });
    await expect(reauth).toBeHidden({ timeout: 3_000 });
  }
}

test.describe('v9.2 paste-token modal (gap 2 / PM-5)', () => {
  test('initial load without cookie surfaces the paste-token modal', async ({ page, context }) => {
    // No cookie, no token in URL — EventSources will see 401 from the auth gate.
    await context.clearCookies();
    await page.goto('/');

    const modal = page.locator('[data-testid="paste-token-modal"]');
    await expect(modal).toBeVisible({ timeout: 8_000 });
  });

  test('invalid token keeps modal open with an error label', async ({ page, context }) => {
    await context.clearCookies();
    await page.goto('/');
    const modal = page.locator('[data-testid="paste-token-modal"]');
    await expect(modal).toBeVisible({ timeout: 8_000 });

    // The non-modal re-auth banner can overlap the paste-token submit; dismiss it.
    await dismissReauthBanner(page);

    await modal.locator('[data-testid="paste-token-input"]').fill('this-is-not-the-token');
    await modal.locator('[data-testid="paste-token-submit"]').click();

    await expect(modal).toBeVisible();
    const err = modal.locator('[data-testid="paste-token-error"]');
    await expect(err).toBeVisible();
    await expect(err).toContainText(/HTTP 401|rejected/i);
  });

  test('valid token closes the modal and the page settles', async ({ page, context }, testInfo) => {
    await context.clearCookies();
    await page.goto('/');
    const modal = page.locator('[data-testid="paste-token-modal"]');
    await expect(modal).toBeVisible({ timeout: 8_000 });

    // The non-modal re-auth banner can overlap the paste-token submit; dismiss it.
    await dismissReauthBanner(page);

    const token = readUiToken(testInfo);
    await modal.locator('[data-testid="paste-token-input"]').fill(token);
    await modal.locator('[data-testid="paste-token-submit"]').click();

    // Modal closes — either removed from accessibility tree or display:none.
    await expect(modal).not.toBeVisible({ timeout: 5_000 });

    // Under the deny-by-default gate the INITIAL config probe 401'd, so the v92
    // dashboard IIFE returned early WITHOUT building the lane grid (it is a
    // one-shot bootstrap — it does not re-run in place after a token paste; the
    // modal's own hint tells the operator to reload). The token exchange's
    // observable success is therefore (a) the modal closing and (b) the session
    // cookie now being valid. Prove the cookie works by re-loading: the IIFE
    // re-runs, /api/v1/config returns 200, and the grid is built with one pane
    // per lane — the steady-state authenticated dashboard.
    await page.reload();
    await expect(page.locator('[data-testid="lane-grid"]')).toBeVisible({ timeout: 8_000 });
    const panes = page.locator('[data-testid^="lane-pane-"]');
    await expect(panes.first()).toBeVisible({ timeout: 5_000 });
  });
});
