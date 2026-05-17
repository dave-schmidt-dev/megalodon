# ADR-006: `make_app(mission_dir=)` factory pattern

- **Status:** Accepted
- **Date:** 2026-05-16
- **Authored by:** agent-fec0 (ARCHITECT, LANE-B)
- **Task:** P3-B
- **Related:** ADR-001 (CAS), ADR-002 (SSE), ADR-003 (HTMX+Alpine), ADR-004 (FS-as-truth), ADR-005 (ASCII task-IDs), `README.md` v8, `findings/agent-fec0-B-P2.5-arch-plan-v2-2026-05-16T17-55Z.md` (P2.5-B + RECONSIDERED)

## Context

Run-1 shipped `ui/server.py` as a 1483-LOC monolith. Its `main()` function (lines 1453-1478) mutates module globals after `argparse`:

```python
def main() -> None:
    parser = argparse.ArgumentParser(...)
    args = parser.parse_args()
    global PROJECT_ROOT, PORT, HOST    # ← mutation of import-time globals
    if args.mission_dir:
        PROJECT_ROOT = Path(args.mission_dir).resolve()
    PORT = args.port
    HOST = args.host
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
```

This is **not a factory**. Three concrete problems:

1. **Tests cannot construct two app instances over two fixture directories in the same process** — the globals are singletons. Integration tests for `make_app(mission_dir=fixtureA)` and `make_app(mission_dir=fixtureB)` in the same `pytest -v` run would collide on `PROJECT_ROOT`.

2. **`from megalodon_ui import primitives` ImportError** at `ui/tests/unit/test_protocol_primitives.py:21` — the package doesn't exist, so all 8 unit tests SKIP. MISSION exit criterion #1 explicitly treats SKIP as FAIL.

3. **Origin allowlist hardcoded to port 8080** in `AppConfig.allowed_origins`, but MISSION.md:20 + `playwright.config.ts:23` use `8765` → all e2e POSTs would return 403 `ORIGIN_REJECTED`.

PHASE-CHALLENGE cross-lane work converged on a single architectural fix: extract `make_app(mission_dir=, port=, config=)` as a true factory function in a proper `megalodon_ui/` package.

## Decision

Adopt `make_app(*, mission_dir: Path, port: int = 8080, config: AppConfig | None = None) -> FastAPI` as the canonical FastAPI app constructor. The function:

1. **Takes `mission_dir` as a keyword-only parameter** — no env-var or argv side-effects required to bind a mission.
2. **Takes `port` as a keyword arg** with default 8080 — used to derive `AppConfig.allowed_origins` (`f"http://127.0.0.1:{port}"`, `f"http://localhost:{port}"`) unless `config.override_origins` is set.
3. **Returns a fully-wired `FastAPI` instance** with state attached to `app.state.megalodon` (frozen `AppState` dataclass; replaces module globals).
4. **Uses modern `lifespan=` context manager** (FastAPI ≥0.93) instead of deprecated `@app.on_event`.
5. **Lives in `megalodon_ui/server.py`** — a proper Python package at repo root, discoverable by pytest without an editable install.
6. **`megalodon_ui/__init__.py` uses PEP 562 lazy `__getattr__`** so `from megalodon_ui import primitives` does NOT trigger `fastapi` import (load-bearing for the stdlib-only test environment of `ui/tests/unit/`).

`ui/server.py` becomes a thin compat wrapper (~50 LOC):

```python
from megalodon_ui import make_app, AppConfig
import argparse, os, uvicorn
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(...)
    parser.add_argument("--mission-dir", default=os.environ.get("MEGALODON_MISSION_DIR"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MEGALODON_UI_PORT", "8080")))
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    mission_dir = Path(args.mission_dir or Path(__file__).resolve().parent.parent).resolve()
    app = make_app(mission_dir=mission_dir, port=args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")

if __name__ == "__main__":
    main()
```

`ui/mutations.py` is **deleted day-zero** (no compat shim) — its contents move to `megalodon_ui/mutations.py` in the same commit that updates `ui/server.py`'s `from mutations import ...` to `from megalodon_ui.mutations import ...`.

## Consequences

### Positive

