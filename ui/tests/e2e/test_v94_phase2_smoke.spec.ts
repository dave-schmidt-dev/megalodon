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
// Case B: Governor audit-log event → governor event in wall
//   The backend ActivityWall (Task 3.2) tails today's
//   .fleet/governor-log-<UTC-date>.jsonl and emits one SSE event of
//   type:"governor" per JSON line. Seeding mechanism (mirrors Case A): write
//   a JSON line to that log via Node fs; the tailer (250 ms poll) picks it up
//   and the FE renders a [data-event-type="governor"] row.
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
//   [data-event-type="governor"]        — governor audit-log row
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

/**
 * Dismiss every currently-rendered alert banner so its fixed-position stack
 * stops intercepting clicks on board controls (e.g. the activity toggle).
 * No-op when no banners are present. Idempotent.
 */
async function dismissAlertBanners(
  page: import('@playwright/test').Page,
): Promise<void> {
  const dismissButtons = page.locator('[data-testid^="alert-dismiss-"]');
  // The stack can hold several banners; dismiss until none remain (bounded so a
  // re-firing alert can never loop forever).
  for (let i = 0; i < 10 && (await dismissButtons.count()) > 0; i++) {
    await dismissButtons.first().click();
    await page.waitForTimeout(50);
  }
}

/** Authenticate via hash-token exchange and navigate to /. */
async function authenticateAndGotoGrid(
  page: import('@playwright/test').Page,
  testInfo: import('@playwright/test').TestInfo,
): Promise<void> {
  const token = readUiToken(testInfo);
  // Activity wall now auto-opens on mount when no preference is stored. This
  // helper drives the open TOGGLE explicitly, so pin the preference CLOSED
  // before the SPA boots; the toggle-to-open below then behaves as written.
  await page.addInitScript(() => {
    try { localStorage.setItem('megalodon.activityWall.open', '0'); } catch (_) { /* ignore */ }
  });
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/', { timeout: 10_000 });
  await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
  // The chromium-board project runs every board spec sequentially against ONE
  // shared server (workers:1), so STATUS-STALE alert banners raised by earlier
  // specs can still be on screen. The banner stack is now an IN-FLOW element
  // (front-door fix) that no longer overlaps the activity toggle, but dismissing
  // any present banners first keeps the page tidy and is harmless.
  await dismissAlertBanners(page);
  // Open the activity wall via the toggle (default-open is pinned off above).
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
// Case B: Governor audit-log line → governor event in wall
// ---------------------------------------------------------------------------

test.describe('v94 phase2 smoke: Case B — governor audit-log activity', () => {

  test('B: write a governor-log JSON line → type:"governor" event appears in the wall within 5 s', async ({ page }, testInfo) => {
    const fixtureRoot = fixtureRootForProject(testInfo);

    await authenticateAndGotoGrid(page, testInfo);

    // Give SSE a moment to connect (and the governor-log tailer to start)
    // before writing the log line — mirrors Case A's 400 ms settle.
    await page.waitForTimeout(400);

    // ---- Seed a governor audit-log line ------------------------------------
    // The backend ActivityWall tails .fleet/governor-log-<UTC today>.jsonl
    // (250 ms poll) and emits one type:"governor" SSE event per JSON line.
    // The log file does not exist in the fixture at startup, so the tailer
    // reads it from the beginning once created — the line is not skipped.
    const utcDate = new Date().toISOString().slice(0, 10); // YYYY-MM-DD (UTC)
    const fleetDir = path.join(fixtureRoot, '.fleet');
    if (!existsSync(fleetDir)) mkdirSync(fleetDir, { recursive: true });
    const govLogPath = path.join(fleetDir, `governor-log-${utcDate}.jsonl`);

    // Unique reason so we can distinguish this line from any other governor
    // events already in the ring buffer.
    const probe = `phase2-governor-${Date.now()}`;
    const govLine =
      JSON.stringify({
        ts: new Date().toISOString(),
        lane: 'A',
        tool: 'Bash',
        permission: 'deny',
        category: 'bash-privilege',
        reason: `privilege escalation: sudo (${probe})`,
        input_sha256: 'abc123',
      }) + '\n';
    appendFileSync(govLogPath, govLine, 'utf-8');

    // ---- Assert a type:"governor" event appears in the wall ----------------
    const list = page.locator('[data-testid="aw-list"]');
    await expect.poll(
      async () => list.locator('[data-event-type="governor"]').count(),
      {
        timeout: 5_000,
        message: 'governor event did not appear in the activity wall within 5 s',
      },
    ).toBeGreaterThan(0);

    // The governor row's summary is "{permission} {category}" → "deny bash-privilege".
    const govSummary = await list
      .locator('[data-event-type="governor"]')
      .first()
      .locator('.aw-row__summary')
      .getAttribute('title');
    expect(govSummary ?? '', 'governor row summary should contain the permission + category')
      .toContain('deny');
  });

});

// Case C (grid-only stale-badge + stale_modal end-to-end) removed 2026-05-24:
// stale_modal.js was mounted only by grid.js, now deleted. The board surfaces
// staleness as a per-row STALE pill — covered by test_board_stale.spec.ts.
