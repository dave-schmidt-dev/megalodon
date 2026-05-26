// test_dashboard_removed.test.js — Wave 4 P2 cleanup (dead-code guard).
//
// Runner: node:test (built-in, Node >= 18).
// Run:    node --test ui/tests/unit/test_dashboard_removed.test.js
//
// The unrouted dead `/` page module ui/static/pages/dashboard.js was deleted in
// Wave 4 (the `/` route loads board.js). This guard asserts:
//   1. The file no longer exists on disk.
//   2. No ES import / dynamic-import of dashboard.js remains under
//      ui/static/js or ui/static/pages.
//
// NOTE: dashboard-v92.js is INTENTIONALLY retained — it is still reachable
// behind the live MEGALODON_V92_DASHBOARD server gate and exercised by the
// chromium/webkit-v92-dashboard Playwright projects. This guard must NOT match
// it; the regexes below target `dashboard.js` exactly (a `/` or start-of-name
// boundary before "dashboard.js"), so "dashboard-v92.js" is never flagged.

import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync, existsSync } from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");

/** @param {string} dir @param {RegExp} filterRe @returns {string[]} */
function collectFiles(dir, filterRe) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry);
    const st = statSync(full);
    if (st.isDirectory()) out.push(...collectFiles(full, filterRe));
    else if (filterRe.test(entry)) out.push(full);
  }
  return out;
}

describe("dashboard.js removal guard", () => {
  test("ui/static/pages/dashboard.js no longer exists", () => {
    const p = path.join(REPO_ROOT, "ui", "static", "pages", "dashboard.js");
    assert.equal(existsSync(p), false, "dead dashboard.js must be deleted");
  });

  test("dashboard-v92.js IS retained (still reachable behind the v92 gate)", () => {
    const p = path.join(REPO_ROOT, "ui", "static", "pages", "dashboard-v92.js");
    assert.equal(existsSync(p), true, "dashboard-v92.js must remain — it is gated, not dead");
  });

  test("no import of dashboard.js remains under ui/static/js and ui/static/pages", () => {
    const dirs = [
      path.join(REPO_ROOT, "ui", "static", "js"),
      path.join(REPO_ROOT, "ui", "static", "pages"),
    ];
    // Match import/export references to .../dashboard.js but NOT dashboard-v92.js:
    // require a `/` or quote immediately before "dashboard.js".
    const importRe = /\b(?:import|export)\b[^\n;]*['"][^'"]*[\/'"]dashboard\.js['"]/;
    const dynImportRe = /\bimport\s*\(\s*['"][^'"]*[\/'"]dashboard\.js['"]\s*\)/;
    /** @type {string[]} */
    const offenders = [];
    for (const dir of dirs) {
      for (const file of collectFiles(dir, /\.js$/)) {
        const text = readFileSync(file, "utf-8");
        for (const line of text.split("\n")) {
          if (importRe.test(line) || dynImportRe.test(line)) {
            offenders.push(`${path.relative(REPO_ROOT, file)}: ${line.trim()}`);
          }
        }
      }
    }
    assert.deepEqual(offenders, [], `dashboard.js must not be imported; found: ${offenders.join(" | ")}`);
  });
});
