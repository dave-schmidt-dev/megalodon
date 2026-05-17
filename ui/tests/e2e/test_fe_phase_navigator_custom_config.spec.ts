// E2E test: Phase navigator reconciliation with custom config.phases (P3.3).
//
// Fixture: scripts/tests/fixtures/configs/minimal_custom_phases/.mission-config.yaml
//   Defines phases: DRAFT, REVIEW, PUBLISH (3 custom phases, no standard 10).
//
// This test verifies P3.3 milestone (OW-4 + CR-10): after loadConfig() resolves,
// dashboard.js reconciles the <ol class="phase-strip"> against config.phases:
//   - Custom phases (DRAFT, REVIEW, PUBLISH) are visible in order.
//   - Default phases absent from config (e.g. INIT) are present in the DOM but
//     hidden via style.display === 'none'.
//
// SKIP marker: this test requires booting the server against the
// minimal_custom_phases fixture (not the standard fix-medium fixture used by
// the default project). The orchestrator validates this test by running:
//   MEGALODON_MISSION_DIR=<path>/scripts/tests/fixtures/configs/minimal_custom_phases \
//   npx playwright test test_fe_phase_navigator_custom_config.spec.ts
//
// When run in the standard CI project (chromium-default), the test is skipped.

import { test, expect } from '@playwright/test';

const CUSTOM_PHASES = ['DRAFT', 'REVIEW', 'PUBLISH'];

async function isCustomPhasesFixture(page: import('@playwright/test').Page): Promise<boolean> {
  try {
    const resp = await page.request.get('/api/v1/config');
    if (!resp.ok()) return false;
    const data = await resp.json();
    const phases: (string | { name: string })[] = data.phases || [];
    const names = phases.map((p: string | { name: string }) =>
      typeof p === 'string' ? p : p.name,
    );
    return (
      names.length === 3 &&
      names.includes('DRAFT') &&
      names.includes('REVIEW') &&
      names.includes('PUBLISH')
    );
  } catch {
    return false;
  }
}

test.describe('Phase navigator hybrid reconciliation (P3.3 milestone)', () => {
  test(
    'shows exactly 3 custom phase segments: DRAFT, REVIEW, PUBLISH in order',
    async ({ page }) => {
      await page.goto('/');

      const isCustom = await isCustomPhasesFixture(page);
      test.skip(!isCustom, 'Requires server booted against minimal_custom_phases fixture — orchestrator validates');

      // Wait for the dashboard to load (skeleton resolves after loadConfig).
      await expect(page.locator('[data-testid^="lane-row-"]')).toHaveCount(1, { timeout: 10_000 });

      // Custom phases appended or shown by reconcilePhaseNavigator.
      const draft = page.locator('[data-testid="phase-segment-DRAFT"]');
      const review = page.locator('[data-testid="phase-segment-REVIEW"]');
      const publish = page.locator('[data-testid="phase-segment-PUBLISH"]');

      await expect(draft).toBeVisible();
      await expect(review).toBeVisible();
      await expect(publish).toBeVisible();

      // Assert ordering: DRAFT before REVIEW before PUBLISH in the DOM.
      const strip = page.locator('ol.phase-strip');
      const visibleSegments = strip.locator('li[data-testid^="phase-segment-"]:not([style*="display: none"])');
      const texts = await visibleSegments.evaluateAll((els) =>
        els.map((el) => (el as HTMLElement).dataset.testid?.replace('phase-segment-', '') ?? ''),
      );
      expect(texts).toEqual(CUSTOM_PHASES);
    },
  );

  test(
    'default phases absent from config are in the DOM but hidden',
    async ({ page }) => {
      await page.goto('/');

      const isCustom = await isCustomPhasesFixture(page);
      test.skip(!isCustom, 'Requires server booted against minimal_custom_phases fixture — orchestrator validates');

      // Wait for the dashboard to load.
      await expect(page.locator('[data-testid^="lane-row-"]')).toHaveCount(1, { timeout: 10_000 });

      // INIT is a default phase not in config.phases → must be in DOM but hidden.
      const initSegment = page.locator('[data-testid="phase-segment-INIT"]');
      await expect(initSegment).toHaveCount(1);
      // Verify hidden via inline style (set by reconcilePhaseNavigator).
      const display = await initSegment.evaluate((el) => (el as HTMLElement).style.display);
      expect(display).toBe('none');

      // Same for another default phase not in config.
      const planSegment = page.locator('[data-testid="phase-segment-PHASE-PLAN"]');
      await expect(planSegment).toHaveCount(1);
      const planDisplay = await planSegment.evaluate((el) => (el as HTMLElement).style.display);
      expect(planDisplay).toBe('none');
    },
  );
});
