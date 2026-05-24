// test_grid_removed.test.js — Task 3.5a: grid-removal guard (CV-2).
//
// Runner: node:test (built-in, Node >= 18).
// Run:    node --test ui/tests/unit/test_grid_removed.test.js
//
// Asserts the board migration left no grid.js residue in the live code paths:
//   1. Zero `grid-page` testid occurrences remain anywhere under ui/tests/e2e
//      (every page sentinel was migrated to `board-page`).
//   2. Zero ES `import`s of grid.js remain under ui/static/js and
//      ui/static/pages (the default route now imports board.js).
//
// NOTE: grid.js the FILE may still exist on disk — its deletion is Task 3.4.
// This guard only asserts there are no IMPORTS referencing it, and no stray
// grid-page sentinels in the e2e suite. Comment mentions of "grid.js" are
// fine and intentionally ignored (we only match import statements).

import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");

/**
 * Recursively collect file paths under `dir` whose name matches `filterRe`.
 * @param {string} dir
 * @param {RegExp} filterRe
 * @returns {string[]}
 */
function collectFiles(dir, filterRe) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry);
    const st = statSync(full);
    if (st.isDirectory()) {
      out.push(...collectFiles(full, filterRe));
    } else if (filterRe.test(entry)) {
      out.push(full);
    }
  }
  return out;
}

describe("grid removal guard", () => {
  test("no grid-page sentinel remains under ui/tests/e2e", () => {
    const e2eDir = path.join(REPO_ROOT, "ui", "tests", "e2e");
    const specs = collectFiles(e2eDir, /\.(ts|js)$/);
    /** @type {string[]} */
    const offenders = [];
    for (const file of specs) {
      const text = readFileSync(file, "utf-8");
      if (text.includes("grid-page")) offenders.push(path.relative(REPO_ROOT, file));
    }
    assert.deepEqual(
      offenders,
      [],
      `grid-page sentinel must be fully migrated to board-page; found in: ${offenders.join(", ")}`,
    );
  });

  test("no import of grid.js remains under ui/static/js and ui/static/pages", () => {
    const dirs = [
      path.join(REPO_ROOT, "ui", "static", "js"),
      path.join(REPO_ROOT, "ui", "static", "pages"),
    ];
    // Match ES import / dynamic-import references to grid.js, e.g.:
    //   import x from "../pages/grid.js"
    //   import("../pages/grid.js")
    //   export ... from "./grid.js"
    // but NOT bare comment mentions of "grid.js".
    const importRe = /\b(?:import|export)\b[^\n;]*['"][^'"]*\/grid\.js['"]/;
    const dynImportRe = /\bimport\s*\(\s*['"][^'"]*\/grid\.js['"]\s*\)/;
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
    assert.deepEqual(
      offenders,
      [],
      `grid.js must not be imported anywhere; found: ${offenders.join(" | ")}`,
    );
  });
});
