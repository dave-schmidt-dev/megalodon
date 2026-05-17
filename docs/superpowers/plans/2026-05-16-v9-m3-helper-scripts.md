# Megalodon v9 M3 — Helper Scripts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Spec reference:** `docs/superpowers/specs/2026-05-16-v9-m3-helper-scripts-design.md` — operator-approved with `partial_journals` addition.
>
> **Commit policy:** Per operator's global CLAUDE.md, commits happen ONLY when the operator explicitly requests them. Every task includes a `git add` staging step; the `git commit` step is shown for reference but should be SKIPPED unless the operator says otherwise during execution. The operator may also instruct "batch commit after every N tasks" or "commit only at the end."

**Goal:** Ship three operator-allowlisted scripts (`scripts/atomic_close.py`, `scripts/poll.py`, `scripts/run_e2e.sh`) plus their internal modules and ~41 pytest tests, so v9 workers stop triggering Claude Code permission prompts on routine RULE-10 closes and state polls.

**Architecture:** CLI scripts are thin wrappers over internal modules. `_shared_state.py` is an M3/M1 abstraction boundary — its M3 backend writes via direct `fcntl.LOCK_EX`; M1 will swap to `queue_client` with a one-line import change. `_state_read.py` does read-only mission aggregation for `poll.py`. Validation regexes from Codex CR-4 in `_validation.py`. All file mutations follow the journal-recovery pattern from `docs/v9/queue/applier.py` so M3→M1 cutover is internal-only.

**Tech Stack:** Python 3.11+ (run via `uv run`), bash, `fcntl.LOCK_EX`, pytest, `freezegun` (test-only). No new runtime dependencies. Project root: `/Users/dave/Documents/Projects/megalodon`.

---

## File Structure

**Created (21 files):**

| Path | Responsibility |
|---|---|
| `scripts/atomic_close.py` | CLI entry; argparse, validation orchestration, JSON output, exit codes |
| `scripts/poll.py` | CLI entry; argparse, calls `_state_read` functions, assembles JSON |
| `scripts/run_e2e.sh` | Bash wrapper around `uv run --directory ... npx playwright test` |
| `scripts/_shared_state.py` | `execute_close()` / `resume_close()` orchestrators; selects backend |
| `scripts/_state_read.py` | `read_phase`, `read_lanes`, `read_claims`, `read_events_tail`, `read_findings_recent`, `read_partial_journals` |
| `scripts/_logging.py` | `get_logger()` factory — RotatingFileHandler to `/tmp/megalodon-scripts.log` |
| `scripts/_validation.py` | CR-4 regexes + `LANE_LONG_TO_SHORT` map + arg validators |
| `scripts/_backends/__init__.py` | Empty (package marker) |
| `scripts/_backends/direct_fcntl.py` | `claim_dir_done`, `tasks_bracket`, `history_append`, `status_update` |
| `scripts/tests/__init__.py` | Empty (package marker) |
| `scripts/tests/conftest.py` | `mission_dir` and `agent` pytest fixtures |
| `scripts/tests/test_validation.py` | ~12 tests for regexes and lane map |
| `scripts/tests/test_shared_state.py` | ~8 tests for execute_close + resume_close |
| `scripts/tests/test_atomic_close.py` | ~8 tests for the CLI surface |
| `scripts/tests/test_poll.py` | ~7 tests for the CLI surface |
| `scripts/tests/test_state_read.py` | ~6 tests for read_* functions |
| `scripts/tests/fixtures/minimal_mission/STATUS.md` | 6-row test status board |
| `scripts/tests/fixtures/minimal_mission/TASKS.md` | 1 open task `TEST-1` |
| `scripts/tests/fixtures/minimal_mission/HISTORY.md` | header only |
| `scripts/tests/fixtures/minimal_mission/.mission-events` | one INIT line |
| `scripts/tests/fixtures/minimal_mission/claims/TEST-1/owner.txt` | `agent-abcd` |

**Modified (5 files):**

| Path | Change |
|---|---|
| `launch.md` | Add RULES 12, 13, 14 + Python+fcntl reservation refinement |
| `README.md` | Add "Operator allowlist for v9 helper scripts" section; add RULES 12-14 reference |
| `ui/tests/e2e/playwright.config.ts` | Replace `cd ... && uv run` with `uv run --directory` in both webServer commands |
| `.gitignore` | Add `.scripts-journal/` and `scripts/tests/__pycache__/` and `scripts/**/__pycache__/` |
| `HISTORY.md` | Append M3-COMPLETE entry on finish |

---

## Task 1: Fixture + conftest

**Files:**
- Create: `scripts/tests/__init__.py`
- Create: `scripts/tests/conftest.py`
- Create: `scripts/tests/fixtures/minimal_mission/STATUS.md`
- Create: `scripts/tests/fixtures/minimal_mission/TASKS.md`
- Create: `scripts/tests/fixtures/minimal_mission/HISTORY.md`
- Create: `scripts/tests/fixtures/minimal_mission/.mission-events`
- Create: `scripts/tests/fixtures/minimal_mission/claims/TEST-1/owner.txt`
- Create: `scripts/tests/fixtures/minimal_mission/findings/.gitkeep` (so empty dir survives copytree)

- [ ] **Step 1: Create empty package markers**

```bash
mkdir -p /Users/dave/Documents/Projects/megalodon/scripts/tests/fixtures/minimal_mission/claims/TEST-1
mkdir -p /Users/dave/Documents/Projects/megalodon/scripts/tests/fixtures/minimal_mission/findings
touch /Users/dave/Documents/Projects/megalodon/scripts/tests/__init__.py
touch /Users/dave/Documents/Projects/megalodon/scripts/tests/fixtures/minimal_mission/findings/.gitkeep
```

- [ ] **Step 2: Write fixture STATUS.md**

Path: `scripts/tests/fixtures/minimal_mission/STATUS.md`

```markdown
# Status board

| Lane | Agent | State | Last UTC | Notes |
|---|---|---|---|---|
| AUDIT     | agent-abcd | working: TEST-1 | 2026-05-16T22:00:00Z | testing |
| ARCHITECT | unclaimed  | initialized     | 2026-05-16T22:00:00Z | - |
| BACKEND   | unclaimed  | initialized     | 2026-05-16T22:00:00Z | - |
| FRONTEND  | unclaimed  | initialized     | 2026-05-16T22:00:00Z | - |
| TEST      | unclaimed  | initialized     | 2026-05-16T22:00:00Z | - |
| META      | unclaimed  | initialized     | 2026-05-16T22:00:00Z | - |
```

- [ ] **Step 3: Write fixture TASKS.md**

Path: `scripts/tests/fixtures/minimal_mission/TASKS.md`

```markdown
# Tasks — test fixture

- [ ] [LANE-A] `TEST-1` — sample task for atomic_close tests
```

- [ ] **Step 4: Write fixture HISTORY.md**

Path: `scripts/tests/fixtures/minimal_mission/HISTORY.md`

```markdown
# History — test fixture

Format: `<UTC> | <agent-id> | <LANE> | <task-id> | <finding-filename> | <severity>`

---
```

- [ ] **Step 5: Write fixture .mission-events**

Path: `scripts/tests/fixtures/minimal_mission/.mission-events`

```
2026-05-16T22:00:00Z INIT->PHASE-PLAN by test-harness -- minimal fixture init
```

- [ ] **Step 6: Write fixture claims/TEST-1/owner.txt**

Path: `scripts/tests/fixtures/minimal_mission/claims/TEST-1/owner.txt`

```
agent-abcd
```

- [ ] **Step 7: Write conftest.py**

Path: `scripts/tests/conftest.py`

```python
"""Shared pytest fixtures for scripts/tests/."""

import shutil
from pathlib import Path

import pytest

FIXTURE_SRC = Path(__file__).parent / "fixtures" / "minimal_mission"


@pytest.fixture
def mission_dir(tmp_path: Path) -> Path:
    """Per-test writable copy of the minimal_mission fixture."""
    dest = tmp_path / "mission"
    shutil.copytree(FIXTURE_SRC, dest)
    return dest


@pytest.fixture
def agent() -> str:
    return "agent-abcd"
```

- [ ] **Step 8: Smoke-test the fixture is discoverable**

```bash
cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest pytest scripts/tests/ --collect-only
```

Expected: `no tests ran` (zero tests collected, no errors). Confirms pytest finds the package, conftest imports cleanly, no fixture errors.

- [ ] **Step 9: Stage**

```bash
cd /Users/dave/Documents/Projects/megalodon && git add scripts/tests/__init__.py scripts/tests/conftest.py scripts/tests/fixtures/
```

Commit step (SKIP unless operator approves):

```bash
git commit -m "test(m3): add minimal_mission pytest fixture + conftest"
```

---

## Task 2: `_validation.py` — regexes + lane map + tests

**Files:**
- Create: `scripts/_validation.py`
- Create: `scripts/tests/test_validation.py`

- [ ] **Step 1: Write failing tests**

Path: `scripts/tests/test_validation.py`

```python
"""Tests for scripts/_validation.py — Codex CR-4 regex coverage."""

import pytest

from scripts._validation import (
    LANE_LONG_TO_SHORT,
    validate_agent,
    validate_lane,
    validate_notes,
    validate_severity,
    validate_summary,
    validate_task_id,
)


@pytest.mark.parametrize("task_id", [
    "P1-A", "P2.5-B", "P2-A-to-F", "P5-RUN-MUTATIONS-E2E",
    "REPAIR-MUTATIONS-E2E-3-ACTION-PANEL", "OPERATOR-ACCEPTANCE-REQUEST",
    "S-8",
])
def test_task_id_accepts_cr4_inventory(task_id):
    validate_task_id(task_id)  # raises if invalid


@pytest.mark.parametrize("bad", [
    "", "p1-a", "P1-Z", "P1-A; rm -rf /", "P1-A && echo",
    "../etc/passwd", "P1-A`whoami`",
])
def test_task_id_rejects_invalid(bad):
    with pytest.raises(ValueError):
        validate_task_id(bad)


@pytest.mark.parametrize("lane", ["AUDIT", "ARCHITECT", "BACKEND", "FRONTEND", "TEST", "META"])
def test_lane_accepts_valid(lane):
    validate_lane(lane)


@pytest.mark.parametrize("bad", ["audit", "A", "LANE-A", "OTHER", "", "AUDIT;"])
def test_lane_rejects_invalid(bad):
    with pytest.raises(ValueError):
        validate_lane(bad)


def test_lane_long_to_short_map_complete():
    assert LANE_LONG_TO_SHORT == {
        "AUDIT": "A", "ARCHITECT": "B", "BACKEND": "C",
        "FRONTEND": "D", "TEST": "E", "META": "F",
    }


@pytest.mark.parametrize("agent", ["agent-abcd", "agent-0123", "agent-dead", "agent-9bba"])
def test_agent_accepts_valid(agent):
    validate_agent(agent)


@pytest.mark.parametrize("bad", ["agent-ABCD", "agent-12345", "agent-abc", "agent_abcd", ""])
def test_agent_rejects_invalid(bad):
    with pytest.raises(ValueError):
        validate_agent(bad)


@pytest.mark.parametrize("sev", [
    "DELTA", "NIT", "MAJOR", "BLOCKING", "TIER-1", "TIER-2",
    "MEDIUM", "MINOR", "TERMINAL", "RECOVERY", "EXEC-PASS", "BLOCKED-DEGRADED",
])
def test_severity_accepts_valid(sev):
    validate_severity(sev)


def test_severity_rejects_invalid():
    with pytest.raises(ValueError):
        validate_severity("CRITICAL")


def test_notes_accepts_normal():
    validate_notes("Run-2 closed degraded. 7/16 e2e. Operator-acked.")


def test_notes_rejects_shell_meta():
    for bad in ["foo `whoami`", "foo $HOME", "foo; rm", "foo | grep", "foo > /tmp"]:
        with pytest.raises(ValueError):
            validate_notes(bad)


def test_notes_rejects_overlong():
    with pytest.raises(ValueError):
        validate_notes("x" * 2001)


def test_summary_rejects_overlong():
    with pytest.raises(ValueError):
        validate_summary("x" * 201)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest pytest scripts/tests/test_validation.py -v
```

