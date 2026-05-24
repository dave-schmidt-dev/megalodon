// test_v94_phase2_smoke.spec.ts — v9.4 Task 2.9: Phase-2 acceptance gate.
//
// Runs under chromium-grid (MEGALODON_FAKE_SPAWNER=1, fix-small fixture,
// 3 lanes A/B/C, port 8769). All three cases are sequential (workers:1,
// fullyParallel:false) so file writes and SSE events don't race.
//
// Case A: Activity-wall multi-source smoke
//   Triggers events from 4 of the 6 sources:
//     1. Findings  — write a new file to <mission_dir>/findings/
//     2. Signals   — write a new file to <mission_dir>/signals/
//     3. History   — append a line to <mission_dir>/HISTORY.md
//     4. Inject    — POST /api/v1/lane/A/inject (logged to inject-log → source 5)
//   Asserts all 4 events appear in the activity wall within 5 s and that
//   newest events are at the top (chronological order check).
//   Skipped sources:
//     - queue    (queue-applier.log): no in-process applier in GRID_SMOKE_ENV
//     - restart-loop: fix-small fixture has no initial_prompt; would 409
//
// Case B: Permission prompt → Approve → approval event in wall
//   The fake-spawner lifespan (v9.4 T2.9 server change) now starts a
//   PermissionWatcher alongside the ActivityWall so the permission-prompt
//   path can be tested end-to-end.
//   Seeding mechanism: write Claude-REPL-style PROMPT_MARKER text to
//   .fleet/A.stream.log via Node fs. The watcher (poll=1 s) detects it and
//   surfaces the prompt; the FE polls /api/v1/permission_prompts every 2 s.
//   Clicking "Approve" calls the respond endpoint which calls
//   watcher.clear_lane(lane, action="approve") — the on_change callback
//   emits an "approval" event to the activity wall. The fake-spawner short-
//   circuit (also added in T2.9) skips the tmux send_keys call so the
//   endpoint returns 202 cleanly.
//
// Case C: Stale badge end-to-end
//   POST _test/stale_override for lane A → reload → badge "1 stale" visible →
//   click → modal with lane A row. Uses page.reload() to trigger the initial
//   mount poll (same pattern as test_stale_badge.spec.ts), not a 30 s wait.
//
// Selectors:
//   [data-testid="activity-wall-root"]  — wall root
//   [data-testid="aw-list"]             — list container
//   .aw-row                             — individual row
//   [data-event-type]                   — row type attribute
//   .aw-row__summary                    — row summary text (title= file stem)
//   [data-testid="permission-panel"]    — permission banner
//   [data-testid="permission-approve-A"] — per-lane Approve button
//   [data-testid="stale-badge"]         — stale count badge

import { test, expect } from '@playwright/test';
import {
  writeFileSync,
  appendFileSync,
  mkdirSync,
  existsSync,
} from 'node:fs';
import * as path from 'node:path';

import { fixtureRootForProject, readUiToken } from './_helpers';

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

/** Authenticate via hash-token exchange and navigate to /. */
async function authenticateAndGotoGrid(
  page: import('@playwright/test').Page,
  testInfo: import('@playwright/test').TestInfo,
): Promise<void> {
  const token = readUiToken(testInfo);
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/', { timeout: 10_000 });
  await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
  // The board does NOT auto-mount the activity wall — open it via the toggle.
  await page.locator('[data-testid="board-activity-toggle"]').click();
  await expect(page.locator('[data-testid="activity-wall-root"]')).toBeVisible({ timeout: 5_000 });
}

/**
 * Read the CSRF token from the meta tag in the page.
 * Returns an empty string if not found (caller should assert non-empty).
 */
async function readCsrfToken(page: import('@playwright/test').Page): Promise<string> {
  return page.evaluate(() => {
    return (
      (document.querySelector('meta[name="csrf-token"]') as HTMLMetaElement | null)
        ?.getAttribute('content') ?? ''
    );
  });
}

