---
title: V9 M4 — Shared constants registry (design spec)
status: APPROVED-FOR-PLAN
version: 1.0
utc: 2026-05-16T21:30Z
roadmap-anchor: docs/v9/V9-ROADMAP.md §M4 + Migration plan §3b
codex-review: not-required (small mechanical change, no new architectural surface)
---

# V9 M4 — Shared constants registry

## 1. Goal

Single source of truth for IDs/keys/event-names referenced by both Python BE and JS FE, so that the run-2 REPAIR-3 class of bug (28-char `CONTROL_MODE_KEY` mismatch breaking 6 e2e tests) is structurally impossible.

## 2. Locked decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **Codegen, not runtime fetch** | Catches mismatch at build time, not 30s into a broken e2e run. Adds one script; removes a runtime API surface that would need contract scanning (M2). |
| D2 | **Python is canonical** | `megalodon_ui/constants.py` is authored by hand. `ui/static/js/constants.js` is generated. JS file has a "DO NOT EDIT" banner. |
| D3 | **Codegen runs via `scripts/gen_js_constants.py`** | Plain Python script. Imports from `megalodon_ui.constants`, emits JS. Invoked manually + pre-commit hook for safety + checked in (committed JS file). |
| D4 | **Drift detection in tests** | `scripts/tests/test_constants.py` re-runs codegen in-memory and diffs against committed JS. Fails if out of sync. |
| D5 | **Scope: HIGH + MEDIUM risk only** | Ship: localStorage keys (1), stale threshold (1), SSE event names (11), API path constants (10). Defer: data-testid prefixes (20+, FE/test-only, low drift risk). Defer: API field name mappings (case differences are FE-defensive; constants don't fix that — that needs unified BE response shape, addressed by M1.6). |
| D6 | **Factory-only** | `megalodon_ui/server.py` migrates to use constants. `ui/server.py` does NOT — it's getting deprecated to a shim in M1.6. Touching it now is wasted effort. |
| D7 | **Backwards-compat migration: one-shot replace** | All callers in scope migrate in same commit. No dual-import-during-transition. Spec D5's scope keeps this tractable. |

## 3. Scope — what's IN

### 3.1 localStorage keys
- `CONTROL_MODE_KEY = "controlMode"` (FE only, source: `ui/static/js/store.js:17`)

### 3.2 Time thresholds
- `STALE_THRESHOLD_SECONDS = 900` (FE + BE; sources: `ui/server.py:78`, `megalodon_ui/server.py:91`, `ui/static/pages/dashboard.js:29`)

### 3.3 SSE event names
- `SSE_STATUS_CHANGE = "status-change"`
- `SSE_TASK_CHANGE = "task-change"`
- `SSE_PHASE_FLIP = "phase-flip"`
- `SSE_FINDING_NEW = "finding-new"`
- `SSE_HISTORY_APPEND = "history-append"`
- `SSE_CLAIM_CREATE = "claim-create"`
- `SSE_CLAIM_DONE = "claim-done"`
- `SSE_SIGNAL_NEW = "signal-new"`
- `SSE_LAGGING = "lagging"`
- `SSE_HEARTBEAT = "heartbeat"`
- `SSE_MISSION_STATUS = "mission-status"`
- `SSE_SYNC = "sync"` (factory-only; was missing from FE EVENT_TYPES enum — fix lands here)
- Plus an exported `SSE_EVENT_TYPES` list/array containing all of the above.

### 3.4 API path constants
- `API_STATE = "/api/v1/state"`
- `API_CONFIG = "/api/v1/config"`
- `API_EVENTS = "/api/v1/events"`
- `API_RECLAIM = "/api/v1/reclaim"`
- `API_FINDINGS = "/api/v1/findings"`
- `API_CHALLENGE = "/api/v1/challenge"`
- `API_SIGNAL = "/api/v1/signal"`
- `API_PHASE_FLIP = "/api/v1/phase-flip"`
- `API_MISSION_STATUS = "/api/v1/mission-status"`
- `API_INJECT_TASK = "/api/v1/inject-task"`

### 3.5 Default ports
- `DEFAULT_PORT = 8080` (env-overridable via `MEGALODON_PORT`)

## 4. Scope — what's OUT (deferred)

- **data-testid prefixes** — FE/test-only, 20+ items, no FE/BE drift surface. Codegen would emit a JS module just for tests to import; benefit/effort low. Revisit in v10 if test fragility becomes a measured problem.
- **API field name mappings** (snake_case vs camelCase) — fixed by M1.6 backend unification + Pydantic schemas. Constants don't solve that.
- **CSS color tokens** — out of M4 scope; would be a separate "design-tokens.json + CSS-var generator" if pursued.
- **ui/server.py migration** — deprecated by M1.6.

## 5. File layout

```
megalodon_ui/
├── constants.py                    # CANONICAL (hand-authored)
├── server.py                       # MODIFIED: imports from .constants
└── ...

ui/static/js/
├── constants.js                    # GENERATED (DO NOT EDIT)
├── store.js                        # MODIFIED: imports CONTROL_MODE_KEY
└── ...

ui/static/pages/
├── dashboard.js                    # MODIFIED: imports STALE_THRESHOLD_SECONDS
├── sse.js                          # MODIFIED: imports SSE_* + API_*
├── findings.js                     # MODIFIED: imports API_FINDINGS
├── mission.js                      # MODIFIED: imports API_* + SSE_*
└── ...

scripts/
├── gen_js_constants.py             # NEW: codegen
└── tests/
    └── test_constants_codegen.py   # NEW: codegen unit tests + drift detection
```

## 6. `megalodon_ui/constants.py` design

```python
"""V9 M4 — shared constants registry.

CANONICAL source of truth for FE+BE shared identifiers. Run
`python3 scripts/gen_js_constants.py` after editing to regenerate
`ui/static/js/constants.js`. Pre-commit hook enforces this.

Do not put module-private constants here. Only FE/BE-shared values.
"""

from __future__ import annotations

# ─── localStorage keys (FE) ──────────────────────────────────────
CONTROL_MODE_KEY = "controlMode"

# ─── Time thresholds (FE + BE) ───────────────────────────────────
STALE_THRESHOLD_SECONDS = 900  # RULE-1, 15 min

# ─── SSE event names (FE + BE) ───────────────────────────────────
SSE_STATUS_CHANGE = "status-change"
SSE_TASK_CHANGE = "task-change"
SSE_PHASE_FLIP = "phase-flip"
SSE_FINDING_NEW = "finding-new"
SSE_HISTORY_APPEND = "history-append"
SSE_CLAIM_CREATE = "claim-create"
SSE_CLAIM_DONE = "claim-done"
SSE_SIGNAL_NEW = "signal-new"
SSE_LAGGING = "lagging"
SSE_HEARTBEAT = "heartbeat"
SSE_MISSION_STATUS = "mission-status"
SSE_SYNC = "sync"

SSE_EVENT_TYPES = (
    SSE_STATUS_CHANGE, SSE_TASK_CHANGE, SSE_PHASE_FLIP,
    SSE_FINDING_NEW, SSE_HISTORY_APPEND, SSE_CLAIM_CREATE,
    SSE_CLAIM_DONE, SSE_SIGNAL_NEW, SSE_LAGGING,
    SSE_HEARTBEAT, SSE_MISSION_STATUS, SSE_SYNC,
)

# ─── API paths (FE + BE) ─────────────────────────────────────────
API_STATE = "/api/v1/state"
API_CONFIG = "/api/v1/config"
API_EVENTS = "/api/v1/events"
API_RECLAIM = "/api/v1/reclaim"
API_FINDINGS = "/api/v1/findings"
API_CHALLENGE = "/api/v1/challenge"
API_SIGNAL = "/api/v1/signal"
API_PHASE_FLIP = "/api/v1/phase-flip"
API_MISSION_STATUS = "/api/v1/mission-status"
API_INJECT_TASK = "/api/v1/inject-task"

# ─── Defaults ────────────────────────────────────────────────────
DEFAULT_PORT = 8080
```

## 7. `scripts/gen_js_constants.py` design

Walks `megalodon_ui.constants` module via `importlib`. Filters to UPPER_CASE module-level attributes. Emits JS via simple Python serialization rules:

| Python type | JS emission |
|-------------|-------------|
| `str` | `JSON.stringify(value)` |
| `int`/`float` | numeric literal |
| `bool` | `true`/`false` |
| `tuple`/`list` of supported scalars | JS array literal |
| anything else | error — refuse to emit |

Output file:
```javascript
// AUTO-GENERATED by scripts/gen_js_constants.py — DO NOT EDIT.
// Source: megalodon_ui/constants.py
// Regenerate with: python3 scripts/gen_js_constants.py

export const CONTROL_MODE_KEY = "controlMode";
export const STALE_THRESHOLD_SECONDS = 900;
// ... (each constant as a separate `export const`)
export const SSE_EVENT_TYPES = ["status-change", "task-change", /* ... */];
```

CLI:
```
python3 scripts/gen_js_constants.py [--check]
```
- Default: regenerate and overwrite `ui/static/js/constants.js`.
- `--check`: regenerate in-memory, compare against on-disk; exit 1 if mismatch (for pre-commit hook + CI).

Exit codes: 0 (success), 1 (drift detected with --check), 2 (unsupported constant type encountered).

## 8. Migration map

| File | Change |
|------|--------|
| `megalodon_ui/server.py:91` | `is_stale = staleness_seconds > 900.0` → `> STALE_THRESHOLD_SECONDS` |
| `megalodon_ui/server.py` SSE emissions (lines 597, 623, +others) | `event="status-change"` → `event=SSE_STATUS_CHANGE` etc. |
| `megalodon_ui/server.py` route declarations | `@app.get("/api/v1/state")` → `@app.get(API_STATE)` |
| `megalodon_ui/__main__.py:26` | `default=int(os.environ.get("MEGALODON_PORT", "8080"))` → use `DEFAULT_PORT` |
| `ui/static/js/store.js:17` | `"controlMode"` → `CONTROL_MODE_KEY` (with `import` line) |
| `ui/static/pages/dashboard.js:29` | `15 * 60` → `STALE_THRESHOLD_SECONDS` |
| `ui/static/pages/sse.js` | `EVENT_TYPES = [...]` → `import { SSE_EVENT_TYPES } from '...'`; URLs use `API_STATE` etc. |
| `ui/static/pages/findings.js:527` | `/api/v1/findings` → `API_FINDINGS` |
| `ui/static/pages/mission.js:479,532,586,642,661,699` | `/api/v1/...` → corresponding `API_*` constant |
| `ui/static/pages/dashboard.js:343` | `/api/v1/reclaim` → `API_RECLAIM` |
| `ui/static/pages/sse.js:67,68,163` | `/api/v1/state`, `/api/v1/config`, `/api/v1/events` → `API_*` |

## 9. Test strategy

### 9.1 `scripts/tests/test_constants_codegen.py`

```python
# Tests:
#   test_generates_valid_js                 — output parses + has expected banner
#   test_string_constants_emitted           — CONTROL_MODE_KEY → 'export const CONTROL_MODE_KEY = "controlMode";'
#   test_int_constants_emitted              — STALE_THRESHOLD_SECONDS → 'export const STALE_THRESHOLD_SECONDS = 900;'
#   test_tuple_constants_emitted_as_array   — SSE_EVENT_TYPES → JS array literal
#   test_skips_private_attrs                — _foo, __all__ not emitted
#   test_skips_non_uppercase                — camelCase, snake_case not emitted
#   test_check_mode_passes_when_synced      — --check exits 0 when JS matches
#   test_check_mode_fails_when_drifted      — --check exits 1 when JS stale
#   test_unsupported_type_refuses           — module with a dict constant → exit 2
#   test_committed_js_matches_python        — drift detection (regression for D4)
```

### 9.2 Integration smoke

After migration, manual smoke:
```bash
uv run --with fastapi --with "uvicorn[standard]" --with sse-starlette --with pyyaml \
    python -m megalodon_ui --mission-dir scripts/tests/fixtures/minimal_mission --port 8080
curl -s http://localhost:8080/api/v1/state | jq .
# Open browser → /static/index.html → confirm SSE connects + no console errors
```

## 10. Pre-commit integration

Add to `.git/hooks/pre-commit` (or document in README — operator-installed):
```bash
python3 scripts/gen_js_constants.py --check || {
    echo "constants.js out of sync with constants.py — run scripts/gen_js_constants.py"
    exit 1
}
```

**Not blocking for M4 ship** — codegen drift is caught by `test_committed_js_matches_python` in pytest, so the safety net exists even if the hook isn't installed. Operator may install at convenience.

## 11. Definition of done

- [ ] `megalodon_ui/constants.py` exists with all D5-scope constants.
- [ ] `scripts/gen_js_constants.py` runs, emits valid JS, supports `--check`.
- [ ] `ui/static/js/constants.js` exists, generated from constants.py, committed.
- [ ] All `megalodon_ui/server.py` references in §8 migration map updated.
- [ ] All `ui/static/**/*.js` references in §8 migration map updated.
- [ ] `scripts/tests/test_constants_codegen.py` — 10 tests, all passing.
- [ ] `scripts/tests/test_constants_codegen.py::test_committed_js_matches_python` passes (no drift).
- [ ] HISTORY.md M4-COMPLETE entry appended.
- [ ] Existing e2e + unit tests still pass (no regression).

## 12. Implementation order (TDD)

1. Write `scripts/tests/test_constants_codegen.py` skeleton (test_generates_valid_js test only) — failing.
2. Write minimal `megalodon_ui/constants.py` (just `CONTROL_MODE_KEY = "controlMode"`).
3. Write `scripts/gen_js_constants.py` (handles `str` only) — test passes.
4. Add remaining tests for type coverage (int, tuple, type-error).
5. Extend gen script to handle int/float/bool/tuple/list. Tests pass.
6. Add `--check` mode + tests.
7. Add `test_committed_js_matches_python` drift detection.
8. Populate `constants.py` with full D5 scope.
9. Run codegen → commit generated `constants.js`.
10. Migrate `megalodon_ui/server.py` per §8. Verify route + SSE behavior unchanged via smoke.
11. Migrate `ui/static/js/store.js` + `ui/static/pages/*.js` per §8. Verify browser smoke.
12. Run full pytest suite + existing e2e tests — confirm no regression.
13. Append HISTORY.md M4-COMPLETE entry.

## 13. Risks

| Risk | Mitigation |
|------|------------|
| ES module imports break in browser (path resolution) | Use relative paths in JS imports (`from '../js/constants.js'`); test in browser smoke before declaring done. |
| Migration misses a literal | `grep -rE '"controlMode"\|"/api/v1/'` across `ui/static/` post-migration as sanity check. |
| Codegen breaks if someone adds an unsupported type | Refuses with exit 2 + clear error message. Test covers. |
| Pre-commit hook not installed by operator | Drift test in pytest catches it; CI/local hook is defense-in-depth, not gate. |
| Generated JS file diff noise in PR | Banner comment explains; reviewers learn quickly. |

## 14. Out-of-scope adjacent items

- data-testid prefix registry (deferred per D5)
- API request/response field-name mappings (M1.6 will unify)
- CSS color tokens (separate effort if pursued)
- TypeScript types from Python constants (future; would need type emission too)

## 15. Document control

- Author: orchestrator (Claude)
- Date: 2026-05-16T21:30Z
- Status: APPROVED-FOR-PLAN (delegated brainstorming per operator 2026-05-16T21:12Z)
- Predecessor: V9-ROADMAP §M4
- Successor: `docs/superpowers/plans/2026-05-16-v9-m4-constants-registry.md`
