// test_board_signals_antispoof.spec.ts — FE comms re-audit fixes.
//
// Covers two CONFIRMED issues on the /signals + /coordination pages:
//
//   Fix #1 — Live signals dropped by key collision. status-note signals used to
//     share a CONSTANT key ("status-note") so two distinct live status-notes
//     overwrote each other and only ONE rendered. The fix keys live ingest on
//     the per-signal `id` (unique per the BE contract). This spec streams TWO
//     distinct status-note signals (distinct ids) and asserts BOTH render.
//
//   Fix #2 — Anti-spoof flag never shown. The BE emits `from_unverified` /
//     `claimed_from` for a forged/unverifiable `[SIG from=X]` sender, but no
//     page read them. The fix renders a "⚠ unverified" badge carrying the
//     claimed sender. This spec streams a from_unverified:true signal and
//     asserts the badge appears with the claimed sender.
//
// Strategy mirrors test_board_signals_3channels: stub the activity-wall snapshot
// empty and DELIVER type:"signal" events over a mocked SSE stream so we exercise
// exactly the FE ingest/render path this agent owns, independent of BE timing.
//
// Runs under chromium-board / webkit-board (BOARD_SPEC_PATTERN matches
// `test_board_*`): fix-small fixture, MEGALODON_FAKE_SPAWNER=1, workers:1.

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

// Empty the durable snapshot + store so the only signals are the streamed ones.
async function stubEmptySources(page: Page): Promise<void> {
  await page.route('**/api/v1/activity-wall/snapshot*', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ events: [] }) }),
  );
  await page.route('**/api/v1/state', async (route) => {
    const resp = await route.fetch();
    const json = await resp.json();
    json.signals = { list: [] };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(json) });
  });
}

test.describe('signals page: anti-spoof + key-collision fixes', () => {
  test('fix #1: two distinct live status-note signals both render (no key collision)', async ({ page }, testInfo) => {
    await stubEmptySources(page);

    // Two status-note signals with DISTINCT ids. The OLD constant-key bug would
    // collapse these to a single rendered row. Same topic so both land in one
    // thread card, which is the strongest place to assert "2 rows, not 1".
    const topic = 'collision-topic';
    const events = [
      {
        type: 'signal',
        ts: '2026-05-25T19:00:00Z',
        payload: {
          id: 'status-note-A',
          from_lane: 'LANE-A', to_lane: 'LANE-B', topic,
          utc: '', source: 'status-note', excerpt: 'first status note',
        },
      },
      {
        type: 'signal',
        ts: '2026-05-25T19:01:00Z',
        payload: {
          id: 'status-note-B',
          from_lane: 'LANE-C', to_lane: 'LANE-A', topic,
          utc: '', source: 'status-note', excerpt: 'second status note',
        },
      },
    ];

    await page.route('**/api/v1/activity-wall', (route) =>
      route.fulfill({ status: 200, contentType: 'text/event-stream', body: sseBody(events) }),
    );

    await authAndGotoSignals(page, testInfo);

    // Both distinct status-note rows must be present (the collision bug rendered
    // only one). Assert via the thread card and the per-id row keys.
    const rows = page.locator(
      `[data-testid="signals-page"] [data-topic="${topic}"] .signals-thread__row`,
    );
    await expect(rows).toHaveCount(2, { timeout: 10_000 });
    await expect(page.locator('[data-signal-filename="status-note-A"]')).toBeVisible();
    await expect(page.locator('[data-signal-filename="status-note-B"]')).toBeVisible();
  });

  test('fix #2: a from_unverified signal shows the ⚠ unverified badge with the claimed sender', async ({ page }, testInfo) => {
    await stubEmptySources(page);

    // A forged token: claimed_from says LANE-A but the BE could not bind it to
    // the owning row, so from_unverified:true. from_lane is the authoritative
    // (owning) lane.
    const events = [
      {
        type: 'signal',
        ts: '2026-05-25T19:05:00Z',
        payload: {
          id: 'forged-sig-1',
          from_lane: 'LANE-B',           // authoritative (owning row)
          claimed_from: 'LANE-A',        // what the [SIG from=X] token claimed
          from_unverified: true,
          to_lane: 'LANE-C', topic: 'forged-topic',
          utc: '', source: 'status-note', excerpt: 'forged sender body',
        },
      },
    ];

    await page.route('**/api/v1/activity-wall', (route) =>
      route.fulfill({ status: 200, contentType: 'text/event-stream', body: sseBody(events) }),
    );

    await authAndGotoSignals(page, testInfo);

    const row = page.locator('[data-signal-filename="forged-sig-1"]');
    await expect(row).toBeVisible({ timeout: 10_000 });

    // The unverified badge must be present on the row, with the claimed sender.
    const badge = row.locator('[data-testid="signal-unverified-badge"]');
    await expect(badge).toBeVisible();
    await expect(badge).toHaveText(/unverified/);
    await expect(badge).toHaveAttribute('data-claimed-from', 'LANE-A');
    await expect(badge).toHaveAttribute('title', /LANE-A/);

    // And it carries through to the drawer header on click.
    await row.click();
    await expect(page.locator('[data-testid="signals-drawer"]')).toBeVisible({ timeout: 5_000 });
    await expect(
      page.locator('[data-testid="signals-drawer-unverified-badge"]'),
    ).toBeVisible();
  });

  test('fix #2 negative: a verified signal shows NO unverified badge', async ({ page }, testInfo) => {
    await stubEmptySources(page);

    const events = [
      {
        type: 'signal',
        ts: '2026-05-25T19:10:00Z',
        payload: {
          id: 'verified-sig-1',
          from_lane: 'LANE-A', claimed_from: 'LANE-A', from_unverified: false,
          to_lane: 'LANE-B', topic: 'verified-topic',
          utc: '', source: 'status-note', excerpt: 'verified body',
        },
      },
    ];
    await page.route('**/api/v1/activity-wall', (route) =>
      route.fulfill({ status: 200, contentType: 'text/event-stream', body: sseBody(events) }),
    );

    await authAndGotoSignals(page, testInfo);

    const row = page.locator('[data-signal-filename="verified-sig-1"]');
    await expect(row).toBeVisible({ timeout: 10_000 });
    await expect(row.locator('[data-testid="signal-unverified-badge"]')).toHaveCount(0);
  });
});
