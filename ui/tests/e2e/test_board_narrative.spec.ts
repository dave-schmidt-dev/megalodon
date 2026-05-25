// test_board_narrative.spec.ts — Task 3.5b: board narrative-row rendering.
//
// Runs under chromium-board (MEGALODON_FAKE_SPAWNER=1, fix-small fixture,
// 3 lanes A/B/C, port 8769; workers:1, fullyParallel:false).
//
// Exercises the board's narrative data path end-to-end:
//   - POST /api/v1/__fake__/narrative seeds the narrative_cache and publishes a
//     uniform { lanes: {...} } frame over narrative-stream (same shape the real
//     scheduler emits), so the board's stream→render path is exercised verbatim.
//   - Each board-row-<short> renders the Last desc, Now phrase, Goal, and
//     board-tokens-<short>.
//   - The state pill reflects the narrative state: "claimed" → RUNNING,
//     "open"/"done" → IDLE (when no prompt pending and no staleness).
//   - The narrator-status-dot flips to data-narrator="offline" when ANY payload
//     carries narrator_ok=false; "ok" when all payloads are narrator_ok=true.
//
// Stale isolation: the fix-small fixture has ancient lane timestamps so the real
// /api/v1/lanes/stale computation reports every lane stale, and STALE overlays
// RUNNING/IDLE in the board's pill precedence. To assert the narrative→pill
// mapping deterministically this spec intercepts /api/v1/lanes/stale and returns
// an empty stale list (network-level, no product change). CV-8 STALE precedence
// itself is covered by test_board_stale.spec.ts.

import { test, expect, Page } from '@playwright/test';
import { readUiToken } from './_helpers';

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

/** Mock /api/v1/lanes/stale to return no stale lanes (isolate from the fixture's
 *  ancient timestamps so RUNNING/IDLE pills are deterministic). */
async function stubNoStaleLanes(page: Page): Promise<void> {
  await page.route('**/api/v1/lanes/stale', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ stale_lanes: [], checked_at_utc: new Date().toISOString() }),
    });
  });
}

/** Authenticate via hash-token exchange and land on the board. */
async function authenticateAndGotoBoard(page: Page, token: string): Promise<void> {
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/', { timeout: 10_000 });
  await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
}

/** Read the CSRF token from the page's meta tag. */
async function readCsrfToken(page: Page): Promise<string> {
  return page.evaluate(
    () =>
      (document.querySelector('meta[name="csrf-token"]') as HTMLMetaElement | null)
        ?.getAttribute('content') ?? '',
  );
}

/**
 * POST /api/v1/__fake__/narrative to seed/publish a narrative frame.
 * Body: { lanes: { <short>: <per-lane payload> } }.
 */