- **Tests pass with 0 SKIPPED, 0 FAILED** (MISSION exit criterion #1). The unit suite's `from megalodon_ui import primitives` resolves at import time; integration tests construct `make_app(mission_dir=tmp_fixture)` per test.
- **Multi-fixture isolation** in a single `pytest` process. Each test's app instance has its own `AppState.mission_dir`, `csrf_token`, `event_bus`, `watcher`.
- **Origin allowlist correctness across launch modes**. `python ui/server.py --mission-dir <X> --port 8765` produces an app whose allowlist is `("http://127.0.0.1:8765", "http://localhost:8765")` — matches Playwright config; no spurious 403s.
- **No module globals to thread-pin**. Future moves (uvicorn workers > 1, multi-mission selector ADR-S9) are unblocked.
- **Tests can inject deterministic config**: `make_app(mission_dir=X, config=AppConfig(csrf_token="test-csrf", poll_interval_seconds=0.05))` — sub-second polling for fast tests.

### Negative

- **Slightly more import complexity** at the package layer. The PEP 562 lazy `__getattr__` is unusual but well-supported (Python ≥3.7) and documented in `megalodon_ui/__init__.py` with a one-line comment explaining the intent.
- **`from megalodon_ui import make_app` is no longer the eager-import form**. Callers must accept lazy resolution — almost always invisible in practice.
- **CH-6 `static_dir` defaults to `Path(__file__).resolve().parent.parent / "ui" / "static"`** which assumes the package is in a repo-root layout. Future `pip install megalodon-ui` will need `AppConfig(static_dir=<resource-path>)` — flagged in SPEC-v2 §8 as a run-3 item.

### Neutral

- **`AppState` is `@dataclass(frozen=True)`** (per BACKEND CH-7). Mutable runtime state (event subscribers, watcher tasks, queue contents) lives *inside* the contained objects (`EventBus`, `PollingWatcher`); the AppState references themselves never rebind.
- **Lifespan migration from `@app.on_event` to `lifespan=` context manager** has a soft FastAPI version floor of 0.93 (released 2023-02-16). MISSION.md:19's `uv run --with fastapi` currently resolves to ≥0.110, so this works today; TEST P3-E may pin via `conftest.py` or docstring.

## Alternatives considered

### A) Keep monolithic `ui/server.py` + add `make_app()` adjacent

Would fix exit criterion #1's import path superficially but leaves the module globals + `main()` mutation in place. Tests still race on the singleton.

**Rejected**: doesn't solve test parallelism. Half-measure.

### B) Move `make_app()` to `ui/server.py` (no `megalodon_ui/` package)

Path of least diff. But `ui/` is not a Python package (no `__init__.py`); `from ui.server import make_app` requires sys.path manipulation in tests. The cleanest install for testing is a package at repo root.

**Rejected**: friction with pytest test-discovery. The minor package-creation cost is worth the clean import path.

### C) Use FastAPI dependency injection for `mission_dir` instead of `app.state`

Could use `Depends(get_mission_dir)` with a per-request resolution. But the mission directory is process-static (one mission per uvicorn process); per-request resolution is unnecessary overhead.

**Rejected**: over-engineered for the constraint.

### D) Keep `ui/mutations.py` as a compat shim re-exporting from `megalodon_ui.mutations`

Considered in my P1-B §1.4. BACKEND CH-8 argued for delete-day-zero: one commit removes the file and updates the single `ui/server.py` import. No transition state.

**Adopted CH-8**: cleaner end-state, same risk. The "shim then delete" path has more steps and an awkward intermediate.

## Acceptance test (from SPEC-v2 §7)

The build satisfies ADR-006 iff:

1. `python -c "from megalodon_ui import primitives; import sys; assert 'fastapi' not in sys.modules"` exits 0.
2. `from megalodon_ui import make_app` works; calling `make_app(mission_dir=Path("/tmp/nonexistent"))` raises `FileNotFoundError`.
3. `make_app(mission_dir=fixture_path, port=9999).state.megalodon.csrf_token` is a 32-char hex string by default; `config.override_origins` of `None` produces port-derived allowlist `("http://127.0.0.1:9999", "http://localhost:9999")`.
4. Two `make_app(mission_dir=fixA)` and `make_app(mission_dir=fixB)` instances in the same Python process serve their own state via `httpx.AsyncClient` without interference.
5. `app.state.megalodon` is `@dataclass(frozen=True)` — attempting `app.state.megalodon.mission_dir = other` raises `FrozenInstanceError`.

TEST P3-E will write the corresponding pytest fixtures and assertions.

## References

- `findings/agent-fec0-B-P1-arch-plan-2026-05-16T17-37Z.md` (P1-B base plan)
- `findings/agent-fec0-B-P2.5-arch-plan-v2-2026-05-16T17-55Z.md` (P2.5-B + RECONSIDERED)
- `findings/agent-84f2-C-P2-challenge-of-architect-2026-05-16T17-58Z.md` (BACKEND CH-1 through CH-9)
- `findings/agent-43d9-E-P2-challenge-of-frontend-2026-05-16T17-49Z.md` (TEST C1+C2)
- `findings/agent-2e7a-D-P2-challenge-of-backend-2026-05-16T18-16Z.md` (FRONTEND C2 CSRF templating)
- `ui/SPEC-v2.md` (companion delta to v1 SPEC)
- `README.md` v8 (protocol — `lifespan=` requires FastAPI ≥0.93)
- `MISSION.md` exit criteria (§"Concrete exit criteria")
- `ui/tests/unit/test_protocol_primitives.py:21` (the import that motivated the whole change)
