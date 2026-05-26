// test_tasks_phase_progress.test.js — audit I4.
//
// Runner: node:test (built-in, Node >= 18).
// Run:    node --test ui/tests/unit/test_tasks_phase_progress.test.js
//
// The /tasks kanban column headers now show done/total per phase and highlight
// the column matching the mission's current phase. These pin the two pure
// helpers that drive that UI.

import { test } from "node:test";
import assert from "node:assert/strict";

const { phaseProgress, isCurrentPhase } = await import("../../static/pages/tasks.js");

test("phaseProgress counts done vs total", () => {
  assert.deepEqual(
    phaseProgress([
      { state: "done" },
      { state: "open" },
      { state: "claimed" },
    ]),
    { done: 1, total: 3 },
  );
});

test("phaseProgress is case-insensitive on state and tolerates junk", () => {
  assert.deepEqual(
    phaseProgress([{ state: "DONE" }, { state: "Done" }, {}, null]),
    { done: 2, total: 4 },
  );
  assert.deepEqual(phaseProgress([]), { done: 0, total: 0 });
  assert.deepEqual(phaseProgress(null), { done: 0, total: 0 });
});

test("isCurrentPhase matches exact, case-insensitively", () => {
  assert.equal(isCurrentPhase("PHASE-EXEC", "phase-exec"), true);
  assert.equal(isCurrentPhase("PHASE-PLAN", "PHASE-EXEC"), false);
});

test("isCurrentPhase matches across canonical/human formats via substring", () => {
  // Human header column name vs canonical mission phase token.
  assert.equal(isCurrentPhase("PHASE 2 — BUILD", "BUILD"), true);
  assert.equal(isCurrentPhase("BUILD", "PHASE 2 — BUILD"), true);
});

test("isCurrentPhase: empty current phase never matches", () => {
  assert.equal(isCurrentPhase("PHASE-PLAN", ""), false);
  assert.equal(isCurrentPhase("", "PHASE-PLAN"), false);
  assert.equal(isCurrentPhase("PHASE-PLAN", null), false);
});
