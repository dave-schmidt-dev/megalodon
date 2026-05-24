// test_board_banner.spec.ts — Task 3.5b: permission-banner regression on the board (SR-3).
//
// Runs under chromium-board (MEGALODON_FAKE_SPAWNER=1, fix-small fixture,
// 3 lanes A/B/C, port 8769; workers:1, fullyParallel:false).
//
// The board mounts the same createPermissionBanner() component the grid used
// (permission-panel on board-page). This spec ports the approve / deny /
// approve-remember coverage from test_v94_phase3_smoke.spec.ts to the board's
// mounted banner and asserts against the REAL endpoints:
//   - permission-panel + per-lane controls render for each pending prompt
//   - permission-approve-all renders with the pending count
//   - Deny (lane C): respond endpoint fires, prompt clears
//   - Approve (lane B): respond endpoint fires, prompt clears
//   - Approve & remember (lane A): confirm-modal shows the extracted pattern,
//     confirm → /approval-rules POST (pattern saved) + respond fires + prompt clears
//
// Seeding: write Claude-REPL prompt blocks to .fleet/<lane>.stream.log (TRUNCATE
// per lane). The PermissionWatcher polls every 1 s; the banner polls every 2 s.

import { test, expect, Page } from '@playwright/test';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import * as path from 'node:path';

import { fixtureRootForProject, readUiToken } from './_helpers';

// ---------------------------------------------------------------------------
// Shared helpers
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

/** Write a Claude-REPL prompt block to <lane>.stream.log (truncate). */
function writePromptBlock(fixtureRoot: string, lane: string, cmd: string): void {
  const fleetDir = path.join(fixtureRoot, '.fleet');
  if (!existsSync(fleetDir)) mkdirSync(fleetDir, { recursive: true });
  const block =
    'Bash command\n' +
    cmd + '\n' +
    'Do you want to proceed?\n' +
    '❯ 1. Yes\n' +
    '  2. Yes, and always allow access\n' +
    '  3. No\n';
  writeFileSync(path.join(fleetDir, `${lane}.stream.log`), block, 'utf-8');
}

/** Truncate a lane's stream log so the watcher stops re-surfacing its prompt. */
function clearPromptBlock(fixtureRoot: string, lane: string): void {
  writeFileSync(path.join(fixtureRoot, '.fleet', `${lane}.stream.log`), '', 'utf-8');
}

