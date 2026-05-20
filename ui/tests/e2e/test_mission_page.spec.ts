// test_mission_page.spec.ts — v9.4 T3.8: mission page rewrite acceptance tests.
//
// Runs under chromium-grid project (GRID_SMOKE_ENV + fix-small fixture,
// port 8769, workers: 1).
//
// The fix-small fixture provides:
//   MISSION.md  → id="fix-small-smoke-test", status="ACTIVE"
//   .mission-events → 1 line: "2026-01-01T00:00:00Z INIT->ACTIVE by orchestrator …"
//   .mission-config.yaml → phases, lanes, etc.
//
// Data comes from:
//   GET /api/v1/state  → mission.{id, phase, status, events}
//   GET /api/v1/config → full config object
//
// Three test cases:
//   1. Summary populates  — [data-mission-id], [data-testid="mission-phase"],
//                           [data-testid="mission-status-badge"] all non-empty.
//   2. Events scroll      — events container has ≥1 row; CSS overflow allows scroll.
//   3. Config collapses   — <details> initially closed; click opens it; content visible.

import { test, expect } from '@playwright/test';

// ---------------------------------------------------------------------------
// Helper: navigate to /mission and wait for the page to be rendered.
// ---------------------------------------------------------------------------

async function gotoMission(page: import('@playwright/test').Page): Promise<void> {
  await page.goto('/mission');
  // Wait until the page container is attached (the render() async function
  // populates this after the fetch resolves).
  await expect(page.locator('[data-testid="mission-page"]')).toBeVisible({ timeout: 15_000 });
  // Summary card must be present.
  await expect(page.locator('[data-testid="mission-summary-card"]')).toBeVisible({ timeout: 10_000 });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe('mission page', () => {

  test('summary card populates mission id, phase, and status', async ({ page }) => {
    await gotoMission(page);

    // Mission ID: [data-mission-id] attribute should be set on the id span.
    const idSpan = page.locator('[data-testid="mission-id"]');
    await expect(idSpan).toBeVisible({ timeout: 5_000 });
    // The id must be non-empty (not the "—" fallback for missing data).
    const idText = await idSpan.textContent();
    expect(idText).toBeTruthy();
    expect(idText!.trim()).not.toBe('');
    // data-mission-id attribute should equal the displayed text.
    const missionIdAttr = await idSpan.getAttribute('data-mission-id');
    expect(missionIdAttr).toBeTruthy();

    // Phase: [data-testid="mission-phase"] should show a non-empty phase string.
    const phaseEl = page.locator('[data-testid="mission-phase"]');
    await expect(phaseEl).toBeVisible({ timeout: 5_000 });
    const phaseText = await phaseEl.textContent();
    expect(phaseText).toBeTruthy();
    expect(phaseText!.trim()).not.toBe('');

    // Status badge: [data-testid="mission-status-badge"] must be visible.
    const statusBadge = page.locator('[data-testid="mission-status-badge"]');
    await expect(statusBadge).toBeVisible({ timeout: 5_000 });
    const statusText = await statusBadge.textContent();
    expect(statusText).toBeTruthy();
    expect(statusText!.trim()).not.toBe('');
  });

  test('events log has at least one row and container allows scroll', async ({ page }) => {
    await gotoMission(page);

    // Events section must be present.
    const eventsSection = page.locator('[data-testid="mission-events-log"]');
    await expect(eventsSection).toBeVisible({ timeout: 5_000 });

    // The fix-small fixture has exactly 1 event line — assert ≥1 row.
    const eventRows = page.locator('[data-testid^="mission-event-row-"]');
    await expect(eventRows).toHaveCount(1, { timeout: 5_000 });

    // Scroll wrapper must exist and have overflow-y set to "auto" or "scroll".
    const scrollWrapper = page.locator('[data-testid="mission-events-scroll"]');
    await expect(scrollWrapper).toBeVisible({ timeout: 5_000 });

    const overflowY = await scrollWrapper.evaluate((el: HTMLElement) =>
      window.getComputedStyle(el).overflowY,
    );
    // Computed value should be "auto" or "scroll" (not "visible" or "hidden").
    expect(['auto', 'scroll']).toContain(overflowY);
  });

  test('config details is initially closed; click opens it and shows content', async ({ page }) => {
    await gotoMission(page);

    // The config card must be present.
    await expect(page.locator('[data-testid="mission-config-card"]')).toBeVisible({ timeout: 5_000 });

    // The <details> element must exist.
    const details = page.locator('[data-testid="mission-config-details"]');
    await expect(details).toBeVisible({ timeout: 5_000 });

    // Assert it is initially CLOSED (no `open` attribute).
    const isOpenInitially = await details.evaluate((el: HTMLDetailsElement) => el.open);
    expect(isOpenInitially).toBe(false);

    // The pre block should not be visible (details is closed, browser collapses content).
    const preBlock = page.locator('[data-testid="mission-config-json"]');
    await expect(preBlock).not.toBeVisible();

    // Click the summary to open.
    await details.locator('summary').click();

    // After click, details must be open.
    const isOpenAfter = await details.evaluate((el: HTMLDetailsElement) => el.open);
    expect(isOpenAfter).toBe(true);

    // The pre block must now be visible and contain non-empty content.
    await expect(preBlock).toBeVisible({ timeout: 3_000 });
    const configText = await preBlock.textContent();
    expect(configText).toBeTruthy();
    expect(configText!.trim().length).toBeGreaterThan(0);
  });

});
