// test_signal_filename_parse.test.js — Unit tests for parseSignalFilename().
//
// Runner: node:test (built-in, Node >= 18).
// Run:    node --test ui/tests/unit/test_signal_filename_parse.test.js
//
// Covers the frozen wire-contract grammar:
//   LANE-<FROM>-to-LANE-<TO>-<topic>-<UTC>.md
// plus the legacy (no trailing UTC) and final fallbacks. The UTC stamp is
// anchored at the end so a dashed topic is parsed correctly.

import { test } from "node:test";
import assert from "node:assert/strict";
import { parseSignalFilename } from "../../static/pages/signals.js";

test("canonical: parses from/to/topic/utc with the UTC anchored at the end", () => {
  const r = parseSignalFilename("LANE-A-to-LANE-B-code-review-2026-05-25T18-49Z.md");
  assert.equal(r.sender_lane, "LANE-A");
  assert.equal(r.receiver_lane, "LANE-B");
  assert.equal(r.topic, "code-review");
  assert.equal(r.utc, "2026-05-25T18-49Z");
});

test("canonical: a dashed multi-word topic is kept whole (greedy middle)", () => {
  const r = parseSignalFilename("LANE-C-to-LANE-D-handoff-parser-grammar-2026-05-25T18-49-30Z.md");
  assert.equal(r.sender_lane, "LANE-C");
  assert.equal(r.receiver_lane, "LANE-D");
  assert.equal(r.topic, "handoff-parser-grammar");
  assert.equal(r.utc, "2026-05-25T18-49-30Z");
});

test("canonical: seconds-precision UTC variant parses", () => {
  const r = parseSignalFilename("LANE-A-to-LANE-B-x-2026-01-02T03-04-05Z.md");
  assert.equal(r.utc, "2026-01-02T03-04-05Z");
  assert.equal(r.topic, "x");
});

test("legacy: no trailing UTC → topic is the rest, utc empty", () => {
  const r = parseSignalFilename("LANE-A-to-LANE-B-code-review.md");
  assert.equal(r.sender_lane, "LANE-A");
  assert.equal(r.receiver_lane, "LANE-B");
  assert.equal(r.topic, "code-review");
  assert.equal(r.utc, "");
});

test("fallback: non-matching name → '?' lanes, base topic, empty utc", () => {
  const r = parseSignalFilename("not-a-signal.md");
  assert.equal(r.sender_lane, "?");
  assert.equal(r.receiver_lane, "?");
  assert.equal(r.topic, "not-a-signal");
  assert.equal(r.utc, "");
});

test("numeric lane ids are accepted", () => {
  const r = parseSignalFilename("LANE-A1-to-LANE-B2-topic-2026-05-25T18-49Z.md");
  assert.equal(r.sender_lane, "LANE-A1");
  assert.equal(r.receiver_lane, "LANE-B2");
});
