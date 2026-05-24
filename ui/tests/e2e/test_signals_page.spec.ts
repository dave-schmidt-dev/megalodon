// test_signals_page.spec.ts — v9.4 T3.7: signals page threading + mark-read.
//
// Runs under chromium-grid project (GRID_SMOKE_ENV + fix-small fixture, port
// 8769, workers:1, MEGALODON_FAKE_SPAWNER=1 for auth).
//
// Test cases:
//   1. Threading — write 2 signals with the same topic to the fixture signals/
//      dir → navigate to /signals → assert one thread card with 2 sub-rows.
//   2. Mark-read persists — click a row → drawer opens → close drawer → reload
//      page → that row has [data-signal-read="true"] and class "is-read".
//   3. Empty state — mock /api/v1/state to return empty signals list → navigate
//      to /signals → assert empty-state element visible.
//
// Auth: chromium-grid uses MEGALODON_FAKE_SPAWNER=1 so a valid ui.token is
// written to the tmpdir. Authenticate via the hash-token exchange before
// writing files or navigating.
//
// Sequential (workers:1) — file writes and SSE state must not race.

import { test, expect } from '@playwright/test';
import { writeFileSync, mkdirSync, existsSync } from 'node:fs';
import * as path from 'node:path';
import type { Page, TestInfo } from '@playwright/test';

import { fixtureRootForProject, readUiToken } from './_helpers';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Ensure signals/ dir exists and write a signal file. */
function writeSignalFile(
  fixtureRoot: string,
  filename: string,
  body: string,
): void {
  const dir = path.join(fixtureRoot, 'signals');
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  writeFileSync(path.join(dir, filename), body, 'utf-8');
}

/**
 * Authenticate via hash-token exchange and wait for grid page.
 * The chromium-grid server requires a valid session cookie for auth-gated
 * endpoints. Navigation to /signals only works after the cookie is set.
 */
async function authenticateAndGotoGrid(page: Page, testInfo: TestInfo): Promise<void> {
  const token = readUiToken(testInfo);
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/', { timeout: 10_000 });
  await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
}

/** Navigate to /signals and wait for the page to mount. */
async function gotoSignals(page: Page): Promise<void> {
  await page.goto('/signals');
  await expect(page.locator('[data-testid="signals-page"]')).toBeVisible({ timeout: 10_000 });
}

// ---------------------------------------------------------------------------
// Test 1: Threading — two signals with same topic → one card, 2 sub-rows
// ---------------------------------------------------------------------------

test.describe('signals page: threading', () => {

  test('1: two signals with same topic produce one thread card with 2 sub-rows', async ({ page }, testInfo: TestInfo) => {
    const fixtureRoot = fixtureRootForProject(testInfo);
    const ts = Date.now();
    const topic = `test-topic-${ts}`;

    // Write two signals with the same topic (but different sender/receiver).
    writeSignalFile(
      fixtureRoot,
      `LANE-A-to-LANE-B-${topic}.md`,
      `# Signal A to B\n\nTopic: ${topic}\nThis is signal 1.\n`,
    );
    writeSignalFile(
      fixtureRoot,
      `LANE-B-to-LANE-A-${topic}.md`,
      `# Signal B to A\n\nTopic: ${topic}\nThis is signal 2.\n`,
    );

    // Wait for the server to ingest the files (poll interval ≈250ms).
    await page.waitForTimeout(600);

    await authenticateAndGotoGrid(page, testInfo);
    await gotoSignals(page);

    // There must be a thread card for our topic.
    const card = page.locator(`[data-testid="signals-page"] [data-topic="${topic}"]`);
    await expect(card).toBeVisible({ timeout: 8_000 });

    // Inside that card there must be exactly 2 sub-rows.
    const rows = card.locator('[data-testid="signals-thread-rows"] [role="button"]');
    await expect(rows).toHaveCount(2, { timeout: 5_000 });
  });

});

// ---------------------------------------------------------------------------
// Test 2: Mark-read persists across page reload
// ---------------------------------------------------------------------------

test.describe('signals page: mark-read persists', () => {

  test('2: clicking a row marks it read; reload shows [data-signal-read="true"]', async ({ page }, testInfo: TestInfo) => {
    const fixtureRoot = fixtureRootForProject(testInfo);
    const ts = Date.now();
    const topic = `read-test-${ts}`;
    const filename = `LANE-A-to-LANE-C-${topic}.md`;

    writeSignalFile(
      fixtureRoot,
      filename,
      `# Signal mark-read test\n\nBody for ${filename}.\n`,
    );

    await page.waitForTimeout(600);

    await authenticateAndGotoGrid(page, testInfo);
    await gotoSignals(page);

    // Wait for the row to appear.
    const row = page.locator(`[data-signal-filename="${filename}"]`);
    await expect(row).toBeVisible({ timeout: 8_000 });

    // Before click: row should NOT be marked read.
    await expect(row).toHaveAttribute('data-signal-read', 'false');

    // Click to open drawer.
    await row.click();
    await expect(page.locator('[data-testid="signals-drawer"]')).toBeVisible({ timeout: 5_000 });

    // After click: row should be marked read in the DOM.
    await expect(row).toHaveAttribute('data-signal-read', 'true');

    // Close drawer.
    await page.locator('[data-testid="signals-drawer-close"]').click();
    await expect(page.locator('[data-testid="signals-drawer"]')).not.toBeVisible({ timeout: 3_000 });

    // Reload — signals page re-mounts and reads localStorage.
    await page.reload();
    await expect(page.locator('[data-testid="signals-page"]')).toBeVisible({ timeout: 10_000 });

    // After reload the row should still carry data-signal-read="true" (from localStorage).
    const rowAfterReload = page.locator(`[data-signal-filename="${filename}"]`);
    await expect(rowAfterReload).toBeVisible({ timeout: 8_000 });
    await expect(rowAfterReload).toHaveAttribute('data-signal-read', 'true');
  });

});

// ---------------------------------------------------------------------------
// Test 3: Empty state when no signals exist
// ---------------------------------------------------------------------------

test.describe('signals page: empty state', () => {

  test('3: empty state text shown when state returns no signals', async ({ page }, testInfo: TestInfo) => {
    // Intercept /api/v1/state to override signals.list = [] regardless of
    // what files are on disk. This avoids depending on fixture isolation when
    // other tests in this file have already written signal files.
    await page.route('**/api/v1/state', async (route) => {
      const resp = await route.fetch();
      const json = await resp.json();
      // Zero out signals in the hydration payload.
      json.signals = { list: [] };
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(json),
      });
    });

    const token = readUiToken(testInfo);
    await page.goto(`/#t=${token}`);
    await expect(page).toHaveURL('/', { timeout: 10_000 });

    // Navigate to /signals — page will hydrate from the mocked empty state.
    await page.goto('/signals');
    await expect(page.locator('[data-testid="signals-page"]')).toBeVisible({ timeout: 10_000 });

    // Empty state element must be visible.
    await expect(page.locator('[data-testid="signals-empty"]')).toBeVisible({ timeout: 5_000 });
    await expect(page.locator('[data-testid="signals-empty"]')).toContainText('No signals yet');
  });

});
