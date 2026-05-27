// test_approval_rules.spec.ts — v9.4 Task 3.5: /approval-rules page + Approve&remember modal.
//
// Runs under chromium-board-mut / webkit-board-mut (MEGALODON_FAKE_SPAWNER=1, fix-small 3-lane fixture).
//
// Case 1: Page renders — navigate to /approval-rules, empty state visible when no rules.
// Case 2: Add manual rule — type pattern + click Add → POST fired → row appears.
// Case 3: Remove rule — click Remove → DELETE fired → row gone.
//
// The /approval-rules CRUD page is still live (it now feeds the governor's
// allow-list). The former "Case 4" (approve&remember via permission prompt)
// was removed with the permission-prompt flow it depended on.

import { test, expect } from '@playwright/test';

import { readUiToken, setControlMode } from './_helpers';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function authenticateAndGotoGrid(
  page: import('@playwright/test').Page,
  testInfo: import('@playwright/test').TestInfo,
): Promise<void> {
  const token = readUiToken(testInfo);
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/', { timeout: 10_000 });
  await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
  // Flip the SERVER-SIDE control-mode flag ON so approval-rules POST/DELETE
  // endpoints return 201/204 instead of 403.
  await setControlMode(page, true);
}

async function navigateToApprovalRules(
  page: import('@playwright/test').Page,
): Promise<void> {
  // Navigate via pushState (same mechanism as app.js router).
  await page.evaluate(() => {
    history.pushState({}, '', '/approval-rules');
    window.dispatchEvent(new PopStateEvent('popstate', { state: {} }));
  });
  await expect(page.locator('[data-testid="approval-rules-page"]')).toBeVisible({ timeout: 8_000 });
}

async function readCsrfToken(page: import('@playwright/test').Page): Promise<string> {
  return page.evaluate(() => {
    return (
      (document.querySelector('meta[name="csrf-token"]') as HTMLMetaElement | null)
        ?.getAttribute('content') ?? ''
    );
  });
}

/** Clean up any existing approval rules so tests start with a blank slate. */
async function clearAllRules(page: import('@playwright/test').Page): Promise<void> {
  const csrf = await readCsrfToken(page);

  // Fetch existing rules and delete each one.
  const rulesResp = await page.request.get('/api/v1/approval-rules');
  if (!rulesResp.ok()) return;
  const body = await rulesResp.json();
  const rules: Array<{ pattern: string }> = body.rules ?? [];
  for (const rule of rules) {
    await page.request.delete(
      `/api/v1/approval-rules?pattern=${encodeURIComponent(rule.pattern)}`,
      { headers: csrf ? { 'X-CSRF-Token': csrf } : {} },
    );
  }
}

// ---------------------------------------------------------------------------
// Case 1: Page renders — empty state when no rules
// ---------------------------------------------------------------------------

test('approval-rules: page renders with empty state when no rules', async ({ page }, testInfo) => {
  await authenticateAndGotoGrid(page, testInfo);
  await clearAllRules(page);
  await navigateToApprovalRules(page);

  await expect(page.locator('[data-testid="approval-rules-heading"]')).toContainText('Approval Rules');
  await expect(page.locator('[data-testid="approval-rules-empty"]')).toBeVisible({ timeout: 5_000 });
  await expect(page.locator('[data-testid="approval-rules-empty"]')).toContainText('No approval rules yet.');
});

// ---------------------------------------------------------------------------
// Case 2: Add manual rule → POST fired → row appears
// ---------------------------------------------------------------------------

test('approval-rules: add manual rule fires POST and row appears', async ({ page }, testInfo) => {
  await authenticateAndGotoGrid(page, testInfo);
  await clearAllRules(page);
  await navigateToApprovalRules(page);

  const pattern = 'Bash(find:*)';

  // Track the POST to approval-rules.
  let postFired = false;
  let postStatus: number | null = null;
  await page.route('**/api/v1/approval-rules', async (route) => {
    if (route.request().method() === 'POST') {
      const resp = await route.fetch();
      postFired = true;
      postStatus = resp.status();
      await route.fulfill({ response: resp });
    } else {
      await route.continue();
    }
  });

  // Type pattern and click Add.
  await page.locator('[data-testid="approval-rules-pattern-input"]').fill(pattern);
  await page.locator('[data-testid="approval-rules-add-btn"]').click();

  // POST must have fired and returned 201.
  await expect.poll(() => postFired, { timeout: 5_000 }).toBe(true);
  expect(postStatus, 'POST should return 201').toBe(201);

  // Row with the pattern must appear in the table.
  await expect(
    page.locator('[data-testid="approval-rules-table"]'),
  ).toBeVisible({ timeout: 5_000 });
  await expect(
    page.locator('[data-pattern="Bash(find:*)"]'),
  ).toBeVisible({ timeout: 3_000 });
});

// ---------------------------------------------------------------------------
// Case 3: Remove rule → DELETE fired → row gone
// ---------------------------------------------------------------------------

test('approval-rules: remove rule fires DELETE and row disappears', async ({ page }, testInfo) => {
  await authenticateAndGotoGrid(page, testInfo);
  await clearAllRules(page);

  // Seed a rule via API before navigating to the page.
  const csrf = await readCsrfToken(page);
  const seedResp = await page.request.post('/api/v1/approval-rules', {
    data: { pattern: 'Bash(pytest:*)', added_by_session: 'test-session' },
    headers: { 'Content-Type': 'application/json', ...(csrf ? { 'X-CSRF-Token': csrf } : {}) },
  });
  expect(seedResp.status(), 'seed rule POST').toBe(201);

  await navigateToApprovalRules(page);

  // Row must exist.
  await expect(page.locator('[data-pattern="Bash(pytest:*)"]')).toBeVisible({ timeout: 5_000 });

  // Track the DELETE.
  let deleteFired = false;
  let deleteStatus: number | null = null;
  await page.route('**/api/v1/approval-rules*', async (route) => {
    if (route.request().method() === 'DELETE') {
      const resp = await route.fetch();
      deleteFired = true;
      deleteStatus = resp.status();
      await route.fulfill({ response: resp });
    } else {
      await route.continue();
    }
  });

  // Click Remove on that row.
  await page.locator('[data-pattern="Bash(pytest:*)"] [data-testid="approval-rule-remove"]').click();

  // DELETE must have fired and returned 204.
  await expect.poll(() => deleteFired, { timeout: 5_000 }).toBe(true);
  expect(deleteStatus, 'DELETE should return 204').toBe(204);

  // Row must be gone.
  await expect(page.locator('[data-pattern="Bash(pytest:*)"]')).not.toBeVisible({ timeout: 5_000 });

  // Empty state should appear (no other rules).
  await expect(page.locator('[data-testid="approval-rules-empty"]')).toBeVisible({ timeout: 3_000 });
});
