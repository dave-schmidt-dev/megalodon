// test_router_path_params.test.js — Unit tests for the path-param router in app.js.
//
// Runner: node:test (built-in, Node >= 18).
// Run:    node --test ui/tests/unit/test_router_path_params.test.js
//
// Tests the matchRoute() exported function and _mountSeq race guard by
// exercising the router logic directly without a real browser.

import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";

// ---------------------------------------------------------------------------
// Minimal DOM stubs required so app.js loads under Node without throwing.
// ---------------------------------------------------------------------------

if (typeof globalThis.localStorage === "undefined") {
  const mem = new Map();
  globalThis.localStorage = {
    getItem: (k) => (mem.has(k) ? mem.get(k) : null),
    setItem: (k, v) => mem.set(k, String(v)),
    removeItem: (k) => mem.delete(k),
    clear: () => mem.clear(),
  };
}

const _listeners = {};
globalThis.document = {
  readyState: "complete",
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: (ev, fn) => {
    if (!_listeners[ev]) _listeners[ev] = [];
    _listeners[ev].push(fn);
  },
  createElement: (tag) => ({
    tag,
    textContent: "",
    className: "",
    setAttribute: () => {},
    getAttribute: () => null,
    removeAttribute: () => {},
    appendChild: () => {},
    removeChild: () => {},
    get firstChild() { return null; },
    dataset: {},
    hasAttribute: () => false,
  }),
};

globalThis.window = {
  addEventListener: () => {},
};

globalThis.history = {
  pushState: () => {},
};

globalThis.location = { pathname: "/" };

// ---------------------------------------------------------------------------
// Import the module under test after stubs are in place.
// app.js calls bootstrap() synchronously (readyState === "complete"), which
// calls attachControlToggle / attachPhaseIndicator / etc. — all safe to no-op
// via the null-returning querySelector stub above.
// ---------------------------------------------------------------------------

const { matchRoute, ROUTES } = await import("../../static/js/app.js");

// ---------------------------------------------------------------------------
// matchRoute — pattern matching and params extraction
// ---------------------------------------------------------------------------

describe("matchRoute", () => {
  test("/lane/A resolves to lane_detail loader with params {short:'A'}", () => {
    const result = matchRoute("/lane/A");
    assert.deepEqual(result.params, { short: "A" });
    // loader must be a function (the dynamic-import thunk)
    assert.equal(typeof result.loader, "function");
    // Verify it's the lane_detail route by checking params shape
    assert.ok("short" in result.params, "params must have short key");
    assert.equal(result.params.short, "A");
  });

  test("/lane/my-lane-01 resolves with correct short param", () => {
    const result = matchRoute("/lane/my-lane-01");
    assert.deepEqual(result.params, { short: "my-lane-01" });
  });

  test("/tasks resolves with empty params {}", () => {
    const result = matchRoute("/tasks");
    assert.deepEqual(result.params, {});
    assert.equal(typeof result.loader, "function");
  });

  test("/findings resolves with empty params", () => {
    const result = matchRoute("/findings");
    assert.deepEqual(result.params, {});
  });

  test("/signals resolves with empty params", () => {
    const result = matchRoute("/signals");
    assert.deepEqual(result.params, {});
  });

  test("/mission resolves with empty params", () => {
    const result = matchRoute("/mission");
    assert.deepEqual(result.params, {});
  });

  test("/approval-rules resolves with empty params", () => {
    const result = matchRoute("/approval-rules");
    assert.deepEqual(result.params, {});
  });

  test("/ resolves with empty params", () => {
    const result = matchRoute("/");
    assert.deepEqual(result.params, {});
  });

  test("unknown path /garbage-xyz falls back to grid route (first route)", () => {
    const result = matchRoute("/garbage-xyz");
    // Must fall back to the same loader as "/"
    const rootResult = matchRoute("/");
    assert.deepEqual(result.params, {});
    // Both must be the same loader reference (ROUTES[0].loader)
    assert.equal(result.loader, rootResult.loader, "unknown path uses grid (first) loader");
  });

  test("unknown path /lane/ (no short) falls back to grid route", () => {
    // /lane/ with no short slug should not match the lane pattern
    const result = matchRoute("/lane/");
    const rootResult = matchRoute("/");
    assert.equal(result.loader, rootResult.loader, "/lane/ falls back to grid");
  });

  test("unknown path /tasks/extra falls back to grid route", () => {
    const result = matchRoute("/tasks/extra");
    const rootResult = matchRoute("/");
    assert.equal(result.loader, rootResult.loader);
  });
});

