---
title: Megalodon v9 M3 — Helper Scripts Design
status: APPROVED — ready for implementation plan
plan-ref: docs/v9/V9-ROADMAP.md §M3 (lines 114-138)
utc: 2026-05-16T22:30Z
author: orchestrator-Claude (resume session)
brainstorm-input: superpowers:brainstorming (one-pass, operator approved sections 1-4 and skipped 5-7 to spec)
---

# Megalodon v9 M3 — Helper Scripts Design

## 1. Purpose

Ship three operator-allowlisted scripts so workers stop triggering Claude Code permission prompts on routine RULE-10 closes and state polls. M3 is the operationally-cheapest piece of v9; it lands first in the Migration plan §3 (`docs/v9/V9-ROADMAP.md`) because it immediately closes the failure class that caused run-2's 17-min "MULTI-LANE-SIMULTANEOUS-SILENCE" event (SIG-ORCH-6 @21:21Z).

## 2. Scope (per brainstorming Q4)

In scope for this spec + the implementation plan that follows:

1. **Three scripts**: `scripts/atomic_close.py`, `scripts/poll.py`, `scripts/run_e2e.sh`, plus internal modules (`_shared_state.py`, `_state_read.py`, `_logging.py`, `_validation.py`, `_backends/direct_fcntl.py`).
2. **launch.md §5 grammar update**: codify that workers MUST use these scripts for RULE-10 close and state polling; NEVER use Python heredocs or compound bash (`cmd1 && cmd2 && for ...; do ...; done`) for those flows. Per V9-ROADMAP M3 paragraph 4.
3. **playwright.config.ts cleanup** (Codex CR-5 ACKNOWLEDGE): replace the two `cd /Users/dave/Documents/Projects/megalodon && uv run ...` webServer commands with `uv run --directory /Users/dave/Documents/Projects/megalodon ...`. No `cd`, no compound chain.
4. **Operator allowlist documentation update**: document the three additions the operator needs to make to allowlists (`python3 scripts/atomic_close.py *`, `python3 scripts/poll.py *`, `./scripts/run_e2e.sh *`). Spec lists what to add; operator applies.

Out of scope (deferred to M1, M1.5, M1.6, or later):

- The queue daemon + applier (M1).
- Migrating `megalodon_ui/server.py` mutation endpoints to queue_client (M1.5).
- Backend unification factory canonical (M1.6).
- Migrating M3 scripts' write backend from direct fcntl to queue_client. **This is a one-line internal swap when M1 lands** — see §5.3.
- `scripts/migrate_claims_to_owner_txt.py` (M1, per CR-6).

## 3. Locked decisions from brainstorming

| # | Question | Decision |
|---|---|---|
| Q1 | Pre-M1 write path for `atomic_close.py` | **Interface-first via `_shared_state.py` shim.** M3 ships direct-fcntl backend; M1 swaps to queue_client backend. atomic_close.py CLI + tests unchanged at swap. |
| Q2 | `poll.py` JSON shape | **Standalone comprehensive shape.** `--brief` drops events_tail + findings_recent; `--full` includes findings file bodies. Decoupled from BE `/api/v1/state`. |
| Q3 | Test strategy | **pytest tmp_path + hand-crafted minimal fixture in `scripts/tests/fixtures/minimal_mission/`.** Per-test copytree to writable temp dir. |
| Q4 | Scope of M3 implementation | **All four items** (3 scripts, launch.md grammar update, playwright.config.ts cleanup, allowlist docs). See §2. |
| Q5 | `_shared_state.py` atomicity model | **Approach B — best-effort + journal.** Per-file fcntl locks, sequential steps, partial-close journal at `mission/.scripts-journal/<request-id>.json`, `--resume` for remediation. Mirrors `docs/v9/queue/applier.py` journal-recovery model so M3→M1 cutover is internal-only. |

## 4. Module layout

```
scripts/
├── atomic_close.py              # CLI entry; thin wrapper around _shared_state
├── poll.py                      # CLI entry; thin wrapper around _state_read
├── run_e2e.sh                   # bash wrapper around uv + playwright
├── _shared_state.py             # M3/M1 abstraction boundary; routes to one _backend
├── _state_read.py               # read-only mission state aggregation for poll.py
├── _logging.py                  # RotatingFileHandler factory; --debug toggle
├── _validation.py               # CR-4 regexes + lane canonicalization
├── _backends/
│   ├── __init__.py
│   └── direct_fcntl.py          # M3 write backend
│   # (queue_delegate.py is added in M1; not in M3 scope)
└── tests/
    ├── __init__.py
    ├── conftest.py              # pytest mission_dir fixture (copytree minimal_mission)
    ├── test_atomic_close.py
    ├── test_poll.py
    ├── test_shared_state.py
    ├── test_state_read.py
    ├── test_validation.py
    └── fixtures/
        └── minimal_mission/     # template — copied per test
            ├── STATUS.md
            ├── TASKS.md
            ├── HISTORY.md
            ├── .mission-events
            ├── claims/
            │   └── TEST-1/      # one open claim, no done marker
            │       └── owner.txt
            └── findings/
```

