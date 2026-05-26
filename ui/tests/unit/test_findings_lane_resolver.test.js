// test_findings_lane_resolver.test.js — Wave 4 P2 cleanup.
//
// Runner: node:test (built-in, Node >= 18).
// Run:    node --test ui/tests/unit/test_findings_lane_resolver.test.js
//
// findings.js used to carry a HARDCODED single-letter short→name map
// (LANE_SHORT_TO_NAME) that silently diverged from a mission's real lane config.
// It now derives the resolver from /api/v1/config's `lanes` array via
// buildLaneResolver(config), with a static fallback so the no-config path never
// regresses to bare short codes. These tests pin both behaviours.

import { test } from "node:test";
import assert from "node:assert/strict";

const { buildLaneResolver } = await import("../../static/pages/findings.js");

test("derives lane names from config.lanes (live source of truth)", () => {
  const resolve = buildLaneResolver({
    lanes: [
      { name: "LANE-A", short: "A" },
      { name: "ALPHA", short: "X" },
    ],
  });
  assert.equal(resolve("A"), "LANE-A");
  assert.equal(resolve("X"), "ALPHA");
  // Config takes precedence over the static fallback for the same short code.
  assert.equal(resolve("a"), "LANE-A", "case-insensitive on the short code");
});

test("falls back to the static default map when config is absent", () => {
  // null config (fetch failed / pre-auth) → static fallback still resolves
  // the canonical 6-lane set rather than echoing the bare short code.
  const resolve = buildLaneResolver(null);
  assert.equal(resolve("A"), "AUDIT");
  assert.equal(resolve("D"), "FRONTEND");
  assert.equal(resolve("F"), "META");
});

test("config without a given short falls through to fallback, then to the short", () => {
  const resolve = buildLaneResolver({ lanes: [{ name: "ALPHA", short: "X" }] });
  // "X" is config-defined.
  assert.equal(resolve("X"), "ALPHA");
  // "B" is not in config but is in the static fallback.
  assert.equal(resolve("B"), "ARCHITECT");
  // "Z" is in neither → echo the (upper-cased) short code.
  assert.equal(resolve("Z"), "Z");
});

test("ignores malformed config entries without throwing", () => {
  const resolve = buildLaneResolver({
    lanes: [null, {}, { name: "" }, { short: "Q" }, { name: "GOOD", short: "G" }],
  });
  assert.equal(resolve("G"), "GOOD");
  // Entry with a short but no name is skipped → fallback / echo.
  assert.equal(resolve("Q"), "Q");
});

test("empty / non-array lanes → pure fallback behaviour", () => {
  assert.equal(buildLaneResolver({ lanes: [] })("A"), "AUDIT");
  assert.equal(buildLaneResolver({})("C"), "BACKEND");
  assert.equal(buildLaneResolver({ lanes: "nope" })("E"), "TEST");
});
