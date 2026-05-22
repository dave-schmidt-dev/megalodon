# Run lifecycle — v9.4 convention

**Date:** 2026-05-22
**Status:** canonical (applies to all runs from v9.4 onward)
**Spec:** `docs/superpowers/specs/2026-05-22-v94-dogfood-and-run-lifecycle-design.md` (rev 2)

## Overview

Every mission run is a self-contained subdirectory scaffolded under `runs/`, archived to `.archive/` when complete, and registered in `.archive/INDEX.md`. No run leaves ephemera in the repo root or in the active-repo doc namespace.

---

## Run directory layout

```
runs/<UTC>--<slug>/
  MISSION.md              scope, lanes, exit criteria, phase progression
  STATUS.md               one-row-per-lane heartbeat board
  TASKS.md                run-scoped work queue (lane-tagged tasks)
  HISTORY.md              append-only run changelog
  README.md               what/why/lanes/what's-here (archive-ready)
  .mission-config.yaml    v9.1 schema; drives fleet spawn
  launch-<LANE>.md        per-lane launch file (x6, generated)
  findings/               per-agent finding files
  claims/                 task claim mutex directories
  signals/                inter-lane message files
  queue/                  applier intents (pending/ applied/ rejected/)
  .fleet/                 stream logs, tokens, applier lock
  .mission-events         structured event log (liveness source of truth)
```

UTC format for directory names: `YYYY-MM-DDTHH-MMZ` (filesystem-safe; colons replaced with dashes).

---

## scripts/new_run.sh

Scaffolds a run directory from `templates/run/`.

```
scripts/new_run.sh <slug> [--title T] [--summary S] [--exit-criteria X] [--force]
```

- Computes `RUN_DIR=runs/<UTC>--<slug>/`.
- Refuses if `RUN_DIR` already exists (unless `--force`).
- Refuses if any existing run under `runs/` is still **live** (unless `--force`); prints which run and how to archive it.
- Copies `templates/run/*` into `RUN_DIR` and substitutes `{{placeholder}}` values.
- Initializes empty `findings/ claims/ signals/ queue/{pending,applied,rejected} .fleet/` (each with `.gitkeep`).
- Seeds `.mission-events` with a structured `RUN-START` line.
- Runs `scripts/gen_lane_launches.py --mission-dir RUN_DIR --out-dir RUN_DIR` to generate per-lane launch files.
- Prints the launch command on success.

After scaffolding:

```bash
./scripts/start_applier.sh runs/<UTC>--<slug> &
./scripts/launch_fleet.sh --mission-dir runs/<UTC>--<slug> --spawn --port 8765
open http://localhost:8765/   # token in runs/<UTC>--<slug>/.fleet/ui.token
```

---

## scripts/archive_run.sh

Transactional archive of a completed run. Idempotent — re-running after a crash is safe.

```
scripts/archive_run.sh <run-dir> [--force]
```

Two committed phases:

1. **Move + verify.** `git mv <run-dir> .archive/<UTC>--<slug>/` (tracked move; git history is the durability guarantee). Verifies the destination file count matches source before writing the `.archived` sentinel.
2. **Register.** Appends one row to `.archive/INDEX.md` from `templates/run/INDEX-entry.tmpl`. Deduplicates by run ID — re-running produces no duplicate row.

The script refuses a live run (see liveness grammar below) unless `--force` is passed.

Path guard: both scripts refuse to operate on any path outside `runs/` or `.archive/`. They never touch repo source.

---

## scripts/preflight.sh

Gate that must pass before starting a real dogfood run.

```
scripts/preflight.sh [--dry-run]
```

Runs four automated checks (each prints `CHECK <name> PASS|FAIL`):

| Check | What it validates |
|---|---|
| `pytest-scope` | `pytest.ini` has `testpaths` + `norecursedirs` excluding `docs/` `.archive/` `runs/` |
| `test-deps` | All test deps resolve (`--extra test`) and the portable suite is green (excludes `isolated`-marked real-tmux tests and the non-portable pipe-pane ANSI test) |
| `friction-allowlist` | `.claude/settings.json` contains the three helper-script wildcards (`scripts/atomic_close.py:*`, `scripts/poll.py:*`, `scripts/run_e2e.sh:*`) |
| `lifecycle-scripts` | Smoke round-trip: `new_run.sh smoke` → write terminal event → `archive_run.sh` → assert archive populated and `runs/` clean |

With `--dry-run`, the manual `loops-armed` check (check 5) is skipped. For a live run, confirm all 6 lanes emit at least 2 STATUS heartbeats within 10 minutes before declaring the run started.

Exits 0 only if all automated checks pass.

---

## Liveness grammar

`.mission-events` lines begin with a structured first token. The **terminal tokens** are:

```
COMPLETE | ABORTED | DEGRADED-CLOSE
```

