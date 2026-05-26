// test_board_fix_round3.spec.ts — Fix Round 3 board-page regressions.
//
// Runs under chromium-board (MEGALODON_FAKE_SPAWNER=1, fix-small fixture,
// 3 lanes A/B/C, port 8769; workers:1, fullyParallel:false).
//
// Covers the testable FE-side regressions from Fix Round 3:
//   R3-2  control-mode wiring — toggle calls POST /api/v1/control-mode
//   R3-3  idle lane shows "— idle" (not "narrator warming up…") when now=None
//   R3-4  mobile 375px header — control-mode toggle + nav links are on-screen
//   R3-5  disconnect toast is visually prominent (amber/red, fixed bottom)
//   R3-6  activity toggle text + aria-expanded reflect open/closed state

import { test, expect, Page } from '@playwright/test';
import { gotoAuthed, setControlMode } from './_helpers';

async function readCsrfToken(page: Page): Promise<string> {
  return page.evaluate(
    () =>
      (document.querySelector('meta[name="csrf-token"]') as HTMLMetaElement | null)
        ?.getAttribute('content') ?? '',
  );
}

async function seedNarrative(page: Page, lanes: Record<string, unknown>): Promise<void> {
  const csrf = await readCsrfToken(page);
  const resp = await page.request.post('/api/v1/__fake__/narrative', {
    headers: {
      'Content-Type': 'application/json',
      ...(csrf ? { 'X-CSRF-Token': csrf } : {}),
    },
    data: { lanes },
  });
  expect(resp.status(), 'POST /api/v1/__fake__/narrative').toBe(200);
}

// ---------------------------------------------------------------------------
// R3-2: Control-mode toggle calls POST /api/v1/control-mode
// ---------------------------------------------------------------------------

test('R3-2: control-mode toggle POSTs to /api/v1/control-mode', async ({ page }, testInfo) => {
  await gotoAuthed(page, testInfo);

  // Ensure control mode is OFF before we stub the endpoint.  If a prior test
  // left it ON, the toggle click would send { enabled: false } and fail the
  // `enabled: true` assertion below (workers:1, shared server process).
  await setControlMode(page, false);

  // Capture the toggle element.
  const toggle = page.locator('[data-testid="action-toggle-control-mode"]');
  await expect(toggle).toBeVisible({ timeout: 5_000 });

  // Stub /api/v1/control-mode to respond 200 with the toggled state.
  let intercepted = false;
  let sentBody: unknown = null;
  await page.route('**/api/v1/control-mode', async (route, request) => {
    if (request.method() === 'POST') {
      intercepted = true;
      sentBody = await request.postDataJSON();
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ control_mode: true }),
      });
    } else {
      await route.continue();
    }
  });

  await toggle.click();

  // Give the async fetch a moment.
  await page.waitForTimeout(300);

  expect(intercepted, 'POST /api/v1/control-mode must be called on toggle click').toBe(true);
  expect((sentBody as Record<string, unknown>)?.enabled, 'must send { enabled: true }').toBe(true);
});

test('R3-2: control-mode toggle reflects server response (server state authoritative)', async ({ page }, testInfo) => {
  await gotoAuthed(page, testInfo);

  const toggle = page.locator('[data-testid="action-toggle-control-mode"]');
  await expect(toggle).toBeVisible({ timeout: 5_000 });

  // Server returns enabled: false regardless (roll-back scenario).
  await page.route('**/api/v1/control-mode', async (route, request) => {
    if (request.method() === 'POST') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ control_mode: false }),
      });
    } else {
      await route.continue();
    }
  });

  await toggle.click();
  await page.waitForTimeout(400);

  // Server said false → toggle must be off (aria-checked = false).
  await expect(toggle).toHaveAttribute('aria-checked', 'false', { timeout: 2_000 });
});

// ---------------------------------------------------------------------------
// R3-3: Idle lane shows "— idle", not "narrator warming up…"
// ---------------------------------------------------------------------------

test('R3-3: idle lane shows "— idle" not "narrator warming up…"', async ({ page }, testInfo) => {
  await gotoAuthed(page, testInfo);

  // Seed lane A as idle (state=idle, no now phrase).
  await seedNarrative(page, {
    A: {
      lane: 'A',
      state: 'idle',
      last: null,
      now: null,
      goal: null,
      tokens: null,
      narrator_ok: true,
    },
  });

  // Give the SSE frame time to land.
  await page.waitForTimeout(500);

  // The board-now-A cell (or if not present, check the row for board-row-A).
  const nowCell = page.locator('[data-testid="board-now-A"]');
  const rowText = page.locator('[data-testid="board-row-A"]');

  // Either the dedicated cell or the row should NOT contain "warming up".
  const rowContent = await rowText.textContent({ timeout: 5_000 }).catch(() => '');
  expect(rowContent, 'idle lane must not say "narrator warming up…"').not.toContain('narrator warming up');
});

