// test_board_stale.spec.ts — Task 3.5b: board STALE-pill (CV-8 STALE overlay).
//
// Runs under chromium-board (MEGALODON_FAKE_SPAWNER=1, fix-small fixture,
// 3 lanes A/B/C, port 8769; workers:1, fullyParallel:false).
//
// The board surfaces staleness as a per-row STALE pill (board-pill-<short>),
// NOT the grid's stale-badge + stale_modal affordance. This spec is the
// board-native replacement for the grid-only stale-badge assertions retired in
// test_stale_badge.spec.ts (Task 3.5a skips).
//
// Flow:
//   1. Force lane A stale via POST /api/v1/_test/stale_override (the same
//      mechanism used by test_stale_badge.spec.ts; registered only in
//      MEGALODON_FAKE_SPAWNER=1 mode). Reload so the board's mount-time
//      /api/v1/lanes/stale fetch consumes the one-shot override.
//   2. Seed lane A RUNNING via __fake__/narrative.
//   3. Assert board-pill-A shows STALE — STALE overlays RUNNING/IDLE in the
//      pill precedence (BLOCKED > STALE > RUNNING/IDLE) when no prompt pending.

import { test, expect, Page } from '@playwright/test';
import { readUiToken } from './_helpers';

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

async function authenticateAndGotoBoard(page: Page, token: string): Promise<void> {
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

/** POST /api/v1/__fake__/narrative to publish a narrative frame. */
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

// ---------------------------------------------------------------------------
// Test: STALE overlays RUNNING when no prompt pending
// ---------------------------------------------------------------------------

test.describe('board stale: STALE pill overlays RUNNING/IDLE', () => {

  test('stale_override + RUNNING narrative → board-pill-A shows STALE', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    await authenticateAndGotoBoard(page, token);

    // Mark lane A stale (1200 s = 20 min).
    await setStaleOverride(page, 'A', 1200);

    // Reload so the board's mount-time /api/v1/lanes/stale fetch consumes the
    // one-shot override and seeds staleLanes for lane A.
    await page.reload();
    await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });

    // Seed lane A RUNNING via narrative — STALE must still win.
    await seedNarrative(page, {
      A: {
        lane: 'A',
        lane_name: 'agent-a',
        state: 'claimed',
        last: { task_id: 'T1', desc: 'stale-last' },
        now: { task_id: 'T2', desc: 'stale-now', phrase: 'stale-now-phrase' },
        goal: 'stale-goal',
        tokens: 42,
        narrator_ok: true,
      },
    });

    // board-pill-A must be STALE (STALE overlays the RUNNING narrative state).
    await expect(page.locator('[data-testid="board-pill-A"]'))
      .toHaveText('STALE', { timeout: 8_000 });
  });

});
