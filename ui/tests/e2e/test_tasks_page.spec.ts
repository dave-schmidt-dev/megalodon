// E2E tests for the /tasks kanban page.
// T3.9: kanban-by-phase rewrite.
//
// Runs under chromium-grid (fix-small fixture: 2 phases — PHASE-PLAN,
// PHASE-EXEC — with tasks across LANE-A, LANE-B, LANE-C).
//
// Cases:
//   1. Kanban renders one column per phase ([data-phase-name] present).
//   2. Card click opens detail drawer; ESC closes it.
//   3. Lane filter chip hides cards from other lanes.

import { test, expect } from '@playwright/test';
import { readUiToken } from './_helpers';

// Authenticate via the hash-token exchange so the now-gated /api/v1/{tasks,state}
// reads succeed (the server requires the mui_session cookie on /api/**).
async function authenticate(page: import('@playwright/test').Page, testInfo: import('@playwright/test').TestInfo) {
  const token = readUiToken(testInfo);
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/', { timeout: 10_000 });
  await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
}

test.describe('Tasks page — kanban by phase (T3.9)', () => {

  test.beforeEach(async ({ page }, testInfo) => {
    await authenticate(page, testInfo);
    await page.goto('/tasks');
    // Wait for the kanban to render (at least one phase column).
    await expect(page.locator('[data-phase-name]').first()).toBeVisible({ timeout: 10_000 });
  });

  // ------------------------------------------------------------------ case 1
  test('kanban renders one column per phase', async ({ page }) => {
    // fix-small fixture has PHASE-PLAN and PHASE-EXEC.
    const planCol = page.locator('[data-phase-name="PHASE-PLAN"]');
    const execCol = page.locator('[data-phase-name="PHASE-EXEC"]');

    await expect(planCol).toBeVisible();
    await expect(execCol).toBeVisible();

    // Each column must contain at least one task card.
    await expect(planCol.locator('[data-testid^="task-card-"]').first()).toBeVisible();
    await expect(execCol.locator('[data-testid^="task-card-"]').first()).toBeVisible();
  });

  // ------------------------------------------- case 1b: root testid parity
  test('tasks page exposes a tasks-page root testid (instrumentation parity)', async ({ page }) => {
    // Matches the *-page convention of board/findings/etc. so harnesses can
    // assert "on the tasks page" the same way they assert "on the board".
    await expect(page.locator('[data-testid="tasks-page"]')).toBeVisible({ timeout: 10_000 });
  });

  // ------------------------------------------------------------------ case 2
  test('card click opens detail drawer; ESC closes it', async ({ page }) => {
    // Click the first visible task card.
    const firstCard = page.locator('[data-testid^="task-card-"]').first();
    const cardId = await firstCard.getAttribute('data-testid');
    // cardId is like "task-card-T1"
    const taskId = cardId?.replace('task-card-', '') ?? '';

    await firstCard.click();

    // Drawer overlay should appear.
    const overlay = page.locator('[data-testid="task-drawer-overlay"]');
    await expect(overlay).toBeVisible({ timeout: 5_000 });

    // Drawer panel should contain the task id.
    const panel = page.locator(`[data-testid="task-drawer-${taskId}"]`);
    await expect(panel).toBeVisible();
    await expect(panel).toContainText(taskId);

    // ESC closes the drawer.
    await page.keyboard.press('Escape');
    await expect(overlay).not.toBeVisible({ timeout: 3_000 });
  });

  // ------------------------------------------------------------------ case 4
  test('column header shows done/total per phase (audit I4)', async ({ page }) => {
    // fix-small TASKS.md: PHASE-PLAN = T1 done + T2 open (1/2);
    //                     PHASE-EXEC = T3 claimed + T4 done (1/2).
    const planProgress = page.locator('[data-testid="phase-progress-phase-plan"]');
    const execProgress = page.locator('[data-testid="phase-progress-phase-exec"]');

    await expect(planProgress).toBeVisible();
    await expect(planProgress).toHaveText('1/2');
    await expect(execProgress).toBeVisible();
    await expect(execProgress).toHaveText('1/2');

    // The done/total is also exposed as data attributes on the column.
    const planCol = page.locator('[data-phase-name="PHASE-PLAN"]');
    await expect(planCol).toHaveAttribute('data-phase-done', '1');
    await expect(planCol).toHaveAttribute('data-phase-total', '2');
  });

  // ------------------------------------------------------------------ case 5
  test('current-phase column is highlighted from mission.phase (audit I4)', async ({ page }) => {
    // Force the mission's current phase to PHASE-EXEC so the highlight is
    // deterministic (the fixture's events report "ACTIVE", which matches no
    // column). Patch /api/v1/state's mission.phase, leaving the rest intact.
    await page.route('**/api/v1/state', async (route) => {
      const resp = await route.fetch();
      const json = await resp.json();
      json.mission = { ...(json.mission || {}), phase: 'PHASE-EXEC' };
      await route.fulfill({ response: resp, json });
    });

    await page.goto('/tasks');
    await expect(page.locator('[data-phase-name]').first()).toBeVisible({ timeout: 10_000 });

    const execCol = page.locator('[data-phase-name="PHASE-EXEC"]');
    const planCol = page.locator('[data-phase-name="PHASE-PLAN"]');

    // PHASE-EXEC is the current phase → marked; PHASE-PLAN is not.
    await expect(execCol).toHaveAttribute('data-current-phase', 'true');
    await expect(planCol).toHaveAttribute('data-current-phase', 'false');

    // The current-phase indicator dot renders only on the matching column.
    await expect(page.locator('[data-testid="phase-current-indicator-phase-exec"]')).toBeVisible();
    await expect(page.locator('[data-testid="phase-current-indicator-phase-plan"]')).toHaveCount(0);
  });

  // ------------------------------------------------------------------ case 3
  test('lane filter chip shows only cards matching that lane', async ({ page }) => {
    // Identify which lanes are present in the fixture (LANE-A, LANE-B, LANE-C).
    // Count total cards before filtering.
    const allCards = page.locator('[data-testid^="task-card-"]');
    const totalCount = await allCards.count();
    expect(totalCount).toBeGreaterThan(1);

    // Click the LANE-A filter chip.
    const laneAChip = page.locator('[data-testid="lane-filter-lane-a"]');
    await expect(laneAChip).toBeVisible();
    await laneAChip.click();

    // After filter: only LANE-A cards should be visible; others hidden.
    const laneACards = page.locator('[data-testid^="task-card-"][data-lane="LANE-A"]');
    const otherCards = page.locator('[data-testid^="task-card-"]:not([data-lane="LANE-A"])');

    await expect(laneACards.first()).toBeVisible({ timeout: 3_000 });

    // Cards from other lanes must not be visible.
    const otherCount = await otherCards.count();
    for (let i = 0; i < otherCount; i++) {
      await expect(otherCards.nth(i)).not.toBeVisible();
    }
  });

});
