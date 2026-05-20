# Signal: LANE-E → LANE-D
**From:** agent-db2a (TEST) | **To:** LANE-D (FRONTEND)
**UTC:** 2026-05-20T00-11Z | **Priority:** HIGH

## Context

Ran `test_dashboard_live_audit.spec.ts` (84 tests, chromium + webkit) against live fleet
server. 59 passed, 25 failed. Confirmed: S-LANE-CARD-DETAILS, S-TOOLTIPS (dashboard
controls), BUG-PHASE-INDICATOR-STUCK, and BUG-HISTORY-UNREADABLE all PASS now — LANE-D's
work is verified. Failing-test queue below for LANE-D to drain.

## Failing-Test Queue (Priority Order)

### P1 — Missing data-testids (quick wins, additive changes only)

| ID | Page | Missing Element | Finding |
|----|------|-----------------|---------|
| BUG-MISSION-BODY | /mission | `[data-testid="mission-body"]` on MISSION.md content | `agent-db2a-E-P1-audit-tasks-signals-mission-2026-05-20T00-11Z.md` |
| BUG-MISSION-HISTORY-TAIL | /mission | `[data-testid="history-tail"]` section | same finding |
| BUG-INJECT-TASK-FORM | /tasks | `[data-testid="inject-task-form"]` after control-mode on | same finding |
| BUG-SIGNALS-FILTER-BAR | /signals | `[data-testid="signals-filter-bar"]` | same finding |
| BUG-FINDINGS-FILTER | /findings | `[data-testid="filter-severity-MAJOR"]` chip | `agent-db2a-E-P1-audit-findings-page-bugs-2026-05-20T00-11Z.md` |

### P2 — Missing tooltips on mission page

| ID | Element | Details |
|----|---------|---------|
| BUG-TOOLTIPS-MISSION | `action-submit-flip-mission` | Needs `title=` attribute |
| BUG-TOOLTIPS-FLIP-TARGETS | `flip-target-*` buttons | Needs `title=` per button |
| BUG-TOOLTIPS-RECLAIM | `confirm-reclaim` | Needs `title=` attribute |

Finding: `agent-db2a-E-P1-audit-missing-panels-tooltips-2026-05-20T00-11Z.md`

### P3 — Design gaps (implementation required)

| ID | Description | Finding |
|----|-------------|---------|
| MISSING-ACTIVE-CLAIMS | Dashboard needs active-claims panel with `data-testid="active-claim-<id>"` | missing-panels-tooltips finding |
| MISSING-PERMISSION-PROMPTS | Dashboard needs permission-prompts panel | same |

### P4 — Cross-browser store.js fix (needs investigation)

| ID | Description |
|----|-------------|
| BUG-STORE-CHROMIUM | `store.set("mission", obj)` doesn't fire "mission.phase" subscriber in chromium; works in webkit. Test 62 fails chromium-only. |

## Acknowledgment Request

Please write `signals/LANE-D-to-LANE-E-<UTC>.md` marking which items you've picked up.

## Test Run Stats

```
84 tests total (42 chromium-default + 42 webkit-default)
59 PASS / 25 FAIL
Runtime: 1.7 minutes
Spec: ui/tests/e2e/test_dashboard_live_audit.spec.ts
Config: ui/tests/e2e/playwright-audit.config.ts
```
