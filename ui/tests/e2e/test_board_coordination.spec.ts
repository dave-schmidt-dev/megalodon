// test_board_coordination.spec.ts — Wave 2 FE: the coordination view.
//
// The headline feature: a single page answering "what are the agents doing,
// coordinating, and handing off?" Three sections fed by GET /api/v1/coordination:
//   1. Who's working what (lanes)
//   2. Claims & contention (contested claims flagged)
//   3. Recent signals (handoffs, clickable to a body drawer)
//
// The Python backend owns /api/v1/coordination (built in parallel). To test the
// FRONTEND deterministically and independently of BE timing, we mock the
// endpoint with a fixed payload that exercises all three sections incl. a
// contested claim and a blocked lane. (When the real endpoint lands, the page
// renders the same shape — see the FROZEN WIRE CONTRACT.)
//
// Runs under chromium-board / webkit-board (BOARD_SPEC_PATTERN matches
// `test_board_*`).

import { test, expect } from '@playwright/test';
import type { Page, TestInfo } from '@playwright/test';

import { readUiToken } from './_helpers';

const COORDINATION_PAYLOAD = {
  lanes: [
    { lane: 'LANE-A', agent: 'agent-aaaa', state: 'working', working_task: 'T2 parser align', blocked: false, notes_excerpt: 'wiring the grammar' },
    { lane: 'LANE-B', agent: 'agent-bbbb', state: 'blocked', working_task: 'T5 endpoint', blocked: true, notes_excerpt: 'waiting on LANE-A handoff' },
    { lane: 'LANE-C', agent: 'agent-cccc', state: 'idle', working_task: '', blocked: false, notes_excerpt: '' },
  ],
  claims: [
    { task_id: 'T2', dirname: 'T2', has_done: false, mtime: 1, owner: 'LANE-A', working_lane: 'LANE-A', contested: false },
    // Mirrors the real endpoint shape: an unowned/orphaned claim has owner:null
    // (not '') and working_lane:null.
    { task_id: 'T9-orphan', dirname: 'T9-orphan', has_done: false, mtime: 1, owner: null, working_lane: null, contested: true },
  ],
  signals_recent: [
    {
      filename: 'LANE-A-to-LANE-B-handoff-parser-2026-05-25T18-49Z.md',
      from_lane: 'LANE-A',
      to_lane: 'LANE-B',
      to: 'LANE-B',
      topic: 'handoff-parser',
      utc: '2026-05-25T18-49Z',
      kind: 'SIGNAL',
      body: 'Parser is aligned to the new grammar. Over to you for the endpoint.',
      source: 'file',
    },
  ],
};

async function authAndGotoCoordination(
  page: Page,
  testInfo: TestInfo,
): Promise<void> {
  // Mock the coordination endpoint BEFORE navigating so the first poll hits it.
  await page.route('**/api/v1/coordination', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(COORDINATION_PAYLOAD),
    });
  });

  const token = readUiToken(testInfo);
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/', { timeout: 10_000 });
  await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
  await page.goto('/coordination');
  await expect(page.locator('[data-testid="coordination-page"]')).toBeVisible({ timeout: 10_000 });
}

test.describe('coordination view', () => {
  test('renders lanes, a blocked lane, a contested claim, and a clickable handoff', async ({ page }, testInfo: TestInfo) => {
    await authAndGotoCoordination(page, testInfo);

    // ---- Section 1: who's working what ----
    const laneA = page.locator('[data-testid="coordination-lane-LANE-A"]');
    await expect(laneA).toBeVisible({ timeout: 8_000 });
    await expect(laneA).toContainText('T2 parser align');

    // LANE-B shows a BLOCKED badge.
    await expect(page.locator('[data-testid="coordination-lane-blocked-LANE-B"]')).toBeVisible();

    // LANE-C is idle.
    await expect(page.locator('[data-testid="coordination-lane-LANE-C"]')).toContainText('idle');

    // ---- Section 2: claims & contention ----
    const contested = page.locator('[data-testid="coordination-claim-T9-orphan"]');
    await expect(contested).toBeVisible();
    await expect(contested).toHaveAttribute('data-contested', 'true');
    await expect(page.locator('[data-testid="coordination-claim-contested-T9-orphan"]')).toBeVisible();

    // The non-contested claim is present and not flagged.
    const t2 = page.locator('[data-testid="coordination-claim-T2"]');
    await expect(t2).toBeVisible();
    await expect(t2).toHaveAttribute('data-contested', 'false');

    // ---- Section 3: recent signals (handoffs) ----
    const sigRow = page.locator('[data-testid="coordination-signal-row"]').first();
    await expect(sigRow).toBeVisible();
    await expect(sigRow).toContainText('handoff-parser');

    // Clicking opens the body drawer.
    await sigRow.click();
    const drawer = page.locator('[data-testid="coordination-drawer"]');
    await expect(drawer).toBeVisible({ timeout: 5_000 });
    await expect(page.locator('[data-testid="coordination-drawer-body"]')).toContainText('Parser is aligned');

    // Close it.
    await page.locator('[data-testid="coordination-drawer-close"]').click();
    await expect(drawer).not.toBeVisible({ timeout: 3_000 });
  });

  test('nav link reaches the coordination page', async ({ page }, testInfo: TestInfo) => {
    await page.route('**/api/v1/coordination', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(COORDINATION_PAYLOAD),
      });
    });
    const token = readUiToken(testInfo);
    await page.goto(`/#t=${token}`);
    await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });

    // The nav link exists and routes via the SPA router (no full reload).
    const navLink = page.locator('[data-testid="nav-coordination"]');
    await expect(navLink).toBeVisible();
    await navLink.click();
    await expect(page).toHaveURL(/\/coordination$/, { timeout: 5_000 });
    await expect(page.locator('[data-testid="coordination-page"]')).toBeVisible({ timeout: 8_000 });

    // The approval-rules nav link is now reachable too.
    await expect(page.locator('[data-testid="nav-approval-rules"]')).toBeVisible();
  });
});
