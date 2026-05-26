// test_board_blocked_and_stale.spec.ts — Task E2: CR-4 task-blocked pill + stale modal.
//
// Runs under chromium-board AND webkit-board (MEGALODON_FAKE_SPAWNER=1,
// fix-small fixture, 3 lanes A/B/C; workers:1, fullyParallel:false).
//
// Test 1 — CR-4 blocked pill:
//   Seed lane A with state: "blocked" via POST /api/v1/__fake__/narrative.
//   Assert board-pill-A shows BLOCKED.
//
// Test 2 — stale modal open/close:
//   Force lane B stale via _test/stale_override, reload so the board's
//   mount-time /lanes/stale fetch seeds staleLanes. Seed narrative so B
//   shows STALE. Click the STALE pill → assert board-stale-modal visible.
//   Close (× button) → assert not visible.
//
// Test 3 — no leaked modal after navigation:
//   Same stale setup → open modal → navigate to /lane/B → navigate back
//   to / → assert NO board-stale-modal in the DOM.

import { test, expect, Page } from '@playwright/test';
import { readUiToken, republishUntil } from './_helpers';

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

async function authenticateAndGotoBoard(page: Page, token: string): Promise<void> {
  // The activity wall now auto-opens on mount (default-open). Its fixed right-
  // side panel can overlap row controls / the stale modal this spec exercises.
  // This spec is about blocked/stale pills + modal, not the wall, so pin the
  // wall CLOSED before the SPA boots (the init script also covers the reload).
  await page.addInitScript(() => {
    try { localStorage.setItem('megalodon.activityWall.open', '0'); } catch (_) { /* ignore */ }
  });
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/', { timeout: 10_000 });
  await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
}

async function readCsrfToken(page: Page): Promise<string> {
  return page.evaluate(
    () =>
      (document.querySelector('meta[name="csrf-token"]') as HTMLMetaElement | null)
        ?.getAttribute('content') ?? '',
  );
}

async function seedNarrative(page: Page, lanes: Record<string, unknown>): Promise<void> {
  const csrf = await readCsrfToken(page);
  const resp = await page.request.post('/api/v1/__fake__/narrative', {
    headers: {
      'Content-Type': 'application/json',
      ...(csrf ? { 'X-CSRF-Token': csrf } : {}),
    },
    data: { lanes },
  });
  expect(resp.status(), 'POST /api/v1/__fake__/narrative').toBe(200);
}

/** POST _test/stale_override to mark a lane stale for `seconds`. */
async function setStaleOverride(page: Page, lane: string, seconds: number): Promise<void> {
  const csrf = await readCsrfToken(page);
  const resp = await page.request.post(
    `/api/v1/_test/stale_override?lane=${encodeURIComponent(lane)}&seconds=${seconds}`,
    {
      headers: {
        'Content-Type': 'application/json',
        ...(csrf ? { 'X-CSRF-Token': csrf } : {}),
      },
      data: {},
    },
  );
  expect(resp.status(), `stale_override POST for lane ${lane}`).toBe(200);
}

/** Stub /api/v1/lanes/stale to return no stale lanes. */
async function stubNoStaleLanes(page: Page): Promise<void> {
  await page.route('**/api/v1/lanes/stale', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ stale_lanes: [], checked_at_utc: new Date().toISOString() }),
    });
  });
}

// ---------------------------------------------------------------------------
// Test 1 — CR-4: task-blocked pill
// ---------------------------------------------------------------------------

test.describe('CR-4: task-blocked pill (state=blocked → BLOCKED pill)', () => {

  // Both tests here seed lane A with state:"blocked" via the real
  // POST /api/v1/__fake__/narrative injector, which persists in the server's
  // shared narrative_cache across tests in the worker (never auto-reset).
  // Reset lane A back to the fix-small neutral baseline (T1 done → state "done"
  // → IDLE pill) so this spec leaves the shared cache as it found it. Only
  // lane A is reset — it is the only lane this spec seeds via the injector.
  test.afterEach(async ({ page }) => {
    await seedNarrative(page, {
      A: {
        lane: 'A',
        lane_name: 'agent-a',
        state: 'done',
        last: null,
        now: null,
        goal: null,
        tokens: null,
        narrator_ok: true,
      },
    });
  });

  test('seeded state=blocked → board-pill shows BLOCKED', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    await stubNoStaleLanes(page);
    await authenticateAndGotoBoard(page, token);

    // Seed lane A with state: "blocked" (as the backend now emits for blocked tasks).
    // Re-publish until the board's narrative SSE subscription is live and the
    // BLOCKED pill renders (closes the seed→subscribe race; see republishUntil).
    const blockedFrame = {
      A: {
        lane: 'A',
        lane_name: 'agent-a',
        state: 'blocked',
        last: null,
        now: null,
        goal: 'blocked-goal',
        tokens: null,
        narrator_ok: true,
      },
    };
    await republishUntil(
      () => seedNarrative(page, blockedFrame),
      async () => (await page.locator('[data-testid="board-pill-A"]').textContent())?.trim() === 'BLOCKED',
    );

    // pill must be BLOCKED — task-blocked is the new source (CR-4).
    await expect(page.locator('[data-testid="board-pill-A"]'))
      .toHaveText('BLOCKED', { timeout: 8_000 });
  });

  test('state=blocked takes precedence over stale', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);

    // Mark lane A stale too.
    await authenticateAndGotoBoard(page, token);
    await setStaleOverride(page, 'A', 1200);
    await page.reload();
    await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });

    // Seed lane A with state: "blocked".
    await republishUntil(
      () => seedNarrative(page, {
        A: {
          lane: 'A',
          lane_name: 'agent-a',
          state: 'blocked',
          last: null,
          now: null,
          goal: 'blocked-goal',
          tokens: null,
          narrator_ok: true,
        },
      }),
      async () => (await page.locator('[data-testid="board-pill-A"]').textContent())?.trim() === 'BLOCKED',
    );

    // BLOCKED wins over STALE (BLOCKED > STALE in pill precedence).
    await expect(page.locator('[data-testid="board-pill-A"]'))
      .toHaveText('BLOCKED', { timeout: 8_000 });
  });

});