**Underscore-prefix convention**: `_shared_state.py` / `_state_read.py` / `_logging.py` / `_validation.py` / `_backends/` are internal; only `atomic_close.py` / `poll.py` / `run_e2e.sh` are operator-allowlisted. Allowlist surface stays at 3.

**Mission-root mutable state**:
- `mission/.scripts-journal/<request-id>.json` — atomic_close partial-close journal entries.
- `/tmp/megalodon-scripts.log` — RotatingFileHandler (1MB / 2 backups), WARNING+ default, DEBUG with `--debug`.

Both are gitignored. `.scripts-journal/` follows the `.phase-flip-locks/` / `.mission-events` precedent of dot-prefixed mission-root mutable state.

## 5. Components

### 5.1 `atomic_close.py` — CLI surface

```
python3 scripts/atomic_close.py \
    --task <TASK-ID>           # CR-4 regex enforced (see §6.1)
    --lane <LANE>              # AUDIT|ARCHITECT|BACKEND|FRONTEND|TEST|META
    --agent <AGENT-ID>         # ^agent-[0-9a-f]{4}$
    --finding <PATH>           # relative to mission-dir
    --severity <SEV>           # see §6.2 for enum
    --notes <TEXT>             # ≤2000 chars, no shell-meta except _,.-/()
    --summary <TEXT>           # ≤200 chars; falls back to first line of --notes
    [--mission-dir <PATH>]     # defaults to $PWD if STATUS.md present, else exit 4
    [--dry-run]                # print planned mutations to stdout, write nothing
    [--resume <REQUEST-ID>]    # resume a partial-close from journal
    [--debug]                  # DEBUG-level file logging
```

**Output** — single-line JSON to stdout, both on success and failure:

```json
{"ok": true, "request_id": "20260516T222400Z-agent-abcd-rule10-CLOSE-a1b2",
 "steps": ["CLAIM_DIR_DONE","TASKS_BRACKET","HISTORY_APPEND","STATUS_UPDATE"],
 "utc": "2026-05-16T22:24:00Z"}
```

```json
{"ok": false, "request_id": "...", "completed": ["CLAIM_DIR_DONE","TASKS_BRACKET"],
 "failed_step": "HISTORY_APPEND", "reason": "fcntl timeout after 5s",
 "resume_hint": "python3 scripts/atomic_close.py --resume <request-id>"}
```

**Exit codes**:

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Unexpected exception (bug in script; check `/tmp/megalodon-scripts.log`). Not deliberately reachable in tests. |
| 2 | Arg validation failure (CR-4 regex, missing flag, bad enum value) |
| 3 | Partial close — journal written, `--resume` available |
| 4 | Precondition failure (task already done, claim dir missing, STATUS row not found) |
| 5 | Lock timeout (one of the per-file fcntl acquires exceeded 5s) |

### 5.2 `poll.py` — CLI surface

```
python3 scripts/poll.py
    [--brief | --full]         # default: full minus findings_recent body
    [--mission-dir <PATH>]
    [--events-tail <N>]        # default 10
    [--findings-recent <N>]    # default 10
    [--debug]
```

**Output schema** (locked in brainstorming Q2):

```json
{
  "utc": "2026-05-16T22:24:00Z",
  "mission_dir": "/Users/dave/Documents/Projects/megalodon",
  "phase": "PHASE-RUN",
  "phase_lock_owner": "agent-9bba",
  "lanes": [
    {
      "lane": "AUDIT", "lane_short": "A", "agent": "agent-dcbc",
      "state": "idle", "last_utc": "2026-05-16T22:08:00Z",
      "stale_seconds": 960,
      "notes": "tick-65 LANE-A-CAPSTONE SHIPPED..."
    }
  ],
  "claims": {
    "open": [
      {"task_id": "P5-RUN-MUTATIONS-E2E", "owner": "agent-43d9",
       "created_utc": "2026-05-16T...", "has_done_marker": false}
    ],
    "done": [
      {"task_id": "P1-A", "owner": "agent-dcbc",
       "done_marker_mtime_utc": "2026-05-16T17:39:00Z"}
    ]
  },
  "events_tail": [
    "2026-05-16T22:10:00Z PHASE-DRAINING->PHASE-COMPLETE by orchestrator -- ..."
  ],
  "findings_recent": [
    {
      "path": "findings/agent-9bba-F-RUN2-CAPSTONE-2026-05-16T22-05Z.md",
      "mtime_utc": "2026-05-16T22:05:00Z",
      "lane": "F", "task_id": "RUN2-CAPSTONE", "severity": "DELTA",
      "body": null
    }
  ],
  "partial_journals": [
    {
      "request_id": "20260516T222400Z-agent-43d9-rule10-CLOSE-a1b2",
      "started_utc": "2026-05-16T22:24:00Z",
      "last_updated_utc": "2026-05-16T22:24:03Z",
      "task_id": "P5-RUN-MUTATIONS-E2E",
      "lane": "TEST", "agent": "agent-43d9",
      "completed_steps": ["CLAIM_DIR_DONE", "TASKS_BRACKET"],
      "failed_step": "HISTORY_APPEND",
      "error": "fcntl timeout after 5s on HISTORY.md",
      "age_seconds": 47,
      "resume_hint": "python3 scripts/atomic_close.py --resume 20260516T222400Z-agent-43d9-rule10-CLOSE-a1b2"
    }
  ]
}
```

