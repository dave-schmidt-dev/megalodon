---
title: V9 M2 — PRE-VERIFY contract scan (design spec)
status: APPROVED-FOR-PLAN
version: 1.0
utc: 2026-05-16T22:00Z
roadmap-anchor: docs/v9/V9-ROADMAP.md §M2 + Migration plan §3c
codex-review: applied (CR-3 + CR-7 — source-of-truth doc + runtime instrumentation; no AST/JS deps)
---

# V9 M2 — PRE-VERIFY contract scan

## 1. Goal

Make 4-cascading-HEAL bugs (run-2 cost: ~70% of wall-clock) structurally impossible by enforcing — before any PHASE-VERIFY claim can close — that every FE→BE call has a documented contract, the contract matches the BE's actual response shape, and no FE call hits an undocumented endpoint.

## 2. Locked decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **`docs/v9/api-contract.md` is source of truth** | Per CR-3 + CR-7 revision: no JS AST parsing, no new deps. The doc is a TIER-1 normative spec, like SPEC-v2. |
| D2 | **Three-pronged enforcement** | (a) Doc as canonical contract, (b) BE startup validates routes-declared vs doc, (c) FE runtime fetch wrapper catches undocumented calls. All three must pass for P3 close. |
| D3 | **`scripts/contract_scan.py` orchestrates** | Single CLI that operator invokes during P3 verify. ~80 LOC. Outputs JSON to stdout. |
| D4 | **Scope: factory routes only** | M1.6 deprecates `ui/server.py`. Contract describes `megalodon_ui` factory only. |
| D5 | **Path templates with `{param}` placeholders** | Per CR-3 (helper-indirection finding) note about `/api/v1/findings/${id}`: contract doc declares `GET /api/v1/findings/{filename}`; FE wrapper normalizes runtime URLs to templates before comparison. |
| D6 | **Pydantic for BE response-shape validation** | Already a FastAPI dependency. Spec response shapes via `BaseModel`. Startup validation iterates routes + asserts `response_model` matches contract doc declaration. |
| D7 | **FE wrapper test-mode only** | Production FE has no overhead. P3 test mode sets `window.__M9_CONTRACT_TRACE__ = true` before page render; wrapper installs in `sse.js` boot if flag set. |
| D8 | **Headless smoke uses playwright** | The wrapper needs a real DOM to install. Reuse `scripts/run_e2e.sh` config; new test file `ui/tests/e2e/contract-trace.spec.ts` drives the smoke. |
| D9 | **Owner: orchestrator-Claude (initially)** | Per WR-1 deferred: ownership of api-contract.md unclear in roadmap. v9 cutover: orchestrator owns. v10+: revisit (likely BE lane owns updates with ARCH review). |
| D10 | **Non-blocking soft-fail mode for development** | `contract_scan.py --soft` prints findings but exits 0. P3 verify uses default (strict) mode. Operators can run soft during dev to iterate. |

## 3. `docs/v9/api-contract.md` format

Plain Markdown with **strict structural conventions** so `contract_scan.py` can parse it. Each endpoint is a heading + fenced YAML block + Pydantic-style response shape block.

```markdown
# Megalodon v9 API Contract

> Canonical source-of-truth for all FE→BE calls. BE startup validates this.
> FE runtime wrapper validates this. ANY drift fails P3 verify.

## Conventions

- Path templates use `{param}` for path parameters (e.g., `/api/v1/findings/{filename}`).
- Response shapes are declared as Pydantic-style class skeletons; full models live in
  `megalodon_ui/schemas.py` and BE startup cross-checks.
- SSE event types are declared per-stream with the event-name vocabulary.

---

## Endpoints

### GET /api/v1/state

```yaml
method: GET
path: /api/v1/state
response_model: StateResponse
status: 200
content_type: application/json
fe_consumers:
  - ui/static/js/sse.js:56  # initial hydrate
description: Returns full mission snapshot for FE bootstrap.
```

**Response shape (`StateResponse`):**
```python
class StateResponse(BaseModel):
    status: StatusBlock
    tasks: list[TaskBlock]
    history: list[HistoryEntry]
    findings: list[FindingSummary]
    config: ConfigBlock
```

---

### GET /api/v1/config
... (one block per endpoint)

---

### GET /api/v1/events  (SSE stream)

```yaml
method: GET
path: /api/v1/events
response_model: SSEStream
status: 200
content_type: text/event-stream
fe_consumers:
  - ui/static/js/sse.js:152
