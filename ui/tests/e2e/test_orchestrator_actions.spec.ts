// E2E tests for orchestrator actions on the /mission page.
// Test IDs T-A-CH-e2e, T-A-RC-e2e, T-A-SG-e2e from P1-E §3.

import { test, expect } from '@playwright/test';

test.describe('Orchestrator actions', () => {

  test.beforeEach(async ({ page }) => {
    await page.goto('/mission');
    // Enter control mode per P2.5-E response to C5 (FRONTEND auth model).
    // Recommended: localStorage toggle (option a).
    await page.evaluate(() => localStorage.setItem('controlMode', 'true'));
    await page.reload();
  });

  test('T-A-CH-e2e — inject CHALLENGE via action panel', async ({ page }) => {
    await page.locator('[data-testid="action-inject-challenge"]').click();
    const fidPicker = page.locator('[data-testid="challenge-finding-picker"]');
    await expect(fidPicker).toBeVisible();
    // Pick the first finding in the picker.
    await fidPicker.locator('option').nth(1).click();
    await page.locator('[data-testid="submit-challenge"]').click();
    // Assertion: TASKS view now shows a CHALLENGE-* row.
    await page.goto('/tasks');
    const challenge = page.locator('[data-testid^="task-card-CHALLENGE-"]');
    await expect(challenge.first()).toBeVisible({ timeout: 5_000 });
  });

  test('T-A-RC-e2e — reclaim stale row via lane action', async ({ page }) => {
    // fix-medium ships 2 stale rows; UI should expose a reclaim affordance.
    await page.goto('/');
    const reclaim = page.locator('[data-testid="action-reclaim-AUDIT"]');
    await reclaim.click();
    // Confirm dialog (if any) — keep simple assumption.
    await page.locator('[data-testid="confirm-reclaim"]').click();
    // STATUS row should now show STALE-RECLAIMED.
    const row = page.locator('[data-testid="lane-row-AUDIT"]');
    await expect(row).toContainText('STALE-RECLAIMED', { timeout: 5_000 });
  });

  test('T-A-SG-e2e — post SIGNAL with evidence requirement', async ({ page }) => {
    await page.locator('[data-testid="action-post-signal"]').click();
    await page.locator('[data-testid="signal-from"]').selectOption('ORCH');
    await page.locator('[data-testid="signal-to"]').selectOption('TEST');
    await page.locator('[data-testid="signal-text"]').fill('please verify finding X');
    // Per RULE 4: cite must be non-empty; form should reject empty cite.
    const submit = page.locator('[data-testid="submit-signal"]');
    await submit.click();
    // Expect error or rejection.
    const err = page.locator('[data-testid="signal-error"]');
    await expect(err).toBeVisible();
    // Now provide cite and re-submit.
    await page.locator('[data-testid="signal-cite"]').fill('findings/X.md:42');
    await submit.click();
    // STATUS row for TEST should now contain the signal text.
    await page.goto('/');
    const row = page.locator('[data-testid="lane-row-TEST"]');
    await expect(row).toContainText('please verify finding X');
  });

  test('T-R11-a-e2e — flip Mission status via UI', async ({ page }) => {
    await page.locator('[data-testid="action-flip-mission"]').click();
    await page.locator('[data-testid="flip-target-DRAINING"]').click();
    await page.locator('[data-testid="confirm-flip"]').click();
    // Mission page should reflect the new phase.
    const phase = page.locator('[data-testid="current-phase"]');
    await expect(phase).toContainText('DRAINING', { timeout: 5_000 });
  });

  // T-A-IT-e2e and T-A-MS-e2e added per MISSION exit-criterion #3 (all 6 POST
  // mutations covered) by TEST P3-E Stage 4 (agent-43d9 @ 2026-05-16T19:49Z).
  // FE P3-D shipped both forms per their STATUS:12 @19:19Z; data-testid names
  // follow my P2-E-to-D §C1 bridge convention (action-X/submit-X pattern).

  test('T-A-IT-e2e — inject TASK via action panel', async ({ page }) => {
    // POST /api/v1/inject-task {task_text, section} per api-contract.md:58.
    await page.locator('[data-testid="action-inject-task"]').click();
    await page
      .locator('[data-testid="inject-task-text"]')
      .fill('[ ] [LANE-A] `TEST-INJECT-1` — synthetic e2e task');
    await page.locator('[data-testid="inject-task-section"]').selectOption('CHALLENGE TASKS');
    await page.locator('[data-testid="submit-inject-task"]').click();
    // Navigate to /tasks; verify the injected row appears.
    await page.goto('/tasks');
    const injected = page.locator('[data-testid^="task-card-TEST-INJECT-1"]');
    await expect(injected.first()).toBeVisible({ timeout: 5_000 });
  });

  test('T-A-MS-e2e — set Mission Status via dedicated form', async ({ page }) => {
    // Distinct from T-R11-a-e2e (which tests phase-flip {from,to,reason}).
    // This tests POST /api/v1/mission-status {status} per api-contract.md:57.
    await page.locator('[data-testid="action-mission-status"]').click();
    await page.locator('[data-testid="mission-status-value"]').selectOption('DRAINING');
    await page.locator('[data-testid="submit-mission-status"]').click();
    // Mission badge should reflect the new status.
    const badge = page.locator('[data-testid="mission-status-badge"]');
    await expect(badge).toContainText('DRAINING', { timeout: 5_000 });
  });

});
