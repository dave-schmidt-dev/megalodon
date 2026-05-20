// P5.3 — dashboard-loads.spec.ts
//
// Asserts the v9.2 dashboard renders one xterm pane per lane reported by
// /api/v1/config, and that the v9.0 chrome is hidden in v9.2 mode.
//
// Runs under `MEGALODON_V92_DASHBOARD=1 MEGALODON_LIFESPAN_TEST_MODE=1`
// (configured in playwright.config.ts -> chromium-v92-dashboard project).
// LIFESPAN_TEST_MODE means no real FleetSpawner is started, so the SSE
// `/api/v1/lane/<NAME>/pane-stream` endpoint returns 404 — that's fine
// here because this spec only verifies grid rendering and modal scaffolding,
// not live byte delivery (that's `streams-render.spec.ts` against a real
// tmux fixture).
//
// Fixture: ui/tests/fixtures/fix-medium (6 default lanes via
// default_v9_0_shape).

import { test, expect } from '@playwright/test';
import { readUiToken } from './_helpers';

test.describe('v9.2 dashboard loads', () => {
  test('renders one xterm pane per lane reported by /api/v1/config', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    await page.goto(`/#t=${token}`);

    // Auth bootstrap wipes the hash; URL must settle to "/".
    await expect(page).toHaveURL('/');

    // v9.2 dashboard takes over: lane-grid container present.
    await expect(page.locator('[data-testid="lane-grid"]')).toBeVisible();

    // /api/v1/config exposes lanes; one pane per lane.
    const configResp = await page.request.get('/api/v1/config');
    expect(configResp.status()).toBe(200);
    const config = await configResp.json();
    expect(config.v92_dashboard).toBe(true);
    const laneCount: number = config.lanes.length;
    expect(laneCount).toBeGreaterThan(0);

    await expect(page.locator('[data-testid^="lane-pane-"]')).toHaveCount(laneCount);
  });

  test('each pane has a Terminal mounted (xterm DOM present)', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    await page.goto(`/#t=${token}`);
    await expect(page).toHaveURL('/');

    // xterm.js renders an inner `.xterm-screen` element when Terminal() opens.
    // Wait for at least one to appear (proves the script ran end-to-end).
    await expect(page.locator('.xterm-screen').first()).toBeVisible({ timeout: 5_000 });

    // Each lane pane should contain its own .xterm-screen.
    const configResp = await page.request.get('/api/v1/config');
    const config = await configResp.json();
    await expect(page.locator('.xterm-screen')).toHaveCount(config.lanes.length);
  });

  test('paste-token modal is in the DOM and hidden at load', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    await page.goto(`/#t=${token}`);
    await expect(page).toHaveURL('/');

    // Modal must be PRESENT in the DOM so it can be shown reactively on 401.
    const modal = page.locator('[data-testid="paste-token-modal"]');
    await expect(modal).toBeAttached();
    await expect(modal).not.toBeVisible();
  });

  test('v9.0 chrome is hidden when v9.2 dashboard active', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    await page.goto(`/#t=${token}`);
    await expect(page).toHaveURL('/');

    // The v9.0 phase-strip is the most visible piece of legacy chrome.
    // Replace mode means it is removed or hidden.
    await expect(page.locator('.phase-strip')).toHaveCount(0).catch(async () => {
      // Either removed from DOM or set display:none — both satisfy the contract.
      await expect(page.locator('.phase-strip')).toBeHidden();
    });
  });
});
