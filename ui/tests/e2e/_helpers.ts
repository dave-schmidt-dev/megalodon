// Shared helpers for e2e specs.
//
// fixtureRootForProject() maps a project name (set in playwright.config.ts) to
// its tmpdir-isolated fixture root (`/tmp/m/<label>`). The v9.2 specs read
// `.fleet/ui.token` from that root to authenticate against the running
// webServer — they used to read from the source fixture path, which is now
// wrong because each project gets its own copy at run start.
//
// The challenge / reclaim / signal / inject-task POST endpoints all return
// HTTP 202 with `{request_id}` and route through an in-process queue applier
// (MEGALODON_INPROCESS_APPLIER=1 — see megalodon_ui/server.py:602). The applier
// drains every 200ms, so observable side-effects (STATUS.md / TASKS.md
// mutations) appear ~200-400ms after the 202.
//
// Tests that POST then navigate immediately race the applier and see stale
// state. `clickAndWaitForApply` wraps the click + queue-poll loop so specs
// can ignore the async machinery.

import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import * as path from 'node:path';

import { expect, Locator, Page, TestInfo } from '@playwright/test';

/**
 * Re-publish a seeded narrative frame until the board's narrative-stream SSE
 * subscription is live and the frame actually lands.
 *
 * Why this is the de-flake (the dominant webkit-board failure): the fake
 * endpoint (server.py:3020) merges the payload into `narrative_cache` AND
 * publishes the frame to `narrative_hub`. But NarrativeHub.publish() fans out
 * ONLY to queues subscribed *at publish time* — there is no last-frame replay on
 * subscribe (narrator/hub.py). The board reads the cache exactly once at mount
 * (board.js applyFrame(initial)) and thereafter re-renders only from live SSE
 * frames. So a single seed POST that fires in the window between "board-page
 * visible" (what the spec helpers wait on) and "the board's EventSource
 * handshake completed + registered on the hub" publishes to ZERO subscribers and
 * is silently lost; the row keeps the fixture's warm-up content. WebKit's slower
 * EventSource handshake lands inside that window far more often than Chromium —
 * hence "stable on chromium, flaky on webkit".
 *
 * `republishUntil` takes the spec's own one-shot `seedNarrative` (or any async
 * publish fn) and a `probe` that resolves truthy once the seeded frame is
 * visibly rendered. It calls the publish fn, then re-publishes on a short
 * interval until `probe` passes or the deadline elapses. Re-publishing is
 * idempotent (each POST re-merges the same payload and re-emits the full cache),
 * so once the board is subscribed the next emit renders the frame. This is
 * test-only and product-neutral: it changes no behaviour and weakens no
 * assertion — the spec still asserts the real rendered condition afterwards.
 * If the deadline elapses it simply returns, letting the spec's own `expect()`
 * report the genuine failure with its normal diagnostics.
 */
export async function republishUntil(
  publish: () => Promise<void>,
  probe: () => Promise<boolean | undefined>,
  { timeout = 8_000, interval = 300 }: { timeout?: number; interval?: number } = {},
): Promise<void> {
  await publish();
  const deadline = Date.now() + timeout;
  for (;;) {
    let ok: boolean | undefined = false;
    try {
      ok = await probe();
    } catch {
      ok = false;
    }
    if (ok) return;
    if (Date.now() >= deadline) return;
    await new Promise((r) => setTimeout(r, interval));
    await publish();
  }
}

// Mirrors the `fixtures` object in playwright.config.ts. Update both if you
// add a project.
const PROJECT_TO_LABEL: Record<string, string> = {
  'chromium-default': 'def-c',
  'chromium-mutations': 'mut-c',
  'chromium-failure-modes': 'fail-c',
  'chromium-v92-dashboard': 'v92-c',
  'chromium-board': 'board-c',
  'chromium-grid-smoke': 'smoke-c',
  'webkit-default': 'def-w',
  'webkit-mutations': 'mut-w',
  'webkit-failure-modes': 'fail-w',
  'webkit-v92-dashboard': 'v92-w',
  'webkit-board': 'board-w',
};

export function fixtureRootForProject(testInfo: TestInfo): string {
  const label = PROJECT_TO_LABEL[testInfo.project.name];
  if (!label) {
    throw new Error(
      `fixtureRootForProject: unknown project ${testInfo.project.name}; ` +
      `update PROJECT_TO_LABEL in _helpers.ts`,
    );
  }
  return path.join('/tmp/m', label);
}

/**
 * Read `.fleet/ui.token` for the project's fixture, waiting for the file to
 * exist if it is not yet present.
 *
 * Why the wait: Playwright's `webServer` gate only proves the TCP listener is
 * accepting connections (the fd is bound in __main__.py step 6, BEFORE uvicorn
 * starts serving and BEFORE the bearer token is written in step 9). The token
 * is written to disk a short moment after the port opens, so a spec that reads
 * the token the instant the server is "ready" can lose the race and hit
 * `ENOENT: .fleet/ui.token`. This was the dominant webkit-board flake: the
 * WebKit browser launch path hits the helper at a slightly different phase of
 * server startup than chromium, so on WebKit the read frequently outran the
 * write while chromium happened to read after it (hence "stable on chromium").
 *
 * This is a test-only, additive wait: it changes no product behaviour and no
 * assertion — it just blocks until the token the server is about to write is
 * actually on disk, then returns it verbatim. Kept synchronous so the ~38
 * existing `const token = readUiToken(testInfo)` call sites need no change.
 * The bounded retry uses a sync sleep (sleep(1) subprocess) to avoid a busy
 * spin; total budget is generous but finite so a genuinely-down server still
 * fails loudly rather than hanging.
 */
