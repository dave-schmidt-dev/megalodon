// P5.3 — followup-send-debounced.spec.ts (gap 4)
//
// Asserts: rapid double-click on the Send button fires EXACTLY ONE POST to
// /api/v1/lane/<short>/followup, and the button stays disabled until either
// (a) the first non-sentinel byte arrives, or (b) 3 s elapse.
//
// Runs under MEGALODON_FAKE_SPAWNER=1 (chromium-v92-dashboard project).

import { test, expect } from '@playwright/test';
import { readUiToken } from './_helpers';

test('followup-send-debounced (gap 4): double-click fires exactly one POST', async ({ page }, testInfo) => {
  const token = readUiToken(testInfo);
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/');
  await expect(page.locator('[data-testid="lane-grid"]')).toBeVisible();

  const configResp = await page.request.get('/api/v1/config');
  const config = await configResp.json();
  const firstLane = config.lanes[0];
  const inputSel = `[data-testid="followup-input-${firstLane.name}"]`;
  const sendSel = `[data-testid="followup-send-${firstLane.name}"]`;

  // Free the connection pool — this test only exercises the POST + button-disable
  // behavior, not byte delivery, so we can close the 6 lane SSE channels.
  await page.evaluate(() => (window as unknown as { __v92_closeAllStreams: () => void }).__v92_closeAllStreams());

  // Settle.
  await page.waitForTimeout(500);

  // Count POSTs to the followup endpoint.
  let postCount = 0;
  page.on('request', (req) => {
    if (req.method() === 'POST' && req.url().endsWith(`/api/v1/lane/${firstLane.short}/followup`)) {
      postCount += 1;
    }
  });

  await page.locator(inputSel).fill('debounce test');
  // Click twice in rapid succession. xterm's hostElement can overlap the
  // Send button under headless viewport; we dispatch a synthetic click on
  // the button to bypass Playwright's overlap check (the click reaches the
  // form via the DOM event path regardless of z-order).
  await page.locator(sendSel).evaluate((btn: HTMLButtonElement) => {
    btn.click();
    btn.click(); // immediate second click — should be a no-op (debounced).
  });

  // Wait long enough for the first POST to land + any second to have been
  // attempted, but shy of the 3 s debounce timeout so the button is still
  // disabled when we assert.
  await page.waitForTimeout(1_500);

  expect(postCount).toBe(1);
  await expect(page.locator(sendSel)).toBeDisabled();
});
