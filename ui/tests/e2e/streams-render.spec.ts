// P5.3 — streams-render.spec.ts
//
// Asserts: a byte chunk emitted via the fake-spawner /__fake__/emit endpoint
// reaches the lane's xterm pane within 5 s. Pin the contract that the v9.2
// dashboard's SSE channel + base64-decoded term.write() path actually delivers
// bytes end-to-end.
//
// Runs under MEGALODON_FAKE_SPAWNER=1 (configured in playwright.config.ts ->
// chromium-v92-dashboard project). The fake spawner installs a deterministic
// in-process FakeFleetSpawner that bypasses real tmux but matches the public
// surface, so the SSE pipeline runs unchanged from index.html through
// dashboard-v92.js through the route handlers.

import { test, expect } from '@playwright/test';
import { readUiToken } from './_helpers';

test('streams-render: emitted bytes appear in the xterm pane within 5 s', async ({ page }, testInfo) => {
  const token = readUiToken(testInfo);
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/');

  // Wait for the v9.2 grid + at least one Terminal mounted.
  await expect(page.locator('[data-testid="lane-grid"]')).toBeVisible();
  await expect(page.locator('.xterm-screen').first()).toBeVisible({ timeout: 5_000 });

  // Discover the first lane's short code.
  const configResp = await page.request.get('/api/v1/config');
  expect(configResp.status()).toBe(200);
  const config = await configResp.json();
  const firstLane = config.lanes[0];
  expect(firstLane).toBeTruthy();
  const short: string = firstLane.short;

  // Settle: give the dashboard time to open SSE + receive the clear/replay events.
  await page.waitForTimeout(800);

  // Emit a unique probe string via the fake spawner.
  const probe = `STREAMS_RENDER_PROBE_${Date.now()}`;
  const dataB64 = Buffer.from(probe, 'utf-8').toString('base64');
  const emitResp = await page.request.post('/api/v1/__fake__/emit', {
    data: { lane: short, data_b64: dataB64 },
  });
  expect(emitResp.status()).toBe(200);

  // Wait for the probe to appear in the xterm pane's textContent.
  await expect.poll(
    async () => {
      const text = await page.locator(`[data-testid="lane-term-${firstLane.name}"]`).textContent();
      return text || '';
    },
    { timeout: 5_000, message: `expected ${probe} in lane ${firstLane.name} pane` },
  ).toContain(probe);
});
