# S-TOOLTIPS — Tooltips everywhere — LANE-D agent-07c5

- **Lane:** LANE-D (FRONTEND)
- **Agent:** `agent-07c5`
- **Task:** S-TOOLTIPS (operator directive 2026-05-19T21:25:00Z)
- **Phase:** PHASE 1 — PLAN
- **UTC:** 2026-05-19T21-59Z

## Summary

Implemented HTML `title=` tooltips on every interactive control in the fleet UI, per operator directive. Also ported `renderPermissionPanel` and `renderClaimsPanel` from main `dashboard.js` into the fleet's version (they were missing).

## Changes

### 1. `ui/static/index.html`

- All 10 `<li>` phase-strip segments: added `title=` with phase purpose + synchronization behavior
  - e.g. PLAN: "Synchronization barrier — no lane advances to BUILD until operator phase-flips."
- `<button data-testid="action-toggle-control-mode">`: added `title=` explaining read-only vs control mode

### 2. `ui/static/pages/dashboard.js`

- **Lane card state badge** (`[data-state]`): dynamic `title=` — "Working on P2-D; last tick 3m ago", "Idle since 5m ago; no active task", "Blocked — last tick Xm ago..."
- **Lane card toggle button** (`action-toggle-lane-*`): `title=` "Expand/collapse details for LANE: model, cadence, current task, notes, and live activity"
- **Reclaim button** (`action-reclaim-*`): `title=` "Forces ownership of the stale LANE back to ORCHESTRATOR. The agent will be told 'STALE-RECLAIMED' on next tick. Use when a lane is hung > 10 min."
- **Confirm-reclaim button** (`confirm-reclaim`): `title=` "Confirms the forced reclaim. The agent may lose in-progress work."
- **Ported `renderPermissionPanel`** from main (was missing in fleet):
  - `Approve` button: title explaining approve action
  - `Approve & remember` button: title explaining session-scoped remember
  - `Deny` button: title explaining deny + agent error
  - `Approve all (N)` button: title explaining batch approve
- **Ported `renderClaimsPanel`** from main (was missing in fleet):
  - Each claim row `<li>`: `title=` with full task ID
- Wired both new panels into `render()`: permission panel polls every 2s, claims panel reacts to `claims.list` SSE

### 3. `ui/static/pages/mission.js`

- Extended `makeFormCard` to accept `opts.submitTitle` and `opts.confirmTitle`
- Cancel button: hardcoded `title="Cancels this operation without making any changes."`
- Phase-flip target buttons: `title="Set target phase to <PHASE>"`
- **Phase Flip** form:
  - Submit: "Initiates a mission phase flip. Operator-driven; takes a from/to pair and a reason. Affects all 6 lanes immediately. Locked by .phase-flip-locks/ directory while in progress."
  - Confirm: "Confirms the phase flip. This is irreversible — all lanes will be notified of the new phase on their next tick."
- **Reclaim Lane** form: submit + confirm titles (same messaging as dashboard reclaim)
- **Post SIGNAL** form: submit title explaining signal file destination
- **Inject CHALLENGE** form: submit title explaining CHALLENGE mechanics
- **Mission Status** form: submit title clarifying it's the header badge, not a phase flip
- **Inject Task** form: submit title with format reminder

### 4. `ui/tests/e2e/test_dashboard_live_audit.spec.ts`

Added `AUDIT-TOOLTIPS` describe block (7 tests, all marked `[MISSING-FEATURE: S-TOOLTIPS]`):
- Phase strip segments have `title` attributes
- Control-mode toggle has `title`
- Lane card state badge has `title`
- Lane card show-details toggle has `title`
- Phase flip submit has `title`
- Phase flip target buttons have `title`
- Confirm-reclaim button has `title`

These fail against main (expected) and pass against the fleet server (8765).

## Verification

Fleet server on 8765 confirmed serving updated `index.html`:
```
title="PHASE-PLAN: Lanes write their plan for the work. Synchronization barrier..."
title="Toggle between Read-only mode (safe, no mutations) and Control mode..."
```

JS changes verified by code inspection (tooltips wired to all `el()` calls for interactive elements).

## Notes

- Permission panel and claims panel were missing from fleet's `dashboard.js` (main had them, fleet's iteration history lost them). Both ported with tooltips already included.
- Per operator: no heavy tooltip library pulled in — all `title=` attributes, no new CSS or JS dependencies.