**`--brief`** drops `events_tail` + `findings_recent` + `partial_journals` (returns the structural snapshot only). **`--full`** populates `findings_recent[].body` with file contents AND includes `partial_journals` entries < 24h old.

**`partial_journals` semantics**: read from `mission/.scripts-journal/*.json` where `status == "PARTIAL"` AND `(now - last_updated_utc) < 24h`. Surfaces orphaned partial closes so the next worker on a lane sees them. Older PARTIAL journals are still on disk for forensic review but not surfaced (assumed abandoned / superseded). Auto-purge of journals > 7 days happens in `atomic_close.py` per §5.4.

**Exit codes**: `0` success; `2` arg validation; `4` mission-dir invalid (STATUS.md or TASKS.md missing); `1` unexpected.

### 5.3 `_shared_state.py` — interface (M3/M1 abstraction)

```python
# scripts/_shared_state.py
from typing import Literal, TypedDict
from pathlib import Path

Step = Literal["CLAIM_DIR_DONE", "TASKS_BRACKET", "HISTORY_APPEND", "STATUS_UPDATE"]
JournalStatus = Literal["PENDING", "PARTIAL", "COMPLETE", "RESUMED-COMPLETE"]

class StepResult(TypedDict):
    step: Step
    ok: bool
    target_file: str
    pre_hash: str           # sha256 of pre-state ("" for new file)
    post_hash: str          # sha256 of post-state
    duration_ms: int
    idempotent: bool        # True when step skipped because state already at target
    error: str | None

class CloseResult(TypedDict):
    request_id: str
    ok: bool
    completed: list[Step]
    failed_step: Step | None
    steps: list[StepResult]
    resume_hint: str | None

def execute_close(
    mission_dir: Path,
    *,
    request_id: str,
    task_id: str,
    lane: str,           # canonical long form e.g. "AUDIT"
    agent: str,
    utc: str,
    finding_path: str,
    severity: str,
    notes: str,
    summary: str,
) -> CloseResult: ...

def resume_close(mission_dir: Path, request_id: str) -> CloseResult: ...
```

**M3 routing** — top of `_shared_state.py`:

```python
from ._backends import direct_fcntl as _backend  # M3
# M1 cutover: change to `from ._backends import queue_delegate as _backend`
```

**M1 cutover impact**: one-line import swap in `_shared_state.py`. `atomic_close.py` source unchanged. `scripts/tests/` source unchanged (tests target the shim, not the backend). Worker-observable behavior (CLI, exit codes, JSON output) unchanged.

### 5.4 Journal format

Path: `mission/.scripts-journal/<request-id>.json`

```json
{
  "schema_version": 1,
  "request_id": "20260516T222400Z-agent-43d9-rule10-CLOSE-a1b2",
  "started_utc": "2026-05-16T22:24:00Z",
  "last_updated_utc": "2026-05-16T22:24:03Z",
  "status": "PARTIAL",
  "task_id": "P5-RUN-MUTATIONS-E2E",
  "lane": "TEST",
  "agent": "agent-43d9",
  "args": {
    "finding": "findings/agent-43d9-E-P5-...-2026-05-16T21-43Z.md",
    "severity": "BLOCKED-DEGRADED",
    "notes": "7 PASS / 9 FAIL post-REPAIR-11 retroactive verify; ...",
    "summary": "P5-RUN-MUTATIONS-E2E 7/9 BLOCKED-DEGRADED"
  },
  "steps": [
    {"step": "CLAIM_DIR_DONE", "ok": true, "completed_utc": "2026-05-16T22:24:01Z",
     "pre_hash": "abc...", "post_hash": "def...", "duration_ms": 12, "idempotent": false},
    {"step": "TASKS_BRACKET", "ok": true, "completed_utc": "2026-05-16T22:24:02Z",
     "pre_hash": "...", "post_hash": "...", "duration_ms": 47, "idempotent": false},
    {"step": "HISTORY_APPEND", "ok": false, "attempted_utc": "2026-05-16T22:24:03Z",
     "error": "fcntl timeout after 5s on HISTORY.md", "duration_ms": 5012}
  ]
}
```

