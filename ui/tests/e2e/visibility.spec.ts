// visibility.spec.ts — v9.4 Task 3.4: interaction-fidelity + dashboard-visibility specs.
//
// Four test suites:
//
//   1. snap-back: navigate to each of the 5 nav routes while a slow
//      loadConfig() might be in flight; assert the URL stays on the clicked
//      tab AND the destination page container renders (does NOT revert to the
//      dashboard). The fix is the _mountSeq counter in app.js (each mountPage()
//      bumps it; stale in-flight renders abort their final DOM write when
//      `myId !== _mountSeq`).
//
//   2. tab-highlight: visit each route; assert aria-current="page" is set on
//      the correct .app-nav <a> and removed from all others. Grounded in
//      app.js updateNavActive() which sets/removes aria-current on `.app-nav a`.
//
//   3. activity-wall fidelity: cause a REAL activity event (write a finding
//      file into the mission dir the test server watches) and assert the
//      activity-wall DOM renders a row reflecting it. The wall is fed by
//      filesystem watchers on findings/signals/history/queue (activity_wall.py
//      _source_findings / _source_signals) → SSE → components/activity_wall.js
//      prependRow() → a .aw-row in [data-testid="aw-list"].
//
//   4. empty-state: load the /signals surface with no signals (fix-medium has
//      no signals/ dir) and assert the [data-testid="signals-empty"] empty
//      state renders (no silent placeholder fallthrough).
//
// Nav routes (from ui/static/index.html + app.js ROUTES):
//   /           → nav-dashboard → page container board-page
//   /tasks      → nav-tasks     → (no stable page-container testid; header h1)
//   /findings   → nav-findings  → findings-page
//   /signals    → nav-signals   → signals-page
//   /mission    → nav-mission   → mission-page
//
// Runs under chromium-default and webkit-default projects (fix-medium fixture,
// NON_MUTATION_DEFAULT_ENV: LIFESPAN_TEST_MODE=1, INPROCESS_APPLIER=1). In test
// mode the server still starts an ActivityWall watching the mission dir
// (server.py:1162-1167), so the finding-file stimulus produces a real SSE event.
// Non-gated pages render without auth; the activity-wall snapshot/SSE endpoints
// are cookie-gated, so the activity-wall test authenticates first.
//
// Run: ./scripts/run_e2e.sh ui/tests/e2e/visibility.spec.ts

import { test, expect } from "@playwright/test";
import { appendFileSync, existsSync, mkdirSync } from "node:fs";
import * as path from "node:path";
import { fixtureRootForProject, readUiToken } from "./_helpers";

// ---------------------------------------------------------------------------
// Route table. pageTestId is the destination page container's data-testid, or
// null when the page has no stable container testid (tasks renders a bare h1).
// ---------------------------------------------------------------------------

const NAV_ROUTES: Array<{
  path: string;
  navTestId: string;
  pageTestId: string | null;
}> = [
  { path: "/", navTestId: "nav-dashboard", pageTestId: "board-page" },
  { path: "/tasks", navTestId: "nav-tasks", pageTestId: null },
  { path: "/findings", navTestId: "nav-findings", pageTestId: "findings-page" },
  { path: "/signals", navTestId: "nav-signals", pageTestId: "signals-page" },
  { path: "/mission", navTestId: "nav-mission", pageTestId: "mission-page" },
];

function urlRe(routePath: string): RegExp {
  return new RegExp(`${routePath.replace("/", "\\/")}$`);
}

// ---------------------------------------------------------------------------
// Suite 1: snap-back prevention
// ---------------------------------------------------------------------------

test.describe("snap-back: navigation stays on clicked tab", () => {
  test("navigate each route rapidly — URL does not revert to dashboard", async ({
    page,
  }) => {
    await page.goto("/");
    // Wait just long enough for the nav to be in the DOM; don't wait for the
    // full grid render (which involves loadConfig()) — that creates the race
    // the _mountSeq fix prevents.
    await page.waitForSelector(".app-nav a", { timeout: 10_000 });

    for (const route of NAV_ROUTES) {
      if (route.path === "/") continue; // skip dashboard itself

      await page.locator(`[data-testid="${route.navTestId}"]`).click();

      await expect(page).toHaveURL(urlRe(route.path), { timeout: 5_000 });

      // Let any in-flight async render resolve, then re-check no snap-back.
      await page.waitForTimeout(300);
      await expect(page).toHaveURL(urlRe(route.path), { timeout: 2_000 });
    }
  });

  // Per-route snap-back: from / click each nav link; assert URL holds AND the
  // destination page container renders (proves the click won the mount race).
  for (const route of NAV_ROUTES) {
    if (route.path === "/") continue;

    test(`snap-back: clicking ${route.navTestId} from / lands on ${route.path}`, async ({
      page,
    }) => {
      await page.goto("/");
      await page.waitForSelector(`[data-testid="${route.navTestId}"]`, {
        timeout: 10_000,
      });

      await page.locator(`[data-testid="${route.navTestId}"]`).click();

      await expect(page).toHaveURL(urlRe(route.path), { timeout: 5_000 });

      // Wait then assert no snap-back to dashboard (the core regression check).
      await page.waitForTimeout(500);
      await expect(page).toHaveURL(urlRe(route.path), { timeout: 2_000 });

      // The destination page container must be present (where it has a testid).
      // board-page must NOT be present (would mean a snap-back to dashboard).
      if (route.pageTestId) {
        await expect(
          page.locator(`[data-testid="${route.pageTestId}"]`),
        ).toBeVisible({ timeout: 5_000 });
      }
      await expect(page.locator('[data-testid="board-page"]')).toHaveCount(0);
    });
  }
});