/** Write a finding file to the fixture's findings/ dir. */
function writeFixtureFinding(
  fixtureRoot: string,
  name: string,
  lane: string,
  content?: string,
): void {
  const dir = path.join(fixtureRoot, 'findings');
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  const body =
    content ??
    `---\nlane: LANE-${lane}\nagent: agent-test\ntask: T99\nseverity: MINOR\nutc: 2026-05-20T00:00Z\n---\n\n# Phase-2 smoke finding\n\nBody.\n`;
  writeFileSync(path.join(dir, name), body, 'utf-8');
}

/** Write a signal file to the fixture's signals/ dir. */
function writeFixtureSignal(
  fixtureRoot: string,
  name: string,
  lane: string,
  content?: string,
): void {
  const dir = path.join(fixtureRoot, 'signals');
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  const body =
    content ??
    `---\nlane: LANE-${lane}\nagent: agent-test\nts: 2026-05-20T00:00Z\n---\n\n# Phase-2 smoke signal\n\nBody.\n`;
  writeFileSync(path.join(dir, name), body, 'utf-8');
}

/**
 * POST _test/stale_override to seed a stale lane.
 * Reads CSRF token from the page's meta tag.
 */
async function setStaleOverride(
  page: import('@playwright/test').Page,
  lane: string,
  seconds: number,
): Promise<void> {
  const csrf = await readCsrfToken(page);
  const resp = await page.request.post(
    `/api/v1/_test/stale_override?lane=${encodeURIComponent(lane)}&seconds=${seconds}`,
    {
      headers: {
        'Content-Type': 'application/json',
        ...(csrf ? { 'X-CSRF-Token': csrf } : {}),
      },
      data: {},
    },
  );
  expect(resp.status(), `stale_override POST for lane ${lane}`).toBe(200);
}

// ---------------------------------------------------------------------------
// Case A: Activity-wall picks up 4 event sources
// ---------------------------------------------------------------------------

