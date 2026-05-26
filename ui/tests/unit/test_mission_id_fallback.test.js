// test_mission_id_fallback.test.js — audit I5.
//
// Runner: node:test (built-in, Node >= 18).
// Run:    node --test ui/tests/unit/test_mission_id_fallback.test.js
//
// /mission used to render "—" for the mission id whenever MISSION.md carried no
// machine-readable id. resolveMissionId(mission, config) now falls back through
// config-declared id/description, a MISSION.md title/heading, then the leading
// token of the most-recent mission event, so the header shows something useful.

import { test } from "node:test";
import assert from "node:assert/strict";

const { resolveMissionId } = await import("../../static/pages/mission.js");

test("prefers the canonical machine id when present", () => {
  assert.equal(
    resolveMissionId({ id: "fix-small-smoke-test", status: "ACTIVE" }, {}),
    "fix-small-smoke-test",
  );
  // A real id wins even when config also declares one.
  assert.equal(
    resolveMissionId({ id: "real-id" }, { mission: { id: "cfg-id" } }),
    "real-id",
  );
});

test("falls back to config.mission.id when MISSION.md has no id", () => {
  assert.equal(
    resolveMissionId({ status: "ACTIVE" }, { mission: { id: "cfg-mission-id" } }),
    "cfg-mission-id",
  );
});

test("falls back to config.mission.description when no ids exist", () => {
  assert.equal(
    resolveMissionId({}, { mission: { description: "3-lane smoke fixture" } }),
    "3-lane smoke fixture",
  );
});

test("falls back to a MISSION.md title/heading when surfaced", () => {
  assert.equal(resolveMissionId({ title: "My Mission" }, {}), "My Mission");
  assert.equal(resolveMissionId({ heading: "Heading X" }, null), "Heading X");
});

test("last resort: leading token of the most-recent event line", () => {
  // Object events with a `raw` line: "<utc> <TOKEN> ...".
  assert.equal(
    resolveMissionId(
      { events: [{ raw: "2026-01-01T00:00:00Z PHASE-PLAN started" }] },
      {},
    ),
    "PHASE-PLAN",
  );
  // Plain-string events.
  assert.equal(
    resolveMissionId({ events: ["2026-05-25T00:00Z ACTIVE ramp"] }, {}),
    "ACTIVE",
  );
});

test("returns empty string when nothing usable is available", () => {
  assert.equal(resolveMissionId({}, {}), "");
  assert.equal(resolveMissionId(null, null), "");
  assert.equal(resolveMissionId({ events: [] }, {}), "");
});
