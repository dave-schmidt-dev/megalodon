// test_board_phase_strip.spec.ts — Wave 4 P2 cleanup.
//
// Runs under chromium-board / webkit-board (fix-small fixture).
//
// The phase strip in index.html ships with the standard 10-phase set as a
// no-JS first-paint baseline. app.js reconcilePhaseStrip() now makes it reflect
// the mission's REAL config.phases once /api/v1/config resolves (this logic used
// to live in the deleted dashboard.js). The fix-small fixture declares a custom
// 3-phase set, so we can assert the live reconciliation here (unlike the
// skip-guarded test_fe_phase_navigator_custom_config.spec.ts, which needs a
// separately-booted fixture).
//
// fix-small .mission-config.yaml phases:
//   PHASE-PLAN, PHASE-EXEC, OPERATOR-ACCEPTANCE

import { test, expect } from '@playwright/test';
import { gotoAuthed } from './_helpers';

const CONFIG_PHASES = ['PHASE-PLAN', 'PHASE-EXEC', 'OPERATOR-ACCEPTANCE'];

// reconcilePhaseStrip() runs only after /api/v1/config resolves; that endpoint
// is session-gated (deny-by-default), so each test authenticates first via
// gotoAuthed (which lands on the board /) — without the cookie config 401s and
// the strip never reconciles to the fixture's custom 3-phase set.

test.describe('phase strip reconciliation against config.phases', () => {
  test('shows exactly the config phases, in order, after config resolves', async ({ page }, testInfo) => {
    await gotoAuthed(page, testInfo);

    const strip = page.locator('ol.phase-strip');
    await expect(strip).toBeVisible({ timeout: 10_000 });

    // Visible segments (display !== none) must equal config.phases, in order.
    const visible = strip.locator('li[data-testid^="phase-segment-"]:not([style*="display: none"])');
    await expect(visible).toHaveCount(CONFIG_PHASES.length, { timeout: 10_000 });

    const names = await visible.evaluateAll((els) =>
      els.map((el) => (el as HTMLElement).dataset.testid?.replace('phase-segment-', '') ?? ''),
    );
    expect(names).toEqual(CONFIG_PHASES);
  });

  test('a standard phase absent from config stays in the DOM but hidden', async ({ page }, testInfo) => {
    await gotoAuthed(page, testInfo);
    await expect(page.locator('ol.phase-strip')).toBeVisible({ timeout: 10_000 });

    // Reconciliation runs after loadConfig resolves; wait for the visible set to
    // settle to the 3 config phases before asserting the hidden ones.
    const visible = page.locator(
      'ol.phase-strip li[data-testid^="phase-segment-"]:not([style*="display: none"])',
    );
    await expect(visible).toHaveCount(CONFIG_PHASES.length, { timeout: 10_000 });

    // INIT is a standard phase not in fix-small's config → present but hidden.
    const init = page.locator('[data-testid="phase-segment-INIT"]');
    await expect(init).toHaveCount(1);
    const display = await init.evaluate((el) => (el as HTMLElement).style.display);
    expect(display).toBe('none');

    // PHASE-OPERATOR-ACCEPTANCE (the standard long form) is distinct from the
    // config's OPERATOR-ACCEPTANCE token → the standard one is hidden.
    const stdOpAck = page.locator('[data-testid="phase-segment-PHASE-OPERATOR-ACCEPTANCE"]');
    await expect(stdOpAck).toHaveCount(1);
    const opDisplay = await stdOpAck.evaluate((el) => (el as HTMLElement).style.display);
    expect(opDisplay).toBe('none');
  });

  test('a config phase with no static <li> is created (PHASE-EXEC)', async ({ page }, testInfo) => {
    await gotoAuthed(page, testInfo);
    await expect(page.locator('ol.phase-strip')).toBeVisible({ timeout: 10_000 });

    // PHASE-EXEC is not part of the standard static strip — it must be created
    // and visible after reconciliation.
    const exec = page.locator('[data-testid="phase-segment-PHASE-EXEC"]');
    await expect(exec).toBeVisible({ timeout: 10_000 });
    await expect(exec).toHaveText('EXEC');
  });
});