async function seedNarrative(
  page: Page,
  lanes: Record<string, unknown>,
): Promise<void> {
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

/** Build a per-lane narrative payload with sensible defaults. */
function payload(over: Record<string, unknown>): Record<string, unknown> {
  return {
    lane: 'A',
    lane_name: 'agent-a',
    state: 'open',
    last: null,
    now: null,
    goal: null,
    tokens: null,
    narrator_ok: true,
    ...over,
  };
}

// ---------------------------------------------------------------------------
// Test 1: rows render Last / Now / Goal / tokens + state pill
// ---------------------------------------------------------------------------

test.describe('board narrative: rows render Last/Now/Goal/tokens + pill', () => {

  test('seeded narrative populates each row and maps state → pill', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    await stubNoStaleLanes(page);
    await authenticateAndGotoBoard(page, token);

    // Distinct payloads per lane. A claimed → RUNNING; B/C → IDLE.
    await seedNarrative(page, {
      A: payload({
        lane: 'A',
        lane_name: 'agent-a',
        state: 'claimed',
        last: { task_id: 'T100', desc: 'A-LAST-shipped-auth' },
        now: { task_id: 'T101', desc: 'A-NOW-fallback', phrase: 'A-NOW-wiring-the-banner' },
        goal: 'A-GOAL-finish-phase-3',
        tokens: 12345,
        narrator_ok: true,
      }),
      B: payload({
        lane: 'B',
        lane_name: 'agent-b',
        state: 'open',
        last: { task_id: 'T200', desc: 'B-LAST-merged-pr' },
        now: { task_id: 'T201', desc: 'B-NOW-idling', phrase: 'B-NOW-waiting-on-review' },
        goal: 'B-GOAL-cut-release',
        tokens: 6789,
        narrator_ok: true,
      }),
      C: payload({
        lane: 'C',
        lane_name: 'agent-c',
        state: 'done',
        last: { task_id: 'T300', desc: 'C-LAST-closed-issue' },
        // No phrase → board falls back to now.desc.
        now: { task_id: 'T301', desc: 'C-NOW-desc-fallback', phrase: null },
        goal: 'C-GOAL-archive',
        tokens: null,
        narrator_ok: true,
      }),
    });

    // ---- Lane A: claimed → RUNNING, phrase preferred over desc -------------
    const rowA = page.locator('[data-testid="board-row-A"]');
    await expect(rowA).toContainText('A-LAST-shipped-auth', { timeout: 8_000 });
    await expect(rowA).toContainText('A-NOW-wiring-the-banner');     // now.phrase
    await expect(rowA).not.toContainText('A-NOW-fallback');          // desc NOT shown
    await expect(rowA).toContainText('A-GOAL-finish-phase-3');
    await expect(page.locator('[data-testid="board-tokens-A"]')).toContainText('12,345');
    await expect(page.locator('[data-testid="board-pill-A"]')).toHaveText('RUNNING');

    // ---- Lane B: open → IDLE ----------------------------------------------
    const rowB = page.locator('[data-testid="board-row-B"]');
    await expect(rowB).toContainText('B-LAST-merged-pr');
    await expect(rowB).toContainText('B-NOW-waiting-on-review');
    await expect(rowB).toContainText('B-GOAL-cut-release');
    await expect(page.locator('[data-testid="board-tokens-B"]')).toContainText('6,789');
    await expect(page.locator('[data-testid="board-pill-B"]')).toHaveText('IDLE');

    // ---- Lane C: done → IDLE; null phrase falls back to now.desc ----------
    const rowC = page.locator('[data-testid="board-row-C"]');
    await expect(rowC).toContainText('C-LAST-closed-issue');
    await expect(rowC).toContainText('C-NOW-desc-fallback');         // now.desc fallback
    await expect(rowC).toContainText('C-GOAL-archive');
    await expect(page.locator('[data-testid="board-tokens-C"]')).toHaveText('—'); // tokens null
    await expect(page.locator('[data-testid="board-pill-C"]')).toHaveText('IDLE');
  });

});

// ---------------------------------------------------------------------------
// Test 1b: Last column renders narrator phrase, falls back to desc (OQ1)
// ---------------------------------------------------------------------------

test.describe('board narrative: Last column phrase-or-desc (OQ1)', () => {

  // The narrative_cache persists across tests in this worker; reset every lane
  // we seed back to a neutral all-null payload so a later test can't observe a
  // stale Last phrase.
  test.afterEach(async ({ page }, testInfo) => {
    try {
      const token = readUiToken(testInfo);
      await authenticateAndGotoBoard(page, token);
      await seedNarrative(page, {
        A: payload({ lane: 'A', lane_name: 'agent-a', state: 'open' }),
        B: payload({ lane: 'B', lane_name: 'agent-b', state: 'open' }),
        C: payload({ lane: 'C', lane_name: 'agent-c', state: 'open' }),
      });
    } catch {
      /* best-effort reset */
    }
  });

  test('last.phrase preferred over desc; null phrase falls back to last.desc', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    await stubNoStaleLanes(page);
    await authenticateAndGotoBoard(page, token);

    await seedNarrative(page, {
      // Lane A: last has a narrator phrase → phrase shown, desc NOT shown.
      A: payload({
        lane: 'A',
        lane_name: 'agent-a',
        state: 'claimed',
        last: {
          task_id: 'T100',
          desc: 'A-LAST-desc-fallback',
          phrase: 'A-LAST-finished-wiring-the-banner',
        },
        now: { task_id: 'T101', desc: 'A-NOW', phrase: 'A-NOW-phrase' },
        goal: 'A-GOAL',
        tokens: 100,
        narrator_ok: true,
      }),
      // Lane B: last.phrase null → board falls back to last.desc.
      B: payload({
        lane: 'B',
        lane_name: 'agent-b',
        state: 'open',
        last: { task_id: 'T200', desc: 'B-LAST-desc-shown', phrase: null },
        now: { task_id: 'T201', desc: 'B-NOW', phrase: null },
        goal: 'B-GOAL',
        tokens: 200,
        narrator_ok: true,
      }),
    });

    // Lane A: narrator phrase wins; deterministic desc is NOT shown.
    const rowA = page.locator('[data-testid="board-row-A"]');
    await expect(rowA).toContainText('A-LAST-finished-wiring-the-banner', { timeout: 8_000 });
    await expect(rowA).not.toContainText('A-LAST-desc-fallback');

    // Lane B: null phrase → deterministic desc fallback shown.
    const rowB = page.locator('[data-testid="board-row-B"]');
    await expect(rowB).toContainText('B-LAST-desc-shown');
  });

});

