// test_board_safety.spec.ts — Wave 3 FE safety UI.
//
// Runs under chromium-board AND webkit-board (MEGALODON_FAKE_SPAWNER=1,
// fix-small fixture, 3 lanes A/B/C; workers:1, fullyParallel:false).
//
// Covers the four operator-trust gaps the campaign targets:
//   (a) a lane with liveness:"dead" shows a distinct DEAD pill.
//   (b) READ-ONLY (default) DISABLES the inject form + the kill-switch; flipping
//       the control-mode toggle to CONTROL enables them.
//   (c) the kill-switch fires DELETE /api/v1/fleet after a confirm modal.
//   (d) an alert from GET /api/v1/alerts surfaces in the dismissible banner.
//   (e) a lane with consecutive_denies >= threshold shows the DENY-LOOP badge.
//
// liveness rides the narrative payload (same path as `governed`), so the
// existing /api/v1/__fake__/narrative injector seeds it unchanged. consecutive_
// denies rides /api/v1/lanes/stale governor_blocked — stubbed via page.route.
// GET /api/v1/alerts is NOT yet implemented by the BE during parallel dev, so it
// is mocked via page.route. DELETE /api/v1/fleet is REAL but mocked here so the
// test does not actually tear down the shared worker server.

import { test, expect, Page } from '@playwright/test';
import { readUiToken } from './_helpers';

async function gotoBoard(page: Page, token: string): Promise<void> {
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/', { timeout: 10_000 });
  await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
}

async function readCsrf(page: Page): Promise<string> {
  return page.evaluate(
    () =>
      (document.querySelector('meta[name="csrf-token"]') as HTMLMetaElement | null)
        ?.getAttribute('content') ?? '',
  );
}

async function seedNarrative(page: Page, lanes: Record<string, unknown>): Promise<void> {
  const csrf = await readCsrf(page);
  const resp = await page.request.post('/api/v1/__fake__/narrative', {
    headers: { 'Content-Type': 'application/json', ...(csrf ? { 'X-CSRF-Token': csrf } : {}) },
    data: { lanes },
  });
  expect(resp.status(), 'POST /api/v1/__fake__/narrative').toBe(200);
}

/** Stub /lanes/stale to a fixed payload (optionally with deny-looping lanes). */
async function stubStale(
  page: Page,
  governorBlocked: Array<{ lane: string; consecutive_denies: number }> = [],
): Promise<void> {
  await page.route('**/api/v1/lanes/stale', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        stale_lanes: [],
        governor_blocked: governorBlocked.map((g) => ({
          lane: g.lane,
          deny_count: g.consecutive_denies,
          consecutive_denies: g.consecutive_denies,
          window_seconds: 60,
          last_category: 'Bash',
          last_reason: 'denied',
        })),
        checked_at_utc: new Date().toISOString(),
      }),
    });
  });
}

/** Stub GET /api/v1/alerts to a fixed list. */
async function stubAlerts(page: Page, alerts: unknown[]): Promise<void> {
  await page.route('**/api/v1/alerts', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ alerts }),
    });
  });
}

function laneC(extra: Record<string, unknown>): Record<string, unknown> {
  return {
    lane: 'C',
    lane_name: 'agent-c',
    state: 'running',
    last: null,
    now: null,
    goal: 'safety-test',
    tokens: null,
    narrator_ok: true,
    governed: true,
    ...extra,
  };
}

const ENABLE_CONTROL = `[data-testid="action-toggle-control-mode"]`;

