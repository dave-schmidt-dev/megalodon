// test_lane_detail.spec.ts — Task 1.6: pages/lane_detail.js E2E tests.
//
// Tests run under chromium-grid project (NON_MUTATION_DEFAULT_ENV + fix-small
// fixture, port 8769). The fix-small fixture has 3 lanes: LANE-A/A, LANE-B/B,
// LANE-C/C.
//
// Test cases:
//   1. Send flow — type text → click Send → assert POST with correct body + header.
//   2. Byte limit — type 16385 chars → Send disabled + warning visible.
//   3. Live char count — count updates as user types.
//   4. Debounce — after 202 response, Send disabled for ~6s then re-enables.
//   5. Back link — URL becomes / and grid renders.
//
// Security: inject endpoint is mocked via page.route() so no real mutations occur.

import { test, expect } from '@playwright/test';
import { readUiToken } from './_helpers';

// Navigate to /lane/A and wait for the page to render.
// Authenticate via the hash-token exchange first: the board's narrative /
// lanes/stale / narrative-stream requests are session-gated, and an
// unauthenticated load now (correctly) surfaces the re-auth modal which would
// overlay and block the row click. (P0 frontend audit: bugs #1/#2.)
async function gotoLaneA(page: import('@playwright/test').Page, testInfo: import('@playwright/test').TestInfo) {
  const token = readUiToken(testInfo);
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/', { timeout: 10_000 });
  await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
  // Wave 3 safety: the inject form + restart button are gated behind Control
  // mode (READ-ONLY is the default). Enable Control mode so these tests can
  // exercise the state-changing affordances they assert on.
  await page.locator('[data-testid="action-toggle-control-mode"]').click();
  await page.locator('[data-testid="board-row-A"]').click();
  await expect(page).toHaveURL(/\/lane\/A$/, { timeout: 5_000 });
  await expect(page.locator('[data-testid="lane-detail-page"]')).toBeVisible({ timeout: 8_000 });
}

test.describe('lane_detail: inject form send flow', () => {

  test('type "hello world" → click Send → POST /api/v1/lane/A/inject with correct body and X-CSRF-Token', async ({ page }, testInfo) => {
    // Intercept the inject POST to inspect it before it reaches the server.
    let capturedRequest: { body: string; headers: Record<string, string> } | null = null;
    await page.route('**/api/v1/lane/A/inject', async (route) => {
      const req = route.request();
      capturedRequest = {
        body: req.postData() || '',
        headers: req.headers(),
      };
      await route.fulfill({ status: 202, contentType: 'application/json', body: '{"ok":true}' });
    });

    await gotoLaneA(page, testInfo);

    await page.locator('[data-testid="inject-textarea"]').fill('hello world');
    // Ensure enter checkbox is checked (default).
    await expect(page.locator('[data-testid="inject-enter-checkbox"]')).toBeChecked();

    await page.locator('[data-testid="inject-send"]').click();
    // Wave 3 safety: inject now requires a confirm modal. Confirm it.
    await expect(page.locator('[data-testid="confirm-modal"]')).toBeVisible({ timeout: 5_000 });
    await page.locator('[data-testid="confirm-modal-confirm"]').click();

    // Wait for the route handler to capture the request.
    await expect.poll(() => capturedRequest, { timeout: 5_000 }).not.toBeNull();

    // Assert POST body.
    const body = JSON.parse(capturedRequest!.body);
    expect(body.text).toBe('hello world');
    expect(body.enter).toBe(true);

    // Assert X-CSRF-Token header is present (may be empty string if meta tag is empty,
    // but the header must be sent — grid.js / server.py both require it).
    expect(Object.keys(capturedRequest!.headers)).toContain('x-csrf-token');
  });

});

