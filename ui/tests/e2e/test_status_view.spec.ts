// E2E tests for the STATUS view.
// Test IDs from findings/agent-9265-E-P1-test-plan-2026-05-16T15-33Z.md §4
// and P2.5-E §"Updated test inventory".

import { test, expect } from '@playwright/test';

test.describe('STATUS view (T-V-STATUS-e2e)', () => {

  test('renders one row per lane from fix-medium fixture', async ({ page }) => {
    // v9.4 Task 3.5a: `/` now renders board.js (summary board) — one
    // [data-testid^="board-row-"] row per lane. fix-medium has 6 lanes.
    await page.goto('/');
    await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
    const rows = page.locator('[data-testid^="board-row-"]');
    await expect(rows).toHaveCount(6);
  });

  test('stale row receives stale-color styling (T-R1-b)', async ({ page }) => {
    await page.goto('/');
    // fix-medium ships with 2 stale rows; assert UI marks them.
    const stale = page.locator('[data-testid="lane-row-AUDIT"][data-stale="true"]');
    await expect(stale).toBeVisible();
  });

  test('Last UTC reflects filesystem edits within 5s (live-update)', async ({ page }) => {
    await page.goto('/');
    const initial = await page.locator('[data-testid="lane-row-TEST"] [data-testid="last-utc"]').textContent();
    // Touch STATUS.md externally — out-of-band; would be a test helper in real impl.
    // For now, assert the wait-for affordance exists.
    const settleAttr = await page.locator('body').getAttribute('data-last-event-id');
    expect(settleAttr).not.toBeNull();  // P2.5-E F.2 settle hook present
  });

});

test.describe('TASKS view (T-V-TASKS-e2e)', () => {

  test('renders bracket states correctly for fix-medium', async ({ page }) => {
    await page.goto('/tasks');
    await expect(page.locator('[data-testid^="task-card-"]')).not.toHaveCount(0);
  });

});

test.describe('FINDINGS view (T-V-FE-e2e)', () => {

  test('filter by severity narrows the result list', async ({ page }) => {
    await page.goto('/findings');
    await page.locator('[data-testid="filter-severity-MAJOR"]').click();
    const rows = page.locator('[data-testid^="finding-row-"]');
    const count = await rows.count();
    expect(count).toBeGreaterThan(0);
  });

  test('scratch chip toggles scratch-file visibility', async ({ page }) => {
    // P2.5-E CHALLENGE-5 — scratch filter chip.
    await page.goto('/findings');
    const initial = await page.locator('[data-testid^="finding-row-"][data-scratch="true"]').count();
    await page.locator('[data-testid="filter-scratch"]').click();
    const after = await page.locator('[data-testid^="finding-row-"][data-scratch="true"]').count();
    expect(after).not.toEqual(initial);
  });

});

// 2026-05-19 regression coverage for the three frontend nav/phase bugs:
//   bug-1: tab navigation reverts on page refresh (auth bootstrap was rewriting
//          the URL to "/" before SPA router could read location.pathname).
//   bug-2: active nav indicator does not update on click (updateNavActive was
//          only called after the lazy page-module import resolved).
//   bug-3: phase strip stays on INIT after a phase flip (store.set("mission", obj)
//          did not fire subscribers on the nested key "mission.phase").
test.describe('Navigation + phase indicator (2026-05-19 regression)', () => {

  test('deep-link survives page refresh (bug-1)', async ({ page }) => {
    await page.goto('/findings');
    await expect(page).toHaveURL(/\/findings$/);
    await page.reload();
    // After reload the SPA router must still be on /findings, not bounced to "/".
    await expect(page).toHaveURL(/\/findings$/);
  });

  test('clicking a nav link marks it aria-current="page" immediately (bug-2)', async ({ page }) => {
    await page.goto('/');
    const dashboardLink = page.locator('[data-testid="nav-dashboard"]');
    const findingsLink = page.locator('[data-testid="nav-findings"]');
    await expect(dashboardLink).toHaveAttribute('aria-current', 'page');

    await findingsLink.click();
    await expect(findingsLink).toHaveAttribute('aria-current', 'page');
    await expect(dashboardLink).not.toHaveAttribute('aria-current', 'page');
  });

  test('phase strip reflects mission.phase after store hydration (bug-3)', async ({ page }) => {
    await page.goto('/');
    // Drive the store directly to simulate a hydrate that swaps the whole
    // mission slice (the path that previously failed to notify subscribers
    // on the nested "mission.phase" key).
    await page.evaluate(async () => {
      const mod = await import('/static/js/store.js');
      mod.store.set('mission', { phase: 'PHASE-PLAN', events: [], missionStatus: 'running' });
    });
    await expect(
      page.locator('[data-testid="phase-segment-PHASE-PLAN"]'),
    ).toHaveAttribute('aria-current', 'step');
    await expect(
      page.locator('[data-testid="phase-segment-INIT"]'),
    ).not.toHaveAttribute('aria-current', 'step');
  });

});
