// E2E tests for orchestrator mutation endpoints.
// Test IDs T-A-CH-e2e, T-A-RC-e2e, T-A-SG-e2e from P1-E §3.
//
// These tests exercise the server-side mutation APIs (challenge, reclaim,
// signal, phase-flip, inject-task, mission-status) directly via
// page.request.post after authenticating and enabling control mode.
//
// The UI action-forms that originally drove these mutations were removed in
// the v9.4 dashboard rebuild (commit b1c867d). The server endpoints are
// unchanged and still fully exercise the round-trip mutation path — the
// MEGALODON_INPROCESS_APPLIER drain loop is in effect (chromium-mutations
// project env), so 202-queued mutations appear in STATUS.md / TASKS.md
// within ~400ms of the POST.
//
// Runs under chromium-mutations (fix-medium fixture, MEGALODON_INPROCESS_APPLIER=1).

import { test, expect } from '@playwright/test';
import { readUiToken, setControlMode } from './_helpers';

// ---------------------------------------------------------------------------
// Shared setup helpers
// ---------------------------------------------------------------------------

async function readCsrf(page: import('@playwright/test').Page): Promise<string> {
  return page.evaluate(
    () =>
      (document.querySelector('meta[name="csrf-token"]') as HTMLMetaElement | null)
        ?.getAttribute('content') ?? '',
  );
}

/**
 * Poll /api/v1/queue/<rid> until status === 'applied' or timeout.
 * The in-process applier drains every ~200ms so we allow 10s total.
 */
async function waitForApplied(
  page: import('@playwright/test').Page,
  requestId: string,
  { timeout = 10_000 }: { timeout?: number } = {},
): Promise<void> {
  await expect
    .poll(async () => {
      const r = await page.request.get(`/api/v1/queue/${requestId}`);
      if (!r.ok()) return 'http-error';
      const j = await r.json();
      return j.status as string;
    }, { timeout })
    .toBe('applied');
}

