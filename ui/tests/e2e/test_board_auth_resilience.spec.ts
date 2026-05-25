// test_board_auth_resilience.spec.ts — P0 frontend audit fixes (bugs #1-#4).
//
// Runs under chromium-board / webkit-board (MEGALODON_FAKE_SPAWNER=1, fix-small
// fixture, 3 lanes A/B/C). Proves the auth-race / re-auth / baseline / error-
// state resilience fixes that were the reason the operator "ran for hours and
// had no usable UI".
//
// Coverage:
//   1. (bug #1) Fresh load renders a POPULATED board with NO 401s in the
//      console — gated requests wait for the token→cookie exchange.
//   2. (bug #2) A 401 on a gated request surfaces the shared re-auth modal, and
//      a successful token re-exchange dismisses it + recovers.
//   3. (bug #3) With an empty narrator, the board seeds a baseline from the
//      ungated /api/status (a working lane shows RUNNING, not blank IDLE) and a
//      "narrator warming up" hint rather than bare "—".
//   4. (bug #4) approval-rules shows an ERROR state (not "No rules yet.") on a
//      401.

import { test, expect, Page, ConsoleMessage } from '@playwright/test';
import { readUiToken } from './_helpers';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function authenticateAndGotoBoard(page: Page, token: string): Promise<void> {
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/', { timeout: 10_000 });
  await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
}

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
    headers: { 'Content-Type': 'application/json', ...(csrf ? { 'X-CSRF-Token': csrf } : {}) },
    data: { lanes },
  });
  expect(resp.status(), 'POST /api/v1/__fake__/narrative').toBe(200);
}

function payload(over: Record<string, unknown>): Record<string, unknown> {
  return {
    lane: 'A', lane_name: 'agent-a', state: 'open',
    last: null, now: null, goal: null, tokens: null, narrator_ok: true,
    ...over,
  };
}

// ---------------------------------------------------------------------------
// Bug #1 — fresh load renders populated, with NO 401s in the console.
// ---------------------------------------------------------------------------

test('auth-race: fresh load renders the board with no 401 console errors', async ({ page }, testInfo) => {
  const token = readUiToken(testInfo);

  // Capture every 401 the page sees on a gated endpoint. If the auth race
  // regressed, the first-paint gated requests fire before the cookie and log
  // 401s here.
  const four01s: string[] = [];
  page.on('response', (resp) => {
    if (resp.status() === 401) four01s.push(`${resp.request().method()} ${resp.url()}`);
  });
  const consoleErrors: string[] = [];
  page.on('console', (msg: ConsoleMessage) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });

  await authenticateAndGotoBoard(page, token);

  // Seed a narrative so the rows are demonstrably populated post-auth.
  await seedNarrative(page, {
    A: payload({ lane: 'A', lane_name: 'agent-a', state: 'claimed', goal: 'A-GOAL-x', now: { phrase: 'A-NOW-live' } }),
    B: payload({ lane: 'B', lane_name: 'agent-b', state: 'open', goal: 'B-GOAL-x' }),
    C: payload({ lane: 'C', lane_name: 'agent-c', state: 'open', goal: 'C-GOAL-x' }),
  });

  // Rows present + populated (not blank).
  await expect(page.locator('[data-testid="board-row-A"]')).toContainText('A-NOW-live', { timeout: 8_000 });
  await expect(page.locator('[data-testid="board-row-A"]')).toContainText('A-GOAL-x');

  // The crux: NO 401 hit any gated endpoint during the first-load race.
  expect(four01s, `unexpected 401s: ${four01s.join(', ')}`).toEqual([]);
  // No uncaught console errors either (auth helper must not throw).
  expect(consoleErrors.filter((e) => /401|auth/i.test(e))).toEqual([]);
});

// ---------------------------------------------------------------------------
// Bug #2 — a 401 surfaces the shared re-auth modal and recovers.
// ---------------------------------------------------------------------------