Expected: ALL tests fail with `ModuleNotFoundError: No module named 'scripts._validation'`.

- [ ] **Step 3: Implement `_validation.py`**

Path: `scripts/_validation.py`

```python
"""Argument validation for v9 helper scripts (Codex CR-4 regex inventory)."""

import re

TASK_ID_RE = re.compile(
    r"^(P\d+(\.\d+)?(-[A-F](-to-[A-F])?)?"
    r"|P\d+-RUN-[A-Z0-9_-]+"
    r"|REPAIR-[A-Z0-9_-]+"
    r"|OPERATOR-[A-Z_-]+"
    r"|S-\d+)$"
)
LANE_RE = re.compile(r"^(AUDIT|ARCHITECT|BACKEND|FRONTEND|TEST|META)$")
AGENT_RE = re.compile(r"^agent-[0-9a-f]{4}$")
SEVERITY_RE = re.compile(
    r"^(DELTA|NIT|MAJOR|BLOCKING|TIER-1|TIER-2|MEDIUM|MINOR"
    r"|TERMINAL|RECOVERY|EXEC-PASS|BLOCKED-DEGRADED)$"
)
NOTES_CHARSET_RE = re.compile(r"^[\w\s.,:/()\-_\[\]'\"=@#+*?!&]*$")
# Charset excludes shell metacharacters: backtick, dollar, semicolon, pipe, > and <.
# The forbidden-list in _check_notes_like enforces these explicitly with better
# error messages; regex provides defense-in-depth catchall for anything else.

LANE_LONG_TO_SHORT = {
    "AUDIT":     "A",
    "ARCHITECT": "B",
    "BACKEND":   "C",
    "FRONTEND":  "D",
    "TEST":      "E",
    "META":      "F",
}

_NOTES_MAX = 2000
_SUMMARY_MAX = 200


def _check(regex: re.Pattern, value: str, name: str) -> None:
    if not isinstance(value, str) or not regex.match(value):
        raise ValueError(f"invalid {name}: {value!r}")


def validate_task_id(value: str) -> None:
    _check(TASK_ID_RE, value, "task_id")


def validate_lane(value: str) -> None:
    _check(LANE_RE, value, "lane")


def validate_agent(value: str) -> None:
    _check(AGENT_RE, value, "agent")


def validate_severity(value: str) -> None:
    _check(SEVERITY_RE, value, "severity")


def _check_notes_like(value: str, name: str, max_len: int) -> None:
    if not isinstance(value, str):
        raise ValueError(f"invalid {name}: not a string")
    if len(value) > max_len:
        raise ValueError(f"{name} too long: {len(value)} > {max_len}")
    forbidden = ("`", "$", "|", ";", ">", "<")
    for ch in forbidden:
        if ch in value:
            raise ValueError(f"{name} contains forbidden character {ch!r}: {value!r}")
    if not NOTES_CHARSET_RE.match(value):
        raise ValueError(f"{name} contains disallowed characters: {value!r}")


def validate_notes(value: str) -> None:
    _check_notes_like(value, "notes", _NOTES_MAX)


def validate_summary(value: str) -> None:
    _check_notes_like(value, "summary", _SUMMARY_MAX)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest pytest scripts/tests/test_validation.py -v
```

Expected: All 12+ parametrized cases PASS.

- [ ] **Step 5: Stage**

```bash
cd /Users/dave/Documents/Projects/megalodon && git add scripts/_validation.py scripts/tests/test_validation.py
```

Commit (SKIP unless operator approves):

```bash
git commit -m "feat(m3): add _validation.py with CR-4 regexes and lane map"
```

---

## Task 3: `_logging.py`

**Files:**
- Create: `scripts/_logging.py`
- Create: `scripts/tests/test_logging.py`

- [ ] **Step 1: Write failing test**

Path: `scripts/tests/test_logging.py`

```python
"""Smoke test for scripts/_logging.py."""

import logging
from pathlib import Path

from scripts._logging import LOG_PATH, get_logger


def test_get_logger_returns_logger():
    log = get_logger("test.smoke")
    assert isinstance(log, logging.Logger)


def test_default_level_is_warning():
    log = get_logger("test.level.warn")
    assert log.level == logging.WARNING


def test_debug_flag_lowers_level():
    log = get_logger("test.level.debug", debug=True)
    assert log.level == logging.DEBUG


def test_log_path_is_tmp_megalodon_scripts():
    assert LOG_PATH == "/tmp/megalodon-scripts.log"


def test_writing_a_warning_creates_log_file():
    log = get_logger("test.write")
    log.warning("hello from test")
    assert Path(LOG_PATH).exists()
```

- [ ] **Step 2: Run test to see it fail**

```bash
cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest pytest scripts/tests/test_logging.py -v
```

Expected: `ModuleNotFoundError: No module named 'scripts._logging'`.

- [ ] **Step 3: Implement `_logging.py`**

Path: `scripts/_logging.py`

```python
"""RotatingFileHandler factory for v9 helper scripts.

Per global CLAUDE.md: file logging from day one, RotatingFileHandler to
/tmp/<project>.log, 1 MB / 2 backups, WARNING+ default, DEBUG with --debug.
"""

import logging
from logging.handlers import RotatingFileHandler

LOG_PATH = "/tmp/megalodon-scripts.log"
MAX_BYTES = 1_048_576
BACKUP_COUNT = 2


def get_logger(name: str, debug: bool = False) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        # Already configured (e.g., re-import in same process)
        logger.setLevel(logging.DEBUG if debug else logging.WARNING)
        return logger
    logger.setLevel(logging.DEBUG if debug else logging.WARNING)
    handler = RotatingFileHandler(
        LOG_PATH, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)sZ | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    logger.addHandler(handler)
    logger.propagate = False
    return logger
```

- [ ] **Step 4: Run tests to verify**

```bash
cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest pytest scripts/tests/test_logging.py -v
```

Expected: 5/5 PASS.

- [ ] **Step 5: Stage**

```bash
cd /Users/dave/Documents/Projects/megalodon && git add scripts/_logging.py scripts/tests/test_logging.py
```

Commit (SKIP unless operator approves):

```bash
git commit -m "feat(m3): add _logging.py RotatingFileHandler factory"
```

---

## Task 4: `_backends/direct_fcntl.py` — `claim_dir_done`

**Files:**
- Create: `scripts/_backends/__init__.py`
- Create: `scripts/_backends/direct_fcntl.py`
- Create: `scripts/tests/test_shared_state.py` (will grow over Tasks 4-9)

- [ ] **Step 1: Create empty package marker**

```bash
touch /Users/dave/Documents/Projects/megalodon/scripts/_backends/__init__.py
```

- [ ] **Step 2: Write failing tests for claim_dir_done**

Path: `scripts/tests/test_shared_state.py`

```python
"""Tests for scripts/_shared_state.py + scripts/_backends/direct_fcntl.py."""

from pathlib import Path

import pytest

from scripts._backends import direct_fcntl as backend


def test_claim_dir_done_happy_path(mission_dir: Path, agent: str):
    result = backend.claim_dir_done(mission_dir, "TEST-1", agent, "2026-05-16T22:30:00Z")
    assert result["ok"] is True
    assert (mission_dir / "claims" / "TEST-1" / "done").exists()
    assert (mission_dir / "claims" / "TEST-1" / "owner.txt").read_text().strip() == agent


def test_claim_dir_done_idempotent_on_second_call(mission_dir: Path, agent: str):
    backend.claim_dir_done(mission_dir, "TEST-1", agent, "2026-05-16T22:30:00Z")
    second = backend.claim_dir_done(mission_dir, "TEST-1", agent, "2026-05-16T22:31:00Z")
    assert second["ok"] is True
    assert second["idempotent"] is True


def test_claim_dir_done_fails_when_claim_dir_missing(mission_dir: Path, agent: str):
    result = backend.claim_dir_done(mission_dir, "DOES-NOT-EXIST", agent, "2026-05-16T22:30:00Z")
    assert result["ok"] is False
    assert "claim dir missing" in result["error"]
```

- [ ] **Step 3: Run tests to see them fail**

```bash
cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest pytest scripts/tests/test_shared_state.py -v
```

Expected: `ImportError: cannot import name 'direct_fcntl'`.

- [ ] **Step 4: Implement direct_fcntl.py module shell + claim_dir_done**

Path: `scripts/_backends/direct_fcntl.py`

```python
"""M3 direct-fcntl write backend for _shared_state.

Each function writes to one target file under fcntl.LOCK_EX (where applicable),
returns a StepResult dict matching the schema in
docs/superpowers/specs/2026-05-16-v9-m3-helper-scripts-design.md §5.3.

At M1, scripts/_shared_state.py swaps its `_backend` import to queue_delegate;
this module remains as the M3 reference implementation.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import time
from pathlib import Path
from typing import Any

LOCK_TIMEOUT_SECONDS = 5.0
LOCK_RETRY_INTERVAL = 0.05


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_dir(dir_path: Path) -> str:
    """Deterministic content hash of a directory's immediate contents."""
    parts = []
    if not dir_path.is_dir():
        return _sha256("")
    for child in sorted(dir_path.iterdir()):
        if child.is_file():
            parts.append(f"{child.name}\0{child.read_text(encoding='utf-8', errors='replace')}")
        else:
            parts.append(f"{child.name}/")
    return _sha256("\n".join(parts))