// ---------------------------------------------------------------------------
// Suite 2: tab-highlight (aria-current="page")
// ---------------------------------------------------------------------------

test.describe("tab-highlight: aria-current set on active nav link", () => {
  for (const route of NAV_ROUTES) {
    test(`aria-current on ${route.navTestId} when visiting ${route.path}`, async ({
      page,
    }) => {
      await page.goto(route.path);
      await page.waitForSelector(".app-nav a", { timeout: 10_000 });

      const activeLink = page.locator(`[data-testid="${route.navTestId}"]`);
      await expect(activeLink).toHaveAttribute("aria-current", "page", {
        timeout: 5_000,
      });

      for (const other of NAV_ROUTES) {
        if (other.navTestId === route.navTestId) continue;
        await expect(
          page.locator(`[data-testid="${other.navTestId}"]`),
        ).not.toHaveAttribute("aria-current", { timeout: 3_000 });
      }
    });
  }
});

// ---------------------------------------------------------------------------
// Suite 3: activity-wall fidelity (real cause → rendered DOM row)
//
// Authenticate (snapshot/SSE are cookie-gated), open the dashboard so the
// activity wall mounts + subscribes to SSE, then write a NEW finding file into
// the served mission dir. The ActivityWall findings watcher detects it and
// pushes a 'finding' SSE event; activity_wall.js prepends a .aw-row whose
// data-event-type="finding" and whose summary reflects the file.
// ---------------------------------------------------------------------------

test.describe("activity-wall fidelity: real finding event renders a row", () => {
  test("writing a finding file surfaces a finding row in the activity wall", async ({
    page,
  }, testInfo) => {
    const token = readUiToken(testInfo);
    const fixtureRoot = fixtureRootForProject(testInfo);

    // Authenticate so the cookie-gated activity-wall snapshot/SSE endpoints work.
    await page.goto(`/#t=${token}`);
    await expect(page).toHaveURL("/", { timeout: 10_000 });

    // The board does NOT auto-mount the activity wall — wait for the board page,
    // then open the wall via the toggle before asserting on the list.
    await expect(page.locator('[data-testid="board-page"]')).toBeVisible({ timeout: 10_000 });
    await page.locator('[data-testid="board-activity-toggle"]').click();

    // Now the activity wall mounts. Wait for the list to exist.
    const awList = page.locator('[data-testid="aw-list"]');
    await expect(awList).toBeVisible({ timeout: 10_000 });

    // Give the wall a moment to take its initial snapshot + open the SSE stream
    // (activity_wall.js: fetchSnapshot().then(startSSE)). The findings watcher
    // also takes its initial snapshot, so only files written AFTER this are new.
    await page.waitForTimeout(1500);

    // Cause a REAL activity event: write a new finding file into the served
    // mission dir. Filename grammar follows the existing fixture findings
    // (agent-<id>-<LANE>-<phase>-<topic>-<UTC>.md) so the lane parses to "A".
    const findingsDir = path.join(fixtureRoot, "findings");
    if (!existsSync(findingsDir)) mkdirSync(findingsDir, { recursive: true });
    const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19) + "Z";
    const findingName = `agent-vis0-A-P1-visibility-stimulus-${ts}.md`;
    appendFileSync(
      path.join(findingsDir, findingName),
      "---\nlane: A\nseverity: MINOR\n---\nactivity-wall fidelity stimulus\n",
      "utf-8",
    );

    // Assert a finding row appears in the activity wall (DOM-level fidelity).
    // Match on the type chip's data-event-type so we assert the rendered row,
    // not merely the container's presence.
    const findingRow = page.locator(
      '[data-testid="aw-list"] .aw-row[data-event-type="finding"]',
    );
    await expect(findingRow.first()).toBeVisible({ timeout: 15_000 });

    // The rendered row must carry the lane we encoded (lane "A") — proves the
    // DOM reflects the actual file, not just any finding row.
    await expect(findingRow.first()).toHaveAttribute("data-event-lane", "A", {
      timeout: 5_000,
    });
  });
});

// ---------------------------------------------------------------------------
// Suite 4: empty-state fidelity (no silent placeholder fallthrough)
//
// fix-medium has no signals/ dir, so /signals must render the explicit
// signals-empty empty state (signals.js renderThreads → "No signals yet.").
// ---------------------------------------------------------------------------

test.describe("empty-state: signals surface renders explicit empty state", () => {
  test("/signals with no signals shows signals-empty (not a blank fallthrough)", async ({
    page,
  }) => {
    await page.goto("/signals");

    // Page container must render.
    await expect(page.locator('[data-testid="signals-page"]')).toBeVisible({
      timeout: 10_000,
    });

    // The explicit empty-state element must render with its labelled copy.
    const empty = page.locator('[data-testid="signals-empty"]');
    await expect(empty).toBeVisible({ timeout: 8_000 });
    await expect(empty).toHaveText(/No signals yet/i);

    // No actual signal thread CARDS should be present (no fallthrough render).
    // Target the card class — NOT a "signals-thread-" testid prefix, which
    // would also match the always-present empty signals-thread-list container.
    await expect(page.locator(".signals-thread-card")).toHaveCount(0);
  });
});
