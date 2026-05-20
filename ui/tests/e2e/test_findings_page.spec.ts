// test_findings_page.spec.ts — v9.4 T3.6: Findings page E2E.
//
// Runs under chromium-grid project (fix-small fixture: 2 findings).
// Fixture findings:
//   agent-aaaa-A-T1.md  (lane LANE-A, severity MINOR)
//   agent-cccc-C-T3.md  (lane LANE-C, severity DELTA)
//
// Test cases:
//   1. Structured rendering — rows have data-finding-lane/agent/phase attributes.
//   2. Drawer — click row → drawer visible; body text has content; ESC closes.
//   3. Empty state — skipped when fixture has findings (it has 2).

import { test, expect } from '@playwright/test';

test.describe('findings page', () => {

  test('navigates to /findings and renders rows with structured attributes', async ({ page }) => {
    await page.goto('/findings');

    // Page container must appear.
    await expect(page.locator('[data-testid="findings-page"]')).toBeVisible({ timeout: 10_000 });

    // At least one row must be present with the required data attributes.
    const rows = page.locator('[data-finding-lane]');
    await expect(rows.first()).toBeVisible({ timeout: 8_000 });

    const count = await rows.count();
    expect(count).toBeGreaterThanOrEqual(1);

    // Verify the three required attributes exist and are non-empty on every row.
    for (let i = 0; i < count; i++) {
      const row = rows.nth(i);
      const lane = await row.getAttribute('data-finding-lane');
      const agent = await row.getAttribute('data-finding-agent');
      const phase = await row.getAttribute('data-finding-phase');

      expect(lane).toBeTruthy();
      expect(agent).toBeTruthy();
      // phase may be "—" for short filenames without a phase segment.
      expect(phase).not.toBeNull();
    }

    // Spot-check: the AUDIT (lane A) finding row.
    const laneARow = page.locator('[data-finding-agent="agent-aaaa"]');
    await expect(laneARow).toBeVisible({ timeout: 5_000 });
    const laneVal = await laneARow.getAttribute('data-finding-lane');
    // The parser may return "A" (short) or "AUDIT" (name) depending on server lane field.
    expect(laneVal).toBeTruthy();
  });

  test('clicking a row opens the drawer; ESC closes it', async ({ page }) => {
    await page.goto('/findings');

    await expect(page.locator('[data-testid="findings-page"]')).toBeVisible({ timeout: 10_000 });

    // Wait for rows to appear.
    const rows = page.locator('[data-finding-lane]');
    await expect(rows.first()).toBeVisible({ timeout: 8_000 });

    // Click the first row.
    await rows.first().click();

    // Drawer must become visible.
    const drawer = page.locator('[data-finding-drawer]');
    await expect(drawer).toBeVisible({ timeout: 5_000 });

    // The drawer body should contain some text (at minimum "Loading…" or actual content).
    const drawerBody = page.locator('[data-testid="finding-drawer-body"]');
    await expect(drawerBody).toBeVisible({ timeout: 5_000 });

    // Wait for actual content to load (body is async-fetched; "Loading…" is transient).
    await expect(drawerBody).not.toHaveText('Loading…', { timeout: 8_000 });
    const bodyText = await drawerBody.textContent();
    expect(bodyText).toBeTruthy();
    // The fixture findings all contain "Smoke-test finding" in their body.
    expect(bodyText).toContain('Smoke-test finding');

    // Press ESC → drawer closes.
    await page.keyboard.press('Escape');
    await expect(drawer).toBeHidden({ timeout: 3_000 });
  });

  test('close button dismisses the drawer', async ({ page }) => {
    await page.goto('/findings');

    await expect(page.locator('[data-testid="findings-page"]')).toBeVisible({ timeout: 10_000 });
    const rows = page.locator('[data-finding-lane]');
    await expect(rows.first()).toBeVisible({ timeout: 8_000 });

    // Open drawer.
    await rows.first().click();
    const drawer = page.locator('[data-finding-drawer]');
    await expect(drawer).toBeVisible({ timeout: 5_000 });

    // Click the × close button.
    await page.locator('[data-testid="finding-drawer-close"]').click();
    await expect(drawer).toBeHidden({ timeout: 3_000 });
  });

  // Empty-state: the fix-small fixture has 2 findings, so we verify the
  // empty-state element does NOT appear (it would only show if findings=0).
  test('empty state is not shown when findings exist', async ({ page }) => {
    await page.goto('/findings');

    await expect(page.locator('[data-testid="findings-page"]')).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('[data-finding-lane]').first()).toBeVisible({ timeout: 8_000 });

    // Empty state must not be present.
    await expect(page.locator('[data-testid="findings-empty"]')).not.toBeVisible();
  });

});
