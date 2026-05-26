// test_board_ungoverned.spec.ts — §3.3: the board surfaces ungoverned lanes.
//
// Runs under chromium-board AND webkit-board (MEGALODON_FAKE_SPAWNER=1,
// fix-small fixture, 3 lanes A/B/C; workers:1, fullyParallel:false).
//
// The UNGOVERNED indicator ([data-testid="board-ungoverned-<short>"]) is
// ORTHOGONAL to the state pill. It is shown iff the lane is in a running state
// AND its narrative payload carries `governed === false` (strict). Idle/absent
// lanes (whose `governed` defaults to false) and governed lanes never show it.
//
// All cases seed lane C via POST /api/v1/__fake__/narrative, which passes the
// per-lane payload (including `governed`) straight into narrative_cache and
// publishes the frame unchanged — the same stream→render path the real
// scheduler drives. No backend change is needed: the fake injector already
// carries arbitrary row fields.
//
// Test 1 — running + governed:false → indicator VISIBLE.
// Test 2 — running + governed:true  → indicator HIDDEN.
// Test 3 — idle + governed:false    → indicator HIDDEN (false-positive guard).
// Test 4 — running→governed transition hides the indicator.

import { test, expect, Page } from '@playwright/test';
import { readUiToken, republishUntil } from './_helpers';

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

/** Stub /api/v1/lanes/stale to return no stale/blocked lanes (keeps the pill
 *  driven purely by narrative state for these governance-focused cases). */
async function stubNoStaleLanes(page: Page): Promise<void> {
  await page.route('**/api/v1/lanes/stale', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        stale_lanes: [],
        governor_blocked: [],
        checked_at_utc: new Date().toISOString(),
      }),
    });
  });
}

/** True once board-pill-<short> renders exactly `text` (republishUntil probe).
 *  Re-publishing the seed until the pill reflects the frame closes the
 *  seed→SSE-subscribe race (see republishUntil in _helpers.ts). */
async function pillIs(page: Page, short: string, text: string): Promise<boolean> {
  const t = await page.locator(`[data-testid="board-pill-${short}"]`).textContent();
  return (t ?? '').trim() === text;
}

/** Row payload for lane C in a given state + governance. */
function laneC(state: string, governed: boolean): Record<string, unknown> {
  return {
    lane: 'C',
    lane_name: 'agent-c',
    state,
    last: null,
    now: null,
    goal: 'ungoverned-test',
    tokens: null,
    narrator_ok: true,
    governed,
  };
}

test.describe('§3.3: UNGOVERNED board indicator', () => {

  // This spec only seeds lane C. Reset it to a neutral idle (done) baseline so
  // it leaves the worker's shared narrative_cache as it found it.
  test.afterEach(async ({ page }) => {
    await seedNarrative(page, {
      C: {
        lane: 'C',
        lane_name: 'agent-c',
        state: 'done',
        last: null,
        now: null,
        goal: null,
        tokens: null,
        narrator_ok: true,
        governed: true,
      },
    });
  });

  test('running + governed:false → UNGOVERNED indicator visible', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    await stubNoStaleLanes(page);
    await authenticateAndGotoBoard(page, token);

    await republishUntil(
      () => seedNarrative(page, { C: laneC('running', false) }),
      () => pillIs(page, 'C', 'RUNNING'),
    );

    // Pill is RUNNING (governance is orthogonal — does not change the pill).
    await expect(page.locator('[data-testid="board-pill-C"]'))
      .toHaveText('RUNNING', { timeout: 8_000 });
    // The UNGOVERNED chip is visible.
    await expect(page.locator('[data-testid="board-ungoverned-C"]'))
      .toBeVisible({ timeout: 8_000 });
  });

  test('running + governed:true → indicator hidden', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    await stubNoStaleLanes(page);
    await authenticateAndGotoBoard(page, token);

    await republishUntil(
      () => seedNarrative(page, { C: laneC('running', true) }),
      () => pillIs(page, 'C', 'RUNNING'),
    );

    await expect(page.locator('[data-testid="board-pill-C"]'))
      .toHaveText('RUNNING', { timeout: 8_000 });
    await expect(page.locator('[data-testid="board-ungoverned-C"]'))
      .not.toBeVisible();
  });

  test('idle + governed:false → indicator hidden (false-positive guard)', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    await stubNoStaleLanes(page);
    await authenticateAndGotoBoard(page, token);

    // "done" is not in the running set; governed:false must NOT flag it.
    await republishUntil(
      () => seedNarrative(page, { C: laneC('done', false) }),
      () => pillIs(page, 'C', 'IDLE'),
    );

    await expect(page.locator('[data-testid="board-pill-C"]'))
      .toHaveText('IDLE', { timeout: 8_000 });
    await expect(page.locator('[data-testid="board-ungoverned-C"]'))
      .not.toBeVisible();
  });

  test('running→governed transition hides the indicator', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    await stubNoStaleLanes(page);
    await authenticateAndGotoBoard(page, token);

    // First frame: running + ungoverned → visible.
    await republishUntil(
      () => seedNarrative(page, { C: laneC('running', false) }),
      () => page.locator('[data-testid="board-ungoverned-C"]').isVisible(),
    );
    await expect(page.locator('[data-testid="board-ungoverned-C"]'))
      .toBeVisible({ timeout: 8_000 });

    // Later frame: lane becomes governed (still running) → indicator disappears.
    // Probe on the pill (still RUNNING) so re-publish drives the transition;
    // the assertion below is the real check that the chip then hides.
    await republishUntil(
      () => seedNarrative(page, { C: laneC('running', true) }),
      async () => !(await page.locator('[data-testid="board-ungoverned-C"]').isVisible()),
    );
    await expect(page.locator('[data-testid="board-ungoverned-C"]'))
      .not.toBeVisible({ timeout: 8_000 });
  });

});
