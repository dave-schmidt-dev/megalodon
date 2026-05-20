// P6.4 — followup.spec.ts (CV-12)
//
// End-to-end POST-wiring assertion: typing into a lane's follow-up textarea
// + clicking Send fires POST /api/v1/lane/<short>/followup with the typed
// prompt, the server returns 202, and the Send button disables (debounce
// kick-in).
//
// The "sentinel reaches the pane within 500 ms" contract from §6.5 is pinned
// at the unit level by scripts/tests/test_respawn_unit.py and the fake
// spawner's matching test in test_fake_spawner.py
// (test_fake_respawn_drains_then_pushes_sentinel). Asserting the sentinel
// here as well would require keeping all 6 lane SSE channels open while the
// POST flies — but Chrome's per-host HTTP/1.1 connection limit (6) means the
// POST would queue indefinitely behind the streaming SSEs. We close SSE for
// this spec; the wire-level POST contract is what we want to pin here.
//
// Runs under MEGALODON_FAKE_SPAWNER=1 (chromium-v92-dashboard project).

import { test, expect } from '@playwright/test';
import { readUiToken } from './_helpers';

test('followup (CV-12): clicking Send fires POST + disables Send (debounce)', async ({ page }, testInfo) => {
  const token = readUiToken(testInfo);
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/');
  await expect(page.locator('[data-testid="lane-grid"]')).toBeVisible();

  const configResp = await page.request.get('/api/v1/config');
  const config = await configResp.json();
  const firstLane = config.lanes[0];
  const inputSel = `[data-testid="followup-input-${firstLane.name}"]`;
  const sendSel = `[data-testid="followup-send-${firstLane.name}"]`;

  // Free the connection pool so the POST doesn't queue behind 6 streaming SSEs.
  await page.evaluate(() => (window as unknown as { __v92_closeAllStreams: () => void }).__v92_closeAllStreams());

  // Settle.
  await page.waitForTimeout(300);

  const postPromise = page.waitForResponse(
    (resp) =>
      resp.url().endsWith(`/api/v1/lane/${firstLane.short}/followup`)
      && resp.request().method() === 'POST',
    { timeout: 5_000 },
  );

  await page.locator(inputSel).fill('test follow-up prompt');
  // Synthetic click via DOM event (xterm host overlaps Send under headless).
  await page.locator(sendSel).evaluate((btn: HTMLButtonElement) => btn.click());

  const resp = await postPromise;
  expect(resp.status()).toBe(202);

  // Send button is disabled until the first non-sentinel byte arrives OR
  // the 3 s timeout fires. We just verify the debounce engaged.
  await expect(page.locator(sendSel)).toBeDisabled();

  // POST body included the typed prompt + lane short matched.
  const postedJson = JSON.parse(resp.request().postData() || '{}');
  expect(postedJson.prompt).toBe('test follow-up prompt');
});