description: Long-lived SSE stream of mission events.
sse_events:
  - status-change
  - task-change
  - phase-flip
  - finding-new
  - history-append
  - claim-create
  - claim-done
  - signal-new
  - lagging
  - heartbeat
  - mission-status
  - sync
```

(SSE event names MUST match `megalodon_ui/constants.py` `SSE_EVENT_TYPES` —
M4 dependency. Contract scan cross-validates.)
```

### 3.1 Why YAML inside Markdown?

- Markdown is human-readable + lives in `docs/v9/`.
- YAML blocks parse deterministically — no ad-hoc Markdown parsing.
- Pydantic class skeletons (in `python` fenced blocks) are documentation; actual shapes live in `megalodon_ui/schemas.py` (to be created).

### 3.2 Endpoints to document (initial scope — factory `/api/v1/*` only)

Per the M4 inventory + factory grep:
- `GET /api/v1/state`
- `GET /api/v1/config`
- `GET /api/v1/events` (SSE)
- `GET /api/v1/findings`
- `GET /api/v1/findings/{filename}`
- `POST /api/v1/reclaim`
- `POST /api/v1/signal`
- `POST /api/v1/challenge`
- `POST /api/v1/phase-flip`
- `POST /api/v1/mission-status`
- `POST /api/v1/inject-task`

Factory routes **NOT** in initial scope (pre-CR-2 vestiges, slated for cleanup or kept as unused):
- `/api/status`, `/api/findings`, `/api/tasks`, `/api/config` (non-`v1`)
- `/api/lanes/{lane}/reclaim`, `/api/lanes/{lane}/signal`
- `/api/mission/flip`
- `/api/v1/status`, `/api/v1/tasks` (BE-only — FE never calls)