A run is **live** if and only if the first whitespace-delimited token of the last non-blank line in `.mission-events` is **not** one of the terminal tokens.

Implementation: `scripts/_run_liveness.py` (`is_live(path)`, `last_token(path)`, `TERMINAL_TOKENS`). Bash scripts call this via `uv run python3 scripts/_run_liveness.py <path>` (exit 0 = live, 1 = not live / missing).

`archive_run.sh` requires a terminal line before archiving. `new_run.sh` refuses to scaffold over a live run. Both accept `--force` to override.

---

## Uniformity rule

Every run follows the same lifecycle:

```
new_run.sh <slug>         ->  runs/<UTC>--<slug>/
archive_run.sh <run-dir>  ->  .archive/<UTC>--<slug>/  +  .archive/INDEX.md row
```

**Grandfathered exception:** `docs/v9/dogfood-2026-05-19/` was committed as planning input before this lifecycle existed. It stays in place and receives only a back-filled `INDEX.md` entry for registry completeness. Its bytes are not moved.

---

## Stimulus harness + Playwright visibility specs

The T4.3 dogfood gate is instrumented, not qualitative.

**`runs_harness/stimulus.py`** forces two failure-mode conditions against the live server and asserts the dashboard reflects each within a deadline:

| Check | Stimulus | Pass assertion |
|---|---|---|
| `stale-lane` | `POST /api/v1/_test/stale_override?lane=A&seconds=100000` (re-armed each poll) | Lane `A` appears in `/api/v1/lanes/stale` response `stale_lanes[]` |
| `signal-fidelity` | Write unique `signals/LANE-A-to-LANE-B-<UTC>-<uuid>.md` to disk | File's `filename` appears in `/api/v1/state` → `signals.list[]` |

CLI:

```bash
uv run python3 -m runs_harness.stimulus \
  --base-url http://localhost:8765 \
  --mission-dir runs/<UTC>--v94-ui-dogfood \
  --json-out runs/<UTC>--v94-ui-dogfood/.fleet/harness.json
```

Prints `CHECK <name> PASS|FAIL <latency>ms` per check; exits non-zero if any fail; writes JSON summary.

**`ui/tests/e2e/visibility.spec.ts`** covers interaction-fidelity at the DOM level (four suites):

| Suite | What it validates |
|---|---|
| snap-back | Navigate each of 5 nav routes during slow `loadConfig()`; assert URL stays on clicked tab (does not revert to dashboard). Fixed by the `_mountSeq` counter in `app.js`. |
| tab-highlight | Visit each route; assert `aria-current="page"` is set on the correct `.app-nav a` and absent from all others. |
| activity-wall fidelity | Write a real finding file into the served mission dir; assert a `.aw-row[data-event-type="finding"]` row with `data-event-lane="A"` appears in `[data-testid="aw-list"]`. |
| empty-state | Load `/signals` with no signals; assert `[data-testid="signals-empty"]` renders with "No signals yet" copy; no `.signals-thread-card` elements present. |

Run: `./scripts/run_e2e.sh ui/tests/e2e/visibility.spec.ts`

Activity-wall and empty-state fidelity live in Playwright (not the Python harness) because `__fake__/emit` feeds lane subscriber byte-queues, not the activity wall, and `__fake__/set_state` does not touch `STATUS.md` — both surface-level assertions require the rendered DOM.

---

## Templates

`templates/run/` contains canonical templates with `{{placeholder}}` substitution:

| Template | Produces |
|---|---|
| `MISSION.md.tmpl` | `MISSION.md` |
| `STATUS.md.tmpl` | `STATUS.md` |
| `TASKS.md.tmpl` | `TASKS.md` |
| `HISTORY.md.tmpl` | `HISTORY.md` |
| `README.md.tmpl` | archive `README.md` |
| `.mission-config.yaml.tmpl` | `.mission-config.yaml` (v9.1 schema, 6-lane default) |
| `INDEX-entry.tmpl` | one `.archive/INDEX.md` row |

Minimum placeholders: `{{SLUG}}`, `{{UTC}}`, `{{DATE}}`, `{{LANES}}`, `{{MISSION_TITLE}}`, `{{MISSION_SUMMARY}}`, `{{EXIT_CRITERIA}}`.

Substitution is handled by `scripts/run_lib.sh:subst_file()` which calls Python for safe replacement (handles slashes and newlines in values).

---

## Related docs

- `docs/v9/api-contract.md` — server API contract (endpoint shapes used by the harness)
- `docs/superpowers/specs/2026-05-22-v94-dogfood-and-run-lifecycle-design.md` — full design spec (rev 2)
- `docs/superpowers/plans/2026-05-22-v94-dogfood-and-run-lifecycle.md` — implementation plan
- `.archive/INDEX.md` — registry of all archived runs
