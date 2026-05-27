// test_board_signals_live.spec.ts — Wave 2 FE: signals appear LIVE.
//
// The schism this fixes: the signals page used to read only the (always-empty)
// signals.list snapshot, so a signal written DURING a run never appeared until
// a reload. This spec proves the live path: with the signals page already
// mounted, drop a signals/ file into the running fleet and assert the row
// shows up WITHOUT a reload — driven by the activity-wall SSE.
//
// Runs under chromium-board / webkit-board (BOARD_SPEC_PATTERN matches
// `test_board_*`): fix-small fixture, MEGALODON_FAKE_SPAWNER=1 (valid ui.token),
// workers:1 so file writes / SSE don't race other specs.

import { test, expect } from '@playwright/test';
import { writeFileSync, mkdirSync, existsSync } from 'node:fs';
import * as path from 'node:path';
import type { Page, TestInfo } from '@playwright/test';

import { fixtureRootForProject, readUiToken } from './_helpers';

function writeSignalFile(fixtureRoot: string, filename: string, body: string): void {
  const dir = path.join(fixtureRoot, 'signals');
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  writeFileSync(path.join(dir, filename), body, 'utf-8');
}

async function authAndGotoSignals(page: Page, testInfo: TestInfo): Promise<void> {
  const token = readUiToken(testInfo);
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/', { timeout: 10_000 });
  await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
  await page.goto('/signals');
  await expect(page.locator('[data-testid="signals-page"]')).toBeVisible({ timeout: 10_000 });
}

test.describe('signals: live via activity-wall SSE', () => {
  test('a signal written after mount appears without reload', async ({ page }, testInfo: TestInfo) => {
    const fixtureRoot = fixtureRootForProject(testInfo);
    const ts = Date.now();
    // Per-test isolation: fold the testId into the topic so the derived signal
    // filename is unique to this test. The signals/ watcher
    // (event_tail.watch_dir_for_new_files) scans the dir non-recursively for
    // regular files, so a per-test SUBDIR would never be observed — namespacing
    // the FILENAME is the watcher-safe equivalent. Belt-and-suspenders within
    // this (workers:1) project. All assertions key off the `filename`/`topic`
    // variables below, so the prefix is transparent to them.
    const topic = `live-handoff-${testInfo.testId}-${ts}`;
    // Canonical grammar: LANE-<FROM>-to-LANE-<TO>-<topic>-<UTC>.md
    const filename = `LANE-A-to-LANE-B-${topic}-2026-05-25T18-49Z.md`;

    // Mount the signals page FIRST (so the EventSource is open), then write.
    await authAndGotoSignals(page, testInfo);

    // Give the page a beat to open its SSE + hydrate the snapshot.
    await page.waitForTimeout(500);

    // Now drop the signal file into the live fleet. The activity-wall signals
    // source watches signals/ and streams a type:"signal" event; the signals
    // page ingests it and re-renders — no reload.
    writeSignalFile(
      fixtureRoot,
      filename,
      `# Handoff\n\nLANE-A hands ${topic} to LANE-B. Please review the parser.\n`,
    );

    // The row must appear LIVE. Generous timeout for the file-watch debounce +
    // SSE fan-out.
    const row = page.locator(`[data-signal-filename="${filename}"]`);
    await expect(row).toBeVisible({ timeout: 15_000 });

    // The thread card carries the parsed topic (not the mashed suffix).
    const card = page.locator(`[data-testid="signals-page"] [data-topic="${topic}"]`);
    await expect(card).toBeVisible({ timeout: 5_000 });

    // Sender→receiver chips render who→whom.
    await expect(row.locator('.signals-lane-chip').first()).toHaveText('LANE-A');
    await expect(row.locator('.signals-lane-chip').nth(1)).toHaveText('LANE-B');

    // A source/channel chip is present.
    await expect(row.locator('[data-testid="signal-source-chip"]')).toBeVisible();

    // Clicking opens the drawer with the body.
    await row.click();
    const drawer = page.locator('[data-testid="signals-drawer"]');
    await expect(drawer).toBeVisible({ timeout: 5_000 });
    await expect(page.locator('[data-testid="signals-drawer-body"]')).toContainText(topic);
  });
});