test.describe('Wave 3: board safety UI', () => {
  // Leave lane C neutral (alive + governed) so the shared narrative_cache is
  // reset for sibling specs in the same worker.
  test.afterEach(async ({ page }) => {
    await seedNarrative(page, {
      C: { lane: 'C', lane_name: 'agent-c', state: 'done', last: null, now: null, goal: null, tokens: null, narrator_ok: true, governed: true, liveness: 'unknown' },
    });
    // Reset control mode to read-only (localStorage) for hermeticity.
    await page.evaluate(() => {
      try { localStorage.setItem('controlMode', 'false'); } catch (_) { /* ignore */ }
    });
  });

  test('(a) liveness:"dead" → DEAD pill visible; EXITED for "exited"', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    await stubStale(page);
    await stubAlerts(page, []);
    await gotoBoard(page, token);

    await seedNarrative(page, { C: laneC({ liveness: 'dead' }) });
    const dead = page.locator('[data-testid="board-liveness-C"]');
    await expect(dead).toBeVisible({ timeout: 8_000 });
    await expect(dead).toHaveText('DEAD');

    // Flip to "exited" → muted EXITED pill.
    await seedNarrative(page, { C: laneC({ liveness: 'exited' }) });
    await expect(dead).toHaveText('EXITED', { timeout: 8_000 });

    // Flip to "running" → pill hidden.
    await seedNarrative(page, { C: laneC({ liveness: 'running' }) });
    await expect(dead).not.toBeVisible({ timeout: 8_000 });
  });

  test('(b) read-only disables inject + kill-switch; control enables them', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    await stubStale(page);
    await stubAlerts(page, []);
    await gotoBoard(page, token);

    // Kill-switch disabled in read-only (default).
    const kill = page.locator('[data-testid="board-kill-switch"]');
    await expect(kill).toBeVisible();
    await expect(kill).toBeDisabled();

    // Inject form (lane detail) disabled in read-only.
    await page.goto(`/lane/C`);
    await expect(page.locator('[data-testid="inject-form"]')).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('[data-testid="inject-send"]')).toBeDisabled();
    await expect(page.locator('[data-testid="inject-textarea"]')).toBeDisabled();

    // Flip to Control mode via the nav toggle → both enable.
    await page.locator(ENABLE_CONTROL).click();
    await expect(page.locator('[data-testid="inject-send"]')).toBeEnabled();
    await expect(page.locator('[data-testid="inject-textarea"]')).toBeEnabled();

    // Back to the board; kill-switch is now enabled.
    await page.goto(`/`);
    await expect(page.locator('[data-testid="board-kill-switch"]')).toBeEnabled({ timeout: 10_000 });
  });

  test('(c) kill-switch fires DELETE /api/v1/fleet after confirm', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    await stubStale(page);
    await stubAlerts(page, []);

    // Mock the DELETE so we don't tear down the shared worker server.
    let deleteFired = false;
    await page.route('**/api/v1/fleet', async (route) => {
      if (route.request().method() === 'DELETE') {
        deleteFired = true;
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'shutdown' }) });
        return;
      }
      await route.continue();
    });

    // Enter control mode before navigating so the kill-switch is enabled.
    await gotoBoard(page, token);
    await page.locator(ENABLE_CONTROL).click();
    const kill = page.locator('[data-testid="board-kill-switch"]');
    await expect(kill).toBeEnabled();

    // Click → confirm modal appears.
    await kill.click();
    await expect(page.locator('[data-testid="confirm-modal"]')).toBeVisible();

    // Cancel first: no DELETE.
    await page.locator('[data-testid="confirm-modal-cancel"]').click();
    await expect(page.locator('[data-testid="confirm-modal"]')).toHaveCount(0);
    expect(deleteFired).toBe(false);

    // Click again → confirm → DELETE fires.
    await kill.click();
    await expect(page.locator('[data-testid="confirm-modal"]')).toBeVisible();
    await page.locator('[data-testid="confirm-modal-confirm"]').click();
    await expect.poll(() => deleteFired, { timeout: 5_000 }).toBe(true);
  });

  test('(d) alert from /api/v1/alerts surfaces in the banner; dismissable', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    await stubStale(page);
    await stubAlerts(page, [
      { ts: '2026-05-25T12:00:00Z', lane: 'C', kind: 'CRASHED', severity: 'blocking', evidence: ['rc=17'], message: 'Lane C process crashed (rc=17).' },
    ]);
    await gotoBoard(page, token);

    const banner = page.locator('[data-testid="alert-banner-C-CRASHED"]');
    await expect(banner).toBeVisible({ timeout: 8_000 });
    await expect(banner).toContainText('CRASHED');
    await expect(banner).toContainText('crashed');

    // Dismiss → banner removed.
    await page.locator('[data-testid="alert-dismiss-C-CRASHED"]').click();
    await expect(banner).toHaveCount(0);
  });

  test('(e) consecutive_denies >= threshold → DENY-LOOP badge', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    await stubStale(page, [{ lane: 'C', consecutive_denies: 7 }]);
    await stubAlerts(page, []);
    await gotoBoard(page, token);

    const deny = page.locator('[data-testid="board-denyloop-C"]');
    await expect(deny).toBeVisible({ timeout: 8_000 });
    await expect(deny).toContainText('DENY');

    // The aggregate alarm strip surfaces (deny-loop is a critical count).
    await expect(page.locator('[data-testid="board-alarm-strip"]')).toBeVisible();
    await expect(page.locator('[data-testid="alarm-count-denyloop"]')).toContainText('1');
  });
});
