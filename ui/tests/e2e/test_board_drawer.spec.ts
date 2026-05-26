// test_board_drawer.spec.ts — Task 3.5b: terminal-drawer single-instance + streaming.
//
// Runs under chromium-board (MEGALODON_FAKE_SPAWNER=1, fix-small fixture,
// 3 lanes A/B/C, port 8769; workers:1, fullyParallel:false).
//
// Verifies the board's terminal-drawer seam:
//   1. Click board-terminal-A → board-drawer visible with a .term-pane xterm.
//   2. Push bytes via POST /api/v1/__fake__/emit → assert they render in the pane.
//   3. Open board-terminal-B → assert exactly ONE drawer/.term-pane exists
//      (single-drawer invariant: lane A's pane disposed when B opens).
//   4. Click board-drawer-close → drawer gone (clean teardown).

import { test, expect, Page } from '@playwright/test';
import { readUiToken } from './_helpers';

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

async function authenticateAndGotoBoard(page: Page, token: string): Promise<void> {
  // The activity wall now auto-opens on mount (default-open). Its fixed right-
  // side panel overlaps the terminal DRAWER (also a right-side overlay) that
  // this spec exercises. This spec is about the terminal drawer, not the wall,
  // so pin the wall CLOSED before the SPA boots.
  await page.addInitScript(() => {
    try { localStorage.setItem('megalodon.activityWall.open', '0'); } catch (_) { /* ignore */ }
  });
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/', { timeout: 10_000 });
  await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
}

/** Emit a single text line into a lane's pane-stream via the fake endpoint. */
async function fakeEmitLine(page: Page, short: string, text: string): Promise<void> {
  const dataB64 = Buffer.from(text + '\r\n', 'utf-8').toString('base64');
  const r = await page.request.post('/api/v1/__fake__/emit', {
    data: { lane: short, data_b64: dataB64 },
  });
  expect(r.status(), 'POST /api/v1/__fake__/emit').toBe(200);
}

// ---------------------------------------------------------------------------
// Test: drawer open → stream → single-instance swap → close
// ---------------------------------------------------------------------------

test.describe('board drawer: single-instance terminal + streaming + teardown', () => {

  test('open A → stream bytes → open B (A disposed) → close', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    await authenticateAndGotoBoard(page, token);

    const drawer = page.locator('[data-testid="board-drawer"]');
    const termPane = page.locator('[data-testid="board-drawer"] .term-pane');

    // ---- Step 1: open lane A's drawer -------------------------------------
    await page.locator('[data-testid="board-terminal-A"]').click();
    await expect(drawer).toBeVisible({ timeout: 8_000 });
    await expect(termPane).toBeVisible({ timeout: 8_000 });
    // Let the pane-stream SSE connect before emitting.
    await page.waitForTimeout(600);

    // ---- Step 2: push bytes via __fake__/emit and assert they render -------
    const probe = `DRAWER_PROBE_${Date.now()}`;
    await fakeEmitLine(page, 'A', probe);
    await expect.poll(
      async () => (await drawer.textContent()) ?? '',
      { timeout: 4_000, message: `emitted bytes "${probe}" did not render in the drawer` },
    ).toContain(probe);

    // ---- Step 3: open lane B → exactly ONE drawer / one .term-pane --------
    await page.locator('[data-testid="board-terminal-B"]').click();
    await expect(drawer).toBeVisible({ timeout: 8_000 });
    // Single-drawer invariant: A's pane disposed, only B's remains.
    await expect(page.locator('[data-testid="board-drawer"]')).toHaveCount(1);
    await expect(page.locator('.term-pane')).toHaveCount(1);
    // Settle the new pane's SSE, then confirm B streams independently.
    await page.waitForTimeout(600);
    const probeB = `DRAWER_PROBE_B_${Date.now()}`;
    await fakeEmitLine(page, 'B', probeB);
    await expect.poll(
      async () => (await drawer.textContent()) ?? '',
      { timeout: 4_000, message: `lane B emit "${probeB}" did not render after swap` },
    ).toContain(probeB);

    // ---- Step 4: close the drawer → gone ----------------------------------
    await page.locator('[data-testid="board-drawer-close"]').click();
    await expect(page.locator('[data-testid="board-drawer"]')).toHaveCount(0, { timeout: 5_000 });
    await expect(page.locator('.term-pane')).toHaveCount(0);
  });

});