// ---------------------------------------------------------------------------
// Test 2 — stale modal: click STALE pill → modal visible; close → hidden
// ---------------------------------------------------------------------------

test.describe('stale modal: STALE pill click opens / closes modal', () => {

  test('click STALE pill opens board-stale-modal; close button hides it', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    await authenticateAndGotoBoard(page, token);

    // Mark lane B stale.
    await setStaleOverride(page, 'B', 900);

    // Reload so the board's mount-time /lanes/stale fetch picks up the override.
    await page.reload();
    await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });

    // Seed lane B so narrative arrives (STALE already wins via poll).
    await seedNarrative(page, {
      B: {
        lane: 'B',
        lane_name: 'agent-b',
        state: 'open',
        last: null,
        now: null,
        goal: 'stale-modal-test',
        tokens: null,
        narrator_ok: true,
      },
    });

    // board-pill-B must be STALE first.
    const pillB = page.locator('[data-testid="board-pill-B"]');
    await expect(pillB).toHaveText('STALE', { timeout: 8_000 });

    // Modal should not be visible initially.
    const modal = page.locator('[data-testid="board-stale-modal"]');
    await expect(modal).not.toBeVisible();

    // Click the STALE pill → modal opens.
    await pillB.click();
    await expect(modal).toBeVisible({ timeout: 5_000 });

    // Close button dismisses the modal.
    await page.locator('[data-testid="stale-modal-close"]').click();
    await expect(modal).not.toBeVisible({ timeout: 5_000 });
  });

});

// ---------------------------------------------------------------------------
// Test 3 — no leaked modal after board cleanup
// ---------------------------------------------------------------------------

test.describe('stale modal: no leaked modal after navigation', () => {

  test('open modal then navigate away → no board-stale-modal in DOM', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);

    // Stub /lanes/stale to return lane A as stale (deterministic — avoids all
    // server-state interactions from other tests in the same project run).
    const staleLanesResponse = {
      stale_lanes: [
        {
          lane: 'A',
          silent_seconds: 1234,
          last_activity_source: 'status-md',
        },
      ],
      governor_blocked: [],
      checked_at_utc: new Date().toISOString(),
    };
    await page.route('**/api/v1/lanes/stale', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(staleLanesResponse),
      });
    });

    // Stub narrative to return no frames so no blocked/running state overlays the STALE pill.
    await page.route('**/api/v1/narrative', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ lanes: {} }),
      });
    });
    // Stub the SSE stream to emit nothing (avoid narrative state from earlier tests).
    await page.route('**/api/v1/narrative-stream', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: '',
      });
    });

    await authenticateAndGotoBoard(page, token);

    // Wait for STALE pill on lane A (driven by the stub).
    const pillA = page.locator('[data-testid="board-pill-A"]');
    await expect(pillA).toHaveText('STALE', { timeout: 8_000 });

    // Open the modal by clicking the STALE pill.
    await pillA.click();
    await expect(page.locator('[data-testid="board-stale-modal"]')).toBeVisible({ timeout: 5_000 });

    // Navigate to a lane detail page — triggers the board's cleanup(),
    // which must close + remove the stale modal element from the DOM.
    await page.goto('/lane/A', { waitUntil: 'domcontentloaded' });

    // Navigate back to the board (fresh mount).
    await page.goto(`/#t=${token}`);
    await expect(page).toHaveURL('/', { timeout: 10_000 });
    await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });

    // The stale modal from the previous mount must not be visible.
    // cleanup() closes and removes it; the fresh mount creates a new hidden one.
    await expect(page.locator('[data-testid="board-stale-modal"]')).not.toBeVisible();
  });

});