export function readUiToken(testInfo: TestInfo): string {
  const tokenPath = path.join(fixtureRootForProject(testInfo), '.fleet', 'ui.token');
  const deadline = Date.now() + 15_000; // generous: server token-write lags port-open by ms, not seconds
  for (;;) {
    try {
      const tok = readFileSync(tokenPath, 'utf-8').trim();
      if (tok.length > 0) return tok;
      // File exists but is empty (atomic write briefly observable mid-rename) — retry.
    } catch (err: unknown) {
      if ((err as NodeJS.ErrnoException)?.code !== 'ENOENT') throw err;
      // Not written yet — fall through to the wait below.
    }
    if (Date.now() >= deadline) {
      throw new Error(
        `readUiToken: ${tokenPath} not present/non-empty after 15s; ` +
        `the webServer never wrote its bearer token (server failed to start?)`,
      );
    }
    // Sync 100ms pause without a busy-spin; node:test/Playwright helpers run in
    // worker processes so blocking here only stalls this one spec's setup.
    try {
      execFileSync('sleep', ['0.1'], { stdio: 'ignore' });
    } catch {
      // sleep unavailable (unlikely on CI ubuntu/macOS); fall back to a short busy wait.
      const until = Date.now() + 100;
      while (Date.now() < until) { /* spin */ }
    }
  }
}

/**
 * Authenticate against the deny-by-default auth gate and land on the board.
 *
 * The server now requires the `mui_session` cookie on every `/api/**` route
 * except the small public allowlist (`/`, `/static/*`, `/healthz`,
 * `POST /api/v1/auth/exchange`). The SPA's bootstrap performs a hash-token
 * exchange (`/#t=<token>` → POST /api/v1/auth/exchange → Set-Cookie) and then
 * strips the hash. After this resolves the browser context (and therefore
 * `page.request`, which shares the context's cookie jar) carries the session
 * cookie, so subsequent page fetches and direct `page.request.*` calls succeed
 * instead of 401-ing into the re-auth modal.
 *
 * This is the canonical replacement for a bare `page.goto('/')` in any spec
 * that depends on a gated endpoint (config / state / status / narrative /
 * findings / tasks / activity-wall / approval-rules / pane-stream).
 *
 * @param target optional SPA path to navigate to AFTER authenticating (the
 *   board cookie is already set, so the target's own gated fetches succeed).
 *   Defaults to staying on the board (`/`).
 */
export async function gotoAuthed(
  page: Page,
  testInfo: TestInfo,
  target?: string,
): Promise<void> {
  const token = readUiToken(testInfo);
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/', { timeout: 10_000 });
  await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
  if (target && target !== '/') {
    await page.goto(target);
  }
}

/**
 * Exchange the hash token into the browser context's cookie jar WITHOUT
 * asserting any board UI. Useful when a spec needs an authenticated
 * `page.request.*` call but does not (yet) want to render the board, or
 * renders a non-board surface (e.g. /static/index.html, the v92 dashboard).
 *
 * After this resolves, `page.request.get('/api/v1/config')` (and friends)
 * carry the `mui_session` cookie and return 200 instead of 401.
 */
export async function establishSession(page: Page, testInfo: TestInfo): Promise<void> {
  const token = readUiToken(testInfo);
  // POST through the page's own request context so the Set-Cookie lands in the
  // context cookie jar shared by page navigations and page.request.*.
  const resp = await page.request.post('/api/v1/auth/exchange', {
    headers: { 'Content-Type': 'application/json' },
    data: { token },
  });
  expect(resp.status(), 'POST /api/v1/auth/exchange').toBe(200);
}

/**
 * Click `submit` (typically a form submit button) and wait until the resulting
 * 202-queued mutation has been applied by the in-process queue drain loop.
 *
 * The url-pattern selects which response counts as "this form's response" —
 * pass a fragment of the API path (e.g., `/api/v1/signal`).
 *
 * Throws if the response is not 202 (caller can check status / debug from
 * trace if the request validates differently than expected).
 */
export async function clickAndWaitForApply(
  page: Page,
  submit: Locator,
  apiPathFragment: string,
  { timeout = 10_000 }: { timeout?: number } = {},
): Promise<void> {
  const responsePromise = page.waitForResponse(
    (r) => r.url().includes(apiPathFragment) && r.request().method() === 'POST',
    { timeout },
  );
  await submit.click();
  const response = await responsePromise;
  // 422 (validation failure): caller will see error UI; nothing to wait on.
  // 200 (sync): nothing to wait on.
  // 202 (queued): poll until applied.
  if (response.status() === 202) {
    const body = await response.json();
    const requestId = body.request_id as string;
    await expect
      .poll(async () => {
        const r = await page.request.get(`/api/v1/queue/${requestId}`);
        if (!r.ok()) return 'http-error';
        const j = await r.json();
        return j.status as string;
      }, { timeout })
      .toBe('applied');
  }
}