test.describe('Orchestrator actions', () => {

  test.beforeEach(async ({ page }, testInfo) => {
    // Authenticate via the hash-token exchange so the session cookie is set
    // and page.request.* calls succeed.
    const token = readUiToken(testInfo);
    await page.goto(`/#t=${token}`);
    await expect(page).toHaveURL('/', { timeout: 10_000 });
    await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });

    // Flip the SERVER-SIDE control-mode flag ON so destructive endpoints
    // return 202 instead of 403.
    await setControlMode(page, true);
  });

  test('T-A-CH-e2e — inject CHALLENGE via API', async ({ page }) => {
    // Fetch the list of findings to pick a real finding filename.
    const findingsResp = await page.request.get('/api/v1/findings');
    const findings = await findingsResp.json();
    const findingFilenames: string[] = findings.findings?.map((f: { filename: string }) => f.filename) ?? [];
    expect(findingFilenames.length, 'need at least one finding in fix-medium').toBeGreaterThan(0);
    const findingFilename = findingFilenames[0];

    const csrf = await readCsrf(page);
    const resp = await page.request.post('/api/v1/challenge', {
      headers: { 'Content-Type': 'application/json', ...(csrf ? { 'X-CSRF-Token': csrf } : {}) },
      data: {
        finding_filename: findingFilename,
        description: `E2E T-A-CH-e2e: challenge on ${findingFilename}`,
      },
    });
    expect(resp.status(), 'POST /api/v1/challenge').toBe(202);
    const body = await resp.json();
    await waitForApplied(page, body.request_id);

    // Assertion: TASKS view now shows a CHALLENGE-* row.
    await page.goto('/tasks');
    const challenge = page.locator('[data-testid^="task-card-CHALLENGE-"]');
    await expect(challenge.first()).toBeVisible({ timeout: 5_000 });
  });

  test('T-A-RC-e2e — reclaim stale row via API', async ({ page }) => {
    // fix-medium ships AUDIT in working: P4-A state (stale candidate).
    // Reclaiming it should write STALE-RECLAIMED to the STATUS row.
    const csrf = await readCsrf(page);
    const resp = await page.request.post('/api/v1/reclaim', {
      headers: { 'Content-Type': 'application/json', ...(csrf ? { 'X-CSRF-Token': csrf } : {}) },
      data: { lane: 'AUDIT' },
    });
    // 202 = queued (lane was working); 204 = no-op (lane already idle).
    expect([202, 204], 'POST /api/v1/reclaim expects 202 or 204').toContain(resp.status());

    if (resp.status() === 202) {
      const body = await resp.json();
      await waitForApplied(page, body.request_id);

      // Verify the reclaim was applied: AUDIT's state should now be stale-reclaimed.
      const statusResp = await page.request.get('/api/v1/status');
      const statusJson = await statusResp.json();
      const auditLane = (statusJson.lanes as Array<{ lane: string; state: string }>)
        .find((r) => r.lane.toUpperCase() === 'AUDIT');
      expect(auditLane, 'AUDIT lane must be present in status').toBeTruthy();
      // Server returns STALE-RECLAIMED (uppercase); compare case-insensitively.
      expect((auditLane?.state ?? '').toUpperCase(), 'AUDIT state after reclaim').toContain('STALE-RECLAIMED');
    } else {
      // Already idle: just verify the lane exists.
      const statusResp = await page.request.get('/api/v1/status');
      const statusJson = await statusResp.json();
      const auditLane = (statusJson.lanes as Array<{ lane: string; state: string }>)
        .find((r) => r.lane.toUpperCase() === 'AUDIT');
      expect(auditLane, 'AUDIT lane must be present in status').toBeTruthy();
    }
  });

  test('T-A-SG-e2e — post SIGNAL with evidence requirement via API', async ({ page }) => {
    // Per RULE 4: evidence (cite) must be non-empty; server returns 422 without it.
    const csrf = await readCsrf(page);
    const emptyEvidence = await page.request.post('/api/v1/signal', {
      headers: { 'Content-Type': 'application/json', ...(csrf ? { 'X-CSRF-Token': csrf } : {}) },
      data: { to_lane: 'TEST', claim: 'please verify finding X', evidence: '' },
    });
    expect(emptyEvidence.status(), 'empty evidence must be rejected (422)').toBe(422);

    // Now provide cite and re-submit.
    const resp = await page.request.post('/api/v1/signal', {
      headers: { 'Content-Type': 'application/json', ...(csrf ? { 'X-CSRF-Token': csrf } : {}) },
      data: { to_lane: 'TEST', claim: 'please verify finding X', evidence: 'findings/X.md:42' },
    });
    expect(resp.status(), 'POST /api/v1/signal with evidence').toBe(202);
    const body = await resp.json();
    await waitForApplied(page, body.request_id);

    // The signal lands in the STATUS.md notes row for TEST. The board filters
    // out SIG-notes from the display (isSignalNote), so verify via the /status
    // API instead of the board row text.
    const statusResp = await page.request.get('/api/v1/status');
    const statusJson = await statusResp.json();
    const testLane = (statusJson.lanes as Array<{ lane: string; notes: string }>)
      .find((r) => r.lane.toUpperCase() === 'TEST');
    expect(testLane, 'TEST lane must be present in status').toBeTruthy();
    expect(testLane?.notes ?? '', 'signal text must be in TEST notes').toContain('please verify finding X');
  });

  test('T-R11-a-e2e — flip Mission status to DRAINING via API', async ({ page }) => {
    // POST /api/v1/mission-status — the endpoint writes to README.md
    // (pattern: **Current: <STATUS>**) and returns {ok, status}.
    // NOTE: GET /api/v1/state reads MISSION.md (**Status:** pattern), so the
    // mission-page badge does NOT reflect this POST — asserting the badge would
    // be a contract mismatch. Pin the server response body instead.
    const csrf = await readCsrf(page);
    const resp = await page.request.post('/api/v1/mission-status', {
      headers: { 'Content-Type': 'application/json', ...(csrf ? { 'X-CSRF-Token': csrf } : {}) },
      data: { status: 'DRAINING' },
    });
    expect(resp.status(), 'POST /api/v1/mission-status → 200').toBe(200);
    const body = await resp.json();
    expect(body.ok, 'response body.ok').toBe(true);
    expect(body.status, 'response body.status').toBe('DRAINING');
  });

  test('T-A-IT-e2e — inject TASK via API', async ({ page }) => {
    // POST /api/v1/inject-task {task_text, section} per api-contract.md:58.
    const csrf = await readCsrf(page);
    const resp = await page.request.post('/api/v1/inject-task', {
      headers: { 'Content-Type': 'application/json', ...(csrf ? { 'X-CSRF-Token': csrf } : {}) },
      data: {
        task_text: '- [ ] [LANE-A] `TEST-INJECT-1` — synthetic e2e task',
        section: 'CHALLENGE TASKS',
      },
    });
    expect(resp.status(), 'POST /api/v1/inject-task').toBe(202);
    const body = await resp.json();
    await waitForApplied(page, body.request_id);

    // Navigate to /tasks; verify the injected row appears.
    await page.goto('/tasks');
    const injected = page.locator('[data-testid^="task-card-TEST-INJECT-1"]');
    await expect(injected.first()).toBeVisible({ timeout: 5_000 });
  });

  test('T-A-MS-e2e — set Mission Status via API', async ({ page }) => {
    // POST /api/v1/mission-status {status} per api-contract.md:57.
    // The endpoint writes to README.md (not MISSION.md), so the /mission page
    // badge (which reads MISSION.md via /api/v1/state) will not reflect this
    // change. Assert the server response body directly.
    const csrf = await readCsrf(page);
    const resp = await page.request.post('/api/v1/mission-status', {
      headers: { 'Content-Type': 'application/json', ...(csrf ? { 'X-CSRF-Token': csrf } : {}) },
      data: { status: 'DRAINING' },
    });
    expect(resp.status(), 'POST /api/v1/mission-status').toBe(200);
    const body = await resp.json();
    expect(body.ok, 'response body.ok').toBe(true);
    expect(body.status, 'response body.status').toBe('DRAINING');
  });

});
