// test_board_precedence.spec.ts — Task 3.5b: CV-8 pill precedence (the important one).
//
// Runs under chromium-board (MEGALODON_FAKE_SPAWNER=1, fix-small fixture,
// 3 lanes A/B/C, port 8769; workers:1, fullyParallel:false).
//
// CV-8 precedence: BLOCKED (governor deny-loop) > STALE > RUNNING/IDLE (narrative).
// A governor-blocked lane ALWAYS shows BLOCKED, and the narrative SSE handler
// must NOT overwrite a blocked lane's pill (no flicker).
//
// The board derives the BLOCKED set from the top-level `governor_blocked` list
// in the GET /api/v1/lanes/stale response (Task 4.1). We STUB that endpoint via
// page.route() with a mutable body so we can drive lane B in and out of the
// blocked set deterministically (mirrors test_board_blocked_and_stale Test 3).
//
// Flow (lane B):
//   1. Stub /api/v1/lanes/stale → { stale_lanes: [], governor_blocked: [B] }.
//   2. Seed lane B RUNNING via __fake__/narrative.
//   3. Assert board-pill-B is BLOCKED (not RUNNING) — the governor_blocked set wins.
//   4. Publish ANOTHER RUNNING narrative frame for lane B and assert the pill
//      STAYS BLOCKED — the SSE handler must not overwrite the blocked lane.
//   5. Flip the stub to governor_blocked: [] and reload (re-triggers the
//      mount-time /lanes/stale poll); assert the pill returns to RUNNING.

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

const RUNNING_B = {
  lane: 'B',
  lane_name: 'agent-b',
  state: 'claimed',
  last: { task_id: 'T1', desc: 'precedence-last' },
  now: { task_id: 'T2', desc: 'precedence-now', phrase: 'precedence-now-phrase' },
  goal: 'precedence-goal',
  tokens: 99,
  narrator_ok: true,
};

/** A single governor_blocked record for lane B (≥5 denies/60s deny-loop). */
const GOVERNOR_BLOCKED_B = {
  lane: 'B',
  deny_count: 6,
  window_seconds: 60,
  last_category: 'bash-interpreter',
  last_reason: 'deny-loop: repeated interpreter invocation',
};

// ---------------------------------------------------------------------------
// CV-8: BLOCKED > RUNNING, SSE does not overwrite, unblock → RUNNING
// ---------------------------------------------------------------------------

test.describe('board precedence (CV-8): governor_blocked forces BLOCKED', () => {

  test('RUNNING + governor_blocked → BLOCKED; SSE keeps BLOCKED; unblock → RUNNING', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);

    // ---- Step 1: stub /lanes/stale with a mutable governor_blocked body -----
    // Lane B is in the governor deny-loop set; no stale lanes (isolate STALE
    // from the fixture's ancient timestamps). We mutate `blockB` and reload to
    // exercise the unblock transition at Step 5.
    let blockB = true;
    await page.route('**/api/v1/lanes/stale', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          stale_lanes: [],
          governor_blocked: blockB ? [GOVERNOR_BLOCKED_B] : [],
          checked_at_utc: new Date().toISOString(),
        }),
      });
    });

    await authenticateAndGotoBoard(page, token);

    // ---- Step 2: seed lane B RUNNING via narrative -------------------------
    await seedNarrative(page, { B: RUNNING_B });

    // ---- Step 3: pill must be BLOCKED, not RUNNING -------------------------
    // governor_blocked (from the stubbed /lanes/stale) wins over the RUNNING
    // narrative state.
    await expect(page.locator('[data-testid="board-pill-B"]'))
      .toHaveText('BLOCKED', { timeout: 8_000 });

    // ---- Step 4: a new RUNNING narrative frame must NOT overwrite BLOCKED ---
    await seedNarrative(page, { B: RUNNING_B });
    // Give the SSE frame time to land, then assert the pill is STILL BLOCKED
    // (no flicker to RUNNING — the precedence guard never overwrites BLOCKED).
    await page.waitForTimeout(2_000);
    await expect(page.locator('[data-testid="board-pill-B"]')).toHaveText('BLOCKED');

    // ---- Step 5: unblock → pill returns to RUNNING -------------------------
    // Flip the stub to an empty governor_blocked set and reload so the board's
    // mount-time /lanes/stale poll picks up the new (empty) blocked set. The
    // RUNNING_B narrative persists in the shared narrative_cache, so the board
    // re-paints lane B as RUNNING once it is no longer in the blocked set.
    blockB = false;
    await page.reload();
    await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
    // Re-seed RUNNING after reload to guarantee an SSE frame lands post-mount.
    await seedNarrative(page, { B: RUNNING_B });

    await expect(page.locator('[data-testid="board-pill-B"]'))
      .toHaveText('RUNNING', { timeout: 10_000 });
  });

});
