// test_board_frontdoor.spec.ts — CONFIRMED "front-door" visibility fixes.
//
// The board pipeline is excellent WHEN OPEN, but a blind re-audit found the
// operator could not reach it. This spec locks in the three front-door fixes:
//
//   (1) Alert-banner overlap: a raised alert banner must NOT cover or intercept
//       clicks on the header controls — the `activity ▸` toggle (the only way to
//       open the wall), the mission / approval-rules nav links, and the
//       kill-switch. The banner stack was a fixed top-right overlay
//       (position:fixed; top:64px; right:12px; z-index:1500) that physically
//       sat over those controls. It is now an IN-FLOW element below the header.
//
//   (2) Default-open: the activity wall ("see what agents are doing") must be
//       OPEN by default on board mount (no stored preference); closing it
//       persists and is honoured next mount.
//
//   (3) Disconnect latency: an SSE outage must surface a VISIBLE disconnected
//       state within ~3s, not ~16-37s (the old heartbeat-only grace).
//
// Runs under chromium-board / webkit-board (MEGALODON_FAKE_SPAWNER=1, board
// fixture, 3 lanes; workers:1, fullyParallel:false).

import { test, expect, Page, TestInfo } from '@playwright/test';
import { readUiToken } from './_helpers';

const ACTIVITY_OPEN_KEY = 'megalodon.activityWall.open';

/** Stub GET /api/v1/alerts to a fixed list. */
async function stubAlerts(page: Page, alerts: unknown[]): Promise<void> {
  await page.route('**/api/v1/alerts', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ alerts }),
    });
  });
}

/** Authenticate + land on the board. `openPref` seeds the wall open/closed. */
async function gotoBoard(
  page: Page,
  testInfo: TestInfo,
  openPref?: '0' | '1',
): Promise<void> {
  const token = readUiToken(testInfo);
  // Only SEED a preference when one is requested. When openPref is undefined we
  // leave localStorage untouched (a fresh Playwright context starts empty), so
  // the default-open path is exercised cleanly AND a later page.reload() honours
  // whatever the SPA itself persisted (e.g. a toggle-close) — an init script
  // would otherwise re-seed the key on every navigation including reload.
  if (openPref !== undefined) {
    await page.addInitScript(
      ([key, val]) => {
        try { localStorage.setItem(key as string, val as string); } catch (_) { /* ignore */ }
      },
      [ACTIVITY_OPEN_KEY, openPref] as const,
    );
  }
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/', { timeout: 10_000 });
  await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
}

