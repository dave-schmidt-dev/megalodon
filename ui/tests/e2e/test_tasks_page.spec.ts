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

test.describe('Tasks page — kanban by phase (T3.9)', () => {

  test.beforeEach(async ({ page }) => {
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
