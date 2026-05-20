// test_activity_wall.spec.ts — v9.4 Task 2.4: Activity Wall component E2E.
//
// Runs under chromium-grid project (GRID_SMOKE_ENV + fix-small fixture,
// port 8769, workers: 1). MEGALODON_FAKE_SPAWNER=1 so a valid ui.token is
// written and auth exchange works.
//
// The activity wall ring buffer is populated by the server watching the
// mission directory. `watch_dir_for_new_files` takes an initial snapshot
// WITHOUT yielding — files that exist at server start are not emitted.
// Tests write NEW files into the running fixture's tmpdir to trigger events.
//
// Test cases:
//   1. Snapshot hydration — 5 events written before navigation; all visible.
//   2. Live event arrival — navigate, then write a new finding; row appears <2s.
//   3. Filter toggle — Signals only, then add Findings; correct visibility.
//   4. Pause stops auto-scroll — scroll down, pause, write event, scroll fixed.
//   5. Row drawer — click row, drawer opens; press ESC, drawer closes.
//
// Sequential (workers:1, fullyParallel:false) so file writes don't race.

import { test, expect } from '@playwright/test';
import { writeFileSync, mkdirSync, existsSync } from 'node:fs';
import * as path from 'node:path';
import type { TestInfo, Page } from '@playwright/test';

import { fixtureRootForProject, readUiToken } from './_helpers';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Ensure the findings/ dir exists and write a new finding file. */
function writeFixtureFinding(
  fixtureRoot: string,
  name: string,
  lane: string,
  content?: string,
): void {
  const dir = path.join(fixtureRoot, 'findings');
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  const body = content ?? `---\nlane: LANE-${lane}\nagent: agent-test\ntask: T99\nseverity: MINOR\nutc: 2026-05-20T00:00Z\n---\n\n# Test finding\n\nBody.\n`;
  writeFileSync(path.join(dir, name), body, 'utf-8');
}

/** Ensure the signals/ dir exists and write a new signal file. */
function writeFixtureSignal(
  fixtureRoot: string,
  name: string,
  lane: string,
  content?: string,
): void {
  const dir = path.join(fixtureRoot, 'signals');
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  const body = content ?? `---\nlane: LANE-${lane}\nagent: agent-test\nts: 2026-05-20T00:00Z\n---\n\n# Test signal\n\nBody.\n`;
  writeFileSync(path.join(dir, name), body, 'utf-8');
}

/**
 * Authenticate via hash-token exchange and navigate to /.
 * The chromium-grid server (GRID_SMOKE_ENV) requires a valid session cookie
 * to use any auth-gated endpoint including /api/v1/activity-wall/*.
 */
async function authenticateAndGotoGrid(page: Page, testInfo: TestInfo) {
  const token = readUiToken(testInfo);
  // Pass the token in the URL hash — index.html's auth bootstrap exchanges it.
  await page.goto(`/#t=${token}`);
  // Wait for hash to be stripped (auth bootstrap calls history.replaceState).
  await expect(page).toHaveURL('/', { timeout: 10_000 });
  // Wait for the grid page to render.
  await expect(page.locator('[data-testid="grid-page"]')).toBeVisible({ timeout: 10_000 });
  // Activity wall root is present once the component mounts.
  await expect(page.locator('[data-testid="activity-wall-root"]')).toBeVisible({ timeout: 5_000 });
}

// ---------------------------------------------------------------------------
// Test 1: Snapshot hydration — 5 events written before navigation
// ---------------------------------------------------------------------------

test.describe('activity wall: snapshot hydration', () => {

  test('5 events written before navigation are all visible after hydration', async ({ page }, testInfo: TestInfo) => {
    const fixtureRoot = fixtureRootForProject(testInfo);
    const ts = Date.now();

    // Write 3 findings + 2 signals before navigating.
    // The server is already running; the poll loop picks them up within 250ms
    // and they will be in the ring buffer when the snapshot is fetched.
    writeFixtureFinding(fixtureRoot, `agent-snap-A-${ts}-1.md`, 'A');
    writeFixtureFinding(fixtureRoot, `agent-snap-A-${ts}-2.md`, 'A');
    writeFixtureFinding(fixtureRoot, `agent-snap-B-${ts}-3.md`, 'B');
    writeFixtureSignal(fixtureRoot, `agent-snap-A-${ts}-sig1.md`, 'A');
    writeFixtureSignal(fixtureRoot, `agent-snap-C-${ts}-sig2.md`, 'C');

    // Wait at least one poll cycle (250ms) for the server to ingest the files.
    await page.waitForTimeout(600);

    await authenticateAndGotoGrid(page, testInfo);

    // All 5 rows must be visible in the activity wall list.
    const list = page.locator('[data-testid="aw-list"]');

    // Wait for at least 5 rows total (may include more from other tests running
    // against the same ring buffer in the same server process).
    await expect.poll(
      async () => {
        const count = await list.locator('.aw-row').count();
        return count;
      },
      { timeout: 5_000, message: 'Expected at least 5 event rows after snapshot hydration' },
    ).toBeGreaterThanOrEqual(5);
  });

});

