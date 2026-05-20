// test_dashboard_live_audit.spec.ts
// LANE-E adversarial audit — operator-priority override 2026-05-19T19:34:00Z
// Pairs with LANE-D FRONTEND. Every failure is a bug for FRONTEND to fix.
// See: findings/agent-db2a-E-P1-dashboard-audit-ack-2026-05-19T20-06Z.md

import { test, expect } from '@playwright/test';

// ── helpers ──────────────────────────────────────────────────────────────────

const NAV_ENTRIES = [
  { label: 'Dashboard', href: '/', testid: 'nav-dashboard' },
  { label: 'Tasks',     href: '/tasks',    testid: 'nav-tasks' },
  { label: 'Findings',  href: '/findings', testid: 'nav-findings' },
  { label: 'Signals',   href: '/signals',  testid: 'nav-signals' },
  { label: 'Mission',   href: '/mission',  testid: 'nav-mission' },
] as const;

// ── AUDIT-NAV: navigation tab integrity ──────────────────────────────────────

test.describe('AUDIT-NAV: navigation tab integrity', () => {

  for (const nav of NAV_ENTRIES) {
    test(`${nav.label}: click sets URL and aria-current`, async ({ page }) => {
      // Use 'load' not 'networkidle' — the SSE stream keeps a connection open
      // permanently, so networkidle never resolves against the live server.
      await page.goto('/');
      await page.waitForLoadState('load');
      await page.locator(`[data-testid="${nav.testid}"]`).click();
      // After SPA pushState navigation, wait for the nav link itself to reflect
      // the active state (updateNavActive is synchronous after mountPage resolves).
      await expect(page.locator(`[data-testid="${nav.testid}"]`))
        .toHaveAttribute('aria-current', 'page', { timeout: 8_000 });

      if (nav.href === '/') {
        await expect(page).toHaveURL(/\/$|\/?$/);
      } else {
        await expect(page).toHaveURL(new RegExp(`${nav.href}(#.*)?$`));
      }
    });

    test(`${nav.label}: aria-current survives hard reload [KNOWN-BUG: tab-revert-on-refresh]`, async ({ page }) => {
      // Navigate directly to the page (bypasses click flow) then reload.
      await page.goto(nav.href);
      await page.waitForLoadState('load');

      // Before reload: tab should already be active.
      await expect(page.locator(`[data-testid="${nav.testid}"]`))
        .toHaveAttribute('aria-current', 'page', { timeout: 5_000 });

      await page.reload();
      await page.waitForLoadState('load');

      // BUG: auth IIFE `finally` calls history.replaceState("", "", "/") on every
      // load that had a hash token, resetting the URL to "/" and losing the active
      // tab highlight for non-root pages.
      if (nav.href !== '/') {
        await expect(page).toHaveURL(new RegExp(`${nav.href}(#.*)?$`));
      }
      await expect(page.locator(`[data-testid="${nav.testid}"]`))
        .toHaveAttribute('aria-current', 'page', { timeout: 5_000 });
    });
  }

  test('hash auth token stripped but pathname preserved [KNOWN-BUG: auth-iife-path-strip]', async ({ page }) => {
    // Repro: load /findings#t=<token> → auth IIFE fires → replaceState("/") → lose /findings
    await page.goto('/findings#t=testtoken');
    await page.waitForLoadState('load');

    // Hash should be stripped but pathname must stay /findings.
    await expect(page).toHaveURL(/\/findings$/);
    await expect(page.locator('[data-testid="nav-findings"]'))
      .toHaveAttribute('aria-current', 'page', { timeout: 5_000 });
  });

});

// ── AUDIT-FINDINGS: findings page ────────────────────────────────────────────

