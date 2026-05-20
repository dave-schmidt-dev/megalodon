// test_terminal_pane.spec.ts — Task 1.4: components/terminal_pane.js E2E tests.
//
// Tests the createTerminalPane() factory via the v9.2 dashboard, which uses it
// after the v9.4 refactor. Four cases from the task spec:
//
//   1. Burst test:   10-line burst renders in xterm within 2 s.
//   2. Timing test:  10 lines at 500ms intervals, each within 1 s of write.
//   3. Memory test:  10 000 lines; xterm row count ≤ configured scrollback.
//   4. Cleanup test: SSE bytes stop arriving after cleanup() is called.
//
// Runs under MEGALODON_FAKE_SPAWNER=1 (chromium-v92-dashboard project).
// Fake-spawner pattern follows streams-render.spec.ts.

import { test, expect, Page } from '@playwright/test';
import { readUiToken } from './_helpers';

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

/** Navigate to the dashboard, wait for xterm, settle SSE, return first lane. */
async function loadDashboard(page: Page, token: string): Promise<{ short: string; name: string }> {
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/');
  await expect(page.locator('[data-testid="lane-grid"]')).toBeVisible({ timeout: 10_000 });
  await expect(page.locator('.xterm-screen').first()).toBeVisible({ timeout: 8_000 });

  const configResp = await page.request.get('/api/v1/config');
  expect(configResp.status()).toBe(200);
  const config = await configResp.json();
  const firstLane = config.lanes[0];
  expect(firstLane).toBeTruthy();

  // Settle: let the SSE connect and receive the initial clear/replay events.
  await page.waitForTimeout(600);

  return { short: firstLane.short as string, name: firstLane.name as string };
}

/** Emit a single text line via the fake-spawner emit endpoint. */
async function fakeEmitLine(page: Page, short: string, text: string): Promise<void> {
  const dataB64 = Buffer.from(text + '\r\n', 'utf-8').toString('base64');
  const r = await page.request.post('/api/v1/__fake__/emit', {
    data: { lane: short, data_b64: dataB64 },
  });
  expect(r.status()).toBe(200);
}

// ---------------------------------------------------------------------------
// Test 1: Burst — 10 lines emitted at once, all must render within 2 s.
// ---------------------------------------------------------------------------
test('terminal_pane: burst of 10 lines renders within 2 s', async ({ page }, testInfo) => {
  const token = readUiToken(testInfo);
  const { short, name } = await loadDashboard(page, token);
  const termSel = `[data-testid="lane-term-${name}"]`;

  // Build and emit a 10-line chunk in a single call.
  const probe = `BURST_PROBE_${Date.now()}`;
  const lines: string[] = [];
  for (let i = 0; i < 10; i++) lines.push(`${probe}_L${i}`);
  const payload = lines.join('\r\n') + '\r\n';
  const dataB64 = Buffer.from(payload, 'utf-8').toString('base64');
  const emitResp = await page.request.post('/api/v1/__fake__/emit', {
    data: { lane: short, data_b64: dataB64 },
  });
  expect(emitResp.status()).toBe(200);

  // All 10 lines must appear within 2 s (last line proves all are present).
  await expect.poll(
    async () => {
      const text = await page.locator(termSel).textContent();
      return text ?? '';
    },
    { timeout: 2_000, message: `expected all 10 burst lines in pane ${name}` },
  ).toContain(`${probe}_L9`);

  // Spot-check the first line too (ensures the whole burst arrived, not just tail).
  const finalText = await page.locator(termSel).textContent();
  expect(finalText ?? '').toContain(`${probe}_L0`);
});