// ---------------------------------------------------------------------------
// ROUTES array shape
// ---------------------------------------------------------------------------

describe("ROUTES", () => {
  test("ROUTES is an array with at least 7 entries", () => {
    assert.ok(Array.isArray(ROUTES), "ROUTES must be an array");
    assert.ok(ROUTES.length >= 7, `expected >= 7 routes, got ${ROUTES.length}`);
  });

  test("every route has pattern, loader, params", () => {
    for (const route of ROUTES) {
      assert.ok(route.pattern instanceof RegExp, "pattern must be a RegExp");
      assert.equal(typeof route.loader, "function", "loader must be a function");
      assert.equal(typeof route.params, "function", "params must be a function");
    }
  });

  test("lane_detail route params extractor returns {short} from match", () => {
    const laneRoute = ROUTES.find((r) => r.pattern.source.includes("lane"));
    assert.ok(laneRoute, "lane route must exist");
    const m = "/lane/ABC-123".match(laneRoute.pattern);
    assert.ok(m, "lane pattern must match /lane/ABC-123");
    assert.deepEqual(laneRoute.params(m), { short: "ABC-123" });
  });

  test("lane_detail route does not match /lane/ with empty slug", () => {
    const laneRoute = ROUTES.find((r) => r.pattern.source.includes("lane"));
    assert.ok(laneRoute);
    const m = "/lane/".match(laneRoute.pattern);
    assert.equal(m, null, "empty slug must not match");
  });

  test("first route is the grid (root) fallback route", () => {
    assert.ok(ROUTES[0].pattern.test("/"), "first route must match /");
    assert.deepEqual(ROUTES[0].params([]), {}, "grid route returns empty params");
  });
});

// ---------------------------------------------------------------------------
// _mountSeq race guard — simulate rapid navigation A→B→C, verify the guard
// fires. We can't call mountPage (needs a real DOM with #app-root), so we
// replicate the guard logic in isolation and verify correctness.
// ---------------------------------------------------------------------------

describe("_mountSeq race guard logic", () => {
  test("only the last navigation wins when three overlap", async () => {
    // Reproduce the exact guard pattern from mountPage:
    //   const myId = ++seq; ... after await: if (myId !== seq) return;
    let seq = 0;
    const results = [];

    async function fakeMount(label, delayMs) {
      const myId = ++seq;
      // Simulate async work (dynamic import + render)
      await new Promise((r) => setTimeout(r, delayMs));
      if (myId !== seq) return;
      results.push(label);
    }

    // Fire A (slow), B (medium), C (fast) — only C should complete.
    const a = fakeMount("A", 30);
    const b = fakeMount("B", 20);
    const c = fakeMount("C", 10);

    await Promise.all([a, b, c]);

    assert.deepEqual(results, ["C"], "only C's render should be recorded");
  });

  test("sequential navigations (non-overlapping) all complete", async () => {
    let seq = 0;
    const results = [];

    async function fakeMount(label) {
      const myId = ++seq;
      await new Promise((r) => setTimeout(r, 5));
      if (myId !== seq) return;
      results.push(label);
    }

    await fakeMount("A");
    await fakeMount("B");
    await fakeMount("C");

    assert.deepEqual(results, ["A", "B", "C"], "sequential navigations all complete");
  });
});