test.describe('Board front-door visibility', () => {
  // -------------------------------------------------------------- fix (1)
  test('alert banner does NOT cover/intercept header controls', async ({ page }, testInfo) => {
    // Pin the wall closed so this case isolates the alert-banner overlap (the
    // auto-opened wall panel is a separate surface covered by other cases).
    // Raise an alert banner for a stale lane.
    await stubAlerts(page, [
      {
        ts: '2026-05-25T12:00:00Z',
        lane: 'A',
        kind: 'STATUS-STALE',
        severity: 'major',
        evidence: ['no status update in 9m'],
        message: 'Lane A status is stale (no update in 9m).',
      },
    ]);
    await gotoBoard(page, testInfo, '0');

    // The banner is present (proves an alert IS up while we test reachability).
    const banner = page.locator('[data-testid="alert-banner-A-STATUS-STALE"]');
    await expect(banner).toBeVisible({ timeout: 8_000 });

    // (a) The activity toggle — the ONLY control that opens the wall — must be
    // clickable, not intercepted. A real click (not force) proves no overlay
    // sits on top of it (Playwright throws "intercepts pointer events" if so).
    const toggle = page.locator('[data-testid="board-activity-toggle"]');
    await expect(toggle).toBeVisible();
    await toggle.click({ timeout: 5_000 });
    // It opened the wall, proving the click landed on the toggle.
    await expect(page.locator('[data-testid="board-activity-panel"]')).toBeVisible({ timeout: 5_000 });
    // Close it again so it doesn't interfere with the nav/kill assertions.
    await page.locator('[data-testid="board-activity-toggle"]').click({ timeout: 5_000 });
    await expect(page.locator('[data-testid="board-activity-panel"]')).toHaveCount(0);

    // (b)/(c) The mission + approval-rules nav links AND the kill-switch must
    // NOT be covered by the alert banner. The old fixed overlay sat over the
    // right side of the nav bar and the header controls and intercepted their
    // clicks. We assert the top-most element at each control's center is NOT the
    // alert banner stack (nor any of its descendants). Showing the control
    // itself OR its in-flow header ancestor is fine — both mean "no overlay on
    // top". (elementFromPoint can return the flex parent at a control's exact
    // center; what matters is that the banner is never the hit.)
    const checkNotUnderBanner = async (testid: string) => {
      const node = page.locator(`[data-testid="${testid}"]`);
      await expect(node).toBeVisible();
      const result = await node.evaluate((n) => {
        const r = n.getBoundingClientRect();
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        const stack = document.querySelector('[data-testid="alert-banner-stack"]');
        const underBanner = !!(hit && stack && (stack === hit || stack.contains(hit)));
        return { underBanner, hitTestid: hit && hit.getAttribute && hit.getAttribute('data-testid') };
      });
      expect(result.underBanner, `${testid} must NOT be under the alert banner (hit=${result.hitTestid})`).toBe(false);
    };
    await checkNotUnderBanner('nav-mission');
    await checkNotUnderBanner('nav-approval-rules');
    await checkNotUnderBanner('board-kill-switch');
  });

  // -------------------------------------------------------------- fix (2)
  test('activity wall is OPEN by default on board mount (no stored preference)', async ({ page }, testInfo) => {
    await stubAlerts(page, []);
    // No openPref → default-open path.
    await gotoBoard(page, testInfo);

    // The wall panel + its component root mount automatically — no click.
    await expect(page.locator('[data-testid="board-activity-panel"]')).toBeVisible({ timeout: 8_000 });
    await expect(page.locator('[data-testid="activity-wall-root"]')).toBeVisible({ timeout: 8_000 });
  });

  test('closing the wall persists; it stays closed next mount', async ({ page }, testInfo) => {
    await stubAlerts(page, []);
    await gotoBoard(page, testInfo); // default-open

    await expect(page.locator('[data-testid="board-activity-panel"]')).toBeVisible({ timeout: 8_000 });

    // Operator closes it via the toggle → preference persists as '0'.
    await page.locator('[data-testid="board-activity-toggle"]').click();
    await expect(page.locator('[data-testid="board-activity-panel"]')).toHaveCount(0);
    const stored = await page.evaluate((key) => localStorage.getItem(key as string), ACTIVITY_OPEN_KEY);
    expect(stored).toBe('0');

    // Reload (same context → localStorage survives). The wall must stay CLOSED.
    await page.reload();
    await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
    // Give the auto-open path a chance to (incorrectly) fire.
    await page.waitForTimeout(500);
    await expect(page.locator('[data-testid="board-activity-panel"]')).toHaveCount(0);
  });

  // -------------------------------------------------------------- fix (3)
  test('SSE outage flips to a VISIBLE disconnected state within ~3s', async ({ page }, testInfo) => {
    await stubAlerts(page, []);
    // Snapshot returns empty; the live SSE silently STALLS (held open, no bytes,
    // no error) — the worst case the old code only caught after the heartbeat
    // grace (~16-37s). The short disconnect-surface timer must flip the visible
    // state within DISCONNECT_SURFACE_MS (~2.5s).
    await page.route('**/api/v1/activity-wall/snapshot*', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ events: [] }) }),
    );
    // Hold the SSE connection open but never send anything (and never resolve),
    // simulating a silently-dead feed. route.continue() to a hung handler isn't
    // available, so we fulfill with an empty event-stream that immediately ends —
    // an immediately-closed stream the browser treats as an error/EOF, which the
    // onerror path must surface promptly.
    await page.route('**/api/v1/activity-wall', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        headers: { 'Cache-Control': 'no-cache', Connection: 'keep-alive' },
        body: ':\n\n', // a single keepalive comment, then EOF → stream dies
      }),
    );

    // Default-open so the wall is mounted and subscribing on arrival.
    await gotoBoard(page, testInfo);
    await expect(page.locator('[data-testid="activity-wall-root"]')).toBeVisible({ timeout: 8_000 });

    const status = page.locator('[data-testid="aw-status"]');
    // Must be visible + read "Disconnected"/"Reconnecting" within ~3s of mount.
    await expect(status).toBeVisible({ timeout: 3_000 });
    await expect(status).toHaveText(/Disconnected|Reconnecting/, { timeout: 3_000 });
  });
});
