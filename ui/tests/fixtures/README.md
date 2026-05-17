# Fixture mission directories

Deterministic past-run snapshots used by `ui/tests/` to drive integration and
E2E tests against the orchestrator-console UI. Each fixture is a self-contained
"mission directory" mimicking the megalodon project layout — the UI reads them
exactly as it reads a live mission dir.

Origin: produced for task `S-17` (cross-task pool) by `agent-9265` (TEST lane)
in mission `2026-05-16--megalodon-self-improvement`. Specified in
`findings/agent-9265-E-P1-test-plan-2026-05-16T15-33Z.md:91-113` and extended in
`findings/agent-9265-E-P2.5-test-plan-v2-2026-05-16T15-44Z.md` §"Updated fixture
inventory".

## Fixtures

| Name | Shape | Size | Generated? | Test workload |
|---|---|---|---|---|
| `fix-small` | 3 lanes, 12 ticks, 1 finding, single phase | tiny (~10 files) | static, checked in | smoke test, parser baseline |
| `fix-medium` | 6 lanes, 40 ticks across 4 phases, 12 findings of varied severity, 2 stale rows >15min, 4 SIGNAL/ACK exchanges | ~80 files | static, checked in | primary E2E workhorse |
| `fix-large` | 8 lanes (over-allocated), 150 ticks, 60 findings, full quorum chain, retroactive-recovery case, stale-reclaimed case | ~250 files | regenerated on demand via `_gen.py` to avoid checkin bulk | stress test (timeline rendering, finding-explorer pagination) |
| `fix-medium-failure-modes` | Same base as `fix-medium`, plus 3 baked-in failure shapes: stuck-phase-flip, multi-form claim collision, HISTORY-format-drift | ~85 files | static, checked in | failure-mode UI surfacing (added per `P2.5-E` plan-v2) |

## How tests consume fixtures

Tests inject the fixture path via env var or DI parameter to the BACKEND
server. Example (pseudo-Python):

```python
import pytest
from megalodon_ui.server import make_app

@pytest.fixture
def app_with_fixture(request):
    fixture_name = request.param  # "fix-small" / "fix-medium" / ...
    fixture_path = Path(__file__).parent / "fixtures" / fixture_name
    return make_app(mission_dir=fixture_path)
```

Per `P1-E` testability requirement **B.2**, BACKEND must accept an injected
`mission_dir`; failure to honor B.2 means tests cannot use fixtures.

## How to regenerate `fix-large`

```bash
python ui/tests/fixtures/_gen.py --target fix-large --seed 42
```

Seeded for determinism. Output is git-ignored under `fix-large/`. CI should
regenerate before test runs.

## How to extend / add a fixture

1. Pick a shape that exercises a specific UI behavior or protocol edge case.
2. If small (<100 files), build it literally and commit.
3. If large, extend `_gen.py` with a new `--target` arm; commit the generator
   change; document the shape in this README.
4. Add at least one test in `ui/tests/{integration,e2e}/` that uses the
   fixture.
5. Run the full test suite to ensure no regressions in other fixtures.

## Adversarial / failure-mode fixtures (CHALLENGE-4 from META `P2-F→E`)

The `fix-medium-failure-modes` fixture deliberately encodes three pathological
shapes the protocol exhibits in production multi-agent runs:

- **Shape-A (stuck phase-flip):** `.phase-flip-locks/PHASE-PLAN-to-PHASE-CHALLENGE`
  exists; `.mission-events` last line is still `INIT->PHASE-PLAN`; lock age = 5 min.
  Tests UI's stuck-flip detection (T-FX-FAILMODE-a).
- **Shape-B (multi-form claim collision):** `claims/P2-C→B/` AND
  `claims/P2-C-to-B/` both exist with `done` markers but only one finding
  exists. Tests UI's claim-dir de-duplication (T-FX-FAILMODE-b).
- **Shape-C (HISTORY-format-drift):** HISTORY.md contains three lane-name
  spellings (`F`, `FRONTEND`, `LANE-C`). Tests UI's drift-warning rendering
  (T-FX-FAILMODE-c).

These shapes are NOT bugs in the protocol per se — they are observed
artifacts that the UI must render gracefully. Without fixture coverage, the
UI's tolerance for these states is latent untested.
