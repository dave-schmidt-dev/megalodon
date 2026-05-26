// test_board_goal_progress.spec.ts — regression for the operator's core
// complaint: "can't see what agents' goals are or their progress."
//
// Confirmed bugs (verified against a mid-run fixture; see board_state.py):
//   B1: a lane whose STATUS reports ``working: P4-A`` but which has a prior DONE
//       ``P3-A`` rendered pill=IDLE, Goal=the DONE task, Now="narrator warming
//       up…". The live ``working:`` marker is now authoritative for current
//       activity (state/now/goal), independent of prior done tasks.
//   B2: basic progress must not require a live LLM narrator phrase — the Now
//       line falls back to the resolved task description.
//   I3: a coordination-signal STATUS note (``SIG-FROM-LANE-D: …``) must never
//       become the Now/Goal line — the resolved task description wins.
//
// This spec drives the board's narrative RENDER path verbatim. To keep the
// assertion deterministic (the fake-fleet narrator scheduler periodically
// re-derives frames from the fixture, which would race a seeded frame) it
// network-stubs the initial GET /api/v1/narrative with the exact uniform
// { lanes: {...} } frame the corrected board_state assembler produces, and
// stubs the SSE stream to deliver nothing further. The assembler's DERIVATION
// precedence itself is unit-covered in scripts/tests/test_board_state.py
// (TestLiveWorkingPrecedence).
//
// Runs under chromium-board (MEGALODON_FAKE_SPAWNER=1, fix-small fixture,
// 3 lanes A/B/C, port 8769).

import { test, expect, Page } from '@playwright/test';
import { readUiToken } from './_helpers';

/** Stub /api/v1/lanes/stale → no stale/blocked lanes (the RUNNING pill must be
 *  deterministic; STALE/BLOCKED precedence is covered elsewhere). */
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

/** Stub the initial narrative snapshot with a fixed frame, and stub the SSE
 *  stream so no scheduler-derived frame can overwrite it (deterministic). */
async function stubNarrative(page: Page, lanes: Record<string, unknown>): Promise<void> {
  await page.route('**/api/v1/narrative', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ lanes }),
    });
  });
  // An SSE stream that opens then stays silent (one keep-alive comment) — the
  // board paints from the snapshot above and receives no overriding frame.
  await page.route('**/api/v1/narrative-stream', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      headers: { 'Cache-Control': 'no-cache', Connection: 'keep-alive' },
      body: ': keep-alive\n\n',
    });
  });
}

async function authenticateAndGotoBoard(page: Page, token: string): Promise<void> {
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/', { timeout: 10_000 });
  await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
}

function payload(over: Record<string, unknown>): Record<string, unknown> {
  return {
    lane: 'A',
    lane_name: 'agent-a',
    state: 'open',
    last: null,
    now: null,
    goal: null,
    tokens: null,
    narrator_ok: true,
    ...over,
  };
}

test.describe('board goal/progress visibility (B1/B2/I3)', () => {
  test('working lane with a prior done task → RUNNING; Now/Goal = current task (not the done one, not warming-up)', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    await stubNoStaleLanes(page);
    // The corrected assembler output for the B1 scenario: live working: P4-A
    // wins for state/now/goal; the prior done P3-A is demoted to `last`. No
    // narrator phrase (phrase:null) — desc is the deterministic fallback (B2).
    await stubNarrative(page, {
      A: payload({
        lane: 'A',
        lane_name: 'agent-a',
        state: 'claimed',
        last: { task_id: 'P3-A', desc: 'dummy phase-3 task', phrase: null },
        now: { task_id: 'P4-A', desc: 'phase-4 deep audit', phrase: null },
        goal: 'phase-4 deep audit',
        tokens: 9000,
        narrator_ok: true,
      }),
    });
    await authenticateAndGotoBoard(page, token);

    const rowA = page.locator('[data-testid="board-row-A"]');
    // Pill is RUNNING, not IDLE.
    await expect(page.locator('[data-testid="board-pill-A"]')).toHaveText('RUNNING', {
      timeout: 8_000,
    });
    // Now line = the CURRENT task desc (B2: no narrator phrase needed) — and
    // explicitly NOT the "narrator warming up…" baseline.
    const nowLine = rowA.locator('.truncate').nth(1); // [Last, Now, Goal]
    await expect(nowLine).toHaveText('phase-4 deep audit');
    await expect(rowA).not.toContainText('narrator warming up');
    // Goal = the current task, NOT the prior done task.
    const goalLine = rowA.locator('.truncate').nth(2);
    await expect(goalLine).toHaveText('phase-4 deep audit');
    // The done task is preserved on the Last line (history), not the Goal.
    const lastLine = rowA.locator('.truncate').nth(0);
    await expect(lastLine).toContainText('dummy phase-3 task');
  });

  test('coordination-signal STATUS note never becomes the Goal (I3)', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    await stubNoStaleLanes(page);
    // The assembler resolved P4-A's DESCRIPTION by task_id and dropped the
    // SIG-FROM-LANE routing note; the frame therefore carries the clean desc.
    await stubNarrative(page, {
      A: payload({
        lane: 'A',
        lane_name: 'agent-a',
        state: 'claimed',
        now: { task_id: 'P4-A', desc: 'phase-4 deep audit', phrase: null },
        goal: 'phase-4 deep audit',
        tokens: 1,
        narrator_ok: true,
      }),
    });
    await authenticateAndGotoBoard(page, token);

    const rowA = page.locator('[data-testid="board-row-A"]');
    await expect(page.locator('[data-testid="board-pill-A"]')).toHaveText('RUNNING', {
      timeout: 8_000,
    });
    const goalLine = rowA.locator('.truncate').nth(2);
    await expect(goalLine).toHaveText('phase-4 deep audit');
    await expect(rowA).not.toContainText('SIG-FROM-LANE');
  });
});
