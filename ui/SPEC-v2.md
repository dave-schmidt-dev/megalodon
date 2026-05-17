# Megalodon Orchestrator-Console UI — Specification v2 (incremental delta)

- **Version:** 2.0 (incremental delta over `ui/SPEC.md` v1.0)
- **Authored by:** agent-fec0 (ARCHITECT, LANE-B)
- **Task:** `P3-B`
- **UTC:** 2026-05-16T19:04Z
- **Status:** Final for PHASE-VERIFY (`P4-A-to-B`)
- **Audience:** BACKEND P3-C (implementor), FRONTEND P3-D (consumer), TEST P3-E (verifier), AUDIT P4-A-to-B (verifier)

This document is a **delta** over `ui/SPEC.md` v1.0. Sections of v1 NOT mentioned here remain in force unchanged. Run-1 deferred items (cursor delta, mobile tiers, axe-core) remain explicitly out-of-scope for run-2.

## 0. What changed v1 → v2 (summary)

v1.0 specified the orchestrator-console as a monolithic FastAPI app under `ui/server.py`. **v2.0 promotes the design to a proper Python package `megalodon_ui/` with a `make_app(mission_dir=, port=, config=)` factory.** This unblocks MISSION exit criterion #1 (`from megalodon_ui import primitives` for unit-test import boundary) and enables multi-fixture integration tests within one process.

Changes are cross-lane-converged: all 5 lanes participated in shaping v2 via PHASE-CHALLENGE + PHASE 2.5 reconcile. Specifically:

| Source | Contribution |
|---|---|
| `findings/agent-fec0-B-P1-arch-plan-2026-05-16T17-37Z.md` (P1-B) | Initial package layout + factory contract |
| `findings/agent-fec0-B-P2.5-arch-plan-v2-2026-05-16T17-55Z.md` (P2.5-B) + RECONSIDERED | Δ1 owner.txt, Δ2 port-derived origins, Δ3 data-testid contract; CH-1 through CH-9 from BACKEND P2-C-to-B incorporated |
| `findings/agent-84f2-C-P2-challenge-of-architect-2026-05-16T17-58Z.md` (P2-C-to-B) | CH-1 PEP-562 lazy `__getattr__`; CH-2 `float` types; CH-3 FastAPI floor pin; CH-6 `static_dir` override; CH-7 frozen AppState; CH-8 delete-day-zero; CH-9 illustrative postAction |
| `findings/agent-84f2-C-P2.5-...` (P2.5-C, per BACKEND STATUS:11 @18:53Z) | 8 deltas concordant with my RECONSIDERED; index-html CSRF templating (Approach A) for FE C2 |
| `findings/agent-43d9-E-P2-challenge-of-frontend-2026-05-16T17-49Z.md` (P2-E-to-D C1+C2) | data-testid contract enumeration; port-allowlist drift |
| `.mission-events:3-5` + SIG-AUDIT-1 (4-LANE BLOCKING) | owner.txt-inside-lock at acquire time |

## 1. Package structure (replaces v1 §6 row "Static serving")

```
megalodon_ui/
├── __init__.py        # PEP 562 lazy __getattr__ (CH-1); exposes primitives submodule
├── primitives.py      # stdlib-only — no fastapi/uvicorn/sse_starlette imports
├── parsers.py         # STATUS/TASKS/HISTORY/.mission-events/findings parsers
├── state.py           # MissionState dataclass + snapshot_state(root)
├── mutations.py       # CAS, atomic write/append (relocated from ui/mutations.py; CH-8 day-zero delete of shim)
├── events.py          # EventBus + PollingWatcher (FastAPI-free; injected at app construction)
├── auth.py            # Origin + CSRF middleware factory
├── config.py          # AppConfig dataclass
└── server.py          # make_app() factory + route registration
```

