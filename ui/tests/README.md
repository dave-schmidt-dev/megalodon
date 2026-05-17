# Megalodon UI Tests

Three-layer test suite for the Megalodon orchestrator-console UI. Built per
`findings/agent-9265-E-P1-test-plan-2026-05-16T15-33Z.md` (P1-E) and
`findings/agent-9265-E-P2.5-test-plan-v2-2026-05-16T15-44Z.md` (P2.5-E).

## Layer ratios

- **Unit (~60 %)** — `tests/unit/` — pure functions, no server. Fast (<1 s).
- **Integration (~25 %)** — `tests/integration/` — API × filesystem via httpx
  TestClient against a per-test fixture mission dir. Medium (~5–10 s).
- **E2E (~15 %)** — `tests/e2e/` — full browser + real server + fixture
  mission dir. Slow (~30–90 s).

## Running

### Unit + integration (pytest)

```
pytest -m unit          # unit tests only
pytest -m integration   # integration only
pytest                  # all pytest layers
```

### E2E (Playwright)

```
cd ui/tests/e2e
playwright install --with-deps chromium    # one-time (operator only; workers cannot install)
playwright test
playwright test --ui                       # interactive debugging
```

### All layers together

```
make test     # if a Makefile target is defined; otherwise pytest && cd e2e && playwright test
```

## Layout

```
ui/tests/
├── README.md                    (this file)
├── pytest.ini                   (markers + config)
├── unit/
│   ├── __init__.py
│   └── test_protocol_primitives.py
├── integration/
│   ├── __init__.py
│   └── test_api_endpoints.py
├── e2e/
│   ├── playwright.config.ts
│   ├── test_status_view.spec.ts
│   ├── test_orchestrator_actions.spec.ts
│   └── test_failure_modes.spec.ts
└── fixtures/
    ├── README.md
    ├── _gen.py                   (seeded fixture generator)
    ├── fix-small/                (literal; smoke test)
    ├── fix-medium/               (generated; primary E2E)
    ├── fix-large/                (regenerated on demand; stress test)
    └── fix-medium-failure-modes/ (generated; pathological shapes)
```

## Test ID conventions

Test IDs follow the matrices defined in `P1-E` §2/3/4 + `P2.5-E` §"Updated test
inventory". Each test docstring opens with its ID for cross-reference, e.g.:

```python
def test_R11_a_phase_flip_winner():
    """T-R11-a — winner branch of phase-flip race; verify full 4-step sequence."""
```

## Adding a test

1. Find the matching test ID in the coverage matrix (P1-E §2, §3, §4, or P2.5-E).
2. Write the test in the correct layer (unit / integration / e2e).
3. Reference the fixture by name (`fix-medium`, etc.).
4. Add the docstring header with test ID.
5. Run locally; commit.

## Hard constraints

- **No installs.** Workers cannot `pip install` or `npm install`
  (`MISSION.md:112`). Test runners assume `pytest` + `playwright` already
  available; if absent, run is BLOCKING (operator pre-step).
- **Mission-dir injection.** Tests inject `mission_dir` via env var or DI
  (testability requirement B.2 in P1-E §6). Server failure to honor B.2 is a
  P4 verification failure.
- **No source-project writes.** Tests under `ui/tests/` are write-allowed
  (RULE 7 allows BUILD lanes to write under their deliverable scope); the
  source-project mission dir is read-only.

## Skeleton-vs-implemented tests

Some tests in this initial scaffold are **skeletons** (decorated with
`@pytest.mark.skip(reason="awaits P3-C / P3-D")`) — they document the
intended assertion shape but cannot execute until BACKEND/FRONTEND lanes
ship code. Skeletons unblock the test plan from being shipped as paper
only; P4 verification will surface any skeletons that need fleshing out.

## P3-E acceptance criteria (from P1-E §9)

- `ui/tests/unit/` — at least one test per applicable RULE 1–11.
- `ui/tests/integration/` — at least one test per orchestrator-action endpoint.
- `ui/tests/e2e/` — at least one test per view (STATUS / TASKS / FINDINGS /
  SIGNALS / MISSION) + one per failure-mode shape.
- `playwright.config.ts` per P1-E §7 (webServer, workers cap, retries, HTML reporter).
- `pytest.ini` with `unit` and `integration` markers.
- This README.
