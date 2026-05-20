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

    const token = readUiToken(testInfo);
    await modal.locator('[data-testid="paste-token-input"]').fill(token);
    await modal.locator('[data-testid="paste-token-submit"]').click();

    // Modal closes — either removed from accessibility tree or display:none.
    await expect(modal).not.toBeVisible({ timeout: 5_000 });

    // Lane panes are present (rendered during the initial v92_dashboard probe).
    const panes = page.locator('[data-testid^="lane-pane-"]');
    await expect(panes.first()).toBeVisible({ timeout: 3_000 });
  });
});