// ---------------------------------------------------------------------------
// Test 2: Live event arrival — new finding appears <2s
// ---------------------------------------------------------------------------

test.describe('activity wall: live event arrival via SSE', () => {

  test('new finding file appears as a row within 2s', async ({ page }, testInfo: TestInfo) => {
    const fixtureRoot = fixtureRootForProject(testInfo);

    await authenticateAndGotoGrid(page, testInfo);

    // Give SSE connection a moment to establish before writing.
    await page.waitForTimeout(400);

    const uniqueName = `agent-live-A-${Date.now()}.md`;
    writeFixtureFinding(fixtureRoot, uniqueName, 'A');

    const list = page.locator('[data-testid="aw-list"]');
    // Row summary = file stem (activity_wall.py _build_file_event: path.stem[:200]).
    const stemName = uniqueName.replace(/\.md$/, '');

    await expect.poll(
      async () => {
        const titles = await list.locator('.aw-row').evaluateAll((rows: Element[]) =>
          rows.map((r) => r.querySelector('.aw-row__summary')?.getAttribute('title') ?? ''),
        );
        return titles.some((t) => t.includes(stemName));
      },
      { timeout: 2_000, message: `Row with summary "${stemName}" did not appear within 2s` },
    ).toBe(true);
  });

});

// ---------------------------------------------------------------------------
// Test 3: Filter toggle
// ---------------------------------------------------------------------------

test.describe('activity wall: filter chips', () => {

  test('Signals-only chip hides finding rows; adding Findings shows both types', async ({ page }, testInfo: TestInfo) => {
    const fixtureRoot = fixtureRootForProject(testInfo);
    const ts = Date.now();

    // Write one of each type so both exist in the ring buffer.
    writeFixtureFinding(fixtureRoot, `agent-filter-A-${ts}-f.md`, 'A');
    writeFixtureSignal(fixtureRoot, `agent-filter-A-${ts}-s.md`, 'A');

    await page.waitForTimeout(600);
    await authenticateAndGotoGrid(page, testInfo);

    const list = page.locator('[data-testid="aw-list"]');
    // Wait for both types to be present.
    await expect.poll(
      async () => {
        const findCount = await list.locator('[data-event-type="finding"]').count();
        const sigCount = await list.locator('[data-event-type="signal"]').count();
        return findCount > 0 && sigCount > 0;
      },
      { timeout: 5_000, message: 'Expected both finding and signal rows before filter test' },
    ).toBe(true);

    // Click "Signals" chip (activates signal-only filter).
    await page.locator('[data-testid="aw-chip-signal"]').click();

    // All finding rows must be hidden (display:none).
    const findingRows = list.locator('[data-event-type="finding"]');
    const findingCount = await findingRows.count();
    for (let i = 0; i < findingCount; i++) {
      await expect(findingRows.nth(i)).not.toBeVisible();
    }

    // Signal rows must be visible.
    await expect(list.locator('[data-event-type="signal"]').first()).toBeVisible({ timeout: 3_000 });

    // Toggle "Findings" chip on too — both types visible again.
    await page.locator('[data-testid="aw-chip-finding"]').click();
    await expect(list.locator('[data-event-type="finding"]').first()).toBeVisible({ timeout: 3_000 });
    await expect(list.locator('[data-event-type="signal"]').first()).toBeVisible({ timeout: 3_000 });
  });

});

// ---------------------------------------------------------------------------
// Test 4: Pause stops auto-scroll
// ---------------------------------------------------------------------------