test.describe('AUDIT-FINDINGS: findings page', () => {

  test('findings page loads and renders at least one finding row', async ({ page }) => {
    await page.goto('/findings');
    await page.waitForLoadState('load');
    // Live server has findings; page must not be empty.
    await expect(page.locator('[data-testid^="finding-row-"]').first())
      .toBeVisible({ timeout: 8_000 });
  });

  test('clicking a finding row opens preview panel with content', async ({ page }) => {
    await page.goto('/findings');
    await page.waitForLoadState('load');
    const firstRow = page.locator('[data-testid^="finding-row-"]').first();
    await firstRow.waitFor({ state: 'visible', timeout: 8_000 });
    await firstRow.click();
    // Preview panel should appear and contain rendered markdown body.
    const preview = page.locator('[data-testid="finding-preview"]');
    await expect(preview).toBeVisible({ timeout: 5_000 });
    await expect(preview).not.toBeEmpty();
  });

  test('severity filter chip narrows the list', async ({ page }) => {
    await page.goto('/findings');
    await page.waitForLoadState('load');
    await page.locator('[data-testid^="finding-row-"]').first().waitFor({ state: 'visible', timeout: 8_000 });
    const initialCount = await page.locator('[data-testid^="finding-row-"]').count();
    await page.locator('[data-testid="filter-severity-MAJOR"]').click();
    const filteredCount = await page.locator('[data-testid^="finding-row-"]').count();
    expect(filteredCount).toBeGreaterThan(0);
    expect(filteredCount).toBeLessThanOrEqual(initialCount);
  });

});

// ── AUDIT-TASKS: tasks page ───────────────────────────────────────────────────

test.describe('AUDIT-TASKS: tasks page', () => {

  test('tasks page renders task cards', async ({ page }) => {
    await page.goto('/tasks');
    await page.waitForLoadState('load');
    await expect(page.locator('[data-testid^="task-card-"]').first())
      .toBeVisible({ timeout: 8_000 });
  });

  test('phase tab bar exists and all tabs are clickable', async ({ page }) => {
    await page.goto('/tasks');
    await page.waitForLoadState('load');
    const tabBar = page.locator('[data-testid="phase-tab-bar"], [role="tablist"]');
    await expect(tabBar).toBeVisible({ timeout: 5_000 });
    const tabs = tabBar.locator('[role="tab"]');
    const tabCount = await tabs.count();
    expect(tabCount).toBeGreaterThanOrEqual(2);
    for (let i = 0; i < tabCount; i++) {
      await tabs.nth(i).click();
      await page.waitForTimeout(200);
      // Page must still have content after each tab click.
      await expect(page.locator('#app-root')).not.toBeEmpty();
    }
  });

  test('inject-task form exists in control mode', async ({ page }) => {
    await page.goto('/tasks');
    await page.waitForLoadState('load');
    // Enable control mode first.
    const toggle = page.locator('[data-testid="action-toggle-control-mode"]');
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-checked', 'true', { timeout: 3_000 });
    // Inject-task form should become visible.
    const form = page.locator('[data-testid="inject-task-form"], form[data-testid*="inject"]');
    await expect(form).toBeVisible({ timeout: 5_000 });
  });

});

// ── AUDIT-SIGNALS: signals page ───────────────────────────────────────────────

test.describe('AUDIT-SIGNALS: signals page', () => {

  test('signals page renders without JS errors', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));

    await page.goto('/signals');
    await page.waitForLoadState('load');

    expect(errors).toHaveLength(0);
    // SVG swim-lane chart OR empty-state placeholder.
    const hasSvg = (await page.locator('svg').count()) > 0;
    const hasEmpty = (await page.locator('.empty-state').count()) > 0;
    const hasContent = (await page.locator('#app-root').textContent())?.trim().length > 0;
    expect(hasSvg || hasEmpty || hasContent).toBe(true);
  });

  test('signals page shows filter chips for sender lanes', async ({ page }) => {
    await page.goto('/signals');
    await page.waitForLoadState('load');
    const filterBar = page.locator('[data-testid="signals-filter-bar"], [aria-label*="filter" i]');
    await expect(filterBar).toBeVisible({ timeout: 5_000 });
  });

});

// ── AUDIT-MISSION: mission page ───────────────────────────────────────────────