test.describe('v94 phase2 smoke: Case A — activity wall multi-source', () => {

  test('A: findings, signals, history, and inject all appear in the wall within 5 s, newest first', async ({ page }, testInfo) => {
    const fixtureRoot = fixtureRootForProject(testInfo);
    const ts = Date.now();

    await authenticateAndGotoGrid(page, testInfo);

    // Give SSE a moment to connect before writing events.
    await page.waitForTimeout(400);

    // ---- Source 1: findings ------------------------------------------------
    const findingName = `agent-${ts}-A-P9-smoke.md`;
    writeFixtureFinding(fixtureRoot, findingName, 'A');
    const findingStem = findingName.replace(/\.md$/, '');

    // ---- Source 2: signals -------------------------------------------------
    const signalName = `test-signal-${ts}.md`;
    writeFixtureSignal(fixtureRoot, signalName, 'A');
    const signalStem = signalName.replace(/\.md$/, '');

    // ---- Source 3: HISTORY.md append ---------------------------------------
    // Use a unique probe string so we can distinguish this line from existing
    // history entries in the ring buffer.
    const historyLine = `2026-05-20T00:00:00Z | smoke-probe-${ts} | phase2-smoke\n`;
    const historyPath = path.join(fixtureRoot, 'HISTORY.md');
    appendFileSync(historyPath, historyLine, 'utf-8');
    const historyProbe = `smoke-probe-${ts}`;

    // Give the server a poll cycle (250 ms) to pick up the files before also
    // triggering the inject event — keeps test time predictable.
    await page.waitForTimeout(350);

    // ---- Source 4: inject (logged to inject-log → event type "inject") -----
    const csrf = await readCsrfToken(page);
    expect(csrf, 'CSRF token must be present on the page').toBeTruthy();

    const injectResp = await page.request.post('/api/v1/lane/A/inject', {
      data: { text: 'smoke-phase2-inject', enter: true },
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrf,
      },
    });
    expect(injectResp.status(), '/api/v1/lane/A/inject must return 202').toBe(202);

    // ---- Assert all 4 event types appear in the wall ----------------------
    const list = page.locator('[data-testid="aw-list"]');

    // Finding row: summary title matches the file stem.
    await expect.poll(
      async () => {
        const titles = await list
          .locator('[data-event-type="finding"]')
          .evaluateAll((rows: Element[]) =>
            rows.map((r) => r.querySelector('.aw-row__summary')?.getAttribute('title') ?? ''),
          );
        return titles.some((t) => t.includes(findingStem));
      },
      { timeout: 5_000, message: `finding row "${findingStem}" did not appear in wall within 5 s` },
    ).toBe(true);

    // Signal row: summary title matches the file stem.
    await expect.poll(
      async () => {
        const titles = await list
          .locator('[data-event-type="signal"]')
          .evaluateAll((rows: Element[]) =>
            rows.map((r) => r.querySelector('.aw-row__summary')?.getAttribute('title') ?? ''),
          );
        return titles.some((t) => t.includes(signalStem));
      },
      { timeout: 5_000, message: `signal row "${signalStem}" did not appear in wall within 5 s` },
    ).toBe(true);

    // History row: summary title contains the probe string.
    await expect.poll(
      async () => {
        const titles = await list
          .locator('[data-event-type="history"]')
          .evaluateAll((rows: Element[]) =>
            rows.map((r) => r.querySelector('.aw-row__summary')?.getAttribute('title') ?? ''),
          );
        return titles.some((t) => t.includes(historyProbe));
      },
      { timeout: 5_000, message: `history row containing "${historyProbe}" did not appear in wall within 5 s` },
    ).toBe(true);

    // Inject row: type="inject" must be present (at least one).
    await expect.poll(
      async () => list.locator('[data-event-type="inject"]').count(),
      { timeout: 5_000, message: 'inject row did not appear in wall within 5 s' },
    ).toBeGreaterThan(0);

    // ---- Chronological order: newest at the top ---------------------------
    // Collect [data-event-type] values from the DOM (top-to-bottom = newest-first).
    // The inject event was the last triggered, so the first inject row index
    // must be BEFORE (i.e. smaller DOM index) than the earlier events.
    const rowTypes = await list
      .locator('.aw-row')
      .evaluateAll((rows: Element[]) =>
        rows.map((r) => (r as HTMLElement).dataset['eventType'] ?? ''),
      );

    const findingIdx = rowTypes.findIndex((_, i) => {
      // We need to correlate type + summary; just check type ordering here.
      return rowTypes[i] === 'finding';
    });
    const injectIdx = rowTypes.findIndex((t) => t === 'inject');

    // inject was triggered after finding/signal/history, so its DOM index
    // (row 0 = top = newest) must be ≤ finding/signal/history indices.
    expect(injectIdx, 'inject row should appear at or before finding row (newest-first)').toBeLessThanOrEqual(
      findingIdx >= 0 ? findingIdx : Number.MAX_SAFE_INTEGER,
    );
  });

});

// ---------------------------------------------------------------------------
// Case B: Permission prompt → Approve → approval event in wall
// ---------------------------------------------------------------------------