// ---------------------------------------------------------------------------
// Test 2: Timing — 10 lines at 500 ms intervals, each within 1 s of write.
// ---------------------------------------------------------------------------
test('terminal_pane: spaced lines (500ms apart) each appear within 1 s', async ({ page }, testInfo) => {
  const token = readUiToken(testInfo);
  const { short, name } = await loadDashboard(page, token);
  const termSel = `[data-testid="lane-term-${name}"]`;

  const probe = `TIMING_PROBE_${Date.now()}`;

  for (let i = 0; i < 10; i++) {
    const line = `${probe}_T${i}`;
    await fakeEmitLine(page, short, line);

    // Each line must appear in the pane within 1 s of the emit call.
    await expect.poll(
      async () => {
        const text = await page.locator(termSel).textContent();
        return text ?? '';
      },
      { timeout: 1_000, message: `line ${i} (${line}) did not appear within 1 s` },
    ).toContain(line);

    // Wait before the next emit.
    if (i < 9) await page.waitForTimeout(500);
  }
});

// ---------------------------------------------------------------------------
// Test 3: Memory — write 10 000 lines; xterm row count must not exceed scrollback.
// ---------------------------------------------------------------------------
test('terminal_pane: 10000 lines do not exceed scrollback cap', async ({ page }, testInfo) => {
  const token = readUiToken(testInfo);
  const { short, name } = await loadDashboard(page, token);
  const termSel = `[data-testid="lane-term-${name}"]`;

  // Emit in batches of 200 to avoid per-call HTTP overhead.
  const batchSize = 200;
  const totalLines = 10_000;
  for (let batch = 0; batch < totalLines / batchSize; batch++) {
    const lines: string[] = [];
    for (let i = 0; i < batchSize; i++) {
      lines.push(`SCROLLBACK_LINE_${batch * batchSize + i}`);
    }
    const payload = lines.join('\r\n') + '\r\n';
    const dataB64 = Buffer.from(payload, 'utf-8').toString('base64');
    await page.request.post('/api/v1/__fake__/emit', {
      data: { lane: short, data_b64: dataB64 },
    });
  }

  // Give xterm time to process and render all bytes.
  await page.waitForTimeout(2_000);

  // Count the visible + buffered rows in the xterm container.
  // xterm renders lines into .xterm-rows children; scrollback + screen rows.
  // dashboard-v92 sets scrollback: 5000 → cap at 5500 with generous margin.
  const rowCount = await page.locator(termSel).evaluate((host: Element) => {
    const rows = host.querySelector('.xterm-rows');
    return rows ? rows.children.length : 0;
  });

  expect(rowCount).toBeLessThanOrEqual(5500);
});

// ---------------------------------------------------------------------------
// Test 4: Cleanup — SSE stops delivering bytes after cleanup() is invoked.
// ---------------------------------------------------------------------------
test('terminal_pane: cleanup closes the pane-stream SSE connection', async ({ page }, testInfo) => {
  const token = readUiToken(testInfo);
  const { short, name } = await loadDashboard(page, token);
  const termSel = `[data-testid="lane-term-${name}"]`;

  // Verify SSE is alive: emit a probe and confirm it appears.
  const probeBefore = `CLEANUP_BEFORE_${Date.now()}`;
  await fakeEmitLine(page, short, probeBefore);
  await expect.poll(
    async () => (await page.locator(termSel).textContent()) ?? '',
    { timeout: 2_000, message: 'probe before cleanup did not appear — SSE not working' },
  ).toContain(probeBefore);

  // Invoke __v92_closeAllStreams() which calls cleanup() on every component.
  // This must close each EventSource so no further bytes arrive.
  await page.evaluate(
    () => (window as unknown as { __v92_closeAllStreams: () => void }).__v92_closeAllStreams(),
  );

  // Emit a new probe AFTER cleanup; it must NOT appear in the pane.
  const probeAfter = `CLEANUP_AFTER_${Date.now()}`;
  await fakeEmitLine(page, short, probeAfter);

  // Wait 1.5 s — any live SSE message would arrive in well under 100 ms.
  await page.waitForTimeout(1_500);

  const textAfter = await page.locator(termSel).textContent();
  expect(textAfter ?? '').not.toContain(probeAfter);
});
