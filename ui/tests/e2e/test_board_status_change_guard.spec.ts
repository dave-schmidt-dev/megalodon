// test_store_status_change_guard.spec.ts — regression for BUG 1.
//
// Live console showed:
//   TypeError: Cannot read properties of undefined (reading 'lane')
//   at store.js (Array.findIndex)
// fired repeatedly. Root cause: a `status-change` SSE event arriving with no
// `row` (or an odd shape) pushed `undefined` into status.lanes; the NEXT
// status-change's `lanes.findIndex((l) => l.lane === payload.lane)` then
// dereferenced that undefined element and threw — uncaught, inside the
// requestAnimationFrame in sse.js.
//
// This spec drives the REAL store singleton (the same module instance the app
// loads) via a dynamic import of the served /static/js/store.js, replaying the
// exact two-event sequence, and asserts:
//   1. no uncaught page error fires, and
//   2. the malformed event is ignored while a subsequent valid event still
//      lands in status.lanes (the board stays functional).
//
// Runs under chromium-board (3-lane fix-small fixture, port 8769).

import { test, expect } from '@playwright/test';
import { gotoAuthed } from './_helpers';

test.describe('store: malformed status-change does not poison status.lanes (BUG 1)', () => {

  test('status-change with no row is ignored and a later valid event still applies', async ({ page }, testInfo) => {
    const pageErrors: string[] = [];
    page.on('pageerror', (err) => pageErrors.push(String(err)));

    // The board's lane rows only render once the session-gated config/narrative
    // fetches succeed, so authenticate first (hash-token exchange).
    await gotoAuthed(page, testInfo);
    await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('[data-testid^="board-row-"]')).toHaveCount(3, { timeout: 5_000 });

    // Drive the real store the way sse.js does: replay a malformed event then a
    // valid one. Pre-fix, the second findIndex over the poisoned array throws.
    const result = await page.evaluate(async () => {
      const mod = await import('/static/js/store.js');
      const store = mod.store ?? mod.default;

      const errors: string[] = [];
      const apply = (type: string, payload: unknown) => {
        try {
          // store.applyEvent runs synchronously; the live bug surfaced inside a
          // requestAnimationFrame but the throwing call is applyEvent itself.
          store.applyEvent(type, payload);
        } catch (e) {
          errors.push(String(e));
        }
      };

      // 1) Malformed: a status-change with NO row. Must be a no-op for lanes.
      apply('status-change', { lane: 'LANE-A', utc: '2026-01-01T00:00:01Z' });
      // 2) Also try a status-change whose row lacks a lane key.
      apply('status-change', { lane: 'LANE-A', row: {}, utc: '2026-01-01T00:00:02Z' });
      // 3) Valid event: pre-fix, findIndex would dereference the undefined
      //    element pushed by (1) and throw here.
      apply('status-change', {
        lane: 'LANE-A',
        row: { lane: 'LANE-A', status: 'WORKING' },
        utc: '2026-01-01T00:00:03Z',
      });

      const lanes = store.get('status.lanes') || [];
      return {
        errors,
        laneCount: lanes.length,
        hasUndefined: lanes.some((l: unknown) => l == null),
        hasWorkingLaneA: lanes.some(
          (l: { lane?: string; status?: string }) =>
            l && l.lane === 'LANE-A' && l.status === 'WORKING',
        ),
      };
    });

    // No exception thrown by any applyEvent call.
    expect(result.errors).toEqual([]);
    // The malformed events never stored an undefined/null lane element.
    expect(result.hasUndefined).toBe(false);
    // The valid event landed.
    expect(result.hasWorkingLaneA).toBe(true);

    // No uncaught page errors of the BUG-1 signature.
    const laneTypeErrors = pageErrors.filter((e) =>
      /Cannot read properties of undefined \(reading 'lane'\)/.test(e),
    );
    expect(laneTypeErrors).toEqual([]);

    // Board is still rendered and interactive after the event storm.
    await expect(page.locator('[data-testid^="board-row-"]')).toHaveCount(3, { timeout: 5_000 });
  });

});