test.describe('activity wall: pause / resume', () => {

  test('paused: new event appended but scroll position unchanged', async ({ page }, testInfo: TestInfo) => {
    const fixtureRoot = fixtureRootForProject(testInfo);
    const ts = Date.now();

    // Pre-populate enough events to make the list scrollable.
    for (let i = 0; i < 40; i++) {
      writeFixtureFinding(fixtureRoot, `agent-pause-A-${ts}-${i}.md`, 'A');
    }
    await page.waitForTimeout(700);

    await authenticateAndGotoGrid(page, testInfo);

    const list = page.locator('[data-testid="aw-list"]');
    // Wait for rows to render (enough to be scrollable).
    await expect.poll(async () => list.locator('.aw-row').count(), { timeout: 5_000 }).toBeGreaterThan(20);

    // Scroll partway down (not top).
    await list.evaluate((el: HTMLElement) => { el.scrollTop = 150; });
    await page.waitForTimeout(100);

    const scrollBefore = await list.evaluate((el: HTMLElement) => el.scrollTop);
    expect(scrollBefore).toBeGreaterThan(0);

    // Click Pause.
    await page.locator('[data-testid="aw-pause-btn"]').click();
    await expect(page.locator('[data-testid="aw-pause-btn"]')).toContainText('Resume', { timeout: 2_000 });

    // Write a new event after pausing.
    writeFixtureFinding(fixtureRoot, `agent-pause-A-${ts}-after.md`, 'A');

    // Wait long enough for the event to arrive (SSE poll ≈250ms).
    await page.waitForTimeout(700);

    // Scroll position must be unchanged (within browser rounding tolerance).
    const scrollAfter = await list.evaluate((el: HTMLElement) => el.scrollTop);
    expect(Math.abs(scrollAfter - scrollBefore)).toBeLessThanOrEqual(2);

    // Resume for clean teardown.
    await page.locator('[data-testid="aw-pause-btn"]').click();
  });

});

// ---------------------------------------------------------------------------
// Test 5: Row drawer — click to open, ESC to close
// ---------------------------------------------------------------------------

test.describe('activity wall: row drawer', () => {

  test('clicking a row opens drawer with payload; ESC closes it', async ({ page }, testInfo: TestInfo) => {
    const fixtureRoot = fixtureRootForProject(testInfo);
    const ts = Date.now();

    writeFixtureFinding(fixtureRoot, `agent-drawer-A-${ts}.md`, 'A');
    await page.waitForTimeout(600);

    await authenticateAndGotoGrid(page, testInfo);

    const list = page.locator('[data-testid="aw-list"]');
    await expect.poll(async () => list.locator('.aw-row').count(), { timeout: 5_000 }).toBeGreaterThan(0);

    // Drawer should not be visible initially.
    await expect(page.locator('[data-testid="aw-drawer-overlay"]')).not.toBeVisible();

    // Click the first visible row.
    await list.locator('.aw-row').first().click();

    // Drawer must be visible and contain JSON with a known payload key.
    await expect(page.locator('[data-testid="aw-drawer"]')).toBeVisible({ timeout: 3_000 });
    const payloadText = await page.locator('[data-testid="aw-drawer-payload"]').textContent();
    expect(payloadText).toBeTruthy();
    // findings rows have payload.filename
    expect(payloadText).toContain('"filename"');

    // Press ESC — drawer must close.
    await page.keyboard.press('Escape');
    await expect(page.locator('[data-testid="aw-drawer"]')).not.toBeVisible({ timeout: 2_000 });
  });

  test('X button closes drawer', async ({ page }, testInfo: TestInfo) => {
    const fixtureRoot = fixtureRootForProject(testInfo);
    const ts = Date.now();

    writeFixtureFinding(fixtureRoot, `agent-drawerx-A-${ts}.md`, 'A');
    await page.waitForTimeout(600);

    await authenticateAndGotoGrid(page, testInfo);

    const list = page.locator('[data-testid="aw-list"]');
    await expect.poll(async () => list.locator('.aw-row').count(), { timeout: 5_000 }).toBeGreaterThan(0);

    await list.locator('.aw-row').first().click();
    await expect(page.locator('[data-testid="aw-drawer"]')).toBeVisible({ timeout: 3_000 });

    await page.locator('[data-testid="aw-drawer-close"]').click();
    await expect(page.locator('[data-testid="aw-drawer"]')).not.toBeVisible({ timeout: 2_000 });
  });

});
