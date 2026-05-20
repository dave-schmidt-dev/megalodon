// test_store_nested_notify.test.js — Unit tests for the nested-key
// notification semantics of ui/static/js/store.js.
//
// Runner: node:test (built-in, Node >= 18).
// Run:    node --test ui/tests/unit/test_store_nested_notify.test.js
//
// Regression coverage for bug-3 (2026-05-19): hydrate() calls
// `store.set("mission", {phase: "PHASE-PLAN", ...})`. Before the fix this
// notified subscribers of "mission" and the root "" but NOT subscribers of
// "mission.phase", so the phase strip in the header stayed stuck on INIT
// after the operator flipped the backend phase. _emitChangedDescendants in
// store.js now fans out to subscribed nested paths whose value changed.

import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

// Stub localStorage so the store module's CONTROL_MODE_KEY read does not
// throw under node:test (which has no DOM).
if (typeof globalThis.localStorage === "undefined") {
  const mem = new Map();
  globalThis.localStorage = {
    getItem: (k) => (mem.has(k) ? mem.get(k) : null),
    setItem: (k, v) => mem.set(k, String(v)),
    removeItem: (k) => mem.delete(k),
    clear: () => mem.clear(),
  };
}

// Re-import the store class each test by clearing the module cache is not
// supported under ESM; instead create fresh Store() instances directly.
import { Store } from "../../static/js/store.js";

let store;
beforeEach(() => {
  store = new Store();
});

// ---------------------------------------------------------------------------
// Bug-3 regression: setting a parent object fires nested-key subscribers
// whose value actually changed.
// ---------------------------------------------------------------------------

test("set(parent, obj) notifies nested-key subscribers whose value changed", () => {
  const calls = [];
  store.subscribe("mission.phase", (next, prev) => calls.push({ next, prev }));

  // Initial state has mission.phase = "". Replacing the parent object with
  // {phase: "PHASE-PLAN", ...} must fire the nested subscriber exactly once.
  store.set("mission", { phase: "PHASE-PLAN", events: [], missionStatus: "" });

  assert.equal(calls.length, 1, "subscriber fired exactly once");
  assert.equal(calls[0].next, "PHASE-PLAN");
  assert.equal(calls[0].prev, "");
});

test("set(parent, obj) does NOT notify nested subscribers when value unchanged", () => {
  store.set("mission", { phase: "PHASE-PLAN", events: [], missionStatus: "" });

  const calls = [];
  store.subscribe("mission.phase", (next) => calls.push(next));

  // Replace the parent again with the same phase value — subscriber must NOT
  // fire (no spurious renders).
  store.set("mission", { phase: "PHASE-PLAN", events: [{ kind: "x" }], missionStatus: "ok" });

  assert.equal(calls.length, 0, "subscriber did not fire on unchanged value");
});

test("hydrate() drives nested-key subscribers", () => {
  const phases = [];
  const statuses = [];
  store.subscribe("mission.phase", (next) => phases.push(next));
  store.subscribe("mission.missionStatus", (next) => statuses.push(next));

  store.hydrate({
    mission: { phase: "PHASE-BUILD", events: [], missionStatus: "running" },
  });

  assert.deepEqual(phases, ["PHASE-BUILD"], "phase subscriber fired with new value");
  assert.deepEqual(statuses, ["running"], "missionStatus subscriber fired with new value");
});

// ---------------------------------------------------------------------------
// Backwards-compatibility: existing notification semantics still hold.
// ---------------------------------------------------------------------------

test("set(nested) still notifies the exact-path subscriber", () => {
  const calls = [];
  store.subscribe("mission.phase", (next) => calls.push(next));
  store.set("mission.phase", "PHASE-VERIFY");
  assert.deepEqual(calls, ["PHASE-VERIFY"]);
});

test("set(nested) still notifies ancestor-path subscribers", () => {
  const calls = [];
  store.subscribe("mission", () => calls.push("mission"));
  store.subscribe("", () => calls.push("root"));
  store.set("mission.phase", "PHASE-RUN");
  assert.ok(calls.includes("mission"), "ancestor 'mission' notified");
  assert.ok(calls.includes("root"), "root '' notified");
});

test("set(nested) does NOT spuriously fire sibling nested-key subscribers", () => {
  const calls = [];
  store.subscribe("mission.missionStatus", (next) => calls.push(next));
  store.set("mission.phase", "PHASE-PLAN");
  assert.equal(calls.length, 0, "sibling subscriber stays quiet");
});

// ---------------------------------------------------------------------------
// Root-level replacement also fans out to subscribed nested paths.
// ---------------------------------------------------------------------------

test("set('', root) fans out to nested-key subscribers", () => {
  const calls = [];
  store.subscribe("mission.phase", (next) => calls.push(next));
  const next = {
    status: { lanes: [], lastUtc: "" },
    tasks: { phases: {}, cross: [] },
    findings: { list: [], byFilename: {} },
    signals: { list: [] },
    claims: { list: [] },
    activitySummaries: {},
    mission: { phase: "COMPLETE", events: [], missionStatus: "" },
    config: {},
    ui: { controlMode: false, lastEventId: "", connectionStatus: "connected" },
  };
  store.set("", next);
  assert.deepEqual(calls, ["COMPLETE"]);
});