// ---------------------------------------------------------------------------
// Test 1c: a long unbroken Now phrase is clipped, never widening the page.
// Regression for the board horizontal-scrollbar bug: a long unbroken token in a
// Now phrase (e.g. a finding path) pinned the flex value cell at full width
// (min-width:auto) and ballooned a grid item (min-width:auto) past the viewport.
// Fix: minmax(0,1fr) on the body grid + min-width:0 on the .truncate value cells.
// ---------------------------------------------------------------------------

test.describe('board narrative: long Now phrase does not overflow the page', () => {

  test.afterEach(async ({ page }, testInfo) => {
    try {
      const token = readUiToken(testInfo);
      await authenticateAndGotoBoard(page, token);
      await seedNarrative(page, {
        A: payload({ lane: 'A', lane_name: 'agent-a', state: 'open' }),
        B: payload({ lane: 'B', lane_name: 'agent-b', state: 'open' }),
        C: payload({ lane: 'C', lane_name: 'agent-c', state: 'open' }),
      });
    } catch {
      /* best-effort reset */
    }
  });

  test('long unbroken Now phrase is truncated; no horizontal scroll', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    await stubNoStaleLanes(page);
    await authenticateAndGotoBoard(page, token);

    // A long unbroken token (mimics a finding path) — the worst case for flex
    // truncation: min-width:auto would pin it full-width and overflow the page.
    const longPhrase =
      'P1-B-draft-filed-findings/agent-011f-B-P1-v10-refactor-scope-2026-05-25T01-42Z.md-' +
      'a-very-long-unbroken-path-segment-that-must-be-clipped-not-widen-the-whole-layout-xxxxxxxxxxxxxxxxxxxx';

    await seedNarrative(page, {
      A: payload({
        lane: 'A',
        lane_name: 'agent-a',
        state: 'claimed',
        now: { task_id: 'P1-B', desc: longPhrase, phrase: longPhrase },
        goal: 'A-GOAL',
        tokens: 1,
        narrator_ok: true,
      }),
    });

    const rowA = page.locator('[data-testid="board-row-A"]');
    await expect(rowA).toContainText('P1-B-draft-filed-findings', { timeout: 8_000 });

    // 1) The page must not scroll horizontally (the user-visible bug).
    const overflowPx = await page.evaluate(() => {
      const de = document.documentElement;
      return de.scrollWidth - de.clientWidth;
    });
    expect(overflowPx, 'document horizontal overflow (px)').toBeLessThanOrEqual(1);

    // 2) The Now cell must actually be clipped — content wider than its box
    //    (proves the ellipsis is doing the work, not just a wide viewport).
    const clipped = await page.evaluate(() => {
      const spans = document.querySelectorAll('[data-testid="board-row-A"] .truncate');
      const nowEl = spans[1] as HTMLElement | undefined; // [Last, Now, Goal]
      return nowEl ? nowEl.scrollWidth > nowEl.clientWidth : false;
    });
    expect(clipped, 'Now cell is clipped (ellipsis active)').toBe(true);
  });

});

// ---------------------------------------------------------------------------
// Test 2: narrator-status-dot offline iff any payload narrator_ok=false
// ---------------------------------------------------------------------------

test.describe('board narrative: narrator-status-dot reflects narrator_ok', () => {

  test('one lane narrator_ok=false → dot offline; all-ok frame → dot ok', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    await stubNoStaleLanes(page);
    await authenticateAndGotoBoard(page, token);

    const dot = page.locator('[data-testid="narrator-status-dot"]');

    // Frame with lane B narrator_ok=false → dot must go offline.
    await seedNarrative(page, {
      A: payload({ lane: 'A', lane_name: 'agent-a', state: 'open', narrator_ok: true }),
      B: payload({ lane: 'B', lane_name: 'agent-b', state: 'open', narrator_ok: false }),
      C: payload({ lane: 'C', lane_name: 'agent-c', state: 'open', narrator_ok: true }),
    });
    await expect(dot).toHaveAttribute('data-narrator', 'offline', { timeout: 8_000 });

    // All-ok frame → dot must return to ok.
    await seedNarrative(page, {
      A: payload({ lane: 'A', lane_name: 'agent-a', state: 'open', narrator_ok: true }),
      B: payload({ lane: 'B', lane_name: 'agent-b', state: 'open', narrator_ok: true }),
      C: payload({ lane: 'C', lane_name: 'agent-c', state: 'open', narrator_ok: true }),
    });
    await expect(dot).toHaveAttribute('data-narrator', 'ok', { timeout: 8_000 });
  });

});
