// E2E tests for the STATUS view.
// Test IDs from findings/agent-9265-E-P1-test-plan-2026-05-16T15-33Z.md §4
// and P2.5-E §"Updated test inventory".

import { test, expect } from '@playwright/test';
import { gotoAuthed } from './_helpers';

test.describe('STATUS view (T-V-STATUS-e2e)', () => {

  test('renders one row per lane from fix-medium fixture', async ({ page }, testInfo) => {
    // v9.4 Task 3.5a: `/` now renders board.js (summary board) — one
    // [data-testid^="board-row-"] row per lane. fix-medium has 6 lanes.
    // The board's data fetches are session-gated (deny-by-default), so
    // authenticate first; an unauthenticated load renders an empty board.
    await gotoAuthed(page, testInfo);
    const rows = page.locator('[data-testid^="board-row-"]');
    await expect(rows).toHaveCount(6);
  });

  // Removed 2026-05-24 (board migration): the `stale row styling` and `Last UTC
  // live-update` tests asserted `lane-row-*` / `last-utc` testids from the
  // pre-v9.4 dashboard, which the board does not render. Staleness is now a
  // board pill (covered by test_board_stale.spec.ts); there is no per-lane
  // last-utc affordance. Not re-added — those features were intentionally
  // dropped in the v9.4 dashboard rebuild.

});

test.describe('TASKS view (T-V-TASKS-e2e)', () => {

  test('renders bracket states correctly for fix-medium', async ({ page }, testInfo) => {
    // /tasks fetches /api/v1/tasks which is session-gated; authenticate first.
    await gotoAuthed(page, testInfo, '/tasks');
    await expect(page.locator('[data-testid^="task-card-"]')).not.toHaveCount(0);
  });

});

// FINDINGS view filter tests removed 2026-05-24: the v9.4 findings rewrite
// (`findings.js`) is a flat list + click-to-open drawer with no severity/scratch
// filter chips. `filter-severity-*`, `filter-scratch`, and `data-scratch` exist
// nowhere in ui/static. The tests asserted removed UI; deleted rather than
// re-introduce a dropped feature. (finding-row rendering is exercised elsewhere.)

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
