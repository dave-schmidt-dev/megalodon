# LANE-E Acknowledgment + Dashboard Audit Plan
**Agent:** agent-db2a | **Lane:** LANE-E (TEST) | **Task:** P1-E (reframed per operator override)
**UTC:** 2026-05-19T20-06Z

## Operator messages acknowledged

- `2026-05-19T19:34:00Z` — URGENT pair-up with LANE-D: drop P1-E plan task, write adversarial Playwright audit suite for broken dashboard, drive LANE-D fix queue.
- `2026-05-19T19:55:49Z` — Orchestrator checkpoint: this iteration IS the response. Fresh session; no prior claim held. Writing suite + findings this iteration.

## P1-E reframed scope (per operator override)

P1-E is now: **TEST plan + dashboard live audit under operator-priority override.**
Done condition: `ui/tests/e2e/test_dashboard_live_audit.spec.ts` exists AND ≥3 documented failures.

## What this iteration does

1. Writes `ui/tests/e2e/test_dashboard_live_audit.spec.ts` covering:
   - Nav tab integrity: click + URL + aria-current + reload-survival
   - Auth IIFE path-strip bug (hash token → URL reset to `/`)
   - Findings page: items load, clicking opens preview
   - Tasks page: cards render, phase tabs work
   - Signals page: renders without JS error
   - Mission page: content renders
   - Dashboard: activity sparkline (design bug: always empty), history tail (design bug: always empty)
   - **MISSING FEATURES** (expected failures): active-claims panel, permission-prompts panel, lane-card model/harness visibility

2. Adds `webkit-default` project to `playwright.config.ts` (only runs new spec; uses existing 8765 server).

3. Runs the suite and files one finding per failure category.

4. Writes `signals/LANE-E-to-LANE-D-2026-05-19T20-06Z.md` with the failing-test queue for FRONTEND.

## Known bugs targeted (from operator feedback)

| ID | Description | File | Severity |
|----|-------------|------|----------|
| BUG-NAV-1 | auth IIFE `finally` calls `history.replaceState(null,"","/")` on hash-token URLs | `ui/static/index.html:41` | MAJOR |
| BUG-NAV-2 | Active-tab `aria-current` doesn't survive page reload | `ui/static/js/app.js` | MAJOR |
| BUG-ACTIVITY | Activity panel reads only `mission.events`; agents in /loop mode don't produce events | `ui/static/pages/dashboard.js:262` | MAJOR |
| BUG-HISTORY | Recent HISTORY panel same issue — empty because no HISTORY.md mission events | `ui/static/pages/dashboard.js:307` | MAJOR |
| MISSING-CLAIMS | No `data-testid="active-claim-*"` panel in dashboard | dashboard.js | BLOCKING |
| MISSING-PROMPTS | No `data-testid="permission-prompts-panel"` in dashboard | dashboard.js | BLOCKING |
| MISSING-LANE-DETAILS | Model/harness/cadence hidden behind "Show details" toggle | dashboard.js | MINOR |

## Next steps

- Run suite → capture results → file per-category findings.
- LANE-D FRONTEND to drain the failing-test queue via `signals/LANE-E-to-LANE-D-*.md`.
