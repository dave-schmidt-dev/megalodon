// test_board_activity_autoscroll.spec.ts — I3 (auto-scroll guard).
//
// The activity wall auto-scrolled the reader to the TOP on every new event,
// checking only `!paused` — never the scroll position. A reader who had
// scrolled back into history was yanked to the top each time a new event
// arrived. The fix: only auto-scroll to top when the reader is already at (or
// within a few px of) the top.
//
// This spec: pre-fill the wall so it's scrollable, scroll the reader DOWN into
// history (NOT paused), inject a new event, and assert the scroll position is
// preserved (not yanked to 0). The complementary "at-top → follows new events"
// behaviour is covered by the snapshot/live specs.
//
// Runs under chromium-board / webkit-board.

import { test, expect, Page, TestInfo } from '@playwright/test';
import { writeFileSync, mkdirSync, existsSync } from 'node:fs';
import * as path from 'node:path';
import { fixtureRootForProject, readUiToken } from './_helpers';

// Per-test isolation: prefix every written filename with the running test's
// testId. The activity-wall file watcher (event_tail.watch_dir_for_new_files)
// scans the findings/ dir non-recursively for regular files, so a per-test
// SUBDIR would never be seen by the server — instead we namespace the FILENAME
// by testId. This keeps two tests in this (workers:1) project from ever
// colliding on a filename even if their Date.now() timestamps coincide.
// Belt-and-suspenders: the project is already serial.
function writeFinding(
  fixtureRoot: string,
  filename: string,
  lane: string,
  testInfo: TestInfo,
): void {
  const dir = path.join(fixtureRoot, 'findings');
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  const scoped = `${testInfo.testId}-${filename}`;
  writeFileSync(
    path.join(dir, scoped),
    `---\nlane: ${lane}\nseverity: MINOR\n---\n${scoped}\n`,
    'utf-8',
  );
}

async function authAndGotoBoard(page: Page, testInfo: TestInfo): Promise<void> {
  const token = readUiToken(testInfo);
  // Activity wall now auto-opens on mount (default-open). This spec toggles it
  // open explicitly, so pin the preference CLOSED before the SPA boots.
  await page.addInitScript(() => {
    try { localStorage.setItem('megalodon.activityWall.open', '0'); } catch (_) { /* ignore */ }
  });
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/', { timeout: 10_000 });
  await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
}

test('I3: a scrolled-back reader is NOT yanked to the top on a new event', async ({ page }, testInfo) => {
  const fixtureRoot = fixtureRootForProject(testInfo);
  const ts = Date.now();

  // Pre-fill enough events to make the list scrollable.
  for (let i = 0; i < 40; i++) {
    writeFinding(fixtureRoot, `agent-scroll-A-${ts}-${i}.md`, 'A', testInfo);
  }
  await page.waitForTimeout(700);

  await authAndGotoBoard(page, testInfo);
  await page.locator('[data-testid="board-activity-toggle"]').click();
  const list = page.locator('[data-testid="aw-list"]');
  await expect(list).toBeVisible({ timeout: 5_000 });
  await expect.poll(async () => list.locator('.aw-row').count(), { timeout: 8_000 }).toBeGreaterThan(20);

  // Reader scrolls DOWN into history (NOT paused).
  await list.evaluate((el: HTMLElement) => { el.scrollTop = 200; });
  await page.waitForTimeout(100);
  const scrollBefore = await list.evaluate((el: HTMLElement) => el.scrollTop);
  expect(scrollBefore).toBeGreaterThan(0);

  // A NEW event arrives while the reader is scrolled back.
  writeFinding(fixtureRoot, `agent-scroll-A-${ts}-after.md`, 'A', testInfo);
  await page.waitForTimeout(900);

  // The reader was NOT yanked to the top. (insertBefore at the top would push
  // content down; overflow-anchor:none keeps the reader's position. Critically,
  // we did NOT force scrollTop = 0.)
  const scrollAfter = await list.evaluate((el: HTMLElement) => el.scrollTop);
  expect(scrollAfter, 'reader must not be yanked to the top').toBeGreaterThan(0);
});

test('I3: a reader at the top DOES follow new events (auto-scroll preserved at top)', async ({ page }, testInfo) => {
  const fixtureRoot = fixtureRootForProject(testInfo);
  const ts = Date.now();

  for (let i = 0; i < 40; i++) {
    writeFinding(fixtureRoot, `agent-attop-A-${ts}-${i}.md`, 'A', testInfo);
  }
  await page.waitForTimeout(700);

  await authAndGotoBoard(page, testInfo);
  await page.locator('[data-testid="board-activity-toggle"]').click();
  const list = page.locator('[data-testid="aw-list"]');
  await expect(list).toBeVisible({ timeout: 5_000 });
  await expect.poll(async () => list.locator('.aw-row').count(), { timeout: 8_000 }).toBeGreaterThan(20);

  // Reader is at the top.
  await list.evaluate((el: HTMLElement) => { el.scrollTop = 0; });
  await page.waitForTimeout(100);

  // A new event arrives — the wall keeps the reader pinned at the top.
  writeFinding(fixtureRoot, `agent-attop-A-${ts}-after.md`, 'A', testInfo);
  await page.waitForTimeout(900);

  const scrollAfter = await list.evaluate((el: HTMLElement) => el.scrollTop);
  expect(scrollAfter, 'reader at top should stay at top to see the newest event').toBeLessThanOrEqual(2);
});