test.describe('AUDIT-MISSION: mission page', () => {

  test('mission page renders content (not stuck on "Loading…")', async ({ page }) => {
    await page.goto('/mission');
    await page.waitForLoadState('load');
    await expect(page.locator('#app-root')).not.toBeEmpty();
    await expect(page.locator('#app-root')).not.toHaveText('Loading…');
  });

  test('mission page shows MISSION.md rendered content', async ({ page }) => {
    await page.goto('/mission');
    await page.waitForLoadState('load');
    const missionBody = page.locator('[data-testid="mission-body"], [data-testid="mission-content"]');
    await expect(missionBody).toBeVisible({ timeout: 8_000 });
    await expect(missionBody).not.toBeEmpty();
  });

  test('mission page exposes orchestrator actions panel', async ({ page }) => {
    await page.goto('/mission');
    await page.waitForLoadState('load');
    // Enable control mode to surface action buttons.
    await page.locator('[data-testid="action-toggle-control-mode"]').click();
    const actions = page.locator(
      '[data-testid="orchestrator-actions"], [aria-label*="orchestrator" i], [data-testid*="phase-flip"]'
    );
    await expect(actions.first()).toBeVisible({ timeout: 5_000 });
  });

  test('mission page shows recent HISTORY tail section', async ({ page }) => {
    await page.goto('/mission');
    await page.waitForLoadState('load');
    const historySection = page.locator('[data-testid="history-tail"], [aria-label*="history" i]');
    await expect(historySection).toBeVisible({ timeout: 5_000 });
  });

});

// ── AUDIT-DASHBOARD: dashboard panels ────────────────────────────────────────

test.describe('AUDIT-DASHBOARD: dashboard panels', () => {

  test('activity sparkline panel is present', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('load');
    await expect(page.locator('[data-testid="activity-sparkline"]')).toBeVisible({ timeout: 5_000 });
  });

  test('activity sparkline always shows "no activity yet" with /loop agents [DESIGN-BUG: events-always-empty]', async ({ page }) => {
    // BUG: dashboard reads mission.events but /loop agents produce no PHASE-FLIP or
    // RECLAIM events during normal iteration. The panel is always blank.
    // Fix options: (a) plumb claim/finding writes as events; (b) use claims.list mtimes.
    // Fix implemented in fleet dashboard.js (agent-07c5). Pending port to main.
    await page.goto('/');
    await page.waitForLoadState('load');
    const sparkline = page.locator('[data-testid="activity-sparkline"]');
    await expect(sparkline).toBeVisible();
    await expect(sparkline).toContainText('no activity yet');
  });

  test('recent HISTORY panel is present', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('load');
    await expect(page.locator('[data-testid="history-tail"]')).toBeVisible({ timeout: 5_000 });
  });

  test('recent HISTORY always shows "no HISTORY entries yet" with /loop agents [DESIGN-BUG: history-always-empty]', async ({ page }) => {
    // BUG: same root cause as activity sparkline — /loop agents don't write mission events.
    // Fix implemented in fleet dashboard.js (agent-07c5). Pending port to main.
    await page.goto('/');
    await page.waitForLoadState('load');
    const history = page.locator('[data-testid="history-tail"]');
    await expect(history).toBeVisible();
    await expect(history).toContainText('no HISTORY entries yet');
  });

  test('active claims panel renders with data-testid per claim [MISSING-FEATURE: active-claims-panel]', async ({ page }) => {
    // FAIL EXPECTED: This panel has not been implemented.
    // Operator spec: top-of-dashboard panel; each active claim renders as
    // data-testid="active-claim-<task-id>".
    await page.goto('/');
    await page.waitForLoadState('load');
    await expect(page.locator('[data-testid^="active-claim-"]').first())
      .toBeVisible({ timeout: 3_000 });
  });

  test('permission prompts panel renders when prompts are pending [MISSING-FEATURE: permission-prompts-panel]', async ({ page }) => {
    // FAIL EXPECTED: Dashboard has no dedicated prompts panel.
    // Operator spec: top-of-dashboard panel; renders pending permission prompts.
    await page.goto('/');
    await page.waitForLoadState('load');
    await expect(page.locator('[data-testid="permission-prompts-panel"]'))
      .toBeVisible({ timeout: 3_000 });
  });

  test('lane cards show model and harness by default without "Show details" toggle [MISSING-FEATURE: S-LANE-CARD-DETAILS]', async ({ page }) => {
    // FAIL EXPECTED: model/harness/cadence are hidden behind the "Show details" button.
    // Operator spec S-LANE-CARD-DETAILS: default-show model, harness, cadence, current task.
    // Fix implemented in fleet dashboard.js (agent-07c5). Pending port to main.
    await page.goto('/');
    await page.waitForLoadState('load');
    const firstCard = page.locator('[data-testid^="lane-row-"]').first();
    await expect(firstCard.locator('[data-testid="lane-model"]')).toBeVisible({ timeout: 3_000 });
  });

  test('lane cards show last-tick-ago without "Show details" toggle [MISSING-FEATURE: S-LANE-CARD-DETAILS]', async ({ page }) => {
    // FAIL EXPECTED: last-tick-ago is hidden.
    // Fix implemented in fleet dashboard.js (agent-07c5). Pending port to main.
    await page.goto('/');
    await page.waitForLoadState('load');
    const firstCard = page.locator('[data-testid^="lane-row-"]').first();
    await expect(firstCard.locator('[data-testid="lane-last-tick"]')).toBeVisible({ timeout: 3_000 });
  });

  test('6 lane cards present for default 6-lane mission', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('load');
    await expect(page.locator('[data-testid^="lane-row-"]')).toHaveCount(6, { timeout: 5_000 });
  });

  test('lane card "Show details" toggle expands drawer', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('load');
    const firstCard = page.locator('[data-testid^="lane-row-"]').first();
    const lane = (await firstCard.getAttribute('data-testid'))?.replace('lane-row-', '') ?? 'AUDIT';
    const toggleBtn = firstCard.locator('[data-testid^="action-toggle-lane-"]');
    await toggleBtn.click();
    await expect(page.locator(`[data-testid="lane-drawer-${lane}"]`)).toBeVisible({ timeout: 3_000 });
  });

});