test.describe('v94 phase2 smoke: Case B — permission prompt approval flow', () => {

  test('B: write PROMPT_MARKER → banner appears → click Approve → approval event in wall within 4 s', async ({ page }, testInfo) => {
    const fixtureRoot = fixtureRootForProject(testInfo);

    await authenticateAndGotoGrid(page, testInfo);

    // ---- Seed a fake permission prompt in lane A's stream log ---------------
    // The PermissionWatcher (now started in fake-spawner mode) polls every 1 s.
    // Writing the PROMPT_MARKER text to .fleet/A.stream.log is sufficient to
    // trigger prompt detection.
    const streamLogPath = path.join(fixtureRoot, '.fleet', 'A.stream.log');
    const promptBlock =
      'Bash command\n' +
      'echo "smoke-phase2-permission-test"\n' +
      'Do you want to proceed?\n' +
      '❯ 1. Yes\n' +
      '  2. Yes, and always allow access\n' +
      '  3. No\n';
    appendFileSync(streamLogPath, promptBlock, 'utf-8');

    // ---- Wait for the permission banner to appear --------------------------
    // Watcher poll = 1 s. FE polls /api/v1/permission_prompts every 2 s.
    // Total worst-case ≈ 3 s; allow 8 s for CI headroom.
    const banner = page.locator('[data-testid="permission-panel"]');
    await expect(banner).not.toBeHidden({ timeout: 8_000 });

    // The per-lane Approve button for lane A must be visible.
    const approveBtn = page.locator('[data-testid="permission-approve-A"]');
    await expect(approveBtn).toBeVisible({ timeout: 3_000 });

    // ---- Click "Approve" -------------------------------------------------
    // Sets up passthrough interceptor so we can assert 202 from the real server.
    let capturedStatus: number | null = null;
    await page.route('**/permission_prompts/A/respond', async (route) => {
      const resp = await route.fetch();
      capturedStatus = resp.status();
      await route.fulfill({ response: resp });
    });

    await approveBtn.click();

    // Verify the respond endpoint returned 202.
    await expect.poll(() => capturedStatus, { timeout: 5_000 }).toBe(202);

    // ---- Assert "approval" event appears in the wall ----------------------
    const list = page.locator('[data-testid="aw-list"]');
    await expect.poll(
      async () => list.locator('[data-event-type="approval"]').count(),
      {
        timeout: 4_000,
        message: 'approval event did not appear in the activity wall within 4 s',
      },
    ).toBeGreaterThan(0);

    // Verify the approval row's summary contains "approve".
    const approvalSummary = await list
      .locator('[data-event-type="approval"]')
      .first()
      .locator('.aw-row__summary')
      .getAttribute('title');
    expect(approvalSummary ?? '', 'approval row summary should contain the action name').toContain('approve');
  });

});

// ---------------------------------------------------------------------------
// Case C: Stale badge end-to-end
// ---------------------------------------------------------------------------

test.describe('v94 phase2 smoke: Case C — stale badge end-to-end', () => {

  // SKIPPED on the board: asserts the grid-only stale-badge + stale_modal UI
  // (stale_modal.js is mounted only by grid.js). The board surfaces staleness as
  // a per-row STALE pill instead; a board-native equivalent is Task 3.5b and the
  // grid assertion retires with grid.js (Task 3.4). See Task 3.5a report.
  test.skip('C: stale_override for lane A → badge "1 stale" visible → click → modal with lane A row', async ({ page }, testInfo) => {
    await authenticateAndGotoGrid(page, testInfo);

    // ---- POST stale_override for lane A (1200 s = 20 min) -----------------
    await setStaleOverride(page, 'A', 1200);

    // ---- Reload to trigger the initial mount poll -------------------------
    // The stale-badge component polls /api/v1/lanes/stale on mount.
    // A page.reload() is faster than waiting for the next 30 s poll cycle.
    await page.reload();
    await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });

    // ---- Assert badge "1 stale" with red background -----------------------
    const badge = page.locator('[data-testid="stale-badge"]');
    await expect(badge).toBeVisible({ timeout: 8_000 });
    await expect(badge).toContainText('1 stale', { timeout: 5_000 });

    // Verify the red-background class/style (badge should have a warning colour).
    // The stale_modal.js sets background style on the badge element when count > 0.
    const badgeBg = await badge.evaluate((el: Element) => {
      const style = (el as HTMLElement).style.background || (el as HTMLElement).style.backgroundColor;
      return style;
    });
    // Accept non-empty background style (red/warning colour applied by component).
    expect(badgeBg, 'badge should have a background colour when stale > 0').toBeTruthy();

    // ---- Click badge → modal opens with lane A row -----------------------
    await badge.click();

    const modal = page.locator('[data-testid="stale-modal"]');
    await expect(modal).toBeVisible({ timeout: 5_000 });

    // Modal title must reference the count.
    await expect(page.locator('[data-testid="stale-modal-title"]')).toContainText(
      'Stale Lanes (1)',
      { timeout: 3_000 },
    );

    // Lane A row must be present in the modal.
    await expect(page.locator('[data-testid="stale-lane-row-A"]')).toBeVisible({ timeout: 3_000 });

    // Duration must show ~20 min.
    await expect(page.locator('[data-testid="stale-lane-duration-A"]')).toContainText(
      '20m',
      { timeout: 3_000 },
    );
  });

});