/** Delete every approval rule so this spec starts (and ends) clean. */
async function clearAllRules(page: Page): Promise<void> {
  const csrf = await readCsrfToken(page);
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
// Banner regression: render + approve + deny + approve-remember
// ---------------------------------------------------------------------------

test.describe('board banner regression (SR-3): approve / deny / approve-remember', () => {

  test('panel + per-lane controls render; approve, deny, approve-remember all work', async ({ page }, testInfo) => {
    const token = readUiToken(testInfo);
    const fixtureRoot = fixtureRootForProject(testInfo);

    await authenticateAndGotoBoard(page, token);
    await clearAllRules(page);

    // ---- Seed pending prompts for all three lanes -------------------------
    // Lane A uses a curl command so the extracted pattern is predictable.
    const cmdA = 'curl -s http://127.0.0.1:8769/banner-remember';
    const expectedPattern = 'Bash(curl -s http://127.0.0.1:8769/*)';
    writePromptBlock(fixtureRoot, 'A', cmdA);
    writePromptBlock(fixtureRoot, 'B', 'echo banner-approve-B');
    writePromptBlock(fixtureRoot, 'C', 'echo banner-deny-C');

    // ---- Panel + per-lane controls render ---------------------------------
    const panel = page.locator('[data-testid="permission-panel"]');
    await expect(panel).not.toBeHidden({ timeout: 10_000 });

    // Wait for all three per-lane prompt rows to surface (watcher + FE poll).
    for (const lane of ['A', 'B', 'C']) {
      await expect(page.locator(`[data-testid="permission-prompt-${lane}"]`))
        .toBeVisible({ timeout: 10_000 });
      await expect(page.locator(`[data-testid="permission-approve-${lane}"]`)).toBeVisible();
      await expect(page.locator(`[data-testid="permission-approve-remember-${lane}"]`)).toBeVisible();
      await expect(page.locator(`[data-testid="permission-deny-${lane}"]`)).toBeVisible();
    }

    // Approve-all control renders with the pending count (3).
    const approveAll = page.locator('[data-testid="permission-approve-all"]');
    await expect(approveAll).toBeVisible();
    await expect(approveAll).toContainText('Approve all (3)');

    // Ensure lane A's row previews the curl command before exercising remember.
    await expect(page.locator('[data-testid="permission-prompt-A"]'))
      .toContainText('curl', { timeout: 10_000 });

    // ===== Deny (lane C): respond fires, prompt clears =====================
    clearPromptBlock(fixtureRoot, 'C'); // stop the watcher re-surfacing C
    let denyStatus: number | null = null;
    await page.route('**/permission_prompts/C/respond', async (route) => {
      const resp = await route.fetch();
      denyStatus = resp.status();
      await route.fulfill({ response: resp });
    });
    await page.locator('[data-testid="permission-deny-C"]').click();
    await expect.poll(() => denyStatus, {
      timeout: 8_000,
      message: 'deny respond endpoint (lane C) must return 202',
    }).toBe(202);
    await expect(page.locator('[data-testid="permission-prompt-C"]'))
      .toHaveCount(0, { timeout: 10_000 });

    // ===== Approve (lane B): respond fires, prompt clears =================
    clearPromptBlock(fixtureRoot, 'B');
    let approveStatus: number | null = null;
    await page.route('**/permission_prompts/B/respond', async (route) => {
      const resp = await route.fetch();
      approveStatus = resp.status();
      await route.fulfill({ response: resp });
    });
    await page.locator('[data-testid="permission-approve-B"]').click();
    await expect.poll(() => approveStatus, {
      timeout: 8_000,
      message: 'approve respond endpoint (lane B) must return 202',
    }).toBe(202);
    await expect(page.locator('[data-testid="permission-prompt-B"]'))
      .toHaveCount(0, { timeout: 10_000 });

    // ===== Approve & remember (lane A): confirm-modal + rule saved =========
    clearPromptBlock(fixtureRoot, 'A');
    let respondStatus: number | null = null;
    let ruleStatus: number | null = null;
    let ruleBody: Record<string, unknown> | null = null;

    await page.route('**/api/v1/permission_prompts/A/respond', async (route) => {
      const resp = await route.fetch();
      respondStatus = resp.status();
      await route.fulfill({ response: resp });
    });
    await page.route('**/api/v1/approval-rules', async (route) => {
      if (route.request().method() === 'POST') {
        const resp = await route.fetch();
        ruleStatus = resp.status();
        try { ruleBody = await resp.json(); } catch { ruleBody = null; }
        await route.fulfill({ response: resp });
      } else {
        await route.continue();
      }
    });

    await page.locator('[data-testid="permission-approve-remember-A"]').click();

    // Confirm modal appears and shows the extracted pattern.
    const modal = page.locator('[data-testid="confirm-modal"]');
    await expect(modal).toBeVisible({ timeout: 5_000 });
    await expect(modal).toContainText('Approve & remember?', { timeout: 3_000 });
    await expect(page.locator('[data-testid="confirm-modal-message"]'))
      .toContainText(expectedPattern, { timeout: 3_000 });

    // Confirm → save rule + approve.
    await page.locator('[data-testid="confirm-modal-confirm"]').click();

    await expect.poll(() => respondStatus, {
      timeout: 8_000,
      message: '/permission_prompts/A/respond (approve_remember) must return 202',
    }).toBe(202);
    await expect.poll(() => ruleStatus, {
      timeout: 8_000,
      message: '/approval-rules POST must return 201',
    }).toBe(201);
    expect((ruleBody as Record<string, unknown> | null)?.pattern ?? '').toBe(expectedPattern);

    // Lane A's prompt clears.
    await expect(page.locator('[data-testid="permission-prompt-A"]'))
      .toHaveCount(0, { timeout: 10_000 });

    // All prompts resolved → panel hides.
    await expect(panel).toBeHidden({ timeout: 10_000 });

    // Tidy up the rule so a later run starts clean.
    await clearAllRules(page);
  });

});
