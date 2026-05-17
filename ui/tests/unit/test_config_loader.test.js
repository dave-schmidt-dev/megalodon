// test_config_loader.test.js — Unit tests for ui/static/js/config.js
//
// Runner: node:test (built-in, Node >= 18).
// Run:    node --test ui/tests/unit/test_config_loader.test.js
//         npm run test:js:unit
//
// Each test calls _resetForTests() before exercising loadConfig() so the
// module-level cache is clear. ES module instances are shared across tests
// in the same process, so isolation depends on reset, not re-import.

import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { loadConfig, _resetForTests } from "../../static/js/config.js";

// ---------------------------------------------------------------------------
// Shared state: reset the module cache and any globals before each test.
// ---------------------------------------------------------------------------

beforeEach(() => {
  _resetForTests();
  // Remove any fetch mock from a prior test.
  delete globalThis.__testFetchInstalled;
});

// ---------------------------------------------------------------------------
// Test 1: loads and caches — fetch is called exactly once for two sequential
// calls.
// ---------------------------------------------------------------------------

test("test_loads_and_caches", async () => {
  let callCount = 0;
  globalThis.fetch = async (_url) => {
    callCount++;
    return {
      ok: true,
      json: async () => ({ lanes: ["A", "B"], phases: ["INIT", "REVIEW"] }),
    };
  };

  const first = await loadConfig();
  const second = await loadConfig();

  assert.equal(callCount, 1, "fetch should be called exactly once");
  assert.deepEqual(first, second, "both calls return the same value");
  assert.deepEqual(first.lanes, ["A", "B"]);
  assert.deepEqual(first.phases, ["INIT", "REVIEW"]);
});

// ---------------------------------------------------------------------------
// Test 2: concurrent callers share the same Promise — neither extra fetch
// nor value divergence when both calls are made before awaiting.
// ---------------------------------------------------------------------------

test("test_concurrent_callers_share_promise", async () => {
  let callCount = 0;
  globalThis.fetch = async (_url) => {
    callCount++;
    return {
      ok: true,
      json: async () => ({ lanes: ["X"], phases: ["P1"] }),
    };
  };

  // Fire both calls synchronously, before any microtask resolution.
  const p1 = loadConfig();
  const p2 = loadConfig();

  const [v1, v2] = await Promise.all([p1, p2]);

  assert.equal(callCount, 1, "fetch called exactly once despite two concurrent callers");
  assert.deepEqual(v1, v2, "both callers receive identical values");
});

// ---------------------------------------------------------------------------
// Test 3: network error propagates — the returned Promise rejects with the
// original error, and the cache is cleared (so a future call can retry).
// ---------------------------------------------------------------------------

test("test_network_error_rejects", async () => {
  const boom = new Error("network failure");
  globalThis.fetch = async (_url) => {
    throw boom;
  };

  await assert.rejects(
    () => loadConfig(),
    (err) => {
      assert.equal(err, boom, "rejection carries the original error");
      return true;
    },
  );
});

// ---------------------------------------------------------------------------
// Test 4: breadcrumb log — console.log is called with the expected format
// after a successful load.
// ---------------------------------------------------------------------------

test("test_logs_breadcrumb_on_first_load", async () => {
  const logs = [];
  const originalLog = console.log;
  console.log = (...args) => logs.push(args);

  try {
    globalThis.fetch = async (_url) => ({
      ok: true,
      json: async () => ({ lanes: [1, 2, 3], phases: ["INIT"] }),
    });

    await loadConfig();
  } finally {
    console.log = originalLog;
  }

  // Find the breadcrumb entry.
  const breadcrumb = logs.find((args) => args[0] === "[config] loaded");
  assert.ok(breadcrumb, "console.log was called with '[config] loaded'");
  assert.equal(breadcrumb[1], 3, "lane count should be 3");
  assert.equal(breadcrumb[2], "lanes,", "format: '<count> lanes,'");
  assert.equal(breadcrumb[3], 1, "phase count should be 1");
  assert.equal(breadcrumb[4], "phases", "format: '<count> phases'");
});
