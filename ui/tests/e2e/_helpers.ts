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
import * as path from 'node:path';

import { expect, Locator, Page, TestInfo } from '@playwright/test';

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

export function readUiToken(testInfo: TestInfo): string {
  const tokenPath = path.join(fixtureRootForProject(testInfo), '.fleet', 'ui.token');
  return readFileSync(tokenPath, 'utf-8').trim();
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
