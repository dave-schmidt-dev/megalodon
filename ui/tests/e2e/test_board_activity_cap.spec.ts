// test_board_activity_cap.spec.ts — Wave 2 FE regression coverage for the two
// activity-wall cap bugs fixed in this wave:
//
//   (a) seenKeys unbounded leak — the dedupe set grew one entry per event
//       forever even though the DOM was capped at 500 rows. Fix: evict the
//       oldest row's key from seenKeys in lockstep with row eviction
//       (_enforceCap + row.dataset.eventKey). Regression guard: seenKeys.size
//       (reflected onto [data-testid="activity-wall-root"]'s data-seen-keys)
//       must stay bounded by the row cap, NOT grow with total events seen.
//
//   (b) emptyEl off-by-one — the placeholder element is a child of the list, so
//       the old cap loop (listEl.children.length / removeChild(lastChild))
//       could evict the placeholder and was off-by-one against the 500 cap.
//       Fix: _enforceCap counts/evicts ONLY .aw-row elements. Regression guard:
//       after overflowing the cap, the [data-testid="aw-empty"] placeholder
//       still exists inside the list (never evicted as if it were a row).
//
// Strategy: stub /api/v1/activity-wall/snapshot to return MORE than MAX_DOM_ROWS
// (500) events in a single hydration, so _enforceCap runs in a real browser DOM.
// We refuse the live SSE so the only events are the stubbed snapshot batch
// (deterministic count). Runs under chromium-board / webkit-board.

import { test, expect } from '@playwright/test';
import type { Page, TestInfo } from '@playwright/test';

import { readUiToken } from './_helpers';

const MAX_DOM_ROWS = 500;
const OVERFLOW = 610; // > cap, so eviction must fire

/** Build a snapshot of `n` finding events, newest-first (server sort order). */
function makeSnapshotEvents(n: number) {
  const events = [];
  for (let i = 0; i < n; i++) {
    // Distinct ts + summary → distinct dedupe key (type|lane|ts|summary).
    // Newest-first: index 0 is the newest.
    const idx = n - 1 - i; // so larger idx = newer
    const secs = String(idx % 60).padStart(2, '0');
    const mins = String(Math.floor(idx / 60) % 60).padStart(2, '0');
    events.push({
      type: 'finding',
      lane: 'A',
      ts: `2026-05-25T10-${mins}-${secs}Z`,
      summary: `cap-event-${idx}`,
      payload: { filename: `agent-cap-A-${idx}.md`, path: `/x/agent-cap-A-${idx}.md` },
    });
  }
  // Newest-first.
  events.sort((a, b) => b.summary.localeCompare(a.summary, undefined, { numeric: true }));
  return events;
}

async function mountWallWithSnapshot(page: Page, testInfo: TestInfo, events: unknown[]) {
  // Stub the snapshot with a fixed, oversized batch.
  await page.route('**/api/v1/activity-wall/snapshot**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ events }),
    });
  });
  // Refuse the live SSE so the row set is exactly the stubbed snapshot.
  await page.route('**/api/v1/activity-wall', async (route) => {
    await route.fulfill({ status: 204, contentType: 'text/event-stream', body: '' });
  });

  const token = readUiToken(testInfo);
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/', { timeout: 10_000 });
  await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
  // Open the wall via the board toggle (it is not auto-mounted).
  await page.locator('[data-testid="board-activity-toggle"]').click();
  await expect(page.locator('[data-testid="activity-wall-root"]')).toBeVisible({ timeout: 5_000 });
}

test.describe('activity wall: row cap + seenKeys bound', () => {
  test('overflowing the cap evicts rows, bounds seenKeys, and keeps the placeholder', async ({ page }, testInfo: TestInfo) => {
    await mountWallWithSnapshot(page, testInfo, makeSnapshotEvents(OVERFLOW));

    const list = page.locator('[data-testid="aw-list"]');
    const rows = list.locator('.aw-row');

    // (b) Row count caps at exactly MAX_DOM_ROWS — NOT OVERFLOW, and NOT
    // MAX_DOM_ROWS-1 (the old off-by-one, where the placeholder ate a slot).
    await expect.poll(async () => rows.count(), { timeout: 8_000 }).toBe(MAX_DOM_ROWS);

    // (b) The empty-state placeholder still exists inside the list — it was
    // never evicted as though it were a row.
    const placeholder = list.locator('[data-testid="aw-empty"]');
    await expect(placeholder).toHaveCount(1);
    // It is hidden because rows exist, but present in the DOM.
    await expect(placeholder).toBeHidden();

    // (a) seenKeys is bounded by the row cap (500), NOT the total events seen
    // (610). The leak fix deletes evicted rows' keys in lockstep.
    const seenKeys = await page
      .locator('[data-testid="activity-wall-root"]')
      .getAttribute('data-seen-keys');
    expect(Number(seenKeys)).toBe(MAX_DOM_ROWS);
  });

  test('an under-cap snapshot keeps every row and a bounded seenKeys', async ({ page }, testInfo: TestInfo) => {
    const n = 12;
    await mountWallWithSnapshot(page, testInfo, makeSnapshotEvents(n));

    const list = page.locator('[data-testid="aw-list"]');
    await expect.poll(async () => list.locator('.aw-row').count(), { timeout: 8_000 }).toBe(n);

    // Placeholder present + hidden.
    await expect(list.locator('[data-testid="aw-empty"]')).toHaveCount(1);

    // seenKeys equals the rendered row count (no eviction needed, no leak).
    const seenKeys = await page
      .locator('[data-testid="activity-wall-root"]')
      .getAttribute('data-seen-keys');
    expect(Number(seenKeys)).toBe(n);
  });
});
