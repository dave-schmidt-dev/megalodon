// test_board_activity_backfill.spec.ts — R5 (BLOCKING).
//
// The activity wall used to silently lose events on an SSE blip: es.onerror only
// reconnected/backfilled when readyState === CLOSED, but a transient outage
// leaves the browser in CONNECTING (auto-retrying), so the snapshot backfill
// never ran and the append-only wall kept a PERMANENT gap — invisibly.
//
// This spec proves the fix:
//   1. an SSE outage surfaces a VISIBLE disconnected/reconnecting state, and
//   2. the reconnect re-runs fetchSnapshot() to BACKFILL the event missed
//      during the gap (so the wall has no permanent hole).
//
// Strategy: serve the activity-wall snapshot ourselves and bump what it returns
// across the outage. The first snapshot has only E1; the SSE is aborted (→
// disconnected + scheduled reconnect + backfill); the reconnect's snapshot
// returns E1+E2, so the previously-missed E2 row appears purely via backfill.
//
// Runs under chromium-board / webkit-board.

import { test, expect, Page, TestInfo } from '@playwright/test';
import { readUiToken } from './_helpers';

async function authAndGotoBoard(page: Page, testInfo: TestInfo): Promise<void> {
  const token = readUiToken(testInfo);
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/', { timeout: 10_000 });
  await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
}

function ev(id: string, summary: string) {
  return {
    type: 'history',
    lane: 'A',
    ts: `2026-05-25T20:00:${id}Z`,
    summary,
    payload: { id },
  };
}

test('activity wall: an SSE outage shows disconnected AND backfills the missed event on reconnect', async ({ page }, testInfo) => {
  // The snapshot starts with only E1; after the outage it also returns E2 (the
  // event that "arrived" while the SSE was down). The wall must backfill E2.
  let snapshotEvents = [ev('01', 'first-event-E1')];
  await page.route('**/api/v1/activity-wall/snapshot*', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ events: snapshotEvents }),
    }),
  );

  // Fail the SSE with a fatal HTTP error (503). A non-2xx response closes the
  // EventSource (readyState → CLOSED) → our onerror CLOSED branch fires the
  // visible disconnected state AND schedules a capped-backoff reconnect that
  // re-runs fetchSnapshot() to backfill. Each scheduled reconnect re-opens the
  // SSE (503 again) until the gap is filled — we never need the SSE itself to
  // carry E2; the backfill snapshot does.
  await page.route('**/api/v1/activity-wall', (route) =>
    route.fulfill({ status: 503, contentType: 'text/plain', body: 'unavailable' }),
  );

  await authAndGotoBoard(page, testInfo);
  await page.locator('[data-testid="board-activity-toggle"]').click();
  await expect(page.locator('[data-testid="activity-wall-root"]')).toBeVisible({ timeout: 5_000 });

  // E1 hydrated from the first snapshot.
  await expect(page.locator('[data-testid="aw-list"] .aw-row')).toContainText('first-event-E1', { timeout: 8_000 });

  // (1) The disconnect is VISIBLE — the status bar surfaces a recognized state
  // rather than freezing silently.
  const status = page.locator('[data-testid="aw-status"]');
  await expect(status).toBeVisible({ timeout: 8_000 });
  await expect(status).toHaveText(/Reconnecting|Disconnected/, { timeout: 8_000 });

  // Now "publish" the event that was missed during the outage: the NEXT snapshot
  // (fetched by the reconnect's backfill) returns E1+E2.
  snapshotEvents = [ev('02', 'missed-event-E2'), ev('01', 'first-event-E1')];

  // (2) BACKFILL: without ever delivering E2 over the SSE, the reconnect's
  // fetchSnapshot() must pull E2 in. The capped backoff reconnect fires within a
  // few seconds; give it room. Target the specific row (avoid strict-mode multi-
  // match) and assert exactly one E2 row exists (no duplicate).
  const e2Row = page.locator('[data-testid="aw-list"] .aw-row', { hasText: 'missed-event-E2' });
  await expect(e2Row).toHaveCount(1, { timeout: 15_000 });

  // E1 is still present (no duplicate, dedupe held); both rows now in the wall.
  await expect(page.locator('[data-testid="aw-list"] .aw-row', { hasText: 'first-event-E1' })).toHaveCount(1);
});
