// test_approval_rules.spec.ts — v9.4 Task 3.5: /approval-rules page + Approve&remember modal.
//
// Runs under chromium-grid (MEGALODON_FAKE_SPAWNER=1, fix-small 3-lane fixture).
//
// Case 1: Page renders — navigate to /approval-rules, empty state visible when no rules.
// Case 2: Add manual rule — type pattern + click Add → POST fired → row appears.
// Case 3: Remove rule — click Remove → DELETE fired → row gone.
// Case 4: Approve&remember flow — trigger fake permission prompt (write PROMPT_MARKER
//         to .fleet/A.stream.log), click Approve&remember → modal opens with extracted
//         pattern → click Confirm → rule appears on /approval-rules within 1 s.

import { test, expect } from '@playwright/test';
import { appendFileSync, mkdirSync, existsSync } from 'node:fs';
import * as path from 'node:path';

import { fixtureRootForProject, readUiToken } from './_helpers';

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
  await expect(page.locator('[data-testid="grid-page"]')).toBeVisible({ timeout: 10_000 });
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

// ---------------------------------------------------------------------------
// Case 4: Approve&remember flow
// ---------------------------------------------------------------------------

test('approval-rules: Approve&remember modal → confirm → rule saved on /approval-rules', async ({ page }, testInfo) => {
  const fixtureRoot = fixtureRootForProject(testInfo);

  await authenticateAndGotoGrid(page, testInfo);
  await clearAllRules(page);

  // ---- Seed a fake permission prompt in lane A's stream log ----
  // Same mechanism as test_v94_phase2_smoke.spec.ts Case B.
  const streamLogPath = path.join(fixtureRoot, '.fleet', 'A.stream.log');
  const fleetDir = path.join(fixtureRoot, '.fleet');
  if (!existsSync(fleetDir)) mkdirSync(fleetDir, { recursive: true });

  // Use a simple, extract-able command so we can predict the pattern.
  // Avoid shell glob chars to prevent issues in the prompt block.
  const approvalCommand = 'find . -name README.md';

  const promptBlock =
    'Bash command\n' +
    approvalCommand + '\n' +
    'Do you want to proceed?\n' +
    '❯ 1. Yes\n' +
    '  2. Yes, and always allow access\n' +
    '  3. No\n';
  appendFileSync(streamLogPath, promptBlock, 'utf-8');

  // ---- Wait for the permission banner to appear (watcher poll=1s, FE polls 2s) ----
  const banner = page.locator('[data-testid="permission-panel"]');
  await expect(banner).not.toBeHidden({ timeout: 10_000 });

  const approveRememberBtn = page.locator('[data-testid="permission-approve-remember-A"]');
  await expect(approveRememberBtn).toBeVisible({ timeout: 5_000 });

  // ---- Track the two POST requests ----
  let ruleSaved = false;
  let respondOk = false;

  await page.route('**/api/v1/approval-rules', async (route) => {
    if (route.request().method() === 'POST') {
      const resp = await route.fetch();
      if (resp.status() === 200 || resp.status() === 201) ruleSaved = true;
      await route.fulfill({ response: resp });
    } else {
      await route.continue();
    }
  });

  await page.route('**/permission_prompts/A/respond', async (route) => {
    const resp = await route.fetch();
    if (resp.status() === 202) respondOk = true;
    await route.fulfill({ response: resp });
  });

  // ---- Click "Approve & remember" ----
  await approveRememberBtn.click();

  // ---- Modal must appear with a pattern in the message ----
  const modal = page.locator('[data-testid="confirm-modal"]');
  await expect(modal).toBeVisible({ timeout: 5_000 });
  await expect(modal).toContainText('Approve & remember?');
  // The modal message should contain the extracted pattern (Bash(find:*))
  const modalMsg = page.locator('[data-testid="confirm-modal-message"]');
  await expect(modalMsg).toContainText('Bash(find:*)', { timeout: 3_000 });

  // ---- Click the confirm button ----
  await page.locator('[data-testid="confirm-modal-confirm"]').click();

  // ---- Both POSTs must have fired ----
  await expect.poll(() => ruleSaved, { timeout: 8_000 }).toBe(true);
  await expect.poll(() => respondOk, { timeout: 8_000 }).toBe(true);

  // ---- Navigate to /approval-rules and verify the rule appears ----
  await navigateToApprovalRules(page);
  await expect(
    page.locator('[data-pattern="Bash(find:*)"]'),
  ).toBeVisible({ timeout: 5_000 });
});
