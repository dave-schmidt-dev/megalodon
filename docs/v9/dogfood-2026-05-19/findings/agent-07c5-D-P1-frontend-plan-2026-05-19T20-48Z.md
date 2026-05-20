# P1-D — FRONTEND Plan: Activate 4 Deferred Playwright Specs

- **Lane:** LANE-D (FRONTEND)
- **Agent:** `agent-07c5`
- **Task:** `P1-D`
- **Phase:** PHASE 1 — PLAN
- **UTC:** 2026-05-19T20-48Z

## Summary

Identified all 4 deferred Playwright specs and the exact changes needed to activate them. The core problem: the fleet's `playwright.config.ts` was branched from a pre-v9.2 state and is missing dedicated projects for both custom-config fixtures and the v92-dashboard test suite. Each spec is "deferred" because it either self-skips (wrong fixture) or has no project to run in.

## The 4 Deferred Specs

### Spec 1: `test_fe_phase_navigator_custom_config.spec.ts`

**What it tests:** Custom phase strip reconciliation — `reconcilePhaseNavigator()` in `dashboard.js` hides default phases not in config and appends custom ones (`DRAFT`, `REVIEW`, `PUBLISH`) with correct DOM `data-testid`.

**Why it's deferred:** Uses `test.skip(!isCustom, ...)` to self-skip when the server config doesn't return 3 custom phases. The fleet's `playwright.config.ts` has no project for this fixture.

**What's needed:**
1. `scripts/tests/fixtures/configs/minimal_custom_phases/.mission-config.yaml` — currently an EMPTY directory. Must define 3 phases: `[DRAFT, REVIEW, PUBLISH]` and 1 lane (e.g. `BETA`).
2. A new Playwright project `chromium-custom-phases` (and `webkit-custom-phases`) in `playwright.config.ts` booting against that fixture.

**Fixture YAML to create:**
```yaml
schema_version: 1
mission:
  id: custom-phases-test
  type: software-engineering
  description: Custom phases fixture for Playwright
lanes:
- name: BETA
  short: B
  role: "test lane"
  harness: {cli: claude, model: claude-sonnet-4-6, extra_args: [], auth_env: []}
  cadence_seconds: 300
phases: [DRAFT, REVIEW, PUBLISH]
task_id_patterns:
  patterns: ["^[A-Z0-9-]+$"]
  description: ''
```

---

### Spec 2: `test_fe_renders_with_custom_3_lane_config.spec.ts`

**What it tests:** `dashboard.js` loads `ALPHA`, `BETA`, `GAMMA` lane cards from `config.lanes` instead of the hardcoded 6-lane fallback. Also verifies the `loading-skeleton` briefly appears before config resolves.

**Why it's deferred:** Same pattern — `minimal_3_lane/` fixture directory is empty (no `.mission-config.yaml`), and no Playwright project targets it.

**What's needed:**
1. `scripts/tests/fixtures/configs/minimal_3_lane/.mission-config.yaml` — define lanes: `ALPHA`, `BETA`, `GAMMA`.
2. A new Playwright project `chromium-3-lane` (and `webkit-3-lane`) in `playwright.config.ts`.

**Fixture YAML to create:**
```yaml
schema_version: 1
mission:
  id: minimal-3-lane-test
  type: software-engineering
  description: 3-lane fixture for Playwright
lanes:
- {name: ALPHA, short: A, role: "alpha", harness: {cli: claude, model: claude-haiku-4-5-20251001, extra_args: [], auth_env: []}, cadence_seconds: 300}
- {name: BETA, short: B, role: "beta", harness: {cli: claude, model: claude-haiku-4-5-20251001, extra_args: [], auth_env: []}, cadence_seconds: 300}
- {name: GAMMA, short: G, role: "gamma", harness: {cli: claude, model: claude-haiku-4-5-20251001, extra_args: [], auth_env: []}, cadence_seconds: 300}
phases: [INIT, PHASE-BUILD, COMPLETE]
task_id_patterns:
  patterns: ["^[A-Z0-9-]+$"]
  description: ''
```

These fixtures need STATUS.md, TASKS.md, HISTORY.md stubs (empty/minimal) so the server boots cleanly.

---

### Spec 3 + 4: Two specs under `chromium-v92-dashboard`

**Which ones:** `dashboard-loads.spec.ts` and `auth-redirect.spec.ts`. These are the two most foundational v9.2 dashboard specs:
- `dashboard-loads.spec.ts` — verifies xterm lane-pane grid renders (`[data-testid="lane-grid"]`, `[data-testid^="lane-pane-"]`) and the paste-token modal is scaffolded.
- `auth-redirect.spec.ts` — verifies the token-paste auth flow: initial 401 → modal visible → invalid token → error label → valid token → modal closes.

