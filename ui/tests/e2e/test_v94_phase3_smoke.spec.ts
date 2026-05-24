// test_v94_phase3_smoke.spec.ts — v9.4 Task 3.10: Phase-3 acceptance gate.
//
// Runs under chromium-grid (MEGALODON_FAKE_SPAWNER=1, fix-small fixture,
// 3 lanes A/B/C, port 8769). Sequential (workers:1, fullyParallel:false).
//
// End-to-end flow under test:
//   1. Trigger fake permission prompt for lane A (curl command in prompt block).
//   2. Wait for permission banner to appear (≤10 s).
//   3. Click "Approve & remember" in the banner.
//   4. Wait for confirm modal (showConfirmModal from T3.5).
//   5. Assert modal text contains the extracted pattern.
//   6. Click "Save rule and approve" (confirm button).
//   7. Assert both POSTs fired: /api/v1/permission_prompts/A/respond (approve_remember)
//      AND /api/v1/approval-rules (the pattern).
//   8. Navigate to /approval-rules — assert saved rule appears in the table.
//   9. Respawn assertion: read .fleet/approval-rules.json directly and assert the
//      pattern is present.
//      RATIONALE: FakeFleetSpawner does not actually run the Claude CLI, so we
//      cannot assert that spawn-argv was updated in a live process. Instead we
//      verify the persistence file (.fleet/approval-rules.json) that spawn.py
//      reads via _load_approval_rule_patterns() at startup.  The unit tests in
//      test_spawn_reads_approval_rules.py already prove that the file is consumed
//      correctly; this E2E test closes the loop by confirming the file is written
//      by the full browser→server→disk flow. This choice avoids adding new BE
//      endpoints and stays within the fake-spawner constraint.
//  10. Idempotency assertion (not fully feasible): see GAP note below.
//
// GAP — step 10 (idempotency / auto-suppress):
//   In MEGALODON_FAKE_SPAWNER=1 mode the Claude CLI is never launched, so
//   there is no real --allowedTools flag enforcement. A duplicate permission
//   prompt written to A.stream.log will still be picked up by the
//   PermissionWatcher and surface a banner — there is no in-process short-
//   circuit that suppresses the prompt based on the saved rule.  The
//   suppression logic lives entirely in the Claude CLI subprocess (which reads
//   --allowedTools at startup). Therefore step 10 is not asserted here.
//   Coverage for the "rule applied to argv" path is provided by the unit tests
//   in test_spawn_reads_approval_rules.py.

import { test, expect } from '@playwright/test';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import * as path from 'node:path';

import { fixtureRootForProject, readUiToken } from './_helpers';

// ---------------------------------------------------------------------------
// Helpers (shared with phase-2 smoke / approval_rules specs)
// ---------------------------------------------------------------------------

async function authenticateAndGotoGrid(
  page: import('@playwright/test').Page,
  testInfo: import('@playwright/test').TestInfo,
): Promise<void> {
  const token = readUiToken(testInfo);
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/', { timeout: 10_000 });
  await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
}

async function readCsrfToken(page: import('@playwright/test').Page): Promise<string> {
  return page.evaluate(() => {
    return (
      (document.querySelector('meta[name="csrf-token"]') as HTMLMetaElement | null)
        ?.getAttribute('content') ?? ''
    );
  });
}

async function navigateToApprovalRules(
  page: import('@playwright/test').Page,
): Promise<void> {
  await page.evaluate(() => {
    history.pushState({}, '', '/approval-rules');
    window.dispatchEvent(new PopStateEvent('popstate', { state: {} }));
  });
  await expect(page.locator('[data-testid="approval-rules-page"]')).toBeVisible({ timeout: 8_000 });
}