test.describe('lane_detail: byte limit enforcement', () => {

  test('type 16385 chars → Send button disabled + warning visible', async ({ page }, testInfo) => {
    await gotoLaneA(page, testInfo);

    const textarea = page.locator('[data-testid="inject-textarea"]');
    const sendBtn = page.locator('[data-testid="inject-send"]');
    const warning = page.locator('[data-testid="inject-limit-warning"]');

    // Fill with exactly 16385 ASCII characters (each is 1 byte → 16385 bytes total).
    await textarea.fill('x'.repeat(16385));

    // Warning must be visible.
    await expect(warning).toBeVisible({ timeout: 3_000 });

    // Send must be disabled.
    await expect(sendBtn).toBeDisabled({ timeout: 3_000 });
  });

  test('type exactly 16384 chars → Send button enabled and no warning', async ({ page }, testInfo) => {
    await gotoLaneA(page, testInfo);

    const textarea = page.locator('[data-testid="inject-textarea"]');
    const sendBtn = page.locator('[data-testid="inject-send"]');
    const warning = page.locator('[data-testid="inject-limit-warning"]');

    // Fill with exactly 16384 ASCII characters (at the limit, not over).
    await textarea.fill('x'.repeat(16384));

    // Warning must NOT be visible.
    await expect(warning).toBeHidden({ timeout: 3_000 });

    // Send should be enabled.
    await expect(sendBtn).toBeEnabled({ timeout: 3_000 });
  });

});

test.describe('lane_detail: live character count', () => {

  test('character count updates as user types', async ({ page }, testInfo) => {
    await gotoLaneA(page, testInfo);

    const textarea = page.locator('[data-testid="inject-textarea"]');
    const countEl = page.locator('[data-testid="inject-byte-count"]');

    // Initially empty.
    await expect(countEl).toContainText('0 / 16384 bytes', { timeout: 3_000 });

    // Type 5 characters.
    await textarea.fill('hello');
    await expect(countEl).toContainText('5 / 16384 bytes', { timeout: 2_000 });

    // Type more.
    await textarea.fill('hello world');
    await expect(countEl).toContainText('11 / 16384 bytes', { timeout: 2_000 });

    // Clear.
    await textarea.fill('');
    await expect(countEl).toContainText('0 / 16384 bytes', { timeout: 2_000 });
  });

});

test.describe('lane_detail: send debounce', () => {

  test('after successful send (202), Send disabled for ~6 seconds then re-enables', async ({ page }, testInfo) => {
    // Intercept inject endpoint to return 202 immediately.
    await page.route('**/api/v1/lane/A/inject', async (route) => {
      await route.fulfill({ status: 202, contentType: 'application/json', body: '{"ok":true}' });
    });

    await gotoLaneA(page, testInfo);

    const textarea = page.locator('[data-testid="inject-textarea"]');
    const sendBtn = page.locator('[data-testid="inject-send"]');

    await textarea.fill('test message');
    await sendBtn.click();
    // Wave 3 safety: inject now requires a confirm modal. Confirm it.
    await expect(page.locator('[data-testid="confirm-modal"]')).toBeVisible({ timeout: 5_000 });
    await page.locator('[data-testid="confirm-modal-confirm"]').click();

    // Immediately after confirm, button should be disabled (6s debounce).
    await expect(sendBtn).toBeDisabled({ timeout: 2_000 });

    // After the 6-second debounce expires, the button should be enabled again.
    // We wait up to 9 seconds total (6s debounce + some overhead).
    await expect(sendBtn).toBeEnabled({ timeout: 9_000 });
  });

});

test.describe('lane_detail: back link navigation', () => {

  test('clicking back link navigates to / and renders board page', async ({ page }, testInfo) => {
    await gotoLaneA(page, testInfo);

    // Click the back link.
    await page.locator('[data-testid="lane-detail-back"]').click();

    // URL should become /.
    await expect(page).toHaveURL(/:\d+\/$/, { timeout: 5_000 });

    // Board page should render.
    await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 8_000 });
  });

});
