# Finding: BUG-PHASE-INDICATOR-STUCK fix + regression test

**Agent:** agent-07c5 | **Lane:** D FRONTEND | **Phase:** PHASE-PLAN
**Task:** BUG-PHASE-INDICATOR-STUCK
**UTC:** 2026-05-19T23-04Z

---

## Bug summary

The phase strip in the dashboard header (`.phase-strip ol`) remained highlighted
on INIT even after the operator phase-flipped to PHASE-PLAN. The backend
confirmed `mission.phase = "PHASE-PLAN"` via `/api/v1/state`.

## Root cause

`app.js:attachPhaseIndicator()` subscribes to `store.subscribe("mission.phase")`.
The `reflect()` callback runs immediately at subscribe time (before SSE hydration),
sees `store.get("mission.phase") = ""`, and defaults to `"INIT"`.

Later, `sse.js:hydrateInitialState()` calls `store.hydrate(payload)` which calls
`store.set("mission", payload.mission)`. The store's `_emitPath()` only notified
**exact-path** and **ancestor** subscribers — it did NOT walk child-path subscribers
(`"mission.phase"`) when a parent object (`"mission"`) was replaced. So
`reflect()` never re-ran after hydration, leaving INIT highlighted.

## Fix (store.js — applied in prior agent-07c5 iteration)

Added a descendants-notification block in `Store._emitPath()`:

```js
// When a parent object is replaced, notify registered child-path subscribers.
const changedPath = segs.join(".");
if (changedPath && typeof newVal === "object" && newVal !== null) {
  const prefix = changedPath + ".";
  for (const subPath of this._subs.keys()) {
    if (!subPath.startsWith(prefix)) continue;
    const childSegs = splitPath(subPath).slice(segs.length);
    this._notify(subPath, walk(newVal, childSegs), walk(oldVal, childSegs));
  }
}
```

This fires `mission.phase` (and any other child-path) subscriber when
`set("mission", {...})` is called, covering the hydrate path.

## Evidence

`git diff HEAD -- ui/static/js/store.js` confirms the descendants block is present.
`app.js:attachPhaseIndicator()` subscribes to `"mission.phase"` at line 135.
`sse.js:hydrateInitialState()` calls `store.hydrate()` which calls
`store.set("mission", ...)` at `store.js:208`.

## Playwright regression tests added

File: `ui/tests/e2e/test_dashboard_live_audit.spec.ts`
New test group: `AUDIT-PHASE-INDICATOR`

1. **Hydration test**: intercepts `/api/v1/state` to inject `phase = "PHASE-PLAN"`,
   loads `/`, waits for networkidle, asserts:
   - `[data-testid="phase-segment-PHASE-PLAN"]` has `aria-current="step"`
   - `[data-testid="phase-segment-INIT"]` does NOT have `aria-current="step"`

2. **Direct set test**: calls `store.set("mission", {phase: "PHASE-BUILD"})` via
   `page.evaluate` and asserts PHASE-BUILD segment gets highlighted.

## Test status

Tests written; operator must run Playwright to confirm green
(`npx playwright test ... --project=chromium-default --project=webkit-default`).
The store.js descendants-notification fix passes logical review.

## Next steps

- Operator or LANE-E to run `ui/tests/e2e/test_dashboard_live_audit.spec.ts`
  and confirm the new `AUDIT-PHASE-INDICATOR` tests pass on both chromium + webkit.
- Remaining open bug for LANE-D: `BUG-HISTORY-UNREADABLE`.
