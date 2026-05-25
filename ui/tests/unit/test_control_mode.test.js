// test_control_mode.test.js — Unit tests for the control-mode helpers added to
// ui/static/js/store.js (Wave 3 FE safety).
//
// Runner: node:test (built-in, Node >= 18).
// Run:    node --test ui/tests/unit/test_control_mode.test.js
//
// controlEnabled() is the single read every state-changing affordance consults;
// onControlMode(fn) fires immediately with the current value then on each flip.
// READ-ONLY (false) is the safe default.

import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

// Stub localStorage before importing the store module (its initialState reads
// CONTROL_MODE_KEY). Start read-only.
if (typeof globalThis.localStorage === "undefined") {
  const mem = new Map();
  globalThis.localStorage = {
    getItem: (k) => (mem.has(k) ? mem.get(k) : null),
    setItem: (k, v) => mem.set(k, String(v)),
    removeItem: (k) => mem.delete(k),
    clear: () => mem.clear(),
  };
}

const { store, controlEnabled, onControlMode } = await import("../../static/js/store.js");

beforeEach(() => {
  store.set("ui.controlMode", false);
});

test("controlEnabled() defaults to false (read-only safe default)", () => {
  store.set("ui.controlMode", false);
  assert.equal(controlEnabled(), false);
});

test("controlEnabled() reflects store flips", () => {
  store.set("ui.controlMode", true);
  assert.equal(controlEnabled(), true);
  store.set("ui.controlMode", false);
  assert.equal(controlEnabled(), false);
});

test("onControlMode fires immediately with current value", () => {
  store.set("ui.controlMode", false);
  const seen = [];
  const unsub = onControlMode((on) => seen.push(on));
  assert.deepEqual(seen, [false], "fires once immediately with current value");
  unsub();
});

test("onControlMode fires on every flip until unsubscribed", () => {
  store.set("ui.controlMode", false);
  const seen = [];
  const unsub = onControlMode((on) => seen.push(on));
  store.set("ui.controlMode", true);
  store.set("ui.controlMode", false);
  assert.deepEqual(seen, [false, true, false]);
  unsub();
  store.set("ui.controlMode", true);
  assert.deepEqual(seen, [false, true, false], "no fire after unsubscribe");
});