**Why they're deferred:** The fleet's `playwright.config.ts` has NO `chromium-v92-dashboard` or `webkit-v92-dashboard` project. These specs exist in `megalodon/ui/tests/e2e/` (the main codebase, which the fleet's Playwright tests run against) but never execute in the fleet's test suite.

**What's needed:**
1. Add `chromium-v92-dashboard` and `webkit-v92-dashboard` projects to `playwright.config.ts` with:
   - `testMatch: /(dashboard-loads|auth-redirect)\.spec\.ts$/` (start with just these 2 deferred ones)
   - `MEGALODON_FAKE_SPAWNER=1` + `MEGALODON_V92_DASHBOARD=1` env vars
   - `fix-medium-v92` fixture (already has claims, findings, STATUS/TASKS)
   - `fullyParallel: false, workers: 1` (fake-spawner state is shared)
2. Add corresponding `webServer` entries for ports 8768 (chromium) and 8778 (webkit).
3. Add `_helpers.ts` update: map `chromium-v92-dashboard` → `v92-c` and `webkit-v92-dashboard` → `v92-w` in `PROJECT_TO_LABEL`.

The fleet's `index.html` needs the v9.2 additions (auth bootstrap IIFE, xterm.js, dashboard-v92.js) to be present for these tests to pass. The auth bootstrap fix I already applied in the previous iteration (`location.pathname + location.search`) is a prerequisite.

---

## Additional P2-D Work Discovered

While reading `test_dashboard_live_audit.spec.ts` (written by LANE-E TEST agent-db2a), I found `[MISSING-FEATURE]` tests that need specific `data-testid` attributes my previous iteration did NOT add:

1. `[data-testid="lane-model"]` on each lane card — my fix renders the model text but without this testid.
2. `[data-testid="lane-last-tick"]` on each lane card — same.
3. `[data-testid^="active-claim-"]` — an active-claims panel on the dashboard (not yet implemented).
4. `[data-testid="permission-prompts-panel"]` — a permission prompts panel (not yet implemented).

The `[DESIGN-BUG]` tests will FAIL after my bug fixes from the previous iteration because they assert the bugs exist (`toContainText('no activity yet')`). In P2-D:
- Invert those tests to assert the proxy data IS shown.
- Add the `lane-model` and `lane-last-tick` testids to `dashboard.js`.
- Implement the active-claims and permission-prompts panels.

---

## Implementation Plan for P2-D

### Step 1: Fixture YAML files (30 min)
Create:
- `scripts/tests/fixtures/configs/minimal_custom_phases/.mission-config.yaml`
- `scripts/tests/fixtures/configs/minimal_3_lane/.mission-config.yaml`
Each needs: `STATUS.md` (empty table), `TASKS.md` (empty), `HISTORY.md` (empty), `findings/` dir, `claims/` dir.

### Step 2: `playwright.config.ts` additions (30 min)
- Add `chromium-custom-phases`, `webkit-custom-phases`, `chromium-3-lane`, `webkit-3-lane` projects
- Add `chromium-v92-dashboard`, `webkit-v92-dashboard` projects
- Add webServer entries for each new project on unique ports (8768–8775 range)
- Update `prepareFixture` entries

### Step 3: `dashboard.js` testid additions (15 min)
Add `data-testid="lane-model"` to the model span and `data-testid="lane-last-tick"` to the staleness span in `renderLaneCard`.

### Step 4: `test_dashboard_live_audit.spec.ts` updates (20 min)
Invert the `[DESIGN-BUG]` tests that assert bugs exist:
- Activity sparkline: should NOT show "no activity yet" after fix (now shows findings)
- Recent HISTORY: should NOT show "no HISTORY entries yet" (now shows findings proxy)

### Step 5: Active claims + permission prompts panels (45 min)
- `claims.list` from store → render `[data-testid^="active-claim-{taskId}"]` cards at top of dashboard
- Permission prompts panel: `GET /api/v1/permission_prompts` (v9.2 gated) → render panel when non-empty

### Step 6: Run full Playwright suite
```bash
cd ui/tests/e2e && npx playwright test --project chromium-default --project chromium-custom-phases --project chromium-3-lane
```

## Estimated total P2-D effort
~2.5 hours. Can be done in 1–2 iterations depending on test stability.

## Next steps
- After this finding: release claim, proceed to P2-D or secondary pool tasks.
- P2-D is PHASE 2 (not current phase); will be claimable after phase flip.
- Until phase flip: can implement the `dashboard.js` testid additions and `test_dashboard_live_audit.spec.ts` fixes as secondary work.