**Hard import boundary** (load-bearing for MISSION exit criterion #1):
- `from megalodon_ui import primitives` MUST NOT pull `fastapi` into `sys.modules`.
- `__init__.py` uses PEP 562 lazy `__getattr__` for `make_app`/`AppConfig` to keep this property.
- Verifies via `python -c "from megalodon_ui import primitives; import sys; assert 'fastapi' not in sys.modules"`.

## 2. Factory contract (canonical; replaces v1 §3 stub-endpoint shape)

```python
def make_app(
    *,
    mission_dir: Path,
    port: int = 8080,                                 # Δ2 from P2.5-B
    config: AppConfig | None = None,
) -> FastAPI:
    """Build a Megalodon orchestrator console FastAPI app bound to mission_dir.

    Args:
        mission_dir: Path to the Megalodon project root. Must exist and be a readable directory.
        port: Bind port. Used to derive AppConfig.allowed_origins if not overridden.
        config: Optional AppConfig overrides. None → AppConfig() defaults.

    Returns:
        FastAPI app with:
          - 12 GET, 6 POST, 1 SSE endpoint
          - app.state.megalodon: AppState (frozen) — see §3
          - lifespan handler (NOT @app.on_event — modern FastAPI ≥0.93 form per CH-3)
          - static files mounted at /static from cfg.static_dir (defaults to <repo>/ui/static)

    Raises:
        FileNotFoundError if mission_dir does not exist.
        NotADirectoryError if mission_dir is not a directory.
        PermissionError if mission_dir not readable.
    """
```

### 2.1 AppConfig (frozen dataclass)

```python
@dataclass(frozen=True)
class AppConfig:
    csrf_token: str = field(default_factory=lambda: secrets.token_hex(16))
    heartbeat_interval_seconds: float = 15.0          # CH-2: float
    poll_interval_seconds: float = 2.0                # CH-2: float (was int=2; tests pass 0.05)
    file_watch_debounce_ms: int = 100
    stale_threshold_seconds: int = 900                # RULE 6 contract = integer seconds
    sse_queue_capacity: int = 100
    override_origins: tuple[str, ...] | None = None   # Δ2: None → port-derive in make_app
    static_dir: Path | None = None                    # CH-6: None → factory-derive default
    log_level: str = "INFO"
```

### 2.2 AppState (frozen; replaces v1 §"Concurrency model" globals)

```python
@dataclass(frozen=True)
class AppState:                                       # CH-7: frozen
    mission_dir: Path
    config: AppConfig
    event_bus: EventBus       # mutable internals, but the reference is frozen
    watcher: PollingWatcher
    csrf_token: str
```

Attached as `app.state.megalodon`. Route handlers acquire via dependency: `def get_state(req: Request) -> AppState: return req.app.state.megalodon`. **This eliminates v1's module-level globals** (`PROJECT_ROOT`, `CSRF_TOKEN`, `PORT`, etc.) that broke test parallelism.

### 2.3 Origin allowlist (replaces v1 §"Authentication" hardcoded list)

```python
def make_app(*, mission_dir, port=8080, config=None) -> FastAPI:
    cfg = config or AppConfig()
    origins = cfg.override_origins or (
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
    )
    # ... origins attached to AppState; auth middleware reads from app.state.megalodon ...
```

**Why this matters** (per TEST P2-E-to-D C2): MISSION.md:20 launches the server at `--port 8765`, `playwright.config.ts:23` uses `8765`, but v1 hardcoded the allowlist to 8080 → all e2e POSTs would 403 `ORIGIN_REJECTED`. v2 derives the allowlist from the actual bind port.

### 2.4 Lifespan handler (replaces v1 `@app.on_event` deprecation)

```python
@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    state: AppState = app.state.megalodon
    await state.watcher.start()
    log.info("Megalodon UI ready: mission=%s, csrf=%s", state.mission_dir, state.csrf_token)
    yield
    await state.watcher.stop()
```

**FastAPI floor pin** (per BACKEND P2-C-to-B CH-3 ACCEPT-MODIFIED): TEST P3-E codifies `--with 'fastapi>=0.93' --with 'uvicorn[standard]>=0.20'` in test docstrings or `conftest.py`. MISSION.md:19 invocation should optionally include the pin; current `uv run --with fastapi ...` resolves to latest (≥0.110), so today's path works, but the pin is cheap insurance.

## 3. Phase-flip handler — owner.txt write (NEW per v8.1-OBS-1)

The `POST /api/v1/phase-flip` handler MUST write `<lock-dir>/owner.txt` atomically inside the lock at mkdir time, BEFORE the `.mission-events` append. This closes SIG-AUDIT-1 (Edit-14 false-positive) in the orchestrator-UI path:

```python
async def post_phase_flip(req: Request, state: AppState = Depends(get_state)) -> dict:
    body = await _safe_json(req)
    from_phase = body["from"]; to_phase = body["to"]; reason = body["reason"].strip()
    # ... existing validation ...
    lock_dir = state.mission_dir / ".phase-flip-locks" / f"{from_phase}-to-{to_phase}"
    try:
        lock_dir.mkdir(exist_ok=False)
    except FileExistsError:
        return JSONResponse(status_code=409, content={
            "ok": False, "code": "CONCURRENT_FLIP", "recoverable": True,
            "lock_path": str(lock_dir.relative_to(state.mission_dir)),
        })
    # NEW v2 — write owner.txt INSIDE the lock at acquire time
    owner_text = f"orchestrator-ui acquired-utc={utc_now_iso()}\n"
    (lock_dir / "owner.txt").write_text(owner_text, encoding="utf-8")
    # Continue with .mission-events append + README update + SSE broadcast.
    ...
```

**First practical exercise**: BACKEND won the PHASE-CHALLENGE→PHASE-BUILD flip @19:01Z and applied this fix (see `.mission-events:6` + `.phase-flip-locks/PHASE-CHALLENGE-to-PHASE-BUILD/owner.txt`). The pattern works.

Workers (non-UI) SHOULD also touch owner.txt at mkdir time. AUDIT P3-A may codify the worker-side rule as a v8.1 protocol amendment.

## 4. CSRF templating — Approach A index.html route (FE P2-D-to-C C2 NOVEL)

The `GET /` index handler templates `__CSRF_TOKEN__` into the served HTML:

```python
@app.get("/", response_class=HTMLResponse)
async def index(state: AppState = Depends(get_state)) -> str:
    template = (state.config.static_dir / "index.html").read_text(encoding="utf-8")
    return template.replace("__CSRF_TOKEN__", state.csrf_token)
```

FE's `mission.js` reads the token from a `<meta name="csrf-token" content="__CSRF_TOKEN__">` tag in the static template — eliminates the `getCsrf()` fetch round-trip on every action and removes a class of race conditions during page bootstrap.

## 5. Data-testid contract (codifies P2.5-B Δ3 + TEST P2-E-to-D C1)

The 6 mutation forms MUST expose these `data-testid` values. This is normative for `ui/static/pages/mission.js` and is what `ui/tests/e2e/test_orchestrator_actions.spec.ts` exercises:

| Form | Required `data-testid` values | Notes |
|---|---|---|
| signal | `action-post-signal` (container), `signal-to`, `signal-text`, `signal-cite`, `signal-error`, `submit-signal` | `signal-from` removed — sender implicit (orchestrator); TEST P3-E updates spec |
| reclaim | `action-reclaim-lane` (mission form), `action-reclaim-{LANE}` (per-lane in dashboard), `confirm-reclaim` (modal) | Destructive → confirm |
| challenge | `action-inject-challenge`, `challenge-finding-picker`, `submit-challenge` | picker = `finding_filename` select |
| phase-flip | `action-flip-mission` (rename from `action-phase-flip`), `flip-target-{PHASE}` (per option), `confirm-flip`, `current-phase` (alias on `mission-phase`) | Destructive into DRAINING/COMPLETE → confirm |
| mission-status | `action-mission-status`, `mission-status-{ACTIVE,DRAINING,COMPLETE,IDLE}`, `submit-mission-status`, `confirm-mission-status` | DRAINING/COMPLETE → confirm |
| inject-task (NEW) | `action-inject-task`, `inject-task-text`, `inject-task-section`, `submit-inject-task`, `inject-task-error` | Only entirely-new form in P3-D |

Each Playwright test selector that misses (per TEST C1 mismatch table) is resolved either by FE adding the bridge testid (Option A) or by TEST updating the spec where the semantic contract differs (Option C). P3-D + P3-E coordinate.

## 6. Migration of `ui/server.py` and `ui/mutations.py` (CH-8 day-zero, no shim)

P3-C executes in this order:

1. Create `megalodon_ui/` package with `primitives.py` + `mutations.py` + `state.py` + ...
2. Implement `make_app()` factory in `megalodon_ui/server.py`
3. Update `ui/server.py` to a thin compat wrapper (~50 LOC) that imports `make_app` and runs `uvicorn.run`
4. `rm ui/mutations.py` (delete-day-zero per BACKEND CH-8; no compat shim)
5. Verify `python ui/server.py --mission-dir ui/tests/fixtures/fix-medium --port 8765` still works

Then run the 5-endpoint curl smoke step (per BACKEND P2.5-C Δ5 / FE P2-D-to-C C3).

## 7. Acceptance criteria (handoff to TEST P3-E and AUDIT P4-A-to-B)

The build (P3-C) satisfies SPEC-v2 iff:

1. **Pure-Python import boundary**: `python -c "from megalodon_ui import primitives; import sys; assert 'fastapi' not in sys.modules"` exits 0.
2. **Factory contract**: `make_app(mission_dir=fixture_path, port=9999)` returns a FastAPI app whose Origin allowlist contains `http://127.0.0.1:9999` and `http://localhost:9999`.
3. **AppState frozen**: `app.state.megalodon.mission_dir = other` raises `FrozenInstanceError`.
4. **Phase-flip owner.txt**: a successful `POST /api/v1/phase-flip` creates `<lock>/owner.txt` with the runtime UTC.
5. **CSRF templating**: `GET /` HTML contains the runtime `csrf_token` substituted into the `<meta name="csrf-token">` tag, NOT the literal `__CSRF_TOKEN__` placeholder.
6. **Lifespan migration**: `app.router.lifespan_context` is set; no `@app.on_event` decorators remain.
7. **Data-testid contract**: each row in §5 has a matching `data-testid="<value>"` in `ui/static/pages/mission.js` for the existing 5 forms; the new `inject-task` form is fully implemented.
8. **Multi-fixture isolation**: TEST integration suite constructs `make_app(mission_dir=A)` and `make_app(mission_dir=B)` in the same process; both serve their own state via `httpx.AsyncClient`.
9. **MISSION exit criteria (1, 2, 3, 4)** pass (operator verifies #5 separately).

## 8. Out of scope for v2 (run-3 candidates)

- Mobile responsive viewport tiers (S-6 from run-1)
- axe-core a11y testing
- Cursor-based delta resync (P2.5-B §6 deferral preserved)
- Multi-mission selector (S-9 from run-1)
- pip-install packaging of `megalodon_ui` (CH-6 sets `static_dir` override to make this future-friendly)
- Worker-side phase-flip owner.txt enforcement (v8.1 protocol amendment, AUDIT P3-A territory)

## 9. ADR index update

v1 ADRs 001-005 remain. NEW:

- **ADR-006** — `make_app(mission_dir=)` factory pattern. Status: **Accepted**. See `ui/adrs/ADR-006-make_app-factory.md`.

## 10. Forward dependencies and PHASE-VERIFY readiness

| Item | Owner | Blocking PHASE-VERIFY |
|---|---|---|
| `megalodon_ui/` package + tests | BACKEND P3-C | Yes |
| `ui/static/pages/mission.js` updates per §5 + `inject-task` form | FRONTEND P3-D | Yes |
| `ui/tests/` updates: import path, fixture make_app, data-testid alignment, Playwright pre-flight | TEST P3-E | Yes |
| Worker-side owner.txt protocol amendment | AUDIT P3-A v8.1 | No (doesn't gate build) |
| Mid-mission META report | META P3-F | No |

SPEC-v2 is final for the run. RECONSIDERED-append welcome; rewrites are not.

---

## RECONSIDERED (2026-05-16T20:04Z): incorporating AUDIT P4-A-to-B G1-G4

AUDIT P4-A-to-B (`findings/agent-dcbc-A-P4-verify-of-architect-2026-05-16T19-59Z.md`) verified SPEC-v2 + ADR-006 with **9 strong concordances + 4 MINOR gaps + 1 cross-ref clarification**, severity DELTA. Per their recommendation ("recommend ARCHITECT RECONSIDERED-append rather than rewrite") and TIER-2 §"RECONSIDERED preserves audit trail," I append fixes for all 4 gaps inline below. AUDIT explicitly noted G3 was self-resolved by TEST P3-E delivery.

### G1 — Cross-ref to v8.1 Edit 23 worker-side owner.txt (RECONSIDERED-§3)

**SPEC-v2 §3** scope is UI-only by design. AUDIT correctly notes the worker-side mkdir owner.txt extension is codified in `docs/v8.1-candidate.md` Edit 23. Cross-reference added:

> §3 final paragraph (amended): *"Worker-side mkdir owner.txt is codified in `docs/v8.1-candidate.md` Edit 23 (AUDIT P3-A @19:11Z). All workers SHOULD touch `claims/<task-id>/owner.txt` with `<agent-id>\n<UTC>` content in the same Bash invocation that does `mkdir claims/<task-id>`. The pattern is identical for `.phase-flip-locks/` and `claims/`; the only difference is which dir."*

### G2 — §7 acceptance #4 owner.txt content format (RECONSIDERED-§7)

**SPEC-v2 §7 #4** is currently binary (exists/not). Per AUDIT G2, Edit 14 detector requires content matching the canonical 2-line `<agent-id>\n<UTC>` format. Amendment:

> §7 acceptance criterion #4 (amended): *"a successful `POST /api/v1/phase-flip` creates `<lock>/owner.txt` with content matching `^agent-[0-9a-f]{4}\n\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?Z\n?$`. Binary existence is necessary but not sufficient — Edit 14 detector reads owner.txt content to identify the lock-holder; malformed owner.txt is equivalent to missing owner.txt."*

This regex matches v8 agent-id format (`agent-<4-hex>`) + ISO-8601 UTC with optional seconds. Tests should assert both file existence AND content shape.

### G3 — §5 `signal-from` removal coordination with TEST (RECONSIDERED-§5)

**Status: SELF-RESOLVED** per AUDIT's tick-31 note (STATUS:9) confirming TEST P3-E DONE @19:54Z. No SPEC change needed; coordination converged.

If TEST P3-E delivery does NOT in fact remove `signal-from` selectors from `test_orchestrator_actions.spec.ts`, this becomes a P5-RUN-MUTATIONS-E2E REPAIR candidate (TEST scope, not SPEC scope).

### G4 — §10 forward-deps "Worker-side owner.txt protocol amendment" status (RECONSIDERED-§10)

**SPEC-v2 §10 row** was published with status "No (doesn't gate build)" at P3-B ship time (19:08Z). AUDIT P3-A landed at 19:11Z. Updated row status:

> §10 row update: *"Worker-side owner.txt protocol amendment | AUDIT P3-A v8.1 | **DONE @19:11Z per `docs/v8.1-candidate.md` Edit 23**; no longer blocking anything; informational cross-reference only."*

### CR1 — §1/§8 ↔ ADR-006 §Consequences static_dir cross-ref (acknowledged, no action)

AUDIT correctly observed that SPEC-v2 §8 (out-of-scope for v2) and ADR-006 §"Consequences:Negative" both reference `pip install megalodon-ui` packaging as a run-3 concern. The cross-reference exists but is implicit. AUDIT explicitly recommended **no action** for this; just informational. Acknowledged.

### Summary

| Gap | Action | Status |
|---|---|---|
| G1 | §3 cross-ref to Edit 23 | RECONSIDERED-appended |
| G2 | §7 #4 owner.txt content regex | RECONSIDERED-appended |
| G3 | §5 signal-from coord | SELF-RESOLVED by TEST P3-E |
| G4 | §10 status update | RECONSIDERED-appended |
| CR1 | §1/§8/ADR-006 static_dir | No action per AUDIT recommendation |

Per AUDIT verdict "shippable as-is OR with cheap RECONSIDERED-appends for G1-G4" — chose the latter.

Verify reciprocal: my P4-B-to-E `findings/agent-fec0-B-P4-verify-of-test-2026-05-16T19-59Z.md` returns the favor (TEST coverage maps to SPEC-v2 §7 with 5 FULL + 3 PARTIAL + 2 MINOR-GAP). No DISSENT from either lane on the other's work.

---

## §3-bis. SSE flush latency contract (PHASE-HEAL clarification, 2026-05-16T20:28Z)

**Trigger.** `findings/agent-43d9-E-P5-RUN-mutations-e2e-2026-05-16T20-12Z.txt` showed 8-of-16 e2e tests hit Playwright's 30s action-timeout on UI-state-change-after-POST assertions. BE root-cause hypothesis (`StreamingResponse` buffering held `status-change` events from `.mission-events` / STATUS.md / TASKS.md mutations) converged with FRONTEND + AUDIT lane analysis. v1 SPEC-v2 §1 mandated "1 SSE endpoint" + `AppConfig.sse_queue_capacity` but did NOT mandate flush latency — under-specified contract surfaces as inter-lane root-cause disputes.

**Normative requirement (new).** The `GET /api/v1/events` endpoint MUST emit each `status-change` SSE event to connected clients within **500 ms** of the underlying server-side state change (STATUS.md mtime tick, TASKS.md bracket flip, .mission-events append, or any of the 6 mutation endpoints completing). The 500 ms upper bound provides ≥60× headroom under Playwright's default 30 s action-timeout.

**Implementation choice (non-normative).** Either path satisfies the contract:
1. `EventSourceResponse` from `sse-starlette` (per-event flush by default).
2. `StreamingResponse` with explicit `b"\n\n"` flush after every payload write.

BE retains implementation discretion. The XFAIL test `ui/tests/integration/test_sse_stream.py::test_sse_stream_emits_status_change_on_file_touch` (currently `strict=True`) is the canonical verification — it must XPASS within 500 ms of the file-touch trigger after the fix.

**Out-of-scope for §3-bis.** This clause does not modify the §3 / §4 / §5 / §6 / §7 surface; it adds a single latency floor for one endpoint. The static-mount regression (BE expanded `REPAIR-MUTATIONS-E2E-1-SSE` scope in TASKS.md @ 20:26Z) is NOT a SPEC defect — §1 already mandates `static files mounted at /static from cfg.static_dir`. That is impl-vs-spec drift, repaired by BE in the same claim.

**Observation (v8.1 candidate OBS-RUN-6, MAJOR — RECONSIDERED-AMENDED 2026-05-16T20:37Z per AUDIT tick-48 PARTIAL-DISSENT).** P4-E-to-C TEST 25-PASS missed the StaticFiles mount absence. **Corrected diagnosis** (AUDIT-verified): `httpx.ASGITransport` DOES route through Starlette's app-router layer including `Mount("/static", ...)` — my initial framing ("ASGITransport bypasses mounts") was imprecise. The actual gap is **test-coverage**: the P4-E-to-C integration suite never *requested* any `/static/*` path. v8.1 fix proposals (unchanged, both still valid): (a) add an explicit acceptance test that asserts a `StaticFiles` route appears in `app.routes` post-`make_app`, OR (b) add a single httpx integration test that requests `/static/css/base.css` against an ASGITransport-mounted app and asserts 200 (this exercises the mount roundtrip). AUDIT correction accepted; fix proposals stand.

---

## §3-ter. SPA route enumeration (PHASE-HEAL clarification, 2026-05-16T20:42Z)

**Trigger.** `findings/agent-2e7a-D-SIGNAL-FE-1-spa-routes-2026-05-16T20-36Z.md` + BACKEND STATUS:11 pre-drafted REPAIR-2 plan. `ui/static/index.html:50-53` nav `<a href>` targets `/tasks`, `/findings`, `/mission`, `/signals`. The factory registers only `/`. Legacy `ui/server.py` ALSO lacks catch-all (BE grep-verified). SPEC-v2 §1 referenced "12 GET" routes without enumerating which 12 — leaving SPA navigation a frontend-implicit assumption.

**Normative requirement (new).** The `make_app()` factory MUST register a SPA-shell handler that returns the rendered `ui/static/index.html` (with CSRF token substitution per §4) for these client-side paths: `GET /`, `GET /tasks`, `GET /findings`, `GET /mission`, `GET /signals`. Equivalent implementation: a single catch-all `GET /{spa_path:path}` registered LAST in `_register_routes` that returns the index template when `spa_path` does not match an api or static route. The §1 route surface ("12 API GETs + 6 mutation POSTs + 1 SSE endpoint") is unchanged; the SPA shell handler is additive infrastructure.

**Why.** Playwright e2e tests call `page.goto("/tasks")`, `page.goto("/findings")`, `page.goto("/mission")` in `ui/tests/e2e/test_failure_modes.spec.ts:22,31,39,48` and `test_status_view.spec.ts:37,46,55`. Without catch-all these return 404 before any DOM/assertion logic runs. Required to satisfy MISSION exit criterion #3 ("browser renders cleanly across primary nav").

**Implementation guidance (non-normative).** Register the catch-all LAST so api/static routes match first. BE's REPAIR-MUTATIONS-E2E-2-SPA-CATCHALL (~10 LOC `@app.get("/{path:path}")` returning index.html for non-api/non-static paths) satisfies this clause. Verify by: (a) Playwright `page.goto("/tasks")` reaches rendered DOM, (b) integration test `await client.get("/findings")` returns 200 with HTML body containing `<meta name="csrf-token">`.

**Out-of-scope for §3-ter.** This clause does not introduce client-side routing logic, lazy-load splits, or per-route SSR. Each SPA path returns the same shell; client-side JS handles route differentiation post-load.

---

## §3-quater. PHASE-HEAL clarification — required `/api/v1/*` endpoints + status response fields (2026-05-16T20:56Z)

**Trigger.** BE STATUS:11 tick-heartbeat @20:52Z line-cited 2 confirmed factory gaps surfaced by P5-RUN-MUTATIONS-E2E re-run (transcript `findings/agent-43d9-E-P5-RUN-mutations-e2e-2026-05-16T20-50Z.txt`): (1) `/api/v1/tasks` endpoint absent from factory (legacy `ui/server.py:927` had it; affects e2e #8 TASKS view); (2) `staleness_seconds`/`is_stale` fields absent from `/api/v1/status` response per row (`megalodon_ui/server.py:65-88`; affects e2e #4 `lane-row-AUDIT[data-stale='true']`).

**Background.** SPEC-v2 §1 declared "12 GET, 6 POST, 1 SSE endpoint" without enumerating which 12. Legacy `ui/server.py` exposed 12 GETs under `/api/v1/` (state, status, tasks, phase, mission-events, findings, findings/{filename}, history, claims, signals, lanes/{lane}, config) + 1 SSE; the factory shipped with only 4 (`status`, `findings`, `config`, `events`). Of the missing 8, only `/api/v1/tasks` is required by run-2 e2e tests. Same enumeration-silence pattern as the SPA-route gap (§3-ter), surfacing again at a different layer.

**Normative requirement (additive minimum for run-2 PASS).**

1. **`GET /api/v1/tasks`** — returns `{phases: [{name: str, tasks: [{id, state, lane, agent, utc, summary}]}]}` parsed from `TASKS.md` per-phase header. The bracketed-state grammar is the existing `[ ] | [claimed: agent-X @ UTC] | [done: agent-X @ UTC] | [blocked: <reason>]`. Used by `ui/static/pages/tasks.js:417,452` (store key `tasks.phases`).

2. **`GET /api/v1/status` response** — each row dict MUST include both `staleness_seconds: float` (computed as `(now_utc - parse(last_utc)).total_seconds()` per row) and `is_stale: bool` (defined as `staleness_seconds > 900` per RULE-1 15-min threshold). Used by `ui/static/pages/dashboard.js:115` (`stalenessBand(row.staleness_seconds)`) and `:187` (`!!row.is_stale`).

**Out-of-scope for §3-quater.** The other 7 legacy `/api/v1/` GET endpoints (`state`, `phase`, `mission-events`, `findings/{filename}`, `history`, `claims`, `signals`, `lanes/{lane}`) are not required by run-2 e2e tests. Severity-filter (#10) + scratch-toggle (#11) e2e residuals are fixture-class per BE diagnosis.

**§3-quater AMENDMENT — RECONSIDERED-RETRACTED 2026-05-16T21:34Z.** I added a normative requirement at 21:30Z for `GET /api/v1/state` based on BE's claim that `sse.js:67`'s `fetch("/api/v1/state")` was the only bootstrap path. **BE retracted that hypothesis in mea-culpa #3 @21:30Z** (TEST RECLASSIFY @21:25Z showed BE REPAIR-5 fixes DID flow through — test #8 went from count=0 to count!=0; orchestrator clicks unblocked). The store hydrates via SSE event handlers + lazy slice-fetches, so the bootstrap call silently 404'ing does NOT prevent hydration. **My grep-level verification was correct in isolation but missed transitive completeness**: I checked that `sse.js:67` calls `/api/v1/state` but didn't check whether `store.js` has fallback hydration paths from SSE events. It does. `/api/v1/state` is therefore NOT a SPEC requirement for run-2; restore `state` to the out-of-scope list above. **ARCHITECT lesson**: SPEC anchors based on consumer-side grep alone are insufficient when consumer has event-driven fallbacks. PRE-CLASSIFY INVARIANT for v8.1: "is this call the ONLY hydration/data path, or are there event-driven fallbacks?" — same primitive BE proposed in their mea-culpa. TEST also RETRACTED REPAIR-11-STATE-ENDPOINT @21:33Z. HEAL-3 continues with REPAIR-7+8 (FE) and REPAIR-9+10 (TEST done) as the correct work surface.

**Implementation guidance (non-normative).** BE's pre-drafted ~30 LOC plan satisfies this clause. Verify via re-run of `ui/tests/e2e/test_status_view.spec.ts` — tests #4 (`lane-row-AUDIT[data-stale='true']`) and #8 (TASKS view bracket states) must PASS without further endpoint additions.
