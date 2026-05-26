// test_board_activity_reconnect.spec.ts — P0 frontend audit fix (bug #5).
//
// Runs under chromium-board / webkit-board. Proves the activity wall now:
//   - shows a visible "disconnected / reconnecting" status when its SSE drops
//     (previously it only console.warn'd and froze silently), and
//   - shows an explicit "No activity yet" empty state so a blank list reads as
//     "nothing happened", not "broken".
//
// The activity-wall SSE is route-aborted so the EventSource fails immediately;
// the component must surface the status bar (not freeze invisibly).

import { test, expect, Page, TestInfo } from '@playwright/test';
import { readUiToken } from './_helpers';

async function authenticateAndGotoBoard(page: Page, testInfo: TestInfo): Promise<void> {
  const token = readUiToken(testInfo);
  // The activity wall now AUTO-OPENS on board mount when no preference is stored
  // (default-open). This spec drives the open/close TOGGLE explicitly, so pin
  // the preference to CLOSED before the SPA boots; the toggle-to-open flow below
  // then behaves as written.
  await page.addInitScript(() => {
    try { localStorage.setItem('megalodon.activityWall.open', '0'); } catch (_) { /* ignore */ }
  });
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/', { timeout: 10_000 });
  await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
}

test('activity wall: shows a disconnected/reconnecting state when the SSE drops', async ({ page }, testInfo) => {
  // Abort the activity-wall SSE so the EventSource onerror → reconnect path runs.
  // Snapshot returns empty so the empty-state is also exercised.
  await page.route('**/api/v1/activity-wall', (route) => route.abort());
  await page.route('**/api/v1/activity-wall/snapshot*', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ events: [] }) }),
  );

  await authenticateAndGotoBoard(page, testInfo);

  // Open the activity wall.
  await page.locator('[data-testid="board-activity-toggle"]').click();
  await expect(page.locator('[data-testid="activity-wall-root"]')).toBeVisible({ timeout: 5_000 });

  // Empty-state visible (no events) — blank ≠ broken.
  await expect(page.locator('[data-testid="aw-empty"]')).toBeVisible({ timeout: 5_000 });
  await expect(page.locator('[data-testid="aw-empty"]')).toContainText('No activity yet');

  // The status bar must surface a disconnected/reconnecting state (not stay
  // hidden/frozen). It cycles connecting↔disconnected during backoff, so assert
  // it is visible with a recognized state.
  const status = page.locator('[data-testid="aw-status"]');
  await expect(status).toBeVisible({ timeout: 8_000 });
  await expect(status).toHaveText(/Reconnecting|Disconnected/, { timeout: 8_000 });
});

test('activity wall: recovers to connected + hides status when the SSE is restored', async ({ page }, testInfo) => {
  // The recovery path waits out the component's capped exponential reconnect
  // backoff (activity_wall.js _scheduleReconnect: 500ms doubling to a 30s cap).
  // While the SSE is blocked the delay climbs, so after restoring the stream the
  // next reconnect attempt can be several seconds out — on WebKit's slower
  // fetch+EventSource handshake this regularly exceeded the original 15s wait
  // (the dominant non-seed webkit-board flake here). Give the recovery a budget
  // that comfortably covers a backed-off reconnect without bumping into the
  // default 30s per-test cap (earlier setup waits already consume ~16s).
  test.setTimeout(45_000);
  // Start by aborting the SSE, then later allow it through to prove recovery.
  let blockSse = true;
  await page.route('**/api/v1/activity-wall', async (route) => {
    if (blockSse) return route.abort();
    return route.continue();
  });
  await page.route('**/api/v1/activity-wall/snapshot*', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ events: [] }) }),
  );

  await authenticateAndGotoBoard(page, testInfo);
  await page.locator('[data-testid="board-activity-toggle"]').click();
  await expect(page.locator('[data-testid="activity-wall-root"]')).toBeVisible({ timeout: 5_000 });

  const status = page.locator('[data-testid="aw-status"]');
  await expect(status).toBeVisible({ timeout: 8_000 });

  // Restore the stream; the capped backoff reconnect must re-open and the
  // status bar must hide (connected). Allow up to the 30s backoff cap plus the
  // re-open handshake so a backed-off WebKit reconnect is not raced.
  blockSse = false;
  await expect(status).toBeHidden({ timeout: 33_000 });
});