**Resume protocol**:

1. `atomic_close.py --resume <request-id>` reads journal.
2. Validates `args.task_id`, `args.lane`, `args.agent` against current STATUS row (rejects with exit 4 if a different worker has taken over the row).
3. Continues from first step where `ok=False`. Each step idempotency-checks (see §6.3) and re-runs.
4. On success: journal `status` → `RESUMED-COMPLETE`.

**Journal cleanup**: every successful `atomic_close.py` run (not just resume) opportunistically deletes journal files older than 7 days for the same `agent`. No separate cleanup script.

## 6. Per-step mutation details

### 6.1 Validation regexes (`_validation.py`)

Per Codex CR-4 (broadened from self-contrarian OW-5 fix):

| Flag | Regex |
|---|---|
| `--task` | `^(P\d+(\.\d+)?(-[A-F](-to-[A-F])?)?\|P\d+-RUN-[A-Z0-9_-]+\|REPAIR-[A-Z0-9_-]+\|OPERATOR-[A-Z_-]+\|S-\d+)$` |
| `--lane` | `^(AUDIT\|ARCHITECT\|BACKEND\|FRONTEND\|TEST\|META)$` |
| `--agent` | `^agent-[0-9a-f]{4}$` |
| `--notes` | length ≤ 2000; rejects shell metacharacters `` ` ``, `$`, `;`, `\|`, `>`, `<` anywhere in value (better error messages); regex `^[\w\s.,:/()\-_\[\]'"=@#+*?!&]*$` provides defense-in-depth catchall |
| `--summary` | length ≤ 200; same charset as `--notes` |
| `--severity` | `^(DELTA\|NIT\|MAJOR\|BLOCKING\|TIER-1\|TIER-2\|MEDIUM\|MINOR\|TERMINAL\|RECOVERY\|EXEC-PASS\|BLOCKED-DEGRADED)$` |
| `--finding` | path must exist under mission-dir; resolved + checked at runtime, not regex |

**Lane canonicalization map** (used by `_shared_state.py` and `_state_read.py`):

```python
LANE_LONG_TO_SHORT = {
    "AUDIT":     "A",
    "ARCHITECT": "B",
    "BACKEND":   "C",
    "FRONTEND":  "D",
    "TEST":      "E",
    "META":      "F",
}
```

### 6.2 Step 1 — `CLAIM_DIR_DONE`

Target: `mission/claims/<task-id>/done` + `mission/claims/<task-id>/owner.txt`.

- Precondition: `claims/<task-id>/` directory exists. Else `StepResult(ok=False, error="claim dir missing")`.
- Idempotent: if `done` exists AND `owner.txt` exists AND `owner.txt` matches `agent`, skip with `idempotent=True`.
- Otherwise: `done_marker.touch()` + `owner_file.write_text(f"{agent}\n")`.
- Pre/post hash: sha256 of sorted directory listing + each file's content (deterministic across OS).
- No fcntl on directory; `touch` and atomic `write_text` are sufficient.

### 6.3 Step 2 — `TASKS_BRACKET`

Target: `mission/TASKS.md`. Replaces the `[ ]` or `[claimed: ...]` bracket with `[done: <agent> @ <utc>]`.

```python
TASK_LINE_RE = re.compile(
    r"^(?P<prefix>- )"
    r"\[(?P<state>[^\]]+)\]"
    r" "
    r"\[LANE-(?P<lane_short>[A-F])\] "
    r"`(?P<task_id>[^`]+)`"
    r"(?P<rest>.*)$"
)
```

- Acquire fcntl LOCK_EX on TASKS.md, 5s timeout → on miss, StepResult(ok=False, error="lock timeout", code=5).
- Find first line where `task_id` matches.
- Idempotent: if `state.startswith("done:")` → skip.
- Otherwise: rewrite bracket, `_atomic_replace(path, new_text)`.
- Not found: `StepResult(ok=False, error="task X not found in TASKS.md")`.

### 6.4 Step 3 — `HISTORY_APPEND`

Target: `mission/HISTORY.md`. Pure append.

Format (matches existing rows, README:122):
```
<UTC> | <agent-id> | <LANE-short> | <task-id> | <finding-filename> | <severity> (<first-line-of-notes>)
```

- Acquire fcntl LOCK_EX on HISTORY.md, 5s timeout.
- Read current text. Idempotent check: scan last 50 lines for any row matching `agent` + `task_id` with UTC within ±60s of provided utc → skip.
- Append line (ensure newline before). Atomic replace.

### 6.5 Step 4 — `STATUS_UPDATE`

Target: `mission/STATUS.md`. Update the row matching `(lane, agent)`.

```python
STATUS_ROW_RE = re.compile(
    r"^\| (?P<lane>AUDIT|ARCHITECT|BACKEND|FRONTEND|TEST|META)\s*"
    r"\| (?P<agent>[^|]+?)\s*"
    r"\| (?P<state>[^|]+?)\s*"
    r"\| (?P<last_utc>[^|]+?)\s*"
    r"\| (?P<notes>.*?)\s*\|$"
)
```

- Acquire fcntl LOCK_EX on STATUS.md, 5s timeout.
- Find row where `lane` (stripped) matches canonical lane AND `agent` (stripped) matches passed agent.
- **Agent identity check**: if no row matches (different agent owns the lane row), `StepResult(ok=False, error="STATUS row owner mismatch", code=4)`. Prevents clobbering another worker's heartbeat.
- Idempotent: if `state.strip() == "idle"` AND `f"{task_id} done"` in notes → skip.
- Otherwise rewrite row with: `state = "idle"`, `last_utc = utc`, `notes = f"{task_id} done — {summary}"`.
- Lane column padded to 9 chars; other columns flow naturally.

## 7. `_state_read.py` design

Read-only counterpart to `_shared_state.py`. Pure functions, no side effects, no fcntl.

```python
def read_phase(mission_dir: Path) -> tuple[str, str | None]:
    """Return (current_phase, lock_owner_or_None). Reads .phase-flip-locks/ + .mission-events tail."""

def read_lanes(mission_dir: Path) -> list[LaneRow]:
    """Parse STATUS.md into structured rows. stale_seconds = (now - last_utc).total_seconds()."""

def read_claims(mission_dir: Path) -> dict[str, list[ClaimEntry]]:
    """Walk claims/, return {open: [...], done: [...]}."""

def read_events_tail(mission_dir: Path, n: int) -> list[str]:
    """Last n non-empty lines of .mission-events."""

def read_findings_recent(mission_dir: Path, n: int, include_body: bool) -> list[FindingEntry]:
    """N most recently mtime'd files in findings/. Parses YAML frontmatter for lane/task_id/severity."""

def read_partial_journals(mission_dir: Path, max_age_seconds: int = 86400) -> list[PartialJournalEntry]:
    """Read mission/.scripts-journal/*.json where status='PARTIAL' AND age < max_age_seconds.
    Returns sorted by last_updated_utc descending. Used by poll.py --full to surface
    orphaned partial closes to the next worker on a lane."""
```

`poll.py` is a thin orchestrator that calls these and assembles the locked JSON shape.

## 8. `run_e2e.sh` design

```bash
#!/usr/bin/env bash
# scripts/run_e2e.sh — canonical playwright invocation
# Forwards all args to `playwright test`.

set -euo pipefail

SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$( cd -- "$SCRIPT_DIR/.." &> /dev/null && pwd )"

exec uv run --directory "$PROJECT_ROOT" \
    --with fastapi --with "uvicorn[standard]" --with sse-starlette --with pyyaml \
    npx playwright test \
    --config ui/tests/e2e/playwright.config.ts \
    "$@"
```

- No `cd`. `uv run --directory` does the cwd work.
- `exec` so playwright is the same PID — signals propagate, no orphan shell.
- All worker-supplied args forwarded (`--grep`, `--project`, `--update-snapshots`, etc.).
- `set -euo pipefail` for fast-fail on misuse.
- Env vars (`MEGALODON_MISSION_DIR`, `MEGALODON_MISSION_DIR_FAILURE_MODES`, `CI`) inherit from parent.

## 9. Logging (`_logging.py`)

Per CLAUDE.md ("File logging from day one: RotatingFileHandler to `/tmp/<project>.log`, 1 MB/2 backups. WARNING+ always, DEBUG with `--debug`."):

```python
# scripts/_logging.py
import logging
from logging.handlers import RotatingFileHandler

LOG_PATH = "/tmp/megalodon-scripts.log"
MAX_BYTES = 1_048_576
BACKUP_COUNT = 2

def get_logger(name: str, debug: bool = False) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG if debug else logging.WARNING)
    handler = RotatingFileHandler(LOG_PATH, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT)
    handler.setFormatter(logging.Formatter(
        "%(asctime)sZ | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    logger.addHandler(handler)
    return logger
```

**Log contents** — what each script emits at WARNING+ level:

- `atomic_close.py`: arg validation failures, lock timeouts, journal-write failures, resume validation rejections.
- `poll.py`: STATUS.md/TASKS.md parse failures (regex didn't match a row).
- `_shared_state.py` / `_backends/direct_fcntl.py`: fcntl acquire timing on slow holds (DEBUG only), step skip-because-idempotent events (DEBUG only).

**Log contents at DEBUG**: every step's pre/post hash, lock-acquire timing, idempotency decisions, journal status transitions.

## 10. Test strategy

### 10.1 Layout

```
scripts/tests/
├── conftest.py
├── test_atomic_close.py     # CLI-level tests via subprocess
├── test_poll.py             # CLI-level tests via subprocess
├── test_shared_state.py     # execute_close() / resume_close() directly
├── test_state_read.py       # read_lanes / read_claims / etc.
├── test_validation.py       # CR-4 regexes (positive + negative)
└── fixtures/
    └── minimal_mission/     # template — copytree per test
```

### 10.2 `conftest.py` fixtures

```python
import shutil
from pathlib import Path
import pytest

FIXTURE_SRC = Path(__file__).parent / "fixtures" / "minimal_mission"

@pytest.fixture
def mission_dir(tmp_path: Path) -> Path:
    dest = tmp_path / "mission"
    shutil.copytree(FIXTURE_SRC, dest)
    return dest

@pytest.fixture
def agent() -> str:
    return "agent-abcd"
```

### 10.3 Test coverage

| File | What it covers | Approx test count |
|---|---|---|
| `test_validation.py` | CR-4 regexes — positive matches for every example from CR-4 finding (P1-A, P2.5-B, P2-A-to-F, P5-RUN-MUTATIONS-E2E, REPAIR-MUTATIONS-E2E-3-ACTION-PANEL, OPERATOR-ACCEPTANCE-REQUEST, S-8); negative matches for shell-meta, length-overflow, wrong-format. | 12 |
| `test_shared_state.py` | `execute_close()` happy path (4 steps, returns ok=True, all 4 files mutated correctly). Idempotency (run twice → second run reports all `idempotent=True`). Partial failure (mock `STATUS_UPDATE` to raise → journal status PARTIAL, exit-code-3-equivalent return). Resume (after partial → `resume_close()` completes). Owner mismatch (different agent in STATUS row → step fails). | 8 |
| `test_atomic_close.py` | CLI subprocess invocation against `mission_dir`. Happy path + JSON output shape. `--dry-run` writes nothing. `--resume` after simulated partial. Each exit code (0, 2, 3, 4, 5) reachable via a constructed scenario. | 8 |
| `test_poll.py` | CLI subprocess invocation. `--brief` shape (no events_tail, no findings_recent, no partial_journals). `--full` shape (findings body populated, partial_journals < 24h surfaced). `stale_seconds` computed correctly relative to fixture's STATUS.md `last_utc` values (use `freezegun.freeze_time` in tests to pin "now" deterministically). Partial-journal age-filter test (24h boundary). | 7 |
| `test_state_read.py` | `read_lanes()` returns 6 rows for fixture. `read_phase()` distinguishes locked vs unlocked phase-flip. `read_claims()` separates open vs done. `read_findings_recent(n=3)` returns 3 most recent. `read_partial_journals()` returns only PARTIAL entries within max_age_seconds. | 6 |

**Total**: ~41 tests. All run via `uv run pytest scripts/tests/` from project root.

### 10.4 Minimal fixture content

`scripts/tests/fixtures/minimal_mission/STATUS.md`:

```
# Status board

| Lane | Agent | State | Last UTC | Notes |
|---|---|---|---|---|
| AUDIT     | agent-abcd | working: TEST-1 | 2026-05-16T22:00:00Z | testing |
| ARCHITECT | unclaimed  | initialized     | 2026-05-16T22:00:00Z | -       |
| BACKEND   | unclaimed  | initialized     | 2026-05-16T22:00:00Z | -       |
| FRONTEND  | unclaimed  | initialized     | 2026-05-16T22:00:00Z | -       |
| TEST      | unclaimed  | initialized     | 2026-05-16T22:00:00Z | -       |
| META      | unclaimed  | initialized     | 2026-05-16T22:00:00Z | -       |
```

`scripts/tests/fixtures/minimal_mission/TASKS.md`:

```
# Tasks — test fixture

- [ ] [LANE-A] `TEST-1` — sample task for atomic_close tests
```

`scripts/tests/fixtures/minimal_mission/HISTORY.md`:

```
# History — test fixture

Format: `<UTC> | <agent-id> | <LANE> | <task-id> | <finding-filename> | <severity>`

---
```

`scripts/tests/fixtures/minimal_mission/.mission-events`:

```
2026-05-16T22:00:00Z INIT->PHASE-PLAN by test-harness -- minimal fixture init
```

`scripts/tests/fixtures/minimal_mission/claims/TEST-1/owner.txt`:

```
agent-abcd
```

(No `done` marker — that's what atomic_close adds.)

## 11. Adjacent items (per scope §2)

### 11.1 launch.md §5 grammar update

Add the following normative rules to `launch.md` §5 (Tool discipline):

```
### RULE 12 — Helper-script-first for RULE-10 close

For RULE-10 atomic completion, workers MUST use `scripts/atomic_close.py`.
NEVER use Python heredocs (`python3 <<'PYEOF' ... PYEOF`) for the four RULE-10 steps.
NEVER use compound bash (`cmd1 && cmd2 && for ...; do ...; done`) for the four steps.

### RULE 13 — Helper-script-first for state polling

For multi-source state polling, workers MUST use `scripts/poll.py`.
NEVER chain compound polls like `cat .mission-events | tail && ls claims/ && grep STATUS.md`
in a single Bash tool call — this triggers permission prompts when the operator is AFK
(SIG-ORCH-6 @2026-05-16T21:21Z root cause).

Parallel single-purpose tool calls (multiple Read/Bash calls in one assistant message)
remain acceptable and preferred over compound bash.

### RULE 14 — E2E invocation via run_e2e.sh

For Playwright E2E runs, workers MUST use `./scripts/run_e2e.sh [args]`.
NEVER use `cd /abs/path && uv run npx playwright test ...` compound (same prompt-block risk).

### Python+fcntl reservation (refinement)

Python heredocs with fcntl remain acceptable ONLY for cross-lane CAS writes where
parallel writers race the same row — primarily STATUS heartbeats during contended
phase-flip windows and `.mission-events` appends during flip-win races.
Lane-prefixed REPAIRs have zero race risk → Edit tool suffices; no heredoc needed.
```

These three rules replace ad-hoc tool-discipline language and codify the M3 deliverable. Add to README.md §RULE 0-11 section (after RULE 11) and reference from launch.md §5.

### 11.2 playwright.config.ts cleanup

`ui/tests/e2e/playwright.config.ts` lines 45-58. Replace both `webServer[].command` strings:

Before:
```javascript
command: 'cd /Users/dave/Documents/Projects/megalodon && uv run --with fastapi ... python3 -m megalodon_ui --port 8765 ...',
```

After:
```javascript
command: 'uv run --directory /Users/dave/Documents/Projects/megalodon --with fastapi --with "uvicorn[standard]" --with sse-starlette --with pyyaml python3 -m megalodon_ui --port 8765 --mission-dir $MEGALODON_MISSION_DIR_DEFAULT',
```

Same change for the port-8766 entry. No `cd`. No `&&`. Codex CR-5 ACKNOWLEDGE noted this doesn't reproduce run-2's silence (Playwright subprocess.spawn is not subject to Claude Code worker permission gating), but it's hygienic and aligns with the M3 grammar.

### 11.3 Operator allowlist documentation

Add to `README.md` (new section after "How to deploy") titled "Operator allowlist for v9 helper scripts":

```
## Operator allowlist for v9 helper scripts

Workers invoke three scripts that must be wildcard-allowlisted once to prevent
mid-mission permission prompts (SIG-ORCH-6 cause). Add to your Claude Code
permissions (settings.json `allow` list or equivalent):

  python3 scripts/atomic_close.py *
  python3 scripts/poll.py *
  ./scripts/run_e2e.sh *

The scripts internally validate ALL args against strict whitelist regexes
(see docs/superpowers/specs/2026-05-16-v9-m3-helper-scripts-design.md §6.1).
Any non-conforming arg is rejected with exit code 2 and a stderr explanation.
The wildcard is safe because the scripts — not the allowlist — enforce
input safety.
```

Operator applies the allowlist additions manually (this spec does not modify `~/.claude/settings.json` or any per-project settings file).

## 12. File manifest

### Created

- `scripts/atomic_close.py`
- `scripts/poll.py`
- `scripts/run_e2e.sh` (mode 0755)
- `scripts/_shared_state.py`
- `scripts/_state_read.py`
- `scripts/_logging.py`
- `scripts/_validation.py`
- `scripts/_backends/__init__.py`
- `scripts/_backends/direct_fcntl.py`
- `scripts/tests/__init__.py`
- `scripts/tests/conftest.py`
- `scripts/tests/test_atomic_close.py`
- `scripts/tests/test_poll.py`
- `scripts/tests/test_shared_state.py`
- `scripts/tests/test_state_read.py`
- `scripts/tests/test_validation.py`
- `scripts/tests/fixtures/minimal_mission/STATUS.md`
- `scripts/tests/fixtures/minimal_mission/TASKS.md`
- `scripts/tests/fixtures/minimal_mission/HISTORY.md`
- `scripts/tests/fixtures/minimal_mission/.mission-events`
- `scripts/tests/fixtures/minimal_mission/claims/TEST-1/owner.txt`

### Modified

- `launch.md` — add RULES 12, 13, 14 + Python+fcntl reservation refinement (§11.1).
- `README.md` — add "Operator allowlist for v9 helper scripts" section (§11.3); add RULES 12-14 to TIER 1 rules block (after RULE 11).
- `ui/tests/e2e/playwright.config.ts` — replace `cd ... && uv run ...` with `uv run --directory ...` in two `webServer[].command` entries (§11.2).
- `.gitignore` — add `.scripts-journal/` (mission-root mutable state) and `scripts/tests/__pycache__/`.
- `HISTORY.md` — append M3-COMPLETE entry on implementation finish.

### Not modified

- `docs/v9/queue/applier.py` / `queue_client.py` — M3 does not touch the queue.
- `megalodon_ui/server.py` — M1.5 territory.
- `ui/server.py` — M1.6 territory.

## 13. Risks and open questions

### 13.1 fcntl on macOS APFS

`fcntl.LOCK_EX` semantics on macOS APFS are correct for advisory locking within a single host. M3 ships single-host (per V9 out-of-scope §357: "v9 assumes single-host"). No cross-host considerations.

### 13.2 Claim dir race with concurrent worker

Two workers running `atomic_close.py --task TEST-1 --agent agent-X` and `--agent agent-Y` simultaneously: both `touch claims/TEST-1/done`. Last-writer-wins on `owner.txt`. STATUS_UPDATE then rejects whichever lost the STATUS row owner check (exit 4). Acceptable: one worker exits cleanly with completion, the other exits with "STATUS row owner mismatch" and journal entry that operator can review.

### 13.3 HISTORY.md size unbounded

Run-2's HISTORY.md is ~13KB after 4h40m. No M3 mitigation; expected to grow ~50KB/run. Rotation deferred to v10 if it becomes a problem.

### 13.4 Resume protocol staleness — RESOLVED

If a worker writes a `PARTIAL` journal then dies, and the next worker on the same lane has no idea to `--resume` it, the partial state persists. **Mitigation (v1, per operator decision 2026-05-16T22:35Z)**: `poll.py --full` includes a top-level `partial_journals: [...]` field surfacing PARTIAL entries < 24h old. See §5.2 schema and §7 `read_partial_journals()`. Test coverage at §10.3 `test_poll.py` + `test_state_read.py`. Next worker on the lane sees the orphaned partial close, can decide to `--resume` it (if it makes sense for the same task) or operator-escalate.

### 13.5 launch.md grammar update timing

The grammar update (§11.1) only takes effect for runs after M3 ships. Run-3 will pick it up. No backward compatibility concern — run-2 is closed.

## 14. Implementation order (for the upcoming plan)

1. `_validation.py` + `test_validation.py` — pure functions, fastest to land.
2. `_logging.py` — used by everything else.
3. `_backends/direct_fcntl.py` + `_shared_state.py` + `test_shared_state.py` — the M3 core.
4. `_state_read.py` + `test_state_read.py` — independent of write path.
5. `atomic_close.py` + `test_atomic_close.py` — CLI wrapper.
6. `poll.py` + `test_poll.py` — CLI wrapper.
7. `run_e2e.sh` (no tests; smoke-validated by running once and seeing playwright start).
8. Fixture content under `scripts/tests/fixtures/minimal_mission/`.
9. `playwright.config.ts` cleanup (§11.2).
10. `launch.md` + `README.md` doc updates (§11.1, §11.3).
11. `.gitignore` updates.
12. `HISTORY.md` M3-COMPLETE append.

Each step can land independently; tests pass after their step. Step 7 (`run_e2e.sh`) and steps 9-12 are pure doc/config and can ship in any order at the end.

## 15. Definition of done

- All 41 pytest tests pass: `uv run pytest scripts/tests/ -v` returns 0.
- `python3 scripts/atomic_close.py --help` produces sensible help.
- `python3 scripts/poll.py` against the live mission dir produces valid JSON (`jq .` parses without error).
- `./scripts/run_e2e.sh --help` starts playwright and shows help (subprocess).
- `playwright.config.ts` updated; e2e suite still runs (smoke).
- `launch.md` + `README.md` reflect RULES 12-14 + allowlist section.
- HISTORY.md M3-COMPLETE entry appended with full file manifest.
- Operator confirms allowlist additions applied.

## 16. References

- `docs/v9/V9-ROADMAP.md` §M3 (lines 114-138), §M1.5 (CR-1), §M1.6 (CR-2), Codex CR-4 (M3 regex), Codex CR-5 (playwright cleanup), Codex CR-6 (owner.txt)
- `docs/v9/queue/applier.py` lines 1-15 (journal-recovery model that M3 mirrors)
- `docs/v9/queue/queue_client.py` lines 84-195 (interface M1 will swap to)
- `README.md` §RULE 10 (lines 118-123) — the 4-step contract M3 automates
- `findings/orchestrator-OPERATOR-DEGRADED-ACK-2026-05-16T21-50Z.md` — run-2 close-out + 9 residuals deferred to run-3 under v9
- `~/Documents/Projects/.plans/megalodon/v9-roadmap-2026-05-16-synthesis.md` — Codex contrarian review synthesis (6 ACCEPT + 1 ACKNOWLEDGE)
- `~/.claude/CLAUDE.md` — file logging convention, test discipline, "no AI slop"

— End of spec —
