// test_signal_merge_dedupe.test.js — Unit tests for CONTRACT-SIGNAL-ID dedupe.
//
// Runner: node:test (built-in, Node >= 18).
// Run:    node --test ui/tests/unit/test_signal_merge_dedupe.test.js
//
// Covers the Fix R3-1 requirement: the mergedSignals() merge MUST dedupe on
// the new stable id ("sig-<sha1[:12]>") so:
//   a) Two events with DIFFERENT ids both survive — no cross-generation drop.
//   b) Two events with the SAME id dedupe to one entry (live wins over snapshot).
//
// mergedSignals() is a closure inside render(), but its dedupe core is the
// exported module-level pure function `mergeSignals(snapshot, liveByKey)` in
// signals.js. We import and exercise that REAL function here (no DOM needed) so
// the test verifies the production merge/dedupe, not a local copy.

import { test } from "node:test";
import assert from "node:assert/strict";

import { mergeSignals as mergedSignals } from "../../static/pages/signals.js";

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test("two signals with different ids both survive the merge (no cross-gen drop)", () => {
  const snapshot = [
    { id: "sig-aaaaaaaaaaaa", filename: "sig-aaaaaaaaaaaa", topic: "alpha" },
    { id: "sig-bbbbbbbbbbbb", filename: "sig-bbbbbbbbbbbb", topic: "beta" },
  ];
  const live = new Map(); // no live events — snapshot only

  const result = mergedSignals(snapshot, live);
  assert.equal(result.length, 2, "both signals survive");
  const topics = result.map((s) => s.topic).sort();
  assert.deepEqual(topics, ["alpha", "beta"]);
});

test("two signals with same id dedupe to one (latest/live wins)", () => {
  const snapshot = [
    { id: "sig-aaaaaaaaaaaa", filename: "sig-aaaaaaaaaaaa", topic: "old" },
  ];
  const live = new Map([
    ["sig-aaaaaaaaaaaa", { id: "sig-aaaaaaaaaaaa", filename: "sig-aaaaaaaaaaaa", topic: "new" }],
  ]);

  const result = mergedSignals(snapshot, live);
  assert.equal(result.length, 1, "deduped to one");
  assert.equal(result[0].topic, "new", "live entry wins");
});

test("file signals without id still dedupe on filename", () => {
  const snapshot = [
    { filename: "LANE-A-to-LANE-B-review-2026-05-25T18-49Z.md", topic: "review", id: "" },
  ];
  const live = new Map([
    [
      "LANE-A-to-LANE-B-review-2026-05-25T18-49Z.md",
      { filename: "LANE-A-to-LANE-B-review-2026-05-25T18-49Z.md", topic: "review-updated", id: "" },
    ],
  ]);

  const result = mergedSignals(snapshot, live);
  assert.equal(result.length, 1, "file signals dedupe by filename");
  assert.equal(result[0].topic, "review-updated", "live wins");
});

test("status-note signals with distinct ids both survive (old cross-gen bug)", () => {
  // Regression: the old code keyed on `filename` which was always "status-note"
  // for status-note events — so two different status-notes collapsed to one.
  // With stable ids ("sig-<sha1[:12]>") each has a unique key.
  const snapshot = [
    { id: "sig-111111111111", filename: "status-note", topic: "note-1", source: "status-note" },
    { id: "sig-222222222222", filename: "status-note", topic: "note-2", source: "status-note" },
  ];
  const live = new Map();

  const result = mergedSignals(snapshot, live);
  assert.equal(result.length, 2, "both status-notes survive with distinct ids");
});

test("live entry with stable id overwrites snapshot entry with same id", () => {
  const snapshot = [
    { id: "sig-deadbeef1234", filename: "status-note", body: "old body", source: "status-note" },
  ];
  const live = new Map([
    ["sig-deadbeef1234", { id: "sig-deadbeef1234", filename: "status-note", body: "new body", source: "status-note" }],
  ]);

  const result = mergedSignals(snapshot, live);
  assert.equal(result.length, 1, "deduped");
  assert.equal(result[0].body, "new body", "live wins");
});

test("mixed: file + status-note signals all survive independently", () => {
  const snapshot = [
    { id: "", filename: "LANE-A-to-LANE-B-code-review-2026T01Z.md", topic: "code-review" },
    { id: "sig-111111111111", filename: "status-note", topic: "note-1" },
  ];
  const live = new Map([
    ["sig-222222222222", { id: "sig-222222222222", filename: "status-note", topic: "note-2" }],
  ]);

  const result = mergedSignals(snapshot, live);
  assert.equal(result.length, 3, "all three survive");
});
