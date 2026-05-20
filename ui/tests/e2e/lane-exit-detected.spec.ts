// P5.3 — lane-exit-detected.spec.ts (CV-8)
//
// Asserts: a lane that exits (rc=17) flips its status pill from "running" to
// "exited (rc=17)" within 5 s of the exit. Drives the fake spawner's
// /__fake__/set_state endpoint to simulate the exit; the dashboard's
// state-polling loop picks it up and updates the DOM.
//
// Runs under MEGALODON_FAKE_SPAWNER=1 (chromium-v92-dashboard project).
//
// The dashboard normally holds one SSE EventSource per lane. With 6 lanes
// that exhausts Chrome's per-host HTTP/1.1 connection pool (6 slots), causing
// subsequent state-poll fetches to queue indefinitely behind streaming SSE
// responses. This test calls `window.__v92_closeAllStreams()` after page load
// to free the pool — the test only exercises state polling, not byte delivery.

import { test, expect } from '@playwright/test';
import { readUiToken } from './_helpers';

test('lane-exit-detected (CV-8): pill flips to exited(rc=17) within 5 s', async ({ page }, testInfo) => {
  const token = readUiToken(testInfo);
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/');
  await expect(page.locator('[data-testid="lane-grid"]')).toBeVisible();

  const configResp = await page.request.get('/api/v1/config');
  const config = await configResp.json();
  const firstLane = config.lanes[0];
  const short: string = firstLane.short;
  const statusSel = `[data-testid="lane-status-${firstLane.name}"]`;

  // Free up the connection pool — close SSE channels we don't need here.
  await page.evaluate(() => (window as unknown as { __v92_closeAllStreams: () => void }).__v92_closeAllStreams());

  // Status starts as "running".
  await expect(page.locator(statusSel)).toHaveText('running', { timeout: 5_000 });

  // Flip the lane to dead via the fake spawner.
  const setResp = await page.request.post('/api/v1/__fake__/set_state', {
    data: { lane: short, running: false, rc: 17 },
  });
  expect(setResp.status()).toBe(200);

  // Dashboard polls /state every 2 s — pill must flip within 5 s.
  await expect(page.locator(statusSel)).toHaveText('exited (rc=17)', { timeout: 5_000 });
});