// ── AUDIT-PHASE-INDICATOR: phase strip is reactive to store hydration ─────────
// Regression for BUG-PHASE-INDICATOR-STUCK (agent-07c5 2026-05-19T22:36Z):
// store.subscribe("mission.phase") did not fire when store.hydrate() called
// set("mission", ...). Fix: _emitPath() now walks child-path subscribers when
// a parent object is replaced (store.js descendants notification block).

test.describe('AUDIT-PHASE-INDICATOR: phase strip reactive to hydration', () => {

  test('phase strip highlights server phase after hydration, not stuck on INIT [BUG-PHASE-INDICATOR-STUCK]', async ({ page }) => {
    // Intercept state endpoint to inject PHASE-PLAN (fixture returns "INIT" by default).
    await page.route('**/api/v1/state', async (route) => {
      const real = await route.fetch();
      const body = await real.json();
      body.mission = { phase: 'PHASE-PLAN', events: [], missionStatus: '' };
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    });

    await page.goto('/');
    await page.waitForLoadState('load');

    // PHASE-PLAN segment must be active: mission.phase subscriber fired on hydration.
    await expect(page.locator('[data-testid="phase-segment-PHASE-PLAN"]'))
      .toHaveAttribute('aria-current', 'step', { timeout: 5_000 });

    // INIT must NOT be active (was stuck before the store.js fix).
    await expect(page.locator('[data-testid="phase-segment-INIT"]'))
      .not.toHaveAttribute('aria-current', 'step');
  });

  test('phase strip updates when store.set("mission", ...) is called directly [BUG-PHASE-INDICATOR-STUCK]', async ({ page }) => {
    // Tests the fix in isolation: direct store.set("mission", obj) must notify
    // the mission.phase subscriber registered by app.js attachPhaseIndicator().
    await page.goto('/');
    await page.waitForLoadState('load');

    await page.evaluate(async () => {
      const { store } = await import('/static/js/store.js');
      store.set('mission', { phase: 'PHASE-BUILD', events: [], missionStatus: '' });
    });

    await expect(page.locator('[data-testid="phase-segment-PHASE-BUILD"]'))
      .toHaveAttribute('aria-current', 'step', { timeout: 3_000 });
    await expect(page.locator('[data-testid="phase-segment-INIT"]'))
      .not.toHaveAttribute('aria-current', 'step');
  });

});

// ── AUDIT-TOOLTIPS: every interactive control has a non-empty title= ──────────
// Tooltip coverage per operator directive 2026-05-19T21:25:00Z.
// Fix implemented in fleet (agent-07c5 S-TOOLTIPS). Pending port to main.
// All tests marked [MISSING-FEATURE: S-TOOLTIPS] — expected to fail against main.

