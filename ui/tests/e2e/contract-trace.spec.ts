// V9 M2 — playwright spec driving the contract-trace wrapper.
// Spec: docs/superpowers/specs/2026-05-16-v9-m2-contract-scan-design.md §10.
//
// Activates window.__M9_CONTRACT_TRACE__ via addInitScript before any
// navigation, walks the major SPA routes, then dumps the captured calls to
// stdout wrapped with the M9_CONTRACT_CALLS_{BEGIN,END} sentinels. The
// scripts/contract_scan.py orchestrator parses those sentinels.

import { test, expect } from '@playwright/test';

test('M2 contract-trace — walks SPA, dumps fetched URLs', async ({ page }) => {
  await page.addInitScript(() => {
    (window as unknown as { __M9_CONTRACT_TRACE__: boolean }).__M9_CONTRACT_TRACE__ = true;
  });

  await page.goto('/static/index.html');

  // Wait for dashboard to render (proves /api/v1/state fired).
  await page.waitForSelector('[data-testid^="lane-row-"]', { timeout: 15000 });

  // Visit other SPA routes that trigger their own fetches. We can't use
  // waitForLoadState('networkidle') because the SSE stream keeps the
  // connection open indefinitely. A short timeout is sufficient for the
  // route-change fetches to fire.
  await page.goto('/static/index.html#/findings');
  await page.waitForTimeout(1500);
  await page.goto('/static/index.html#/mission');
  await page.waitForTimeout(1500);

  const calls = await page.evaluate(() => {
    return (window as unknown as { __M9_CONTRACT_CALLS__: unknown[] }).__M9_CONTRACT_CALLS__;
  });
  expect(Array.isArray(calls)).toBe(true);
  expect((calls as unknown[]).length).toBeGreaterThan(0);

  // Emit calls to stdout for contract_scan.py to parse (sentinel-wrapped).
  console.log('M9_CONTRACT_CALLS_BEGIN' + JSON.stringify(calls) + 'M9_CONTRACT_CALLS_END');
});
