// test_board_precedence.spec.ts — Task 3.5b: CV-8 pill precedence (the important one).
//
// Runs under chromium-board (MEGALODON_FAKE_SPAWNER=1, fix-small fixture,
// 3 lanes A/B/C, port 8769; workers:1, fullyParallel:false).
//
// CV-8 precedence: BLOCKED (pending prompt) > STALE > RUNNING/IDLE (narrative).
// A lane with a pending permission prompt ALWAYS shows BLOCKED, and the
// narrative SSE handler must NOT overwrite a blocked lane's pill (no flicker).
//
// Flow (lane B):
//   1. Stub /api/v1/lanes/stale → empty (isolate from the fixture's ancient
//      timestamps so STALE does not confound BLOCKED-vs-RUNNING).
//   2. Seed lane B RUNNING via __fake__/narrative.
//   3. Create a pending permission prompt for lane B by writing a Claude-REPL
//      prompt block to .fleet/B.stream.log (same mechanism as the v94 smokes;
//      the PermissionWatcher polls every 1 s, the banner polls every 2 s).
//   4. Assert board-pill-B is BLOCKED (not RUNNING).
//   5. Publish ANOTHER RUNNING narrative frame for lane B and assert the pill
//      STAYS BLOCKED — the SSE handler must not overwrite the active banner.
//   6. Approve the prompt and assert the pill returns to RUNNING.

import { test, expect, Page } from '@playwright/test';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import * as path from 'node:path';

import { fixtureRootForProject, readUiToken } from './_helpers';

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

async function stubNoStaleLanes(page: Page): Promise<void> {
  await page.route('**/api/v1/lanes/stale', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ stale_lanes: [], checked_at_utc: new Date().toISOString() }),
    });
  });
}

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

/**
 * Write a Claude-REPL permission-prompt block to <lane>.stream.log (TRUNCATE).
 * The PermissionWatcher (poll=1 s) detects "Do you want to proceed?" and
 * surfaces a prompt for the lane. Truncating (not appending) keeps this the
 * only prompt block in the log so no stale block from an earlier spec lingers.
 */
function writePromptBlock(fixtureRoot: string, lane: string, cmd: string): void {
  const fleetDir = path.join(fixtureRoot, '.fleet');
  if (!existsSync(fleetDir)) mkdirSync(fleetDir, { recursive: true });
  const streamLogPath = path.join(fleetDir, `${lane}.stream.log`);
  const block =
    'Bash command\n' +
    cmd + '\n' +
    'Do you want to proceed?\n' +
    '❯ 1. Yes\n' +
    '  2. Yes, and always allow access\n' +
    '  3. No\n';
  writeFileSync(streamLogPath, block, 'utf-8');
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

// ---------------------------------------------------------------------------
// CV-8: BLOCKED > RUNNING, SSE does not overwrite, approve → RUNNING
// ---------------------------------------------------------------------------

test.describe('board precedence (CV-8): pending prompt forces BLOCKED', () => {

  test('RUNNING + pending prompt → BLOCKED; SSE keeps BLOCKED; approve → RUNNING', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    const fixtureRoot = fixtureRootForProject(testInfo);

    await stubNoStaleLanes(page);
    await authenticateAndGotoBoard(page, token);

    // ---- Step 2: seed lane B RUNNING via narrative -------------------------
    await seedNarrative(page, { B: RUNNING_B });
    await expect(page.locator('[data-testid="board-pill-B"]'))
      .toHaveText('RUNNING', { timeout: 8_000 });

    // ---- Step 3: create a pending permission prompt for lane B -------------
    const cmd = 'curl -s http://127.0.0.1:8769/precedence-probe';
    writePromptBlock(fixtureRoot, 'B', cmd);

    // Wait until the banner surfaces lane B's prompt (watcher 1 s + FE poll 2 s).
    const banner = page.locator('[data-testid="permission-panel"]');
    await expect(banner).not.toBeHidden({ timeout: 10_000 });
    await expect(page.locator('[data-testid="permission-prompt-B"]'))
      .toContainText('curl', { timeout: 10_000 });

    // ---- Step 4: pill must flip to BLOCKED, not RUNNING --------------------
    await expect(page.locator('[data-testid="board-pill-B"]'))
      .toHaveText('BLOCKED', { timeout: 8_000 });

    // ---- Step 5: a new RUNNING narrative frame must NOT overwrite BLOCKED ---
    await seedNarrative(page, { B: RUNNING_B });
    // Give the SSE frame + a banner poll cycle time to land, then assert the
    // pill is STILL BLOCKED (no flicker to RUNNING).
    await page.waitForTimeout(2_500);
    await expect(page.locator('[data-testid="board-pill-B"]')).toHaveText('BLOCKED');

    // ---- Step 6: approve the prompt → pill returns to RUNNING --------------
    // Truncate the stream log first so the watcher does not re-detect the block
    // on its next poll and re-surface the prompt after we clear it.
    writeFileSync(path.join(fixtureRoot, '.fleet', 'B.stream.log'), '', 'utf-8');

    let respondStatus: number | null = null;
    await page.route('**/permission_prompts/B/respond', async (route) => {
      const resp = await route.fetch();
      respondStatus = resp.status();
      await route.fulfill({ response: resp });
    });

    await page.locator('[data-testid="permission-approve-B"]').click();
    await expect.poll(() => respondStatus, { timeout: 8_000 }).toBe(202);

    // Banner clears (no pending prompts) and the pill falls back to the
    // narrative RUNNING state once blockedLanes no longer contains B.
    await expect(page.locator('[data-testid="permission-prompt-B"]'))
      .toHaveCount(0, { timeout: 10_000 });
    await expect(page.locator('[data-testid="board-pill-B"]'))
      .toHaveText('RUNNING', { timeout: 10_000 });
  });

});