/** Clean up any existing approval rules so each test starts with a blank slate. */
async function clearAllRules(page: import('@playwright/test').Page): Promise<void> {
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

/**
 * Write a Claude-REPL permission-prompt block to A.stream.log.
 * The PermissionWatcher (poll=1 s) detects "Do you want to proceed?" and
 * surfaces a prompt whose command_preview is "[Bash command] <cmd>".
 *
 * This TRUNCATES the log (writeFileSync, not append) so the watcher's next
 * poll sees ONLY this block. The phase-2 smoke (Case B) appends its own prompt
 * block to the SAME lane-A log earlier in the chromium-board serial run; the
 * watcher re-detects that lingering block every poll, so an append here would
 * leave the OLDER (echo) prompt at the head of the banner and this test would
 * approve the wrong pattern. Truncating gives this test a clean lane-A prompt.
 */
function writePromptBlock(fixtureRoot: string, cmd: string): void {
  const fleetDir = path.join(fixtureRoot, '.fleet');
  if (!existsSync(fleetDir)) mkdirSync(fleetDir, { recursive: true });
  const streamLogPath = path.join(fleetDir, 'A.stream.log');
  const block =
    'Bash command\n' +
    cmd + '\n' +
    'Do you want to proceed?\n' +
    '❯ 1. Yes\n' +
    '  2. Yes, and always allow access\n' +
    '  3. No\n';
  writeFileSync(streamLogPath, block, 'utf-8');
}

// ---------------------------------------------------------------------------
// Phase-3 smoke: Approve&remember persists across respawn
// ---------------------------------------------------------------------------

test.describe('v94 phase3 smoke: Approve&remember end-to-end persistence', () => {

  test('phase3 smoke: approve&remember flow → rule persists in .fleet/approval-rules.json', async ({ page }, testInfo) => {
    const fixtureRoot = fixtureRootForProject(testInfo);

    // ---- Pre-condition: start with empty approval-rules list ----------------
    await authenticateAndGotoGrid(page, testInfo);
    await clearAllRules(page);

    // ---- Step 1: Trigger fake permission prompt for lane A ------------------
    // Use a curl command so the extracted pattern is predictable and
    // recognizable. extract_pattern("curl -s http://127.0.0.1:8769/smoke-test")
    // → Bash(curl -s http://127.0.0.1:8769/*)
    const smokeCmd = 'curl -s http://127.0.0.1:8769/smoke-test';
    const expectedPattern = 'Bash(curl -s http://127.0.0.1:8769/*)';
    writePromptBlock(fixtureRoot, smokeCmd);

    // ---- Step 2: Wait for permission banner to appear (≤10 s) ---------------
    // Watcher poll = 1 s; FE polls /api/v1/permission_prompts every 2 s.
    // Worst-case ≈ 3 s; 10 s for CI headroom.
    const banner = page.locator('[data-testid="permission-panel"]');
    await expect(banner).not.toBeHidden({ timeout: 10_000 });

    // The per-lane "Approve & remember" button for lane A must be visible.
    const approveRememberBtn = page.locator('[data-testid="permission-approve-remember-A"]');
    await expect(approveRememberBtn).toBeVisible({ timeout: 5_000 });

    // Ensure the watcher has re-synced to the truncated log (our curl block) so
    // we don't approve a lingering prompt from an earlier spec. Poll the API
    // until lane A's pending prompt previews the curl command we just wrote.
    await expect.poll(async () => {
      const r = await page.request.get('/api/v1/permission_prompts');
      if (!r.ok()) return '';
      const j = await r.json();
      const promptA = (j.prompts ?? []).find((p: { lane: string }) => p.lane === 'A');
      return promptA?.command ?? '';
    }, { timeout: 10_000, message: 'lane A prompt did not re-sync to the curl command' }).toContain('curl');

    // Also wait for the banner's per-lane row to render the curl command before
    // clicking — the banner polls every 2 s, so its DOM can lag the API by one
    // cycle and still show an earlier spec's prompt for lane A.
    await expect(page.locator('[data-testid="permission-prompt-A"]'))
      .toContainText('curl', { timeout: 8_000 });

    // ---- Steps 7a/7b: Set up route interceptors before clicking -------------
    // We intercept the two POSTs in passthrough mode so the real server still
    // processes them (no mocking), and we can assert they were called.
    let respondStatus: number | null = null;
    let respondBody: Record<string, unknown> | null = null;
    let ruleStatus: number | null = null;
    let ruleBody: Record<string, unknown> | null = null;

    await page.route('**/api/v1/permission_prompts/A/respond', async (route) => {
      const resp = await route.fetch();
      respondStatus = resp.status();
      try { respondBody = await resp.json(); } catch { respondBody = null; }
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

    // ---- Step 3: Click "Approve & remember" ---------------------------------
    await approveRememberBtn.click();

    // ---- Step 4: Wait for confirm modal to appear ---------------------------
    const modal = page.locator('[data-testid="confirm-modal"]');
    await expect(modal).toBeVisible({ timeout: 5_000 });

    // ---- Step 5: Assert modal text contains the extracted pattern -----------
    // grid.js renders: "Pattern: <pattern>\n\nThis pattern will be saved..."
    // The confirm-modal-message element contains this text.
    await expect(modal).toContainText('Approve & remember?', { timeout: 3_000 });
    const modalMsg = page.locator('[data-testid="confirm-modal-message"]');
    await expect(modalMsg).toContainText(expectedPattern, { timeout: 3_000 });

    // ---- Step 6: Click "Save rule and approve" (confirm button) -------------
    await page.locator('[data-testid="confirm-modal-confirm"]').click();

    // ---- Step 7: Assert both POSTs fired ------------------------------------
    // 7a: /api/v1/permission_prompts/A/respond with approve_remember → 202
    await expect.poll(() => respondStatus, {
      timeout: 8_000,
      message: '/api/v1/permission_prompts/A/respond must return 202',
    }).toBe(202);

    // 7b: /api/v1/approval-rules POST with the pattern → 201
    await expect.poll(() => ruleStatus, {
      timeout: 8_000,
      message: '/api/v1/approval-rules POST must return 201',
    }).toBe(201);

    // Confirm the rule body echoes our expected pattern.
    expect((ruleBody as Record<string, unknown> | null)?.pattern ?? '').toBe(expectedPattern);

    // ---- Step 8: Navigate to /approval-rules — rule appears in table --------
    await navigateToApprovalRules(page);
    // CSS.escape is a browser-only global; escape the pattern for use in a
    // CSS attribute selector by replacing special chars that would break it.
    const escapedPattern = expectedPattern.replace(/["\\\n]/g, '\\$&');
    await expect(
      page.locator(`[data-pattern="${escapedPattern}"]`),
    ).toBeVisible({ timeout: 5_000 });

    // ---- Step 9: Respawn assertion — .fleet/approval-rules.json has pattern -
    // FakeFleetSpawner does not run the Claude CLI, so we cannot assert
    // spawn-argv in a live process. Instead we verify the persistence layer:
    // read .fleet/approval-rules.json directly from the fixture tmpdir and
    // confirm the pattern is present.  spawn._load_approval_rule_patterns()
    // reads this same file at startup — its correctness is covered by
    // test_spawn_reads_approval_rules.py (T3.3 unit tests).
    const rulesFilePath = path.join(fixtureRoot, '.fleet', 'approval-rules.json');
    const rulesFileExists = existsSync(rulesFilePath);
    expect(rulesFileExists, `.fleet/approval-rules.json must exist after Approve&remember`).toBe(true);

    if (rulesFileExists) {
      const raw = readFileSync(rulesFilePath, 'utf-8');
      let parsedRules: Array<{ pattern: string }> = [];
      try {
        parsedRules = JSON.parse(raw);
      } catch {
        throw new Error(`.fleet/approval-rules.json is not valid JSON: ${raw.slice(0, 200)}`);
      }
      const patternInFile = parsedRules.some((r) => r.pattern === expectedPattern);
      expect(patternInFile, `Pattern "${expectedPattern}" must be present in .fleet/approval-rules.json`).toBe(true);
    }

    // ---- Step 10: Idempotency / prompt-suppression — NOT ASSERTED (documented gap) ----
    // In MEGALODON_FAKE_SPAWNER=1 mode the Claude CLI is never launched, so
    // there is no real --allowedTools enforcement in the running process.
    // Writing an identical prompt block to A.stream.log would still surface
    // a permission banner because the PermissionWatcher only checks the stream
    // log, not the approval-rules list. Auto-suppression is a Claude CLI
    // behaviour driven by the --allowedTools flag at spawn time. Since no
    // real spawn occurs, this assertion is not feasible in fake-spawner mode.
    // Coverage: test_spawn_reads_approval_rules.py proves the rule is merged
    // into --allowedTools argv when FleetSpawner starts a real lane.
    test.info().annotations.push({
      type: 'note',
      description:
        'Step 10 (idempotency) skipped: FakeFleetSpawner cannot enforce --allowedTools. ' +
        'Unit coverage: test_spawn_reads_approval_rules.py.',
    });
  });

});