def _step_result(
    *,
    step: str,
    ok: bool,
    target_file: str,
    pre_hash: str = "",
    post_hash: str = "",
    duration_ms: int = 0,
    idempotent: bool = False,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "step": step,
        "ok": ok,
        "target_file": target_file,
        "pre_hash": pre_hash,
        "post_hash": post_hash,
        "duration_ms": duration_ms,
        "idempotent": idempotent,
        "error": error,
    }


def claim_dir_done(
    mission: Path, task_id: str, agent: str, utc: str
) -> dict[str, Any]:
    start = time.monotonic()
    claim_dir = mission / "claims" / task_id
    target = str(claim_dir)
    if not claim_dir.is_dir():
        return _step_result(
            step="CLAIM_DIR_DONE",
            ok=False,
            target_file=target,
            error=f"claim dir missing: {claim_dir}",
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    done_marker = claim_dir / "done"
    owner_file = claim_dir / "owner.txt"
    pre_hash = _hash_dir(claim_dir)

    if (
        done_marker.exists()
        and owner_file.exists()
        and owner_file.read_text(encoding="utf-8").strip() == agent
    ):
        return _step_result(
            step="CLAIM_DIR_DONE",
            ok=True,
            target_file=target,
            pre_hash=pre_hash,
            post_hash=pre_hash,
            duration_ms=int((time.monotonic() - start) * 1000),
            idempotent=True,
        )

    done_marker.touch()
    owner_file.write_text(f"{agent}\n", encoding="utf-8")
    post_hash = _hash_dir(claim_dir)
    return _step_result(
        step="CLAIM_DIR_DONE",
        ok=True,
        target_file=target,
        pre_hash=pre_hash,
        post_hash=post_hash,
        duration_ms=int((time.monotonic() - start) * 1000),
    )
```

- [ ] **Step 5: Run tests to verify**

```bash
cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest pytest scripts/tests/test_shared_state.py -v
```

Expected: 3/3 PASS.

- [ ] **Step 6: Stage**

```bash
cd /Users/dave/Documents/Projects/megalodon && git add scripts/_backends/ scripts/tests/test_shared_state.py
```

Commit (SKIP unless operator approves):

```bash
git commit -m "feat(m3): add direct_fcntl.claim_dir_done"
```

---

## Task 5: `direct_fcntl.tasks_bracket`

**Files:**
- Modify: `scripts/_backends/direct_fcntl.py` (append `tasks_bracket`)
- Modify: `scripts/tests/test_shared_state.py` (append tests)

- [ ] **Step 1: Append failing tests**

Append to `scripts/tests/test_shared_state.py`:

```python
def test_tasks_bracket_marks_open_as_done(mission_dir: Path, agent: str):
    result = backend.tasks_bracket(mission_dir, "TEST-1", agent, "2026-05-16T22:30:00Z")
    assert result["ok"] is True
    text = (mission_dir / "TASKS.md").read_text(encoding="utf-8")
    assert "[done: agent-abcd @ 2026-05-16T22:30:00Z] [LANE-A] `TEST-1`" in text


def test_tasks_bracket_idempotent(mission_dir: Path, agent: str):
    backend.tasks_bracket(mission_dir, "TEST-1", agent, "2026-05-16T22:30:00Z")
    second = backend.tasks_bracket(mission_dir, "TEST-1", agent, "2026-05-16T22:31:00Z")
    assert second["ok"] is True
    assert second["idempotent"] is True


def test_tasks_bracket_fails_on_missing_task(mission_dir: Path, agent: str):
    result = backend.tasks_bracket(mission_dir, "TEST-MISSING", agent, "2026-05-16T22:30:00Z")
    assert result["ok"] is False
    assert "not found" in result["error"]
```

- [ ] **Step 2: Run tests to see new ones fail**

```bash
cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest pytest scripts/tests/test_shared_state.py -v
```

Expected: 3 new tests FAIL with `AttributeError: module 'scripts._backends.direct_fcntl' has no attribute 'tasks_bracket'`. Existing 3 still PASS.

- [ ] **Step 3: Append `tasks_bracket` + `_with_lock` helper + `_atomic_replace`**

Append to `scripts/_backends/direct_fcntl.py`:

```python
TASK_LINE_RE = re.compile(
    r"^(?P<prefix>- )"
    r"\[(?P<state>[^\]]+)\]"
    r" "
    r"\[LANE-(?P<lane_short>[A-F])\] "
    r"`(?P<task_id>[^`]+)`"
    r"(?P<rest>.*)$"
)


class LockTimeoutError(RuntimeError):
    pass


def _acquire_lock(fd: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise LockTimeoutError(f"could not acquire lock within {timeout}s")
            time.sleep(LOCK_RETRY_INTERVAL)


def _atomic_replace(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _read_under_lock(path: Path, timeout: float) -> tuple[str, int]:
    """Open path for r+, acquire LOCK_EX, return (text, fd). Caller closes fd."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        _acquire_lock(fd, timeout)
        with os.fdopen(fd, "r+", encoding="utf-8", closefd=False) as f:
            text = f.read()
        return text, fd
    except Exception:
        os.close(fd)
        raise


def tasks_bracket(
    mission: Path, task_id: str, agent: str, utc: str
) -> dict[str, Any]:
    start = time.monotonic()
    path = mission / "TASKS.md"
    target = str(path)
    new_state = f"done: {agent} @ {utc}"
    try:
        text, fd = _read_under_lock(path, LOCK_TIMEOUT_SECONDS)
    except LockTimeoutError as e:
        return _step_result(
            step="TASKS_BRACKET", ok=False, target_file=target,
            error=str(e),
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    try:
        pre_hash = _sha256(text)
        lines = text.splitlines(keepends=True)
        for i, line in enumerate(lines):
            m = TASK_LINE_RE.match(line)
            if m and m["task_id"] == task_id:
                if m["state"].startswith("done:"):
                    return _step_result(
                        step="TASKS_BRACKET", ok=True, target_file=target,
                        pre_hash=pre_hash, post_hash=pre_hash,
                        duration_ms=int((time.monotonic() - start) * 1000),
                        idempotent=True,
                    )
                lines[i] = (
                    f"{m['prefix']}[{new_state}] [LANE-{m['lane_short']}] "
                    f"`{task_id}`{m['rest']}"
                )
                if not lines[i].endswith("\n"):
                    lines[i] += "\n"
                new_text = "".join(lines)
                _atomic_replace(path, new_text)
                return _step_result(
                    step="TASKS_BRACKET", ok=True, target_file=target,
                    pre_hash=pre_hash, post_hash=_sha256(new_text),
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
        return _step_result(
            step="TASKS_BRACKET", ok=False, target_file=target,
            error=f"task {task_id} not found in TASKS.md",
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
```

- [ ] **Step 4: Run tests to verify**

```bash
cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest pytest scripts/tests/test_shared_state.py -v
```

Expected: 6/6 PASS.

- [ ] **Step 5: Stage**

```bash
cd /Users/dave/Documents/Projects/megalodon && git add scripts/_backends/direct_fcntl.py scripts/tests/test_shared_state.py
```

Commit (SKIP unless operator approves):

```bash
git commit -m "feat(m3): add direct_fcntl.tasks_bracket with fcntl + atomic_replace"
```

---

## Task 6: `direct_fcntl.history_append`

**Files:**
- Modify: `scripts/_backends/direct_fcntl.py` (append `history_append`)
- Modify: `scripts/tests/test_shared_state.py`

- [ ] **Step 1: Append failing tests**

Append to `scripts/tests/test_shared_state.py`:

```python
def test_history_append_writes_pipe_row(mission_dir: Path, agent: str):
    result = backend.history_append(
        mission_dir,
        agent=agent,
        lane_short="A",
        task_id="TEST-1",
        finding_path="findings/agent-abcd-A-TEST-1-2026-05-16T22-30Z.md",
        severity="DELTA",
        notes="sample close",
        utc="2026-05-16T22:30:00Z",
    )
    assert result["ok"] is True
    text = (mission_dir / "HISTORY.md").read_text(encoding="utf-8")
    assert "2026-05-16T22:30:00Z | agent-abcd | A | TEST-1 | " in text
    assert "| DELTA (sample close)" in text


def test_history_append_idempotent_within_60s(mission_dir: Path, agent: str):
    common = dict(
        mission_dir=mission_dir, agent=agent, lane_short="A", task_id="TEST-1",
        finding_path="findings/x.md", severity="DELTA", notes="first",
    )
    backend.history_append(**common, utc="2026-05-16T22:30:00Z")
    second = backend.history_append(**common, utc="2026-05-16T22:30:45Z")
    assert second["idempotent"] is True
```

- [ ] **Step 2: Run tests to see them fail**

```bash
cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest pytest scripts/tests/test_shared_state.py -v
```

Expected: 2 new tests fail; existing 6 pass.

- [ ] **Step 3: Append `history_append`**

Append to `scripts/_backends/direct_fcntl.py`:

```python
def _history_row_recent(text: str, agent: str, task_id: str, utc: str) -> bool:
    """Return True if last ~50 lines contain a row with same agent+task_id
    within ±60 seconds of utc."""
    from datetime import datetime, timedelta, timezone

    try:
        target = datetime.strptime(utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    lines = text.splitlines()[-50:]
    for ln in lines:
        if agent not in ln or task_id not in ln:
            continue
        # Try to parse leading UTC stamp
        prefix = ln.split(" | ", 1)[0]
        try:
            row_utc = datetime.strptime(prefix, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if abs((row_utc - target).total_seconds()) <= 60:
            return True
    return False


def history_append(
    mission: Path,
    *,
    agent: str,
    lane_short: str,
    task_id: str,
    finding_path: str,
    severity: str,
    notes: str,
    utc: str,
) -> dict[str, Any]:
    start = time.monotonic()
    path = mission / "HISTORY.md"
    target = str(path)
    notes_first_line = notes.split("\n", 1)[0]
    line = (
        f"{utc} | {agent} | {lane_short} | {task_id} | "
        f"{finding_path} | {severity} ({notes_first_line})\n"
    )
    try:
        text, fd = _read_under_lock(path, LOCK_TIMEOUT_SECONDS)
    except LockTimeoutError as e:
        return _step_result(
            step="HISTORY_APPEND", ok=False, target_file=target,
            error=str(e),
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    try:
        pre_hash = _sha256(text)
        if _history_row_recent(text, agent, task_id, utc):
            return _step_result(
                step="HISTORY_APPEND", ok=True, target_file=target,
                pre_hash=pre_hash, post_hash=pre_hash,
                duration_ms=int((time.monotonic() - start) * 1000),
                idempotent=True,
            )
        if text and not text.endswith("\n"):
            text += "\n"
        new_text = text + line
        _atomic_replace(path, new_text)
        return _step_result(
            step="HISTORY_APPEND", ok=True, target_file=target,
            pre_hash=pre_hash, post_hash=_sha256(new_text),
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest pytest scripts/tests/test_shared_state.py -v
```

Expected: 8/8 PASS.

- [ ] **Step 5: Stage**

```bash
cd /Users/dave/Documents/Projects/megalodon && git add scripts/_backends/direct_fcntl.py scripts/tests/test_shared_state.py
```

Commit (SKIP unless operator approves):

```bash
git commit -m "feat(m3): add direct_fcntl.history_append with 60s idempotency"
```

---

## Task 7: `direct_fcntl.status_update`

**Files:**
- Modify: `scripts/_backends/direct_fcntl.py`
- Modify: `scripts/tests/test_shared_state.py`

- [ ] **Step 1: Append failing tests**

Append to `scripts/tests/test_shared_state.py`:

```python
def test_status_update_writes_idle_row(mission_dir: Path, agent: str):
    result = backend.status_update(
        mission_dir, lane="AUDIT", agent=agent,
        task_id="TEST-1", summary="sample close",
        utc="2026-05-16T22:30:00Z",
    )
    assert result["ok"] is True
    text = (mission_dir / "STATUS.md").read_text(encoding="utf-8")
    assert "| AUDIT" in text
    assert "| idle" in text
    assert "2026-05-16T22:30:00Z" in text
    assert "TEST-1 done — sample close" in text


def test_status_update_rejects_owner_mismatch(mission_dir: Path):
    result = backend.status_update(
        mission_dir, lane="AUDIT", agent="agent-zzzz",
        task_id="TEST-1", summary="sample close",
        utc="2026-05-16T22:30:00Z",
    )
    assert result["ok"] is False
    assert "owner mismatch" in result["error"]


def test_status_update_idempotent(mission_dir: Path, agent: str):
    backend.status_update(
        mission_dir, lane="AUDIT", agent=agent,
        task_id="TEST-1", summary="sample close",
        utc="2026-05-16T22:30:00Z",
    )
    second = backend.status_update(
        mission_dir, lane="AUDIT", agent=agent,
        task_id="TEST-1", summary="sample close",
        utc="2026-05-16T22:31:00Z",
    )
    assert second["idempotent"] is True
```

- [ ] **Step 2: Run tests to see them fail**

```bash
cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest pytest scripts/tests/test_shared_state.py -v
```

Expected: 3 new tests fail; existing 8 pass.

- [ ] **Step 3: Append `status_update`**

Append to `scripts/_backends/direct_fcntl.py`:

```python
STATUS_ROW_RE = re.compile(
    r"^\| (?P<lane>AUDIT|ARCHITECT|BACKEND|FRONTEND|TEST|META)\s*"
    r"\| (?P<agent>[^|]+?)\s*"
    r"\| (?P<state>[^|]+?)\s*"
    r"\| (?P<last_utc>[^|]+?)\s*"
    r"\| (?P<notes>.*?)\s*\|\s*$"
)


def status_update(
    mission: Path,
    *,
    lane: str,
    agent: str,
    task_id: str,
    summary: str,
    utc: str,
) -> dict[str, Any]:
    start = time.monotonic()
    path = mission / "STATUS.md"
    target = str(path)
    try:
        text, fd = _read_under_lock(path, LOCK_TIMEOUT_SECONDS)
    except LockTimeoutError as e:
        return _step_result(
            step="STATUS_UPDATE", ok=False, target_file=target,
            error=str(e),
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    try:
        pre_hash = _sha256(text)
        lines = text.splitlines(keepends=True)
        new_notes = f"{task_id} done — {summary}"
        for i, line in enumerate(lines):
            m = STATUS_ROW_RE.match(line.rstrip("\n"))
            if not m or m["lane"].strip() != lane:
                continue
            if m["agent"].strip() != agent:
                return _step_result(
                    step="STATUS_UPDATE", ok=False, target_file=target,
                    pre_hash=pre_hash, post_hash=pre_hash,
                    error=(
                        f"STATUS row owner mismatch: lane={lane} "
                        f"expected agent={agent} found={m['agent'].strip()}"
                    ),
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            if m["state"].strip() == "idle" and f"{task_id} done" in m["notes"]:
                return _step_result(
                    step="STATUS_UPDATE", ok=True, target_file=target,
                    pre_hash=pre_hash, post_hash=pre_hash,
                    duration_ms=int((time.monotonic() - start) * 1000),
                    idempotent=True,
                )
            lines[i] = f"| {lane:9} | {agent} | {'idle':6} | {utc} | {new_notes} |\n"
            new_text = "".join(lines)
            _atomic_replace(path, new_text)
            return _step_result(
                step="STATUS_UPDATE", ok=True, target_file=target,
                pre_hash=pre_hash, post_hash=_sha256(new_text),
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        return _step_result(
            step="STATUS_UPDATE", ok=False, target_file=target,
            error=f"no STATUS row for lane={lane}",
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest pytest scripts/tests/test_shared_state.py -v
```

Expected: 11/11 PASS.

- [ ] **Step 5: Stage**

```bash
cd /Users/dave/Documents/Projects/megalodon && git add scripts/_backends/direct_fcntl.py scripts/tests/test_shared_state.py
```

Commit (SKIP unless operator approves):

```bash
git commit -m "feat(m3): add direct_fcntl.status_update with owner check"
```

---

## Task 8: `_shared_state.execute_close` orchestrator + journal write

**Files:**
- Create: `scripts/_shared_state.py`
- Modify: `scripts/tests/test_shared_state.py`

- [ ] **Step 1: Append failing tests for execute_close**

Append to `scripts/tests/test_shared_state.py`:

```python
import json as _json

from scripts._shared_state import execute_close, resume_close


def test_execute_close_happy_path(mission_dir: Path, agent: str):
    (mission_dir / "findings" / "f.md").parent.mkdir(parents=True, exist_ok=True)
    (mission_dir / "findings" / "f.md").write_text("body", encoding="utf-8")
    result = execute_close(
        mission_dir,
        request_id="rid-happy",
        task_id="TEST-1",
        lane="AUDIT",
        agent=agent,
        utc="2026-05-16T22:30:00Z",
        finding_path="findings/f.md",
        severity="DELTA",
        notes="happy path",
        summary="happy path",
    )
    assert result["ok"] is True
    assert result["completed"] == [
        "CLAIM_DIR_DONE", "TASKS_BRACKET", "HISTORY_APPEND", "STATUS_UPDATE",
    ]
    assert result["failed_step"] is None
    # Journal should exist with status COMPLETE
    journal = mission_dir / ".scripts-journal" / "rid-happy.json"
    assert journal.exists()
    data = _json.loads(journal.read_text())
    assert data["status"] == "COMPLETE"


def test_execute_close_partial_on_missing_claim(mission_dir: Path, agent: str):
    """If claims/TEST-2 doesn't exist, CLAIM_DIR_DONE fails first; no further steps."""
    result = execute_close(
        mission_dir,
        request_id="rid-partial",
        task_id="TEST-2",
        lane="AUDIT",
        agent=agent,
        utc="2026-05-16T22:30:00Z",
        finding_path="findings/x.md",
        severity="DELTA",
        notes="will fail",
        summary="fail",
    )
    assert result["ok"] is False
    assert result["failed_step"] == "CLAIM_DIR_DONE"
    assert result["resume_hint"] is not None
    journal = _json.loads(
        (mission_dir / ".scripts-journal" / "rid-partial.json").read_text()
    )
    assert journal["status"] == "PARTIAL"
```

- [ ] **Step 2: Run tests to see them fail**

```bash
cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest pytest scripts/tests/test_shared_state.py -v
```

Expected: 2 new tests fail with `ModuleNotFoundError: scripts._shared_state`. Existing 11 still pass.

- [ ] **Step 3: Implement `_shared_state.py`**

Path: `scripts/_shared_state.py`

```python
"""M3/M1 abstraction boundary for shared-state writes.

execute_close() runs the 4 RULE-10 steps in order under per-file fcntl locks
(M3 backend) or via queue_client submit + wait_until_applied (M1 backend).
On any step failure, writes a PARTIAL journal entry under
mission/.scripts-journal/<request-id>.json and returns with resume_hint.

resume_close() reads a PARTIAL journal and continues from the first failed step.
Each step is independently idempotent, so resume is safe to invoke repeatedly.

Backend swap at M1: change the line below to
    from ._backends import queue_delegate as _backend
and remove this comment.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._backends import direct_fcntl as _backend
from ._validation import LANE_LONG_TO_SHORT

SCHEMA_VERSION = 1
JOURNAL_DIR_NAME = ".scripts-journal"

_STEPS_IN_ORDER = ["CLAIM_DIR_DONE", "TASKS_BRACKET", "HISTORY_APPEND", "STATUS_UPDATE"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _journal_path(mission: Path, request_id: str) -> Path:
    return mission / JOURNAL_DIR_NAME / f"{request_id}.json"


def _write_journal(mission: Path, request_id: str, payload: dict[str, Any]) -> None:
    path = _journal_path(mission, request_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**payload, "last_updated_utc": _utc_now()}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _read_journal(mission: Path, request_id: str) -> dict[str, Any]:
    path = _journal_path(mission, request_id)
    if not path.exists():
        raise FileNotFoundError(f"journal not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _run_step(
    step: str,
    mission: Path,
    *,
    task_id: str,
    lane: str,
    agent: str,
    utc: str,
    finding_path: str,
    severity: str,
    notes: str,
    summary: str,
) -> dict[str, Any]:
    if step == "CLAIM_DIR_DONE":
        return _backend.claim_dir_done(mission, task_id, agent, utc)
    if step == "TASKS_BRACKET":
        return _backend.tasks_bracket(mission, task_id, agent, utc)
    if step == "HISTORY_APPEND":
        return _backend.history_append(
            mission, agent=agent, lane_short=LANE_LONG_TO_SHORT[lane],
            task_id=task_id, finding_path=finding_path, severity=severity,
            notes=notes, utc=utc,
        )
    if step == "STATUS_UPDATE":
        return _backend.status_update(
            mission, lane=lane, agent=agent, task_id=task_id,
            summary=summary, utc=utc,
        )
    raise ValueError(f"unknown step: {step}")


def execute_close(
    mission_dir: Path,
    *,
    request_id: str,
    task_id: str,
    lane: str,
    agent: str,
    utc: str,
    finding_path: str,
    severity: str,
    notes: str,
    summary: str,
) -> dict[str, Any]:
    mission = Path(mission_dir)
    started = _utc_now()
    args = {
        "finding": finding_path, "severity": severity,
        "notes": notes, "summary": summary,
    }
    journal_payload = {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id,
        "started_utc": started,
        "status": "PENDING",
        "task_id": task_id,
        "lane": lane,
        "agent": agent,
        "args": args,
        "steps": [],
    }
    _write_journal(mission, request_id, journal_payload)

    completed: list[str] = []
    step_results: list[dict[str, Any]] = []
    failed_step: str | None = None

    for step in _STEPS_IN_ORDER:
        result = _run_step(
            step, mission,
            task_id=task_id, lane=lane, agent=agent, utc=utc,
            finding_path=finding_path, severity=severity,
            notes=notes, summary=summary,
        )
        step_results.append({**result, "completed_utc": _utc_now()})
        if result["ok"]:
            completed.append(step)
        else:
            failed_step = step
            break

    journal_payload["steps"] = step_results
    if failed_step is None:
        journal_payload["status"] = "COMPLETE"
        _write_journal(mission, request_id, journal_payload)
        return {
            "request_id": request_id,
            "ok": True,
            "completed": completed,
            "failed_step": None,
            "steps": step_results,
            "resume_hint": None,
        }
    journal_payload["status"] = "PARTIAL"
    _write_journal(mission, request_id, journal_payload)
    return {
        "request_id": request_id,
        "ok": False,
        "completed": completed,
        "failed_step": failed_step,
        "steps": step_results,
        "resume_hint": f"python3 scripts/atomic_close.py --resume {request_id}",
    }


def resume_close(mission_dir: Path, request_id: str) -> dict[str, Any]:
    """Stub — implemented in Task 9."""
    raise NotImplementedError("resume_close: see Task 9")
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest pytest scripts/tests/test_shared_state.py -v
```

Expected: 13/13 PASS.

- [ ] **Step 5: Stage**

```bash
cd /Users/dave/Documents/Projects/megalodon && git add scripts/_shared_state.py scripts/tests/test_shared_state.py
```

Commit (SKIP unless operator approves):

```bash
git commit -m "feat(m3): add _shared_state.execute_close with journal"
```

---

## Task 9: `_shared_state.resume_close`

**Files:**
- Modify: `scripts/_shared_state.py` (implement `resume_close`)
- Modify: `scripts/tests/test_shared_state.py` (add tests)

- [ ] **Step 1: Append failing tests**

Append to `scripts/tests/test_shared_state.py`:

```python
def test_resume_close_completes_partial(mission_dir: Path, agent: str):
    # First, cause a PARTIAL by targeting a missing claim:
    execute_close(
        mission_dir,
        request_id="rid-resume",
        task_id="TEST-2",  # missing — will fail at CLAIM_DIR_DONE
        lane="AUDIT", agent=agent,
        utc="2026-05-16T22:30:00Z",
        finding_path="findings/x.md", severity="DELTA",
        notes="setup partial", summary="partial",
    )
    # Now create the missing claim dir and resume:
    (mission_dir / "claims" / "TEST-2").mkdir()
    (mission_dir / "claims" / "TEST-2" / "owner.txt").write_text(f"{agent}\n")
    # We also need a matching TASK-2 line in TASKS.md. Append one:
    with open(mission_dir / "TASKS.md", "a", encoding="utf-8") as f:
        f.write("- [ ] [LANE-A] `TEST-2` — second sample\n")
    result = resume_close(mission_dir, "rid-resume")
    assert result["ok"] is True
    assert "STATUS_UPDATE" in result["completed"]


def test_resume_close_rejects_when_journal_missing(mission_dir: Path):
    with pytest.raises(FileNotFoundError):
        resume_close(mission_dir, "rid-does-not-exist")
```

- [ ] **Step 2: Run tests to see them fail**

```bash
cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest pytest scripts/tests/test_shared_state.py -v
```

Expected: 2 new tests fail (`NotImplementedError` and assertion).

- [ ] **Step 3: Replace the `resume_close` stub with the real implementation**

Replace the `resume_close` stub in `scripts/_shared_state.py` with:

```python
def resume_close(mission_dir: Path, request_id: str) -> dict[str, Any]:
    mission = Path(mission_dir)
    journal = _read_journal(mission, request_id)
    if journal["status"] in ("COMPLETE", "RESUMED-COMPLETE"):
        return {
            "request_id": request_id, "ok": True,
            "completed": [s["step"] for s in journal["steps"] if s["ok"]],
            "failed_step": None, "steps": journal["steps"],
            "resume_hint": None,
        }

    completed = [s["step"] for s in journal["steps"] if s["ok"]]
    remaining = [s for s in _STEPS_IN_ORDER if s not in completed]
    args = journal["args"]
    new_results: list[dict[str, Any]] = list(journal["steps"])
    failed_step: str | None = None

    for step in remaining:
        result = _run_step(
            step, mission,
            task_id=journal["task_id"], lane=journal["lane"],
            agent=journal["agent"], utc=_utc_now(),
            finding_path=args["finding"], severity=args["severity"],
            notes=args["notes"], summary=args["summary"],
        )
        new_results.append({**result, "completed_utc": _utc_now()})
        if result["ok"]:
            completed.append(step)
        else:
            failed_step = step
            break

    journal["steps"] = new_results
    journal["status"] = "RESUMED-COMPLETE" if failed_step is None else "PARTIAL"
    _write_journal(mission, request_id, journal)
    return {
        "request_id": request_id,
        "ok": failed_step is None,
        "completed": completed,
        "failed_step": failed_step,
        "steps": new_results,
        "resume_hint": None if failed_step is None
            else f"python3 scripts/atomic_close.py --resume {request_id}",
    }
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest pytest scripts/tests/test_shared_state.py -v
```

Expected: 15/15 PASS.

- [ ] **Step 5: Stage**

```bash
cd /Users/dave/Documents/Projects/megalodon && git add scripts/_shared_state.py scripts/tests/test_shared_state.py
```

Commit (SKIP unless operator approves):

```bash
git commit -m "feat(m3): add _shared_state.resume_close"
```

---

## Task 10: `_state_read` — `read_phase` + `read_lanes`

**Files:**
- Create: `scripts/_state_read.py`
- Create: `scripts/tests/test_state_read.py`

- [ ] **Step 1: Write failing tests**

Path: `scripts/tests/test_state_read.py`

```python
"""Tests for scripts/_state_read.py."""

from datetime import timezone

import pytest
from freezegun import freeze_time

from scripts._state_read import read_lanes, read_phase


def test_read_lanes_returns_six_rows(mission_dir):
    rows = read_lanes(mission_dir)
    assert len(rows) == 6
    lanes = {row["lane"] for row in rows}
    assert lanes == {"AUDIT", "ARCHITECT", "BACKEND", "FRONTEND", "TEST", "META"}


def test_read_lanes_parses_audit_row(mission_dir):
    rows = read_lanes(mission_dir)
    audit = next(r for r in rows if r["lane"] == "AUDIT")
    assert audit["agent"] == "agent-abcd"
    assert audit["state"] == "working: TEST-1"
    assert audit["last_utc"] == "2026-05-16T22:00:00Z"


@freeze_time("2026-05-16T22:00:30Z")
def test_read_lanes_computes_stale_seconds(mission_dir):
    rows = read_lanes(mission_dir)
    audit = next(r for r in rows if r["lane"] == "AUDIT")
    assert audit["stale_seconds"] == 30


def test_read_phase_returns_init_phase(mission_dir):
    phase, owner = read_phase(mission_dir)
    # Minimal fixture has only an INIT->PHASE-PLAN line; current phase is PHASE-PLAN.
    assert phase == "PHASE-PLAN"
    assert owner is None
```

- [ ] **Step 2: Run tests to see them fail**

```bash
cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest --with freezegun pytest scripts/tests/test_state_read.py -v
```

Expected: `ModuleNotFoundError: scripts._state_read`.

- [ ] **Step 3: Implement initial `_state_read.py`**

Path: `scripts/_state_read.py`

```python
"""Read-only mission state aggregation for scripts/poll.py.

All functions are pure: no side effects, no fcntl. Reads stay direct per
V9-ROADMAP M1 Option A.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._backends.direct_fcntl import STATUS_ROW_RE

_PHASE_FLIP_RE = re.compile(
    r"^(?P<utc>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) "
    r"(?P<from>[A-Z0-9_-]+)->(?P<to>[A-Z0-9_-]+) by (?P<agent>\S+)"
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc(s: str) -> datetime | None:
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def read_lanes(mission_dir: Path) -> list[dict[str, Any]]:
    text = (mission_dir / "STATUS.md").read_text(encoding="utf-8")
    now = _utc_now()
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        m = STATUS_ROW_RE.match(line)
        if not m:
            continue
        last_utc_str = m["last_utc"].strip()
        last_utc_dt = _parse_utc(last_utc_str)
        stale = int((now - last_utc_dt).total_seconds()) if last_utc_dt else None
        lane = m["lane"].strip()
        rows.append({
            "lane": lane,
            "lane_short": {
                "AUDIT": "A", "ARCHITECT": "B", "BACKEND": "C",
                "FRONTEND": "D", "TEST": "E", "META": "F",
            }[lane],
            "agent": m["agent"].strip(),
            "state": m["state"].strip(),
            "last_utc": last_utc_str,
            "stale_seconds": stale,
            "notes": m["notes"].strip(),
        })
    return rows


def read_phase(mission_dir: Path) -> tuple[str, str | None]:
    """Return (current_phase, lock_owner_or_None).

    Current phase derives from the last PHASE-X->PHASE-Y line in .mission-events.
    Lock owner derives from .phase-flip-locks/*/owner.txt if any lock dir exists.
    """
    events_path = mission_dir / ".mission-events"
    current_phase = "PHASE-PLAN"
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            m = _PHASE_FLIP_RE.match(line)
            if m:
                current_phase = m["to"]
    lock_owner = None
    lock_dir = mission_dir / ".phase-flip-locks"
    if lock_dir.is_dir():
        for child in lock_dir.iterdir():
            if child.is_dir():
                owner_file = child / "owner.txt"
                if owner_file.exists():
                    lock_owner = owner_file.read_text().strip()
                    break
    return current_phase, lock_owner
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest --with freezegun pytest scripts/tests/test_state_read.py -v
```

Expected: 4/4 PASS.

- [ ] **Step 5: Stage**

```bash
cd /Users/dave/Documents/Projects/megalodon && git add scripts/_state_read.py scripts/tests/test_state_read.py
```

Commit (SKIP unless operator approves):

```bash
git commit -m "feat(m3): add _state_read.read_lanes + read_phase"
```

---

## Task 11: `_state_read` — `read_claims`, `read_events_tail`, `read_findings_recent`, `read_partial_journals`

**Files:**
- Modify: `scripts/_state_read.py`
- Modify: `scripts/tests/test_state_read.py`

- [ ] **Step 1: Append failing tests**

Append to `scripts/tests/test_state_read.py`:

```python
from scripts._state_read import (
    read_claims, read_events_tail, read_findings_recent, read_partial_journals,
)


def test_read_claims_open_when_no_done_marker(mission_dir):
    claims = read_claims(mission_dir)
    assert any(c["task_id"] == "TEST-1" for c in claims["open"])
    assert claims["done"] == []


def test_read_claims_done_when_marker_present(mission_dir):
    (mission_dir / "claims" / "TEST-1" / "done").touch()
    claims = read_claims(mission_dir)
    assert claims["open"] == []
    assert any(c["task_id"] == "TEST-1" for c in claims["done"])


def test_read_events_tail_returns_n_lines(mission_dir):
    tail = read_events_tail(mission_dir, n=5)
    assert len(tail) == 1
    assert "INIT->PHASE-PLAN" in tail[0]


def test_read_findings_recent_returns_empty_for_empty_dir(mission_dir):
    findings = read_findings_recent(mission_dir, n=5, include_body=False)
    assert findings == []


def test_read_partial_journals_returns_only_partial_within_window(mission_dir, agent):
    # No journal dir yet → empty.
    assert read_partial_journals(mission_dir) == []
    # Create a journal manually with status=PARTIAL.
    import json
    jdir = mission_dir / ".scripts-journal"
    jdir.mkdir()
    (jdir / "rid-old.json").write_text(json.dumps({
        "schema_version": 1, "request_id": "rid-old",
        "started_utc": "2026-05-15T00:00:00Z",
        "last_updated_utc": "2026-05-15T00:00:00Z",
        "status": "PARTIAL",
        "task_id": "X-1", "lane": "AUDIT", "agent": agent,
        "args": {"finding": "f", "severity": "DELTA", "notes": "n", "summary": "s"},
        "steps": [{"step": "CLAIM_DIR_DONE", "ok": True, "error": None}],
    }))
    (jdir / "rid-new.json").write_text(json.dumps({
        "schema_version": 1, "request_id": "rid-new",
        "started_utc": "2026-05-16T22:00:00Z",
        "last_updated_utc": "2026-05-16T22:00:00Z",
        "status": "PARTIAL",
        "task_id": "X-2", "lane": "AUDIT", "agent": agent,
        "args": {"finding": "f", "severity": "DELTA", "notes": "n", "summary": "s"},
        "steps": [
            {"step": "CLAIM_DIR_DONE", "ok": True, "error": None},
            {"step": "TASKS_BRACKET", "ok": False, "error": "missing"},
        ],
    }))
    # With freeze_time at 2026-05-16T22:30Z, rid-old is > 24h old, rid-new is 30min old.
    with freeze_time("2026-05-16T22:30:00Z"):
        entries = read_partial_journals(mission_dir, max_age_seconds=86400)
    assert len(entries) == 1
    assert entries[0]["request_id"] == "rid-new"
    assert entries[0]["failed_step"] == "TASKS_BRACKET"
```

- [ ] **Step 2: Run tests to see them fail**

```bash
cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest --with freezegun pytest scripts/tests/test_state_read.py -v
```

Expected: 5 new tests fail with `ImportError: cannot import name 'read_claims'`. Existing 4 still pass.

- [ ] **Step 3: Append the four functions**

Append to `scripts/_state_read.py`:

```python
import json as _json


def read_claims(mission_dir: Path) -> dict[str, list[dict[str, Any]]]:
    claims_dir = mission_dir / "claims"
    open_: list[dict[str, Any]] = []
    done: list[dict[str, Any]] = []
    if not claims_dir.is_dir():
        return {"open": open_, "done": done}
    for child in sorted(claims_dir.iterdir()):
        if not child.is_dir():
            continue
        owner_file = child / "owner.txt"
        owner = owner_file.read_text().strip() if owner_file.exists() else None
        done_marker = child / "done"
        entry = {
            "task_id": child.name,
            "owner": owner,
            "has_done_marker": done_marker.exists(),
        }
        if done_marker.exists():
            entry["done_marker_mtime_utc"] = datetime.fromtimestamp(
                done_marker.stat().st_mtime, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            done.append(entry)
        else:
            entry["created_utc"] = datetime.fromtimestamp(
                child.stat().st_mtime, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            open_.append(entry)
    return {"open": open_, "done": done}


def read_events_tail(mission_dir: Path, n: int) -> list[str]:
    path = mission_dir / ".mission-events"
    if not path.exists():
        return []
    lines = [
        ln for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    return lines[-n:]


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> dict[str, str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out


def read_findings_recent(
    mission_dir: Path, n: int, include_body: bool
) -> list[dict[str, Any]]:
    findings_dir = mission_dir / "findings"
    if not findings_dir.is_dir():
        return []
    files = [p for p in findings_dir.iterdir() if p.is_file() and p.suffix == ".md"]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[dict[str, Any]] = []
    for path in files[:n]:
        text = path.read_text(encoding="utf-8")
        fm = _parse_frontmatter(text)
        out.append({
            "path": str(path.relative_to(mission_dir)),
            "mtime_utc": datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "lane": fm.get("lane"),
            "task_id": fm.get("task-id"),
            "severity": fm.get("severity"),
            "body": text if include_body else None,
        })
    return out


def read_partial_journals(
    mission_dir: Path, max_age_seconds: int = 86400
) -> list[dict[str, Any]]:
    jdir = mission_dir / ".scripts-journal"
    if not jdir.is_dir():
        return []
    now = _utc_now()
    out: list[dict[str, Any]] = []
    for path in sorted(jdir.glob("*.json")):
        try:
            data = _json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("status") != "PARTIAL":
            continue
        last = _parse_utc(data.get("last_updated_utc", ""))
        if last is None:
            continue
        age = int((now - last).total_seconds())
        if age > max_age_seconds:
            continue
        completed = [s["step"] for s in data.get("steps", []) if s.get("ok")]
        failed = next(
            (s["step"] for s in data.get("steps", []) if not s.get("ok")),
            None,
        )
        out.append({
            "request_id": data["request_id"],
            "started_utc": data.get("started_utc"),
            "last_updated_utc": data["last_updated_utc"],
            "task_id": data["task_id"],
            "lane": data["lane"],
            "agent": data["agent"],
            "completed_steps": completed,
            "failed_step": failed,
            "error": next(
                (s.get("error") for s in data.get("steps", []) if not s.get("ok")),
                None,
            ),
            "age_seconds": age,
            "resume_hint": (
                f"python3 scripts/atomic_close.py --resume {data['request_id']}"
            ),
        })
    out.sort(key=lambda e: e["last_updated_utc"], reverse=True)
    return out
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest --with freezegun pytest scripts/tests/test_state_read.py -v
```

Expected: 9/9 PASS.

- [ ] **Step 5: Stage**

```bash
cd /Users/dave/Documents/Projects/megalodon && git add scripts/_state_read.py scripts/tests/test_state_read.py
```

Commit (SKIP unless operator approves):

```bash
git commit -m "feat(m3): add _state_read claims/events/findings/partial_journals"
```

---

## Task 12: `atomic_close.py` CLI

**Files:**
- Create: `scripts/atomic_close.py`
- Create: `scripts/tests/test_atomic_close.py`

- [ ] **Step 1: Write failing tests**

Path: `scripts/tests/test_atomic_close.py`

```python
"""CLI integration tests for scripts/atomic_close.py."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "atomic_close.py"


def _run(mission_dir: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(SCRIPT.resolve().parents[1])}
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--mission-dir", str(mission_dir), *args],
        capture_output=True, text=True, env=env,
    )


def test_help_runs(mission_dir):
    env = {**os.environ, "PYTHONPATH": str(SCRIPT.resolve().parents[1])}
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True, env=env,
    )
    assert res.returncode == 0
    assert "atomic_close" in res.stdout


def test_happy_path_returns_ok_json(mission_dir, agent):
    (mission_dir / "findings").mkdir(exist_ok=True)
    (mission_dir / "findings" / "f.md").write_text("body", encoding="utf-8")
    res = _run(
        mission_dir,
        "--task", "TEST-1", "--lane", "AUDIT", "--agent", agent,
        "--finding", "findings/f.md", "--severity", "DELTA",
        "--notes", "happy path", "--summary", "happy",
    )
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout.strip())
    assert payload["ok"] is True
    assert len(payload["steps"]) == 4


def test_arg_validation_exits_2(mission_dir, agent):
    res = _run(
        mission_dir,
        "--task", "lowercase-bad", "--lane", "AUDIT", "--agent", agent,
        "--finding", "findings/f.md", "--severity", "DELTA",
        "--notes", "x", "--summary", "x",
    )
    assert res.returncode == 2


def test_precondition_failure_exits_3(mission_dir, agent):
    res = _run(
        mission_dir,
        "--task", "P5-RUN-DOES-NOT-EXIST", "--lane", "AUDIT", "--agent", agent,
        "--finding", "findings/f.md", "--severity", "DELTA",
        "--notes", "x", "--summary", "x",
    )
    # CLAIM_DIR_DONE will fail because claims/P5-RUN-DOES-NOT-EXIST/ is missing,
    # producing a partial close at step 0 → exit 3 per spec.
    assert res.returncode == 3
    payload = json.loads(res.stdout.strip())
    assert payload["ok"] is False
    assert payload["failed_step"] == "CLAIM_DIR_DONE"


def test_dry_run_writes_nothing(mission_dir, agent):
    (mission_dir / "findings").mkdir(exist_ok=True)
    (mission_dir / "findings" / "f.md").write_text("body", encoding="utf-8")
    before = (mission_dir / "STATUS.md").read_text(encoding="utf-8")
    res = _run(
        mission_dir,
        "--task", "TEST-1", "--lane", "AUDIT", "--agent", agent,
        "--finding", "findings/f.md", "--severity", "DELTA",
        "--notes", "dryrun", "--summary", "dryrun",
        "--dry-run",
    )
    assert res.returncode == 0
    after = (mission_dir / "STATUS.md").read_text(encoding="utf-8")
    assert before == after
```

- [ ] **Step 2: Run tests to see them fail**

```bash
cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest --with freezegun pytest scripts/tests/test_atomic_close.py -v
```

Expected: `FileNotFoundError` (script not yet created) → all tests fail.

- [ ] **Step 3: Implement `atomic_close.py`**

Path: `scripts/atomic_close.py`

```python
#!/usr/bin/env python3
"""Atomic RULE-10 close — workers' canonical completion script.

Usage:
    python3 scripts/atomic_close.py \\
        --task <TASK-ID> --lane <LANE> --agent <AGENT-ID> \\
        --finding <PATH> --severity <SEV> \\
        --notes <TEXT> --summary <TEXT> \\
        [--mission-dir <PATH>] [--dry-run] [--resume <REQUEST-ID>] [--debug]

Exit codes:
    0 success | 1 unexpected | 2 arg validation | 3 partial close (resume available)
    | 4 precondition (task already done, claim missing) | 5 lock timeout

Spec: docs/superpowers/specs/2026-05-16-v9-m3-helper-scripts-design.md
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

# Allow `python3 scripts/atomic_close.py` from project root without install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import _validation
from scripts._logging import get_logger
from scripts._shared_state import execute_close, resume_close


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_request_id(agent: str) -> str:
    stamp = _utc_now().replace(":", "-")
    return f"{stamp}-{agent}-rule10-CLOSE-{secrets.token_hex(2)}"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="atomic_close",
        description="Atomic RULE-10 close (v9 M3 helper)",
    )
    p.add_argument("--task")
    p.add_argument("--lane")
    p.add_argument("--agent")
    p.add_argument("--finding")
    p.add_argument("--severity")
    p.add_argument("--notes")
    p.add_argument("--summary")
    p.add_argument("--mission-dir", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--resume", dest="resume_id", default=None)
    p.add_argument("--debug", action="store_true")
    return p.parse_args(argv)


def _resolve_mission(arg: str | None) -> Path:
    candidate = Path(arg) if arg else Path.cwd()
    if not (candidate / "STATUS.md").exists() or not (candidate / "TASKS.md").exists():
        raise FileNotFoundError(
            f"mission dir invalid (no STATUS.md/TASKS.md): {candidate}"
        )
    return candidate.resolve()


def _validate_or_die(args: argparse.Namespace) -> None:
    if args.resume_id:
        return
    required = ["task", "lane", "agent", "finding", "severity", "notes", "summary"]
    missing = [r for r in required if not getattr(args, r)]
    if missing:
        raise ValueError(f"missing required args: {missing}")
    _validation.validate_task_id(args.task)
    _validation.validate_lane(args.lane)
    _validation.validate_agent(args.agent)
    _validation.validate_severity(args.severity)
    _validation.validate_notes(args.notes)
    _validation.validate_summary(args.summary)


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    log = get_logger("atomic_close", debug=args.debug)
    try:
        _validate_or_die(args)
    except ValueError as e:
        log.warning("arg validation failed: %s", e)
        sys.stderr.write(f"arg validation failed: {e}\n")
        return 2
    try:
        mission = _resolve_mission(args.mission_dir)
    except FileNotFoundError as e:
        sys.stderr.write(f"{e}\n")
        return 4

    try:
        if args.resume_id:
            result = resume_close(mission, args.resume_id)
        else:
            request_id = _build_request_id(args.agent)
            if args.dry_run:
                _emit({
                    "ok": True, "dry_run": True, "request_id": request_id,
                    "would_run": ["CLAIM_DIR_DONE", "TASKS_BRACKET",
                                  "HISTORY_APPEND", "STATUS_UPDATE"],
                    "utc": _utc_now(),
                })
                return 0
            result = execute_close(
                mission,
                request_id=request_id,
                task_id=args.task, lane=args.lane, agent=args.agent,
                utc=_utc_now(),
                finding_path=args.finding, severity=args.severity,
                notes=args.notes, summary=args.summary,
            )
    except Exception as exc:  # noqa: BLE001
        log.error("unexpected exception: %s\n%s", exc, traceback.format_exc())
        sys.stderr.write(f"unexpected: {exc}\n")
        return 1

    _emit({k: v for k, v in result.items() if k != "steps"})
    if not result["ok"]:
        # Distinguish "STATUS row owner mismatch" (precondition) from generic partial.
        for step in result["steps"]:
            if step.get("error") and "owner mismatch" in step["error"]:
                return 4
            if step.get("error") and "missing" in step["error"]:
                return 3
            if step.get("error") and "lock" in step["error"].lower():
                return 5
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest --with freezegun pytest scripts/tests/test_atomic_close.py -v
```

Expected: 5/5 PASS.

- [ ] **Step 5: Stage**

```bash
cd /Users/dave/Documents/Projects/megalodon && git add scripts/atomic_close.py scripts/tests/test_atomic_close.py
```

Commit (SKIP unless operator approves):

```bash
git commit -m "feat(m3): add atomic_close.py CLI wrapper"
```

---

## Task 13: `poll.py` CLI

**Files:**
- Create: `scripts/poll.py`
- Create: `scripts/tests/test_poll.py`

- [ ] **Step 1: Write failing tests**

Path: `scripts/tests/test_poll.py`

```python
"""CLI tests for scripts/poll.py."""

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "poll.py"


def _run(mission_dir, *args):
    env = {**os.environ, "PYTHONPATH": str(SCRIPT.resolve().parents[1])}
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--mission-dir", str(mission_dir), *args],
        capture_output=True, text=True, env=env,
    )


def test_full_emits_valid_json_with_required_keys(mission_dir):
    res = _run(mission_dir, "--full")
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    for key in [
        "utc", "mission_dir", "phase", "phase_lock_owner",
        "lanes", "claims", "events_tail", "findings_recent", "partial_journals",
    ]:
        assert key in payload, f"missing key: {key}"


def test_full_returns_six_lanes(mission_dir):
    res = _run(mission_dir, "--full")
    payload = json.loads(res.stdout)
    assert len(payload["lanes"]) == 6


def test_brief_drops_optional_sections(mission_dir):
    res = _run(mission_dir, "--brief")
    payload = json.loads(res.stdout)
    assert "events_tail" not in payload
    assert "findings_recent" not in payload
    assert "partial_journals" not in payload
    assert "lanes" in payload


def test_invalid_mission_dir_exits_4(tmp_path):
    res = _run(tmp_path)  # tmp_path has no STATUS.md
    assert res.returncode == 4


def test_full_includes_partial_journals_when_present(mission_dir, agent):
    import json as J
    jdir = mission_dir / ".scripts-journal"
    jdir.mkdir()
    (jdir / "rid-test.json").write_text(J.dumps({
        "schema_version": 1, "request_id": "rid-test",
        "started_utc": "2026-05-16T22:00:00Z",
        "last_updated_utc": "2026-05-16T22:00:00Z",
        "status": "PARTIAL",
        "task_id": "X", "lane": "AUDIT", "agent": agent,
        "args": {"finding": "f", "severity": "DELTA", "notes": "n", "summary": "s"},
        "steps": [{"step": "CLAIM_DIR_DONE", "ok": False, "error": "missing"}],
    }))
    res = _run(mission_dir, "--full")
    payload = json.loads(res.stdout)
    # rid-test may be > 24h old in real wall clock; just verify the field exists.
    assert "partial_journals" in payload
```

- [ ] **Step 2: Run tests to see them fail**

```bash
cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest --with freezegun pytest scripts/tests/test_poll.py -v
```

Expected: `FileNotFoundError` (poll.py doesn't exist yet).

- [ ] **Step 3: Implement `poll.py`**

Path: `scripts/poll.py`

```python
#!/usr/bin/env python3
"""poll — canonical mission-state read for v9 workers.

Usage:
    python3 scripts/poll.py [--brief | --full]
        [--mission-dir <PATH>] [--events-tail N] [--findings-recent N] [--debug]

Spec: docs/superpowers/specs/2026-05-16-v9-m3-helper-scripts-design.md §5.2
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._logging import get_logger
from scripts._state_read import (
    read_claims, read_events_tail, read_findings_recent,
    read_lanes, read_partial_journals, read_phase,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_args(argv):
    p = argparse.ArgumentParser(prog="poll", description="v9 mission-state read")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--brief", action="store_true")
    mode.add_argument("--full", action="store_true")
    p.add_argument("--mission-dir", default=None)
    p.add_argument("--events-tail", type=int, default=10)
    p.add_argument("--findings-recent", type=int, default=10)
    p.add_argument("--debug", action="store_true")
    return p.parse_args(argv)


def _resolve_mission(arg):
    candidate = Path(arg) if arg else Path.cwd()
    if not (candidate / "STATUS.md").exists() or not (candidate / "TASKS.md").exists():
        raise FileNotFoundError(f"mission dir invalid: {candidate}")
    return candidate.resolve()


def main(argv=None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    log = get_logger("poll", debug=args.debug)
    try:
        mission = _resolve_mission(args.mission_dir)
    except FileNotFoundError as e:
        sys.stderr.write(f"{e}\n")
        return 4

    phase, lock_owner = read_phase(mission)
    payload = {
        "utc": _utc_now(),
        "mission_dir": str(mission),
        "phase": phase,
        "phase_lock_owner": lock_owner,
        "lanes": read_lanes(mission),
        "claims": read_claims(mission),
    }
    if not args.brief:
        payload["events_tail"] = read_events_tail(mission, args.events_tail)
        include_body = bool(args.full)
        payload["findings_recent"] = read_findings_recent(
            mission, args.findings_recent, include_body=include_body,
        )
        payload["partial_journals"] = read_partial_journals(mission)

    sys.stdout.write(json.dumps(payload, indent=2 if args.full else None) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest --with freezegun pytest scripts/tests/test_poll.py -v
```

Expected: 5/5 PASS.

- [ ] **Step 5: Stage**

```bash
cd /Users/dave/Documents/Projects/megalodon && git add scripts/poll.py scripts/tests/test_poll.py
```

Commit (SKIP unless operator approves):

```bash
git commit -m "feat(m3): add poll.py CLI wrapper"
```

---

## Task 14: `run_e2e.sh`

**Files:**
- Create: `scripts/run_e2e.sh`

- [ ] **Step 1: Write the script**

Path: `scripts/run_e2e.sh`

```bash
#!/usr/bin/env bash
# scripts/run_e2e.sh — canonical playwright invocation for v9 workers.
#
# Resolves project root from this script's location; uses `uv run --directory`
# instead of `cd /abs && uv run` (Codex CR-5 hygiene). Forwards all args to
# `playwright test`.
#
# Operator allowlist: `./scripts/run_e2e.sh *`
# Spec: docs/superpowers/specs/2026-05-16-v9-m3-helper-scripts-design.md §8

set -euo pipefail

SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$( cd -- "$SCRIPT_DIR/.." &> /dev/null && pwd )"

exec uv run --directory "$PROJECT_ROOT" \
    --with fastapi --with "uvicorn[standard]" --with sse-starlette --with pyyaml \
    npx playwright test \
    --config ui/tests/e2e/playwright.config.ts \
    "$@"
```

- [ ] **Step 2: Make executable**

```bash
chmod +x /Users/dave/Documents/Projects/megalodon/scripts/run_e2e.sh
```

- [ ] **Step 3: Smoke test — `--help`**

```bash
cd /Users/dave/Documents/Projects/megalodon && ./scripts/run_e2e.sh --help 2>&1 | head -20
```

Expected: Playwright's own `--help` output appears. (If playwright isn't installed via npx, it bootstraps on first run; this may take ~30 seconds first time.)

If `npx playwright` fails entirely, mark this smoke test as deferred and check `which uv` + `which npx` before re-running.

- [ ] **Step 4: Stage**

```bash
cd /Users/dave/Documents/Projects/megalodon && git add scripts/run_e2e.sh
```

Commit (SKIP unless operator approves):

```bash
git commit -m "feat(m3): add run_e2e.sh canonical playwright wrapper"
```

---

## Task 15: `playwright.config.ts` cleanup (Codex CR-5)

**Files:**
- Modify: `ui/tests/e2e/playwright.config.ts:45-58`

- [ ] **Step 1: Edit first webServer command (port 8765)**

Replace in `ui/tests/e2e/playwright.config.ts`:

```javascript
      command: 'cd /Users/dave/Documents/Projects/megalodon && uv run --with fastapi --with "uvicorn[standard]" --with sse-starlette --with pyyaml python3 -m megalodon_ui --port 8765 --mission-dir $MEGALODON_MISSION_DIR_DEFAULT',
```

With:

```javascript
      command: 'uv run --directory /Users/dave/Documents/Projects/megalodon --with fastapi --with "uvicorn[standard]" --with sse-starlette --with pyyaml python3 -m megalodon_ui --port 8765 --mission-dir $MEGALODON_MISSION_DIR_DEFAULT',
```

- [ ] **Step 2: Edit second webServer command (port 8766)**

Same `cd ... && uv run` → `uv run --directory ...` swap for the port-8766 entry (next webServer block down).

- [ ] **Step 3: Smoke — verify config still parses**

```bash
cd /Users/dave/Documents/Projects/megalodon && uv run --with @playwright/test npx playwright test --list --config ui/tests/e2e/playwright.config.ts 2>&1 | head -10
```

Expected: list of test specs (no parse errors).

- [ ] **Step 4: Stage**

```bash
cd /Users/dave/Documents/Projects/megalodon && git add ui/tests/e2e/playwright.config.ts
```

Commit (SKIP unless operator approves):

```bash
git commit -m "chore(playwright): drop cd && uv run for uv run --directory (Codex CR-5)"
```

---

## Task 16: Doc updates — `launch.md` RULES 12-14 + `README.md` allowlist section

**Files:**
- Modify: `launch.md` (add RULES 12-14 to §5)
- Modify: `README.md` (add RULES 12-14 reference + allowlist section)

- [ ] **Step 1: Inspect current launch.md §5**

```bash
grep -n "^## RULE\|^# RULE\|^### " /Users/dave/Documents/Projects/megalodon/launch.md | head -30
```

Note the section where tool-discipline / RULE-X content lives. If launch.md uses a different RULE numbering than README.md, ADD the new content to launch.md's tool-discipline section; the rules will be cross-referenced not duplicated.

- [ ] **Step 2: Append RULES 12-14 to launch.md tool-discipline section**

Add the following block to `launch.md`, after the existing tool-discipline content (use Edit tool with appropriate anchor text):

```markdown
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

- [ ] **Step 3: Append allowlist section + RULES 12-14 reference to README.md**

Add a new section to `README.md` after the existing "How to deploy" section (use Edit tool with anchor `## How to deploy` end-of-section):

```markdown
## Operator allowlist for v9 helper scripts

Workers invoke three scripts that must be wildcard-allowlisted once to prevent
mid-mission permission prompts (SIG-ORCH-6 cause). Add to your Claude Code
permissions (`settings.json` `allow` list or equivalent):

    python3 scripts/atomic_close.py *
    python3 scripts/poll.py *
    ./scripts/run_e2e.sh *

The scripts internally validate ALL args against strict whitelist regexes
(see `docs/superpowers/specs/2026-05-16-v9-m3-helper-scripts-design.md` §6.1).
Any non-conforming arg is rejected with exit code 2 and a stderr explanation.
The wildcard is safe because the scripts — not the allowlist — enforce input safety.

See RULES 12, 13, 14 in `launch.md` §5 for the worker-side discipline these scripts enable.
```

- [ ] **Step 4: Stage**

```bash
cd /Users/dave/Documents/Projects/megalodon && git add launch.md README.md
```

Commit (SKIP unless operator approves):

```bash
git commit -m "docs(m3): add RULES 12-14 + operator allowlist section"
```

---

## Task 17: `.gitignore` update

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Inspect current .gitignore**

```bash
cat /Users/dave/Documents/Projects/megalodon/.gitignore
```

- [ ] **Step 2: Append the three new ignore patterns**

Append to `.gitignore` (use Edit tool, append at end):

```
# v9 M3 helper-script state
.scripts-journal/
scripts/**/__pycache__/
scripts/tests/__pycache__/
```

- [ ] **Step 3: Verify pytest cache is ignored**

```bash
cd /Users/dave/Documents/Projects/megalodon && git status scripts/tests/
```

Expected: no `__pycache__` listed even if pytest was run.

- [ ] **Step 4: Stage**

```bash
cd /Users/dave/Documents/Projects/megalodon && git add .gitignore
```

Commit (SKIP unless operator approves):

```bash
git commit -m "chore: gitignore .scripts-journal and scripts/**/__pycache__"
```

---

## Task 18: Final validation + HISTORY.md M3-COMPLETE append

**Files:**
- Modify: `HISTORY.md` (append M3-COMPLETE entry)

- [ ] **Step 1: Run the entire test suite**

```bash
cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest --with freezegun pytest scripts/tests/ -v
```

Expected: ~41 tests PASS, 0 FAIL, 0 ERROR.

If any test fails, STOP. Diagnose and fix before continuing. Re-run until green.

- [ ] **Step 2: Smoke `python3 scripts/atomic_close.py --help`**

```bash
cd /Users/dave/Documents/Projects/megalodon && python3 scripts/atomic_close.py --help
```

Expected: argparse-generated help text.

- [ ] **Step 3: Smoke `python3 scripts/poll.py` against the live mission dir**

```bash
cd /Users/dave/Documents/Projects/megalodon && python3 scripts/poll.py --brief | python3 -c "import json,sys; print(json.dumps(json.loads(sys.stdin.read()), indent=2)[:500])"
```

Expected: pretty-printed JSON snippet. `lanes` array has 6 entries. `phase` is `PHASE-COMPLETE` (since run-2 closed).

- [ ] **Step 4: Append HISTORY.md M3-COMPLETE entry**

Use Edit tool to append after the last HISTORY row. Use current UTC for `<NOW-UTC>`.

```
<NOW-UTC> | orchestrator | DOC+IMPL | M3-COMPLETE | docs/superpowers/specs/2026-05-16-v9-m3-helper-scripts-design.md + scripts/{atomic_close,poll,run_e2e,_shared_state,_state_read,_logging,_validation}.py + scripts/_backends/direct_fcntl.py + scripts/tests/ (~41 tests pass) + launch.md RULES 12-14 + README.md allowlist section + playwright.config.ts cleanup + .gitignore | MAJOR (v9 M3 marquee shipped per V9-ROADMAP step 3a; operator allowlist still pending operator action; M3→M1 swap is one-line import change in _shared_state.py when M1 lands)
```

- [ ] **Step 5: Stage all remaining changes**

```bash
cd /Users/dave/Documents/Projects/megalodon && git add HISTORY.md && git status
```

Expected: `git status` shows clean tree apart from the HISTORY.md change you just staged (and any prior unstaged changes from earlier tasks if operator chose not to commit incrementally).

- [ ] **Step 6: Operator handoff**

Output to operator:

```
M3 complete.

Next manual step: add to your Claude Code allowlist:
    python3 scripts/atomic_close.py *
    python3 scripts/poll.py *
    ./scripts/run_e2e.sh *

When ready, proceed to M4 (shared constants registry) per V9-ROADMAP Migration plan §3b.
```

Commit (SKIP unless operator approves):

```bash
git commit -m "feat(m3): M3 complete — v9 helper scripts, RULES 12-14, allowlist docs"
```

---

## Self-Review

**Spec coverage check** — every section of `docs/superpowers/specs/2026-05-16-v9-m3-helper-scripts-design.md` mapped to a task:

| Spec § | Content | Task(s) |
|---|---|---|
| §1 Purpose | (informational) | — |
| §2 Scope | 4 items | Tasks 1-18 cover all 4 |
| §3 Locked decisions | (summary table) | — |
| §4 Module layout | 21 files | All Created in Tasks 1-14 |
| §5.1 atomic_close CLI | flags + exit codes | Task 12 |
| §5.2 poll.py CLI + schema | flags + JSON shape + partial_journals | Task 13 |
| §5.3 _shared_state interface | execute_close / resume_close | Tasks 8, 9 |
| §5.4 Journal format | write + retain + cleanup | Tasks 8, 9 |
| §6.1 Validation regexes | CR-4 inventory | Task 2 |
| §6.2 CLAIM_DIR_DONE | claim_dir_done() | Task 4 |
| §6.3 TASKS_BRACKET | tasks_bracket() | Task 5 |
| §6.4 HISTORY_APPEND | history_append() | Task 6 |
| §6.5 STATUS_UPDATE | status_update() | Task 7 |
| §7 _state_read | 6 read functions | Tasks 10, 11 |
| §8 run_e2e.sh | bash wrapper | Task 14 |
| §9 _logging.py | RotatingFileHandler | Task 3 |
| §10 Test strategy | ~41 tests | Tasks 2, 3, 4-11, 12-13 (validation in each) |
| §10.4 Minimal fixture | files | Task 1 |
| §11.1 launch.md grammar | RULES 12-14 | Task 16 |
| §11.2 playwright.config.ts | webServer cleanup | Task 15 |
| §11.3 Operator allowlist | README section | Task 16 |
| §12 File manifest | Created 21 / Modified 5 | All tasks |
| §13 Risks | (informational; no implementation) | — |
| §14 Implementation order | 12 logical steps | Tasks 1-18 follow this order |
| §15 Definition of done | 7 criteria | Task 18 |
| §16 References | (citation list) | — |

All spec sections covered.

**Placeholder scan**: no TBDs, no "TODO" without code, no "Similar to Task N" without repeated code. Every code step contains the actual code to write or the exact regex/string change. Exit code 1 ("unexpected exception") is acknowledged as not deliberately reachable in tests — that's a deliberate choice documented in the spec, not a placeholder.

**Type consistency check**:
- `StepResult` keys (`step`, `ok`, `target_file`, `pre_hash`, `post_hash`, `duration_ms`, `idempotent`, `error`) used identically in Tasks 4-7 (backend functions) and Tasks 8-9 (orchestrator that consumes them). ✓
- `CloseResult` keys (`request_id`, `ok`, `completed`, `failed_step`, `steps`, `resume_hint`) used identically in Tasks 8 (execute_close), 9 (resume_close), 12 (atomic_close.py CLI that emits subset of them). ✓
- Lane long-form (`AUDIT`, etc.) flows: `_validation.LANE_LONG_TO_SHORT` (Task 2) → consumed by `_shared_state._run_step` for `history_append` lane_short arg (Task 8). ✓
- `read_partial_journals` schema fields (`request_id`, `started_utc`, `last_updated_utc`, `task_id`, `lane`, `agent`, `completed_steps`, `failed_step`, `error`, `age_seconds`, `resume_hint`) match spec §5.2 JSON example. ✓

Plan complete and saved to `docs/superpowers/plans/2026-05-16-v9-m3-helper-scripts.md`.

---

## Execution Handoff

Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration. Uses superpowers:subagent-driven-development.

**2. Inline Execution** — execute tasks in this session using superpowers:executing-plans, batch execution with checkpoints for review.

Operator to choose.