These are flagged in `contract_scan.py` output under `untested_be_routes` (informational only; doesn't fail P3).

## 4. `megalodon_ui/schemas.py` — Pydantic response models

New module. Imports from `megalodon_ui/constants.py` for SSE event vocabulary.

```python
"""V9 M2 — Pydantic response models for contract enforcement."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field
from .constants import SSE_EVENT_TYPES

SSEEventName = Literal[
    "status-change", "task-change", "phase-flip", "finding-new",
    "history-append", "claim-create", "claim-done", "signal-new",
    "lagging", "heartbeat", "mission-status", "sync",
]
# Asserts at import time:
_declared = frozenset(SSEEventName.__args__)
_canonical = frozenset(SSE_EVENT_TYPES)
assert _declared == _canonical, (
    f"schemas.py SSEEventName drifted from constants.SSE_EVENT_TYPES: "
    f"missing={_canonical - _declared} extra={_declared - _canonical}"
)

class LaneStatus(BaseModel):
    lane: str
    agent: str | None
    state: str
    last_utc: str
    staleness_seconds: float
    is_stale: bool
    notes: str

class StatusBlock(BaseModel):
    lanes: list[LaneStatus]
    current_phase: str

# ... (one model per endpoint response — pulled from existing factory code)

class StateResponse(BaseModel):
    status: StatusBlock
    tasks: list[dict]      # task shapes evolve; not tightened in v9
    history: list[dict]
    findings: list[dict]
    config: dict
```

**Scope of strictness**: top-level fields validated strictly; inner dicts (tasks/history/findings) stay loose for v9. M2 cares that the **route exists + returns 200 + has the top-level shape**. Field-level enforcement is a v10 tightening.

## 5. BE startup validation

In `megalodon_ui/server.py::make_app()` (or a new `_validate_contract()` called from `make_app`):

```python
def _validate_contract(app: FastAPI) -> None:
    """V9 M2 — assert routes declared in api-contract.md match registered routes."""
    from .contract_loader import load_contract  # new helper
    contract = load_contract(REPO_ROOT / "docs" / "v9" / "api-contract.md")
    registered = {
        (route.methods.pop() if route.methods else "GET", route.path)
        for route in app.routes
        if hasattr(route, "path") and route.path.startswith("/api/v1/")
    }
    declared = {(e["method"], e["path"]) for e in contract["endpoints"]}
    missing = declared - registered
    if missing:
        raise RuntimeError(f"BE contract violation: declared routes not registered: {missing}")
    # Note: extra registered routes (not declared) are warned, not blocked,
    # so the legacy /api/status etc. don't crash startup.
    extras = registered - declared
    if extras:
        import warnings
        warnings.warn(f"Routes registered but not in contract: {extras}")
```

Hooked into `make_app(...)` right after route registration. Fails fast at startup.

## 6. `megalodon_ui/contract_loader.py` — parse api-contract.md

New module. Parses the structured Markdown into:
```python
{
    "endpoints": [
        {"method": "GET", "path": "/api/v1/state", "response_model": "StateResponse",
         "status": 200, "content_type": "application/json", "fe_consumers": [...],
         "sse_events": [...] | None},
        ...
    ]
}
```

Implementation: regex-based YAML block extraction (`^```yaml\n(.*?)\n```$` non-greedy, per section), then `yaml.safe_load` per block. ~40 LOC.

## 7. FE runtime fetch wrapper

New module: `ui/static/js/contract-trace.js`.

```javascript
// V9 M2 — runtime fetch wrapper for contract scan instrumentation.
// Active only when window.__M9_CONTRACT_TRACE__ === true.

if (typeof window !== "undefined" && window.__M9_CONTRACT_TRACE__) {
  const calls = [];
  const originalFetch = window.fetch.bind(window);
  const originalEventSource = window.EventSource;

  window.fetch = async function (input, init) {
    const url = typeof input === "string" ? input : input.url;
    const method = (init && init.method) || (typeof input === "string" ? "GET" : input.method) || "GET";
    calls.push({ kind: "fetch", method, url, ts: Date.now() });
    return originalFetch(input, init);
  };

  window.EventSource = function (url, options) {
    calls.push({ kind: "eventsource", method: "GET", url, ts: Date.now() });
    return new originalEventSource(url, options);
  };

  window.__M9_CONTRACT_CALLS__ = calls;
}
```

Loaded in `ui/static/index.html` via `<script src="/static/js/contract-trace.js"></script>` placed **before** the main bundle. No-op unless `__M9_CONTRACT_TRACE__` is set.

For the contract scan: the playwright test sets `window.__M9_CONTRACT_TRACE__ = true` via `page.addInitScript()` before navigation, then walks the SPA, then evaluates `window.__M9_CONTRACT_CALLS__` to extract the call log.

## 8. `scripts/contract_scan.py` — CLI orchestrator

~120 LOC (slightly over the V9-ROADMAP "~80 LOC" estimate, but the source-of-truth doc + runtime wrapper revision absorbed AST cost; net is still well under original budget).

```python
"""V9 M2 — PRE-VERIFY contract scan.

Three checks:
  1. BE startup: spawn `python -m megalodon_ui` and confirm it boots
     (BE-side validation in make_app() crashes if contract drift).
  2. Routes declared vs registered: cross-check via /api/v1/__contract_introspect__
     (new BE-side endpoint that returns registered route list).
  3. FE runtime: spawn playwright `ui/tests/e2e/contract-trace.spec.ts`
     which walks SPA + dumps all fetched URLs; cross-check against contract.

Outputs JSON to stdout:
{
  "pass": true|false,
  "contracts": [{"endpoint": "GET /api/v1/state", "status": "ok|missing|unregistered"}],
  "undocumented_fetches": ["GET /api/v1/foo"],
  "schema_mismatches": [],
  "untested_be_routes": ["GET /api/v1/status"],
  "duration_seconds": 12.4
}
"""
```

CLI:
```
python3 scripts/contract_scan.py [--soft] [--mission-dir PATH] [--port N]
```

- Default: strict — exit 1 if any `undocumented_fetches` or `schema_mismatches` non-empty.
- `--soft`: exit 0 always, report findings only.
- `--mission-dir`: defaults to `scripts/tests/fixtures/minimal_mission`.
- `--port`: defaults to 8089 (avoid 8080 collision).

Exit codes: 0 (pass), 1 (drift detected), 2 (BE failed to start), 3 (playwright failed).

## 9. New BE introspection endpoint

`GET /api/v1/__contract_introspect__` (factory-only, debug-tier):

```python
@app.get("/api/v1/__contract_introspect__")
async def contract_introspect():
    """V9 M2 — list registered routes for contract scan cross-check.

    Returns only /api/v1/* routes. Not part of public contract (declared with
    leading double-underscore by convention; contract scan special-cases it).
    """
    return {
        "registered": sorted({
            (next(iter(r.methods), "GET"), r.path)
            for r in app.routes
            if hasattr(r, "path") and r.path.startswith("/api/v1/")
            and not r.path.endswith("__contract_introspect__")
        }),
    }
```

Excluded from contract scan validation (special-case in `contract_scan.py`).

## 10. `ui/tests/e2e/contract-trace.spec.ts`

New playwright test driven by `contract_scan.py`:

```typescript
import { test, expect } from '@playwright/test';

test('contract trace — walk SPA + dump fetched URLs', async ({ page }) => {
  await page.addInitScript(() => { (window as any).__M9_CONTRACT_TRACE__ = true; });
  await page.goto('/static/index.html');
  await page.waitForSelector('[data-testid^="lane-row-"]', { timeout: 10000 });

  // Walk the major SPA routes
  await page.goto('/static/index.html#/findings');
  await page.waitForLoadState('networkidle');
  await page.goto('/static/index.html#/mission');
  await page.waitForLoadState('networkidle');

  // Wait for SSE to connect + emit some events
  await page.waitForTimeout(2000);

  const calls = await page.evaluate(() => (window as any).__M9_CONTRACT_CALLS__);
  console.log(JSON.stringify(calls));  // contract_scan.py parses stdout
});
```

Configured via `playwright.config.ts` (existing). Run via `./scripts/run_e2e.sh --grep contract-trace`.

## 11. Test strategy

### 11.1 `scripts/tests/test_contract_loader.py`
- `test_parses_endpoint_yaml_blocks`
- `test_parses_sse_events`
- `test_handles_path_templates`
- `test_rejects_malformed_yaml`
- `test_empty_contract_returns_empty_endpoints`

### 11.2 `scripts/tests/test_contract_scan.py`
- `test_passes_when_synced` — fixture contract + fixture mission + mock playwright → exit 0
- `test_fails_on_undocumented_fetch` — wrapper logs `/api/v1/foo` not in contract → exit 1
- `test_fails_on_unregistered_route` — contract declares `/api/v1/bar` not in introspect → exit 1
- `test_soft_mode_exits_zero` — same as fail case but with `--soft` → exit 0
- `test_be_start_failure_reports_exit_2` — kill the BE process pre-spawn → exit 2

### 11.3 `scripts/tests/test_be_contract_validation.py`
- `test_startup_passes_when_routes_match_contract`
- `test_startup_fails_when_route_declared_but_missing` — synthetic contract with bogus path → RuntimeError

### 11.4 Schema drift assert (in schemas.py, import-time)
- `SSEEventName` Literal must match `SSE_EVENT_TYPES` — asserted at import; catches M4 drift automatically.

**Total new tests:** ~14 across 3 files.

## 12. File manifest

| File | Action | LOC |
|------|--------|-----|
| `docs/v9/api-contract.md` | **Create** | ~250 (1 endpoint block × 11 endpoints) |
| `megalodon_ui/schemas.py` | **Create** | ~100 (12 BaseModel classes + SSEEventName drift assert) |
| `megalodon_ui/contract_loader.py` | **Create** | ~50 |
| `megalodon_ui/server.py` | **Modify** | +30 (call _validate_contract, add introspect endpoint) |
| `ui/static/js/contract-trace.js` | **Create** | ~30 |
| `ui/static/index.html` | **Modify** | +1 (load contract-trace.js) |
| `ui/tests/e2e/contract-trace.spec.ts` | **Create** | ~40 |
| `scripts/contract_scan.py` | **Create** | ~120 |
| `scripts/tests/test_contract_loader.py` | **Create** | ~80 (5 tests) |
| `scripts/tests/test_contract_scan.py` | **Create** | ~120 (5 tests + fixtures) |
| `scripts/tests/test_be_contract_validation.py` | **Create** | ~50 (2 tests) |
| `scripts/tests/fixtures/contracts/` | **Create** | 2-3 fixture contract.md files for testing |
| `HISTORY.md` | **Modify** | +1 entry |

**Total:** ~870 LOC across 13 files.

## 13. Definition of done

- [ ] `docs/v9/api-contract.md` lists all 11 factory `/api/v1/*` endpoints.
- [ ] `megalodon_ui/schemas.py` has Pydantic models for each endpoint + SSE drift assert.
- [ ] `megalodon_ui/contract_loader.py` parses contract MD into dict.
- [ ] `megalodon_ui/server.py::make_app()` validates contract at startup.
- [ ] `megalodon_ui/server.py` exposes `/api/v1/__contract_introspect__`.
- [ ] `ui/static/js/contract-trace.js` instruments fetch + EventSource (test-mode only).
- [ ] `ui/static/index.html` loads contract-trace.js.
- [ ] `ui/tests/e2e/contract-trace.spec.ts` walks SPA + dumps calls.
- [ ] `scripts/contract_scan.py` orchestrates 3-pronged check + JSON output.
- [ ] All 14 new pytest tests pass.
- [ ] Manual: `python3 scripts/contract_scan.py` against the live `scripts/tests/fixtures/minimal_mission` → exit 0, JSON shows all 11 endpoints `ok`.
- [ ] Manual: intentionally remove an endpoint from `api-contract.md` → re-run → exit 1, `undocumented_fetches` non-empty.
- [ ] HISTORY.md M2-COMPLETE entry appended.

## 14. Implementation order (TDD)

1. **Stub** `megalodon_ui/contract_loader.py` (raises NotImplementedError). Write failing `test_contract_loader.py` first test. Implement just enough. Add remaining 4 tests + impl.
2. **Build** `docs/v9/api-contract.md` for all 11 endpoints (data work, not code).
3. **Create** `megalodon_ui/schemas.py` with Pydantic models. Verify import-time SSE drift assert passes.
4. **Add** BE startup validation. Write `test_be_contract_validation.py` first (failing). Implement. Tests pass.
5. **Add** `/api/v1/__contract_introspect__` endpoint. Quick curl smoke.
6. **Create** `ui/static/js/contract-trace.js` + add `<script>` to `index.html`. Browser smoke: `window.__M9_CONTRACT_CALLS__` populates when flag set.
7. **Write** `ui/tests/e2e/contract-trace.spec.ts`. Run via `./scripts/run_e2e.sh --grep contract-trace`. Verify it dumps URLs.
8. **Create** `scripts/contract_scan.py`. Write `test_contract_scan.py` first (failing). Implement orchestration. Tests pass.
9. **End-to-end smoke**: `python3 scripts/contract_scan.py` against minimal mission → exit 0, JSON output sane.
10. **Negative smoke**: comment out one endpoint in `api-contract.md` → re-run → exit 1, finding reported.
11. **Append** HISTORY.md.

## 15. Risks

| Risk | Mitigation |
|------|------------|
| Pydantic schemas drift from actual response (existing factory might return extra keys) | Top-level-only validation (D6 scope); inner dicts loose. Tighten in v10. |
| api-contract.md parsing is brittle (Markdown structure changes) | Strict regex + YAML block convention; loader fails fast with clear errors. |
| Playwright spawn from inside `contract_scan.py` is fragile | Reuse `./scripts/run_e2e.sh`; capture stdout/stderr; clear exit-3 on failure. |
| BE startup validation breaks existing dev workflow | Validation in `_validate_contract()` is opt-in via env var initially (`M9_VALIDATE_CONTRACT=1`); flipped to default-on once contract.md is complete. |
| FE wrapper breaks production by accident | Hard-gated on `window.__M9_CONTRACT_TRACE__` flag; wrapper file is 30 LOC and trivially auditable. |
| Path-template normalization edge cases (regex vs `{param}`) | Normalize via simple sub: `re.sub(r'/[a-f0-9-]{8,}', '/{filename}', url)` for IDs/UUIDs; documented in CR-3 normalization rule. |

## 16. Out-of-scope adjacent items

- **Field-level response shape strictness** — defer to v10 (D6 scope decision).
- **Request body schema validation** — defer to v10; M2 only enforces routes + response shapes.
- **Legacy `/api/*` (non-v1) endpoint validation** — won't exist post-M1.6 cleanup.
- **Contract scan in CI** — defer to operator decision (this spec doesn't add GH Actions config).
- **TypeScript types from Pydantic models** — defer to v10 if pursued.

## 17. Dependency on M4

M2 imports `SSE_EVENT_TYPES` from `megalodon_ui.constants` (via schemas.py drift assert) and `API_*` constants for the schemas.py SSEEventName Literal. M4 must be complete before M2 implementation begins. (M4 already shipped per the milestone sequence.)

## 18. Document control

- Author: orchestrator (Claude)
- Date: 2026-05-16T22:00Z
- Status: APPROVED-FOR-PLAN (delegated brainstorming per operator 2026-05-16T21:12Z)
- Predecessor: V9-ROADMAP §M2 (post-CR-3 + CR-7)
- Successor: `docs/superpowers/plans/2026-05-16-v9-m2-contract-scan.md`