test('re-auth: a 401 on a gated fetch shows the re-auth modal', async ({ page }, testInfo) => {
  const token = readUiToken(testInfo);
  await authenticateAndGotoBoard(page, token);

  // The modal must NOT be open on a healthy board.
  await expect(page.locator('[data-testid="reauth-modal"]')).toHaveCount(0);

  // Force the next narrative fetch to 401 (simulate an invalidated cookie),
  // then trigger a re-fetch by re-mounting the board via the router.
  await page.route('**/api/v1/narrative', (route) =>
    route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ detail: 'authentication required' }) }),
  );
  await page.evaluate(() => {
    history.pushState({}, '', '/approval-rules');
    window.dispatchEvent(new PopStateEvent('popstate', { state: {} }));
    history.pushState({}, '', '/');
    window.dispatchEvent(new PopStateEvent('popstate', { state: {} }));
  });

  // The shared global re-auth modal must appear (open <dialog>).
  const modal = page.locator('[data-testid="reauth-modal"]');
  await expect(modal).toBeVisible({ timeout: 8_000 });
  await expect(page.locator('[data-testid="reauth-heading"]')).toContainText('Session expired');

  // Submitting a valid token re-exchanges and closes the modal.
  await page.unroute('**/api/v1/narrative');
  await page.locator('[data-testid="reauth-token-input"]').fill(token);
  await page.locator('[data-testid="reauth-submit"]').click();
  await expect(modal).toBeHidden({ timeout: 8_000 });
});

// ---------------------------------------------------------------------------
// Bug #3 — empty narrator → /api/status baseline, not blank IDLE.
// ---------------------------------------------------------------------------

test('baseline: empty narrator seeds state/last from /api/status (not blank IDLE)', async ({ page }, testInfo) => {
  const token = readUiToken(testInfo);

  // Return an EMPTY narrative + an empty narrative-stream so the /api/status
  // baseline is the only data source. (The fix-small STATUS.md has LANE-B in
  // state "working: T2" and A/C idle.) Without stubbing the stream, the server
  // publishes its own warmed frames which would overlay the baseline.
  await page.route('**/api/v1/narrative', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ lanes: {} }) }),
  );
  // Empty SSE body → the board's narrative-stream EventSource opens but never
  // delivers a frame, so the baseline (not a server frame) drives the row.
  await page.route('**/api/v1/narrative-stream', (route) =>
    route.fulfill({ status: 200, contentType: 'text/event-stream', body: ':\n\n' }),
  );
  // Keep stale out of the way so the baseline RUNNING pill isn't overlaid STALE.
  await page.route('**/api/v1/lanes/stale', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ stale_lanes: [], governor_blocked: [] }) }),
  );

  await authenticateAndGotoBoard(page, token);

  // Lane B is "working: T2" in STATUS.md → baseline maps to RUNNING (not IDLE).
  await expect(page.locator('[data-testid="board-pill-B"]')).toHaveText('RUNNING', { timeout: 8_000 });
  // Its Now cell shows the explicit "warming up" hint, not a bare dash.
  await expect(page.locator('[data-testid="board-row-B"]')).toContainText('narrator warming up');
  // Baseline Last carries the STATUS.md last-seen timestamp, not "—".
  await expect(page.locator('[data-testid="board-row-B"]')).toContainText('last seen');
});

// ---------------------------------------------------------------------------
// Bug #4 — approval-rules shows an ERROR state on 401, not "No rules yet."
// ---------------------------------------------------------------------------

test('approval-rules: a 401 renders an error state, not the empty state', async ({ page }, testInfo) => {
  const token = readUiToken(testInfo);
  await authenticateAndGotoBoard(page, token);

  // Force the rules GET to 401.
  await page.route('**/api/v1/approval-rules', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ detail: 'authentication required' }) });
    }
    return route.continue();
  });

  await page.evaluate(() => {
    history.pushState({}, '', '/approval-rules');
    window.dispatchEvent(new PopStateEvent('popstate', { state: {} }));
  });
  await expect(page.locator('[data-testid="approval-rules-page"]')).toBeVisible({ timeout: 8_000 });

  // Error state visible; empty state NOT shown.
  await expect(page.locator('[data-testid="approval-rules-error"]')).toBeVisible({ timeout: 5_000 });
  await expect(page.locator('[data-testid="approval-rules-error"]')).toContainText('Session expired');
  await expect(page.locator('[data-testid="approval-rules-empty"]')).toHaveCount(0);
});
