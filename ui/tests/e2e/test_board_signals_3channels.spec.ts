// test_signals_three_channels_live.spec.ts — R2-FE + M2.
//
// The /signals live ingest used to (a) require payload.filename and (b) sort by
// `utc` only, so finding- and status-note-channel signals — which carry an `id`
// (no on-disk filename) and often an EMPTY `utc` — were either DROPPED outright
// or sank to the bottom of every thread forever.
//
// This spec proves the fix:
//   - All three channels (file / finding / status-note) ingest live and render
//     a row with the correct source/channel chip.
//   - M2: a status-note signal with an empty `utc` but a recent event `ts` is
//     ranked by that ts (best-available time) instead of perpetually last.
//
// Strategy: stub the activity-wall snapshot empty and DELIVER three type:"signal"
// events over the (mocked) SSE stream. This exercises exactly the FE ingest +
// sort path this agent owns, independent of backend channel-emission timing.
//
// Runs under chromium-board / webkit-board.

import { test, expect, Page, TestInfo } from '@playwright/test';
import { readUiToken } from './_helpers';

// Build a text/event-stream body delivering the given events as default
// (onmessage) SSE messages.
function sseBody(events: object[]): string {
  return events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('') + ':\n\n';
}

async function authAndGotoSignals(page: Page, testInfo: TestInfo): Promise<void> {
  const token = readUiToken(testInfo);
  await page.goto(`/#t=${token}`);
  await expect(page).toHaveURL('/', { timeout: 10_000 });
  await page.goto('/signals');
  await expect(page.locator('[data-testid="signals-page"]')).toBeVisible({ timeout: 10_000 });
}

test('signals: finding + status-note + file signals all ingest live with correct channel chips', async ({ page }, testInfo) => {
  // Empty snapshot so the only signals are the three we stream.
  await page.route('**/api/v1/activity-wall/snapshot*', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ events: [] }) }),
  );
  // Empty store signals so nothing leaks in from disk.
  await page.route('**/api/v1/state', async (route) => {
    const resp = await route.fetch();
    const json = await resp.json();
    json.signals = { list: [] };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(json) });
  });

  // Three signal events, one per channel. finding/status-note carry an `id`
  // (no filename), file carries a filename. All come over the live SSE.
  const events = [
    {
      type: 'signal',
      ts: '2026-05-25T18:50:00Z',
      payload: {
        filename: 'LANE-A-to-LANE-B-file-review-2026-05-25T18-50Z.md',
        from_lane: 'LANE-A', to_lane: 'LANE-B', topic: 'file-review',
        utc: '2026-05-25T18-50Z', source: 'file', excerpt: 'file channel body',
      },
    },
    {
      type: 'signal',
      ts: '2026-05-25T18:51:00Z',
      payload: {
        id: 'finding-sig-001',
        from_lane: 'LANE-B', to_lane: 'LANE-C', topic: 'finding-handoff',
        utc: '2026-05-25T18-51Z', source: 'finding', excerpt: 'finding channel body',
      },
    },
    {
      type: 'signal',
      ts: '2026-05-25T18:52:00Z',
      payload: {
        id: 'status-note-sig-001',
        from_lane: 'LANE-C', to_lane: 'LANE-A', topic: 'status-update',
        utc: '', source: 'status-note', excerpt: 'status-note channel body',
      },
    },
  ];

  await page.route('**/api/v1/activity-wall', (route) =>
    route.fulfill({ status: 200, contentType: 'text/event-stream', body: sseBody(events) }),
  );

  await authAndGotoSignals(page, testInfo);

  // All three channels rendered a row with the right channel chip.
  const chips = page.locator('[data-testid="signal-source-chip"]');
  await expect(chips).toHaveCount(3, { timeout: 10_000 });

  await expect(page.locator('[data-testid="signal-source-chip"][data-source="file"]')).toHaveCount(1);
  await expect(page.locator('[data-testid="signal-source-chip"][data-source="finding"]')).toHaveCount(1);
  await expect(page.locator('[data-testid="signal-source-chip"][data-source="status-note"]')).toHaveCount(1);

  // The finding + status-note signals (keyed by id, not filename) are present.
  await expect(page.locator('[data-signal-filename="finding-sig-001"]')).toBeVisible();
  await expect(page.locator('[data-signal-filename="status-note-sig-001"]')).toBeVisible();
});

test('signals M2: a status-note signal with empty utc sorts by event ts, not last', async ({ page }, testInfo) => {
  await page.route('**/api/v1/activity-wall/snapshot*', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ events: [] }) }),
  );
  await page.route('**/api/v1/state', async (route) => {
    const resp = await route.fetch();
    const json = await resp.json();
    json.signals = { list: [] };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(json) });
  });

  // Same topic so both land in ONE thread card; assert their row order.
  //  - OLD file signal: utc 18-40 (older).
  //  - NEW status-note signal: empty utc, but event ts 18-55 (newer).
  // Before M2 the status-note (empty utc) sorted LAST despite being newest.
  const topic = 'm2-ordering';
  const events = [
    {
      type: 'signal',
      ts: '2026-05-25T18:40:00Z',
      payload: {
        filename: `LANE-A-to-LANE-B-${topic}-2026-05-25T18-40Z.md`,
        from_lane: 'LANE-A', to_lane: 'LANE-B', topic,
        utc: '2026-05-25T18-40Z', source: 'file', excerpt: 'older file signal',
      },
    },
    {
      type: 'signal',
      ts: '2026-05-25T18:55:00Z',
      payload: {
        id: 'status-note-newer',
        from_lane: 'LANE-C', to_lane: 'LANE-A', topic,
        utc: '', source: 'status-note', excerpt: 'newer status-note signal',
      },
    },
  ];

  await page.route('**/api/v1/activity-wall', (route) =>
    route.fulfill({ status: 200, contentType: 'text/event-stream', body: sseBody(events) }),
  );

  await authAndGotoSignals(page, testInfo);

  const rows = page.locator(`[data-testid="signals-page"] [data-topic="${topic}"] .signals-thread__row`);
  await expect(rows).toHaveCount(2, { timeout: 10_000 });

  // Newest-first: the status-note signal (event ts 18:55) must be ABOVE the
  // older file signal (utc 18:40) — i.e. sorted by best-available time (M2).
  await expect(rows.nth(0)).toHaveAttribute('data-signal-filename', 'status-note-newer');
  await expect(rows.nth(1)).toHaveAttribute(
    'data-signal-filename',
    `LANE-A-to-LANE-B-${topic}-2026-05-25T18-40Z.md`,
  );
});