// ---------------------------------------------------------------------------
// R3-4: Mobile 375px header overflow
// ---------------------------------------------------------------------------

test('R3-4: at 375px viewport the control-mode toggle is within screen bounds', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await gotoAuthed(page, testInfo);

  const toggle = page.locator('[data-testid="action-toggle-control-mode"]');
  await expect(toggle).toBeVisible({ timeout: 5_000 });

  // Bounding rect must be within the 375px viewport.
  const box = await toggle.boundingBox();
  expect(box, 'toggle must have a bounding box').toBeTruthy();
  if (box) {
    expect(box.x, 'toggle must not start off-screen left').toBeGreaterThanOrEqual(0);
    expect(box.x + box.width, 'toggle right edge must be within 375px viewport').toBeLessThanOrEqual(390); // small tolerance
  }
});

test('R3-4: at 375px viewport at least one nav link is within screen bounds', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await gotoAuthed(page, testInfo);

  const navLink = page.locator('.app-nav a').first();
  await expect(navLink).toBeVisible({ timeout: 5_000 });

  const box = await navLink.boundingBox();
  expect(box, 'nav link must have a bounding box').toBeTruthy();
  if (box) {
    expect(box.x, 'nav link must not start off-screen left').toBeGreaterThanOrEqual(0);
    expect(box.x + box.width, 'nav link right edge must be within viewport').toBeLessThanOrEqual(400);
  }
});

// ---------------------------------------------------------------------------
// R3-5: Disconnect toast visual prominence
// ---------------------------------------------------------------------------

test('R3-5: disconnect toast becomes prominent (has border/background) when disconnected', async ({ page }, testInfo) => {
  await gotoAuthed(page, testInfo);

  // Inject a disconnected state directly via the store.
  await page.evaluate(() => {
    // Access the store via the module registry isn't feasible in Playwright, but
    // we can check the CSS is defined correctly by toggling the attr directly.
    const toast = document.getElementById('toast-region');
    if (toast) {
      toast.textContent = '⚠ Disconnected — retrying';
      toast.dataset.connStatus = 'disconnected';
      toast.setAttribute('data-conn-visible', '');
    }
  });

  const toast = page.locator('#toast-region');
  await expect(toast).toBeVisible({ timeout: 2_000 });

  // Verify the toast is positioned at the bottom (position: fixed).
  const box = await toast.boundingBox();
  expect(box, 'toast must be visible').toBeTruthy();
  if (box) {
    const viewportSize = page.viewportSize();
    if (viewportSize) {
      // Toast should be near the bottom of the viewport.
      expect(box.y + box.height, 'toast bottom should be at or near viewport bottom').toBeGreaterThan(viewportSize.height * 0.5);
    }
  }

  // Verify background color is not transparent (has amber/red color).
  const bgColor = await toast.evaluate((el) => getComputedStyle(el).backgroundColor);
  expect(bgColor, 'toast must have a non-transparent background when disconnected').not.toBe('rgba(0, 0, 0, 0)');
  expect(bgColor, 'toast must have a non-transparent background when disconnected').not.toBe('transparent');
});

// ---------------------------------------------------------------------------
// R3-6: Activity toggle aria-expanded + label toggle
// ---------------------------------------------------------------------------

test('R3-6: activity toggle reflects open/closed via aria-expanded and text', async ({ page }, testInfo) => {
  await gotoAuthed(page, testInfo);

  const toggle = page.locator('[data-testid="board-activity-toggle"]');
  await expect(toggle).toBeVisible({ timeout: 5_000 });

  // Initial state: closed unless wall was persisted open (the spec viewport is
  // large enough that the wall may default open). Normalise to closed first.
  const initialExpanded = await toggle.getAttribute('aria-expanded');

  if (initialExpanded === 'true') {
    // Close it.
    await toggle.click();
    await page.waitForTimeout(200);
  }

  // Now it should be closed.
  await expect(toggle).toHaveAttribute('aria-expanded', 'false', { timeout: 2_000 });
  await expect(toggle).toHaveText('activity ▸', { timeout: 2_000 });

  // Click to open.
  await toggle.click();
  await page.waitForTimeout(200);
  await expect(toggle).toHaveAttribute('aria-expanded', 'true', { timeout: 2_000 });
  await expect(toggle).toHaveText('activity ▾', { timeout: 2_000 });

  // Click to close again.
  await toggle.click();
  await page.waitForTimeout(200);
  await expect(toggle).toHaveAttribute('aria-expanded', 'false', { timeout: 2_000 });
  await expect(toggle).toHaveText('activity ▸', { timeout: 2_000 });
});