test.describe('AUDIT-TOOLTIPS: interactive controls have title attributes', () => {

  test('phase strip segments have title attributes [MISSING-FEATURE: S-TOOLTIPS]', async ({ page }) => {
    // FAIL EXPECTED: main index.html phase <li> elements have no title=.
    // Fix: add title= to each phase-segment in fleet index.html (agent-07c5).
    await page.goto('/');
    await page.waitForLoadState('load');
    const segments = page.locator('li[data-testid^="phase-segment-"]');
    const count = await segments.count();
    expect(count).toBeGreaterThan(0);
    for (let i = 0; i < count; i++) {
      const title = await segments.nth(i).getAttribute('title');
      expect(title, `phase-segment[${i}] should have non-empty title`).toBeTruthy();
    }
  });

  test('control-mode toggle has title attribute [MISSING-FEATURE: S-TOOLTIPS]', async ({ page }) => {
    // FAIL EXPECTED: main index.html control-mode-toggle has no title=.
    await page.goto('/');
    await page.waitForLoadState('load');
    const toggle = page.locator('[data-testid="action-toggle-control-mode"]');
    await expect(toggle).toBeVisible();
    const title = await toggle.getAttribute('title');
    expect(title, 'control-mode toggle should have non-empty title').toBeTruthy();
  });

  test('lane card state badge has title attribute [MISSING-FEATURE: S-TOOLTIPS]', async ({ page }) => {
    // FAIL EXPECTED: state badge rendered without title= in main dashboard.js.
    await page.goto('/');
    await page.waitForLoadState('load');
    const badge = page.locator('[data-testid^="lane-row-"]').first().locator('[data-state]');
    await expect(badge).toBeVisible({ timeout: 5_000 });
    const title = await badge.getAttribute('title');
    expect(title, 'lane state badge should have non-empty title').toBeTruthy();
  });

  test('lane card show-details toggle has title attribute [MISSING-FEATURE: S-TOOLTIPS]', async ({ page }) => {
    // FAIL EXPECTED: toggle button rendered without title= in main dashboard.js.
    await page.goto('/');
    await page.waitForLoadState('load');
    const toggleBtn = page.locator('[data-testid^="action-toggle-lane-"]').first();
    await expect(toggleBtn).toBeVisible({ timeout: 5_000 });
    const title = await toggleBtn.getAttribute('title');
    expect(title, 'lane toggle button should have non-empty title').toBeTruthy();
  });

  test('phase flip submit button has title attribute [MISSING-FEATURE: S-TOOLTIPS]', async ({ page }) => {
    // FAIL EXPECTED: main mission.js phase-flip submit has no title=.
    await page.goto('/mission');
    await page.waitForLoadState('load');
    const btn = page.locator('[data-testid="action-submit-flip-mission"]');
    await expect(btn).toBeVisible({ timeout: 5_000 });
    const title = await btn.getAttribute('title');
    expect(title, 'phase-flip submit should have non-empty title').toBeTruthy();
  });

  test('phase flip target buttons have title attributes [MISSING-FEATURE: S-TOOLTIPS]', async ({ page }) => {
    // FAIL EXPECTED: target phase buttons in main mission.js have no title=.
    await page.goto('/mission');
    await page.waitForLoadState('load');
    const targets = page.locator('[data-testid^="flip-target-"]');
    const count = await targets.count();
    expect(count).toBeGreaterThan(0);
    for (let i = 0; i < count; i++) {
      const title = await targets.nth(i).getAttribute('title');
      expect(title, `flip-target[${i}] should have non-empty title`).toBeTruthy();
    }
  });

  test('reclaim button in stale panel has title attribute [MISSING-FEATURE: S-TOOLTIPS]', async ({ page }) => {
    // FAIL EXPECTED: reclaim button in main dashboard.js stale panel has no title=.
    // Note: stale panel only appears when a lane is stale; this test checks the
    // confirm-reclaim button which is always rendered (hidden until activated).
    await page.goto('/');
    await page.waitForLoadState('load');
    const confirmReclaim = page.locator('[data-testid="confirm-reclaim"]');
    // confirm-reclaim is in DOM (hidden), check title on it
    const title = await confirmReclaim.getAttribute('title');
    expect(title, 'confirm-reclaim button should have non-empty title').toBeTruthy();
  });

});
