# Agent Tool-Surface Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove every unbounded interpreter (`python`/`python3`, `bash -c`, compound chains, general `curl`) from the spawned-fleet `--allowedTools` allowlist, route the four interpreter-backed bootstrap operations through bounded path-scoped tools, rewrite `launch.md` to direct agents only to those tools, and add an enforcement test that fails if any unbounded pattern returns.

**Architecture:** The fleet's permission surface is a single space-separated `--allowedTools` string built in `megalodon_ui/harnesses/claude.py` (`build_argv`, `live_repl=True` branch, lines 83-126). Narrowing that string is the core change. Two new path-scoped scripts (`scripts/claim.sh`, `scripts/queue_submit.py`) replace the two operations that today require `mkdir … && echo …` and `python -m …`. Agent-ID generation already has a spawn-time placeholder-bake mechanism in `megalodon_ui/spawn.py` (`_bake_agent_id_in_launch_file`), so the `launch.md` `python3 -c` blocks are replaced by the existing `{{AGENT_ID}}` placeholder rather than new generator code. The existing harness test (`scripts/tests/test_harness_claude.py`) is the keystone: its assertions are inverted from "broad surface present" to "unbounded patterns absent."

**Tech Stack:** Python 3 (stdlib only for the new scripts), bash, pytest (`uv run --extra test pytest`), Claude Code CLI `--allowedTools`.

## Threat model (operator-confirmed 2026-05-22)

This policy targets **(a) approval-prompt friction** during bootstrap and **(b) accidental or operator re-admission of `python`** (the "approve & remember" loop that broadened the surface in the v94 dogfood). It is **NOT a security sandbox**: agents are trusted Claude instances doing authorized dev work. `Edit`/`Write` are auto-approved and `run_tests.sh`/pytest execute arbitrary project Python by design, so a *hostile* agent could rewrite an allowlisted script or drop a malicious `conftest.py`. Per Claude Code docs, Read/Edit deny rules "do not apply to arbitrary subprocesses … like a Python or Node script"; true isolation requires OS-level sandboxing (`code.claude.com/docs/en/sandboxing`), which is **explicitly out of scope** here and tracked separately if ever needed. The "bounded" language in this plan means *bounded against friction and accidental re-admission*, not *bounded against a malicious agent*.

## Dependency graph (for parallel execution — CV-10)

- Tasks **1, 2, 3.5, 7** are independent → start in parallel.
- Task **3** depends on Task 1 (`queue_client.main`).
- Task **4** (allowlist + filter + keystone test) is the join: depends on 2, 3, 3.5 (the scripts it allowlists must exist).
- Task **5** (launch.md) depends on 2, 3, 3.5 existing; Task **6** (lint) depends on 5.
- Task **8** (validation + docs + manual gate) is last.

## Execution notes (pre-mortem mitigations)

- **Line numbers are approximate anchors.** Cited ranges (`claude.py:62-133`, `launch.md:51-63`, …) drift as edits accumulate. Locate every edit by its **section heading or a unique nearby string**, not by line number.
- **Task 4 TDD ordering (PM-1/SR-1):** the keystone test's `_is_unbounded_tool` / `_FORBIDDEN_HEAD_CMDS` imports are **function-local** (inside the test bodies), so pytest *collection* still succeeds at Step 2 — the new-symbol tests error RED and the contract test fails RED, both the expected pre-implementation state. Write the test (Step 1) and the implementation (Step 3) within the **same task and commit**.

---

## Deviations from the approved spec (research-driven; resolved at self-contrarian gate 2026-05-22)

The spec was approved before code-level research. Three of its six areas changed once the code was read, and the self-contrarian gate resolved the resulting design questions (operator decisions recorded below).

1. **Agent-ID (spec area 4): use the existing spawn-time placeholder bake — DROP spec area 4.** [Resolved O-1] `megalodon_ui/spawn.py:377-387` already substitutes a `{{AGENT_ID}}` placeholder with a freshly generated id at spawn (tested by `test_agent_id_prebake.py`). `launch.md` simply never contains the placeholder — it tells agents to run `python3 -c` instead. The fix puts `{{AGENT_ID}}` into `launch.md` and lets the existing bake fill it. The bake uses a *random* id; the baked value is written to disk and survives crash/recompact (the agent re-reads the file), which is all determinism ever provided here. `scripts/gen_lane_launches.py` is **unchanged**.

2. **Run-seed (spec area 6) is near-moot.** `scripts/new_run.sh` creates `.fleet/` but seeds **no** `approval-rules.json`, and no such template exists under `templates/run/`. Area 6 reduces to a guard test (Task 8) that no broad seed is introduced. No `new_run.sh` code change.

3. **The "keystone" enforcement test already exists.** `scripts/tests/test_harness_claude.py:67-138` currently asserts the **old broad surface as required-present** (`Bash(cat:*)`, `Bash(echo:*)`, `Bash(curl -s http://127.0.0.1*)` …). Narrowing the allowlist *breaks* this test. The plan **inverts** these assertions (Task 5) rather than creating a brand-new test file.

4. **Bounded non-interpreter utilities ARE included.** [Resolved O-2] `Bash(sleep:*)`, `Bash(date:*)`, `Bash(printf:*)` — needed for stagger-wait, UTC heartbeat stamps, and terminal title. Non-interpreters; no `-exec` escape.

5. **Tests run via a new `scripts/run_tests.sh` wrapper, NOT bare `pytest`.** [Resolved OW-2] `pyproject.toml` puts `freezegun`/`pytest-asyncio`/`pytest-forked` behind the `test` extra; bare `pytest` would fail on missing deps, and `uv run --extra test pytest` is forbidden. New `scripts/run_tests.sh` (mirrors `run_e2e.sh`) execs `uv run --extra test pytest "$@"`; allowlist `Bash(scripts/run_tests.sh:*)`; **drop** `Bash(pytest:*)`. (Tasks 3.5 + 4.)

6. **PM-8 runtime approval-rules append is FILTERED.** [Resolved PW-1] `claude.py` strips any interpreter/curl/compound pattern from `extra_allowed_tools` before appending, so an operator "approve & remember" can never silently re-admit `python` via `.fleet/approval-rules.json`. (Task 4.)

7. **`claim.sh` vs RULE-15 queue routing.** `claim.sh` does a *local filesystem mutex* (`mkdir claims/<id>/` + `owner.txt`) — distinct from shared-*document* mutations (STATUS/TASKS/HISTORY) which still flow through the queue. The claims/ directory is a pre-queue filesystem mutex. `launch.md` documents the distinction.

**Out of scope:** `.claude/settings.json` (`test_settings_friction_allowlist.py`) governs the *operator's own* interactive Claude sessions, a separate allowlist from the fleet harness string in `claude.py`. Not touched by this plan.

---

## File Structure

**Modified:**
- `megalodon_ui/harnesses/claude.py` — narrow the `live_repl` `--allowedTools` string (lines 62-126), add the `_is_unbounded_tool` filter for the PM-8 `extra_allowed_tools` append (lines 127-133).
- `megalodon_ui/queue/queue_client.py` — extract the inline `__main__` argparse block (lines 337-401) into `def main(argv: list[str]) -> int:` so it is importable by the wrapper.
- `launch.md` — rewrite Steps 2, 4, §5.A, RULE-15, and remove the dead Python+fcntl carve-out.
- `scripts/tests/test_harness_claude.py` — invert the allowlist assertions + add the PM-8 filter test (the keystone enforcement test).

**Created:**
- `scripts/claim.sh` — bounded `claims/` directory-mutex tool.
- `scripts/queue_submit.py` — bounded shebang wrapper over `queue_client.main`.
- `scripts/run_tests.sh` — bounded test runner (`uv run --extra test pytest "$@"`).
- `scripts/tests/test_claim_sh.py` — claim.sh behavior tests.
- `scripts/tests/test_queue_submit.py` — wrapper forwarding tests.
- `scripts/tests/test_launch_protocol_no_interpreters.py` — launch.md lint (no forbidden tokens).

**Unchanged (deviations 1, 2):** `scripts/gen_lane_launches.py`, `scripts/new_run.sh` (no code change; covered by a guard test only).

---

## Task 1: Extract a callable `main()` in queue_client

The wrapper (Task 3) cannot `import` the CLI because it lives inline under `if __name__ == "__main__"`. Extract it first.

**Files:**
- Modify: `megalodon_ui/queue/queue_client.py:337-401`
- Test: `scripts/tests/test_queue_client.py` (existing — add one case)

- [ ] **Step 1: Write the failing test**

Add to `scripts/tests/test_queue_client.py`:

```python
def test_main_callable_status_forwards(tmp_path, monkeypatch):
    """queue_client.main(argv) is importable and forwards to status_update."""
    import megalodon_ui.queue.queue_client as qc

    captured = {}

    def fake_status_update(**kwargs):
        captured.update(kwargs)
        return "req-123"

    monkeypatch.setattr(qc, "status_update", fake_status_update)
    rc = qc.main(
        [
            "--mission-dir", str(tmp_path),
            "--agent", "agent-abcd",
            "--lane", "BACKEND",
            "status", "--state", "idle", "--notes", "hb",
        ]
    )
    assert rc == 0
    assert captured["new_state"] == "idle"
    assert captured["agent"] == "agent-abcd"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest scripts/tests/test_queue_client.py::test_main_callable_status_forwards -v`
Expected: FAIL with `AttributeError: module 'megalodon_ui.queue.queue_client' has no attribute 'main'`

- [ ] **Step 3: Refactor `__main__` into `main(argv)`**

Replace the block at `megalodon_ui/queue/queue_client.py:337-401` with:

```python
# ---- CLI for shell-friendly invocations (handles cas_write use case directly) ----

def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Megalodon v9 queue client")
    p.add_argument("--mission-dir", required=True, type=Path)
    p.add_argument("--agent", required=True)
    p.add_argument("--lane", required=True)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("status")
    sp.add_argument("--state", required=True)
    sp.add_argument("--utc", default=None)
    sp.add_argument("--notes", required=True)

    sp = sub.add_parser("claim")
    sp.add_argument("--task", required=True)

    sp = sub.add_parser("done")
    sp.add_argument("--task", required=True)

    sp = sub.add_parser("history")
    sp.add_argument("--task", required=True)
    sp.add_argument("--finding", required=True)
    sp.add_argument("--severity", required=True)

    sp = sub.add_parser("event")
    sp.add_argument("--line", required=True)

    sp = sub.add_parser("claim-dir")
    sp.add_argument("--task", required=True)

    sp = sub.add_parser("claim-done")
    sp.add_argument("--task", required=True)

    args = p.parse_args(argv)
    common = dict(mission_dir=args.mission_dir, agent=args.agent, lane=args.lane)

    if args.cmd == "status":
        rid = status_update(
            **common, new_state=args.state, new_utc=args.utc, new_notes=args.notes
        )
    elif args.cmd == "claim":
        rid = task_claim(**common, task_id=args.task)
    elif args.cmd == "done":
        rid = task_done(**common, task_id=args.task)
    elif args.cmd == "history":
        rid = history_append(
            **common,
            task_id=args.task,
            finding_path=args.finding,
            severity=args.severity,
        )
    elif args.cmd == "event":
        rid = mission_event(**common, line=args.line)
    elif args.cmd == "claim-dir":
        rid = claim_dir_create(**common, task_id=args.task)
    elif args.cmd == "claim-done":
        rid = claim_dir_done(**common, task_id=args.task)
    else:
        p.print_help()
        return 2

    print(rid)
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra test pytest scripts/tests/test_queue_client.py -v`
Expected: PASS (new test + all existing queue_client tests)

- [ ] **Step 5: Commit**

```bash
git add megalodon_ui/queue/queue_client.py scripts/tests/test_queue_client.py
git commit -m "refactor(queue): extract queue_client.main(argv) for path-scoped wrapper"
```

---

## Task 2: Create `scripts/claim.sh` (bounded claims/ mutex)

**Files:**
- Create: `scripts/claim.sh`
- Test: `scripts/tests/test_claim_sh.py`

- [ ] **Step 1: Write the failing test**

Create `scripts/tests/test_claim_sh.py`:

```python
"""Tests for scripts/claim.sh — the bounded claims/ directory mutex."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CLAIM = REPO / "scripts" / "claim.sh"


def _run(task_id, agent, cwd):
    # Invoke the REAL command shape agents use (Bash(scripts/claim.sh:*)),
    # i.e. direct exec relying on the shebang + executable bit — NOT `bash <f>`,
    # so a missing chmod/shebang fails here (CR-6).
    return subprocess.run(
        [str(CLAIM), task_id, agent],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def test_claim_sh_is_executable():
    import os, stat
    assert os.stat(CLAIM).st_mode & stat.S_IXUSR, "claim.sh missing executable bit"


def test_claim_creates_dir_and_owner(tmp_path):
    (tmp_path / "claims").mkdir()
    r = _run("P1-A", "agent-abcd", tmp_path)
    assert r.returncode == 0, r.stderr
    owner = tmp_path / "claims" / "P1-A" / "owner.txt"
    assert owner.exists()
    assert owner.read_text().strip() == "agent-abcd"


def test_idempotent_same_agent(tmp_path):
    (tmp_path / "claims").mkdir()
    assert _run("P1-A", "agent-abcd", tmp_path).returncode == 0
    r = _run("P1-A", "agent-abcd", tmp_path)
    assert r.returncode == 0, r.stderr


def test_conflict_different_agent_fails(tmp_path):
    (tmp_path / "claims").mkdir()
    assert _run("P1-A", "agent-aaaa", tmp_path).returncode == 0
    r = _run("P1-A", "agent-bbbb", tmp_path)
    assert r.returncode != 0
    # owner.txt unchanged
    assert (tmp_path / "claims" / "P1-A" / "owner.txt").read_text().strip() == "agent-aaaa"


def test_path_traversal_rejected(tmp_path):
    (tmp_path / "claims").mkdir()
    r = _run("../escape", "agent-abcd", tmp_path)
    assert r.returncode != 0
    assert not (tmp_path / "escape").exists()


def test_missing_args_exit_nonzero(tmp_path):
    r = subprocess.run(
        [str(CLAIM), "P1-A"], cwd=str(tmp_path), capture_output=True, text=True
    )
    assert r.returncode != 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest scripts/tests/test_claim_sh.py -v`
Expected: FAIL (claim.sh does not exist → non-zero from bash "No such file")

- [ ] **Step 3: Write `scripts/claim.sh`**

```bash
#!/usr/bin/env bash
# claim.sh — bounded claims/ directory mutex for Megalodon workers.
#
# Usage: scripts/claim.sh <task-id> <agent-id>
#
# Atomically claims a task by creating claims/<task-id>/ (mkdir is the mutex)
# and writing <agent-id> to claims/<task-id>/owner.txt. Run from the mission
# directory (cwd contains claims/).
#
# Exit codes:
#   0  claimed (or idempotent re-claim by the same agent)
#   2  argument / validation error (missing args, bad task-id)
#   3  already claimed by a DIFFERENT agent
#
# This is the ONLY sanctioned claims/ mutation path. It is a local filesystem
# mutex, distinct from RULE-15 queue-routed shared-DOCUMENT mutations.
set -euo pipefail

TASK_ID="${1:-}"
AGENT_ID="${2:-}"

if [[ -z "$TASK_ID" || -z "$AGENT_ID" ]]; then
  echo "usage: claim.sh <task-id> <agent-id>" >&2
  exit 2
fi

# Reject anything that isn't a flat, safe task-id (blocks path traversal).
if [[ ! "$TASK_ID" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "claim.sh: invalid task-id '$TASK_ID' (must match [A-Za-z0-9._-]+)" >&2
  exit 2
fi

CLAIM_DIR="claims/$TASK_ID"
OWNER="$CLAIM_DIR/owner.txt"

if [[ -d "$CLAIM_DIR" ]]; then
  # Already exists — idempotent only if the same agent owns it.
  if [[ -f "$OWNER" ]] && [[ "$(cat "$OWNER")" == "$AGENT_ID" ]]; then
    exit 0
  fi
  echo "claim.sh: $TASK_ID already claimed by $(cat "$OWNER" 2>/dev/null || echo '?')" >&2
  exit 3
fi

# mkdir is the atomic mutex: two racing callers — exactly one wins the create.
if mkdir "$CLAIM_DIR" 2>/dev/null; then
  printf '%s' "$AGENT_ID" > "$OWNER"
  exit 0
fi

# Lost the race between the -d check and mkdir: re-evaluate ownership.
if [[ -f "$OWNER" ]] && [[ "$(cat "$OWNER")" == "$AGENT_ID" ]]; then
  exit 0
fi
echo "claim.sh: $TASK_ID claimed concurrently by another agent" >&2
exit 3
```

- [ ] **Step 4: Make executable and run test to verify it passes**

```bash
chmod +x scripts/claim.sh
```
Run: `uv run --extra test pytest scripts/tests/test_claim_sh.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/claim.sh scripts/tests/test_claim_sh.py
git commit -m "feat(scripts): add claim.sh bounded claims/ mutex (replaces mkdir && echo)"
```

---

## Task 3: Create `scripts/queue_submit.py` (bounded queue wrapper)

**Files:**
- Create: `scripts/queue_submit.py`
- Test: `scripts/tests/test_queue_submit.py`
- Depends on: Task 1 (`queue_client.main`)

- [ ] **Step 1: Write the failing test**

Create `scripts/tests/test_queue_submit.py`:

```python
"""Tests for scripts/queue_submit.py — path-scoped wrapper over queue_client.main."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "scripts" / "queue_submit.py"


def test_wrapper_is_executable():
    import os, stat
    assert os.stat(WRAPPER).st_mode & stat.S_IXUSR, "queue_submit.py missing executable bit"


def test_help_via_direct_exec_exits_zero():
    """Real command shape: direct exec via shebang (Bash(scripts/queue_submit.py:*)),
    NOT `python wrapper.py` — so a missing chmod/shebang fails here (CR-6)."""
    r = subprocess.run([str(WRAPPER), "--help"], capture_output=True, text=True)
    assert r.returncode == 0
    assert "queue" in r.stdout.lower()


def test_missing_required_args_exit_nonzero():
    # No --mission-dir/--agent/--lane → argparse error (exit 2).
    r = subprocess.run([sys.executable, str(WRAPPER)], capture_output=True, text=True)
    assert r.returncode != 0


def test_forwards_to_queue_client_main(monkeypatch):
    """mod.main forwards its argv verbatim to queue_client.main."""
    sys.path.insert(0, str(REPO))
    import importlib

    qsub = importlib.import_module("scripts.queue_submit")
    import megalodon_ui.queue.queue_client as qc

    seen = {}
    monkeypatch.setattr(qc, "main", lambda argv: (seen.update(argv=argv), 0)[1])
    # queue_submit binds `_qc_main` at import; patch the bound name too.
    monkeypatch.setattr(qsub, "_qc_main", qc.main)

    rc = qsub.main(["--mission-dir", "/tmp/m", "--agent", "a", "--lane", "BACKEND",
                    "status", "--state", "idle", "--notes", "hb"])
    assert rc == 0
    assert seen["argv"][:6] == ["--mission-dir", "/tmp/m", "--agent", "a", "--lane", "BACKEND"]
```

> Note: `scripts/queue_submit.py` must be importable as `scripts.queue_submit` (the `scripts/` package has `__init__.py`). The wrapper binds `_qc_main = main` at import, so the test re-patches `qsub._qc_main` after patching `qc.main`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest scripts/tests/test_queue_submit.py -v`
Expected: FAIL (file does not exist)

- [ ] **Step 3: Write `scripts/queue_submit.py`**

```python
#!/usr/bin/env python3
"""queue_submit — path-scoped queue-intent submission for v9 workers.

Thin wrapper over megalodon_ui.queue.queue_client.main so agents can submit
queue intents via an allowlisted PATH (Bash(scripts/queue_submit.py:*)) instead
of `python -m megalodon_ui.queue.queue_client` (an unbounded `python -m`).

Usage (identical to queue_client CLI):
    scripts/queue_submit.py --mission-dir <PATH> --agent <ID> --lane <LANE> \\
        <status|claim|done|history|event|claim-dir|claim-done> [subcommand args]

Exit codes: forwarded from queue_client.main (0 ok, 2 arg error).

Spec: docs/superpowers/specs/2026-05-22-agent-tool-surface-policy-design.md
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `scripts/queue_submit.py` from project root without install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from megalodon_ui.queue.queue_client import main as _qc_main


def main(argv: list[str] | None = None) -> int:
    return _qc_main(argv if argv is not None else sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Make executable and run test to verify it passes**

```bash
chmod +x scripts/queue_submit.py
```
Run: `uv run --extra test pytest scripts/tests/test_queue_submit.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/queue_submit.py scripts/tests/test_queue_submit.py
git commit -m "feat(scripts): add queue_submit.py path-scoped wrapper (replaces python -m)"
```

---

## Task 3.5: Create `scripts/run_tests.sh` (bounded test runner)

Bare `pytest` lacks the `test`-extra deps (`freezegun`, `pytest-asyncio`, `pytest-forked`); `uv run --extra test pytest` is the canonical command but `uv run` is forbidden. This wrapper is the bounded path, mirroring `run_e2e.sh`.

**Files:**
- Create: `scripts/run_tests.sh`
- Test: `scripts/tests/test_run_tests_sh.py`

- [ ] **Step 1: Write the failing test**

Create `scripts/tests/test_run_tests_sh.py`:

```python
"""Tests for scripts/run_tests.sh — the bounded test runner."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUN_TESTS = REPO / "scripts" / "run_tests.sh"


def test_exists_and_executable():
    assert RUN_TESTS.exists()
    assert os.stat(RUN_TESTS).st_mode & stat.S_IXUSR


def test_invokes_uv_directory_extra_test_pytest():
    text = RUN_TESTS.read_text()
    assert "uv run --directory" in text and "--extra test pytest" in text
    assert "BASH_SOURCE" in text  # mirrors run_e2e.sh root resolution (CV-8)
    assert '"$@"' in text          # forwards args, execs (no trailing commands)


def test_collect_only_smoke():
    """Wrapper drives pytest collection via its REAL command shape (direct exec)."""
    r = subprocess.run(
        [str(RUN_TESTS), "--collect-only", "-q",
         "scripts/tests/test_run_tests_sh.py"],
        cwd=str(REPO), capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, r.stderr
    assert "test_invokes_uv_directory_extra_test_pytest" in r.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest scripts/tests/test_run_tests_sh.py -v`
Expected: FAIL (file does not exist)

- [ ] **Step 3: Write `scripts/run_tests.sh`**

```bash
#!/usr/bin/env bash
# run_tests.sh — bounded pytest runner for Megalodon workers.
#
# Resolves project root from this script's location and uses `uv run --directory`
# (mirrors scripts/run_e2e.sh house style — CV-8). The `test` extra carries
# freezegun/pytest-asyncio/pytest-forked. `uv run` is NOT allowlisted for agents;
# this wrapper IS (Bash(scripts/run_tests.sh:*)), giving the TEST lane and
# self-verifying lanes a bounded path to the full suite.
#
# Usage: scripts/run_tests.sh [pytest args...]
set -euo pipefail

SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$( cd -- "$SCRIPT_DIR/.." &> /dev/null && pwd )"

exec uv run --directory "$PROJECT_ROOT" --extra test pytest "$@"
```

- [ ] **Step 4: Make executable and run test to verify it passes**

```bash
chmod +x scripts/run_tests.sh
```
Run: `uv run --extra test pytest scripts/tests/test_run_tests_sh.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/run_tests.sh scripts/tests/test_run_tests_sh.py
git commit -m "feat(scripts): add run_tests.sh bounded test runner (replaces bare pytest)"
```

---

## Task 4: Narrow the harness allowlist + filter PM-8 + invert the keystone test

This is the security-critical change. Do the test inversion, the allowlist edit, and the PM-8 filter together so the test defines the new contract. Depends on Tasks 2, 3, 3.5 (the scripts the allowlist references must exist).

**Files:**
- Modify: `megalodon_ui/harnesses/claude.py:62-133` (allowlist string + `_is_unbounded_tool` filter)
- Modify: `scripts/tests/test_harness_claude.py:67-138`

- [ ] **Step 1: Rewrite the keystone test to assert the NEW contract**

Replace `test_build_argv_live_repl_omits_print_and_prompt` (`scripts/tests/test_harness_claude.py:67-138`) with:

```python
def test_build_argv_live_repl_omits_print_and_prompt():
    """live_repl=True returns REPL argv with a BOUNDED --allowedTools surface.

    Policy (2026-05-22 tool-surface): no unbounded interpreter is allowlisted.
    Agents reach every operation through native tools or path-scoped scripts.
    """
    argv, env = ADAPTER.build_argv(
        "ignored-because-repl-takes-input-via-send-keys",
        model="claude-opus-4-7",
        cwd=Path("/tmp"),
        live_repl=True,
    )
    assert argv[:2] == ["claude", "--model"]
    assert "claude-opus-4-7" in argv
    assert "--print" not in argv
    assert env == {}
    assert "--allowedTools" in argv
    allowed = argv[argv.index("--allowedTools") + 1]

    # --- ALLOWED: native tools ---
    for tool in ["Read", "Edit", "Write", "Grep", "Glob", "ScheduleWakeup"]:
        assert tool in allowed, f"missing native tool: {tool}"

    # --- ALLOWED: path-scoped scripts (the only mutation/inspection paths) ---
    for pat in [
        "Bash(scripts/poll.py:*)",
        "Bash(scripts/atomic_close.py:*)",
        "Bash(scripts/claim.sh:*)",
        "Bash(scripts/queue_submit.py:*)",
        "Bash(scripts/run_e2e.sh:*)",
        "Bash(./scripts/run_e2e.sh:*)",
        "Bash(scripts/run_tests.sh:*)",
    ]:
        assert pat in allowed, f"missing bounded tool: {pat}"

    # --- NOT explicitly allowlisted: git is auto-run read-only by Claude;
    #     explicit Bash(git diff*) would broaden to `git diff --output=<file>`
    #     writes (CR-5/CR-7). Any explicit git pattern is a regression. ---
    assert "Bash(git" not in allowed, "explicit git patterns must be dropped (CR-5/CR-7)"

    # --- ALLOWED: bounded non-interpreter utilities (O-2) ---
    for pat in ["Bash(sleep:*)", "Bash(date:*)", "Bash(printf:*)"]:
        assert pat in allowed, f"missing bounded utility: {pat}"

    # --- NEVER ALLOWED: unbounded interpreters / escapes (the keystone guard) ---
    forbidden_substrings = [
        "Bash(python",       # python / python3 -c / -m
        "Bash(uv run",       # uv run … python -c
        "Bash(bash",         # bash -c
        "Bash(sh ",          # sh -c
        "Bash(eval",
        "Bash(curl",         # NO curl at all (queue now via queue_submit.py)
        "Bash(wget",
        "Bash(find:*)",      # find -exec
        "Bash(*)",
        "Bash(rm:*)",
        "Bash(git branch",   # git branch <name> mutates
        "Bash(cat:*)",       # inspection → poll.py / Read
        "Bash(ls:*)",
        "Bash(grep:*)",
        "Bash(echo:*)",      # echo > file was the old claim write-path
        "Bash(npx",
        "Bash(npm",
    ]
    for bad in forbidden_substrings:
        assert bad not in allowed, f"FORBIDDEN pattern leaked into allowlist: {bad}"

    # --- No bare compound-chain operators baked into any pattern ---
    assert "&&" not in allowed
    assert "| " not in allowed


def test_allowlist_has_no_compound_or_interpreter_tokens():
    """Regression guard: the base allowlist must never re-admit an interpreter."""
    argv, _ = ADAPTER.build_argv(
        "x", model="claude-opus-4-7", cwd=Path("/tmp"), live_repl=True
    )
    allowed = argv[argv.index("--allowedTools") + 1]
    lowered = allowed.lower()
    for token in ["python", "bash -c", "sh -c", "eval", "curl", "wget", "npx", "npm"]:
        assert token not in lowered, f"interpreter/network token in allowlist: {token}"


def test_pm8_extra_allowed_tools_filters_unbounded_patterns():
    """An operator approval-rule that names an interpreter/destructive/compound
    command is dropped, not appended. Bounded scripts/ paths are kept."""
    extra = [
        "Bash(python3:*)",            # interpreter — dropped
        "Bash( python3:*)",           # leading space (CV-6) — still dropped
        "Bash(uv run:*)",             # interpreter launcher — dropped
        "Bash(curl http://evil*)",    # network — dropped
        "Bash(find:*)",               # find (CV-3) — dropped
        "Bash(rm -rf /)",             # destructive (CV-3) — dropped
        "Bash(sudo systemctl x)",     # destructive — dropped
        "Bash(echo x; curl evil)",    # compound ';' (CR-4) — dropped
        "Bash(echo a & evil)",        # background '&' (CR-4) — dropped
        "Bash(scripts/custom_tool.sh:*)",      # bounded path-scoped — KEPT
        "Bash(scripts/findings_report.sh:*)",  # 'find' prefix must NOT false-trip — KEPT
    ]
    argv, _ = ADAPTER.build_argv(
        "x", model="claude-opus-4-7", cwd=Path("/tmp"),
        live_repl=True, extra_allowed_tools=extra,
    )
    allowed = argv[argv.index("--allowedTools") + 1]
    assert "Bash(scripts/custom_tool.sh:*)" in allowed
    assert "Bash(scripts/findings_report.sh:*)" in allowed
    for bad in ["python3", "uv run", "curl", "Bash(find:*)", "rm -rf", "sudo", "; curl", " & evil"]:
        assert bad not in allowed, f"unbounded pattern leaked: {bad}"


def test_is_unbounded_tool_unit():
    """Direct coverage of the filter predicate, incl. boundary cases."""
    from megalodon_ui.harnesses.claude import _is_unbounded_tool
    for p in ["Bash(python3:*)", "Bash( python3:*)", "Bash(uv run:*)",
              "Bash(find:*)", "Bash(rm -rf /)", "Bash(curl x)", "Bash(a && b)",
              "Bash(a | b)", "Bash(a & b)", "Bash(./python3 x)"]:
        assert _is_unbounded_tool(p) is True, p
    for p in ["Bash(scripts/findings_report.sh:*)", "Bash(scripts/poll.py:*)",
              "Bash(sleep:*)", "Read", "Edit", "Bash(scripts/run_tests.sh:*)"]:
        assert _is_unbounded_tool(p) is False, p


def test_forbidden_constants_are_single_source(monkeypatch):
    """DRY (CV-9): the contract test asserts against claude.py's exported
    forbidden heads, so the filter and the test can't drift."""
    from megalodon_ui.harnesses.claude import _FORBIDDEN_HEAD_CMDS
    assert "python" in _FORBIDDEN_HEAD_CMDS and "uv run" in _FORBIDDEN_HEAD_CMDS
    argv, _ = ADAPTER.build_argv("x", model="claude-opus-4-7", cwd=Path("/tmp"), live_repl=True)
    allowed = argv[argv.index("--allowedTools") + 1].lower()
    # No forbidden head appears as a Bash(<head> ...) token in the base allowlist.
    import re
    heads = [m.group(1) for m in re.finditer(r"bash\(([^):*]+)", allowed)]
    for h in heads:
        h = h.strip().lstrip("./")
        if h.startswith("scripts/"):
            continue
        assert not any(h.startswith(c) for c in _FORBIDDEN_HEAD_CMDS), f"forbidden head in base: {h}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest scripts/tests/test_harness_claude.py -v`
Expected: FAIL — the current broad allowlist still contains `Bash(cat:*)`, `Bash(curl …)`, `Bash(git branch*)`, etc.

- [ ] **Step 3: Narrow the allowlist in `claude.py`**

Replace `megalodon_ui/harnesses/claude.py:62-126` (the comment block + the `allowed = ( … )` assignment) with:

```python
            # --allowedTools policy (2026-05-22 tool-surface hardening):
            #
            # PRINCIPLE: never allowlist an unbounded interpreter. Every agent
            # operation reaches a native tool or a dedicated, path-scoped script.
            # Origin: v94-ui-dogfood approval-friction finding + operator
            # constraint "i am not approving python".
            #
            # AUTO-APPROVED (no operator prompt):
            #  * Native tools: Read/Edit/Write/Grep/Glob, ScheduleWakeup, Task*.
            #    All file reads + ad-hoc inspection go through Read/Grep.
            #  * Path-scoped scripts (the sanctioned shell mutation/inspection
            #    paths — added here; they are NOT in the pre-policy allowlist):
            #      poll.py (state inspection), atomic_close.py (RULE-10 close),
            #      claim.sh (claims/ mutex), queue_submit.py (queue intents),
            #      run_e2e.sh (Playwright), run_tests.sh (full pytest suite).
            #  * Bounded non-interpreter utilities: sleep/date/printf (stagger
            #    wait, UTC stamp, terminal title — no code-exec, no -exec escape).
            #
            # DELIBERATELY NOT LISTED (Claude auto-runs these read-only builtins
            # without a prompt in every mode — code.claude.com/docs/en/permissions):
            #  * cat/ls/grep/find/head/tail/wc/echo/pwd/which/diff/stat AND
            #    read-only git (status/diff/log/show/rev-parse/ls-files). Listing
            #    them is redundant, and an explicit `Bash(git diff*)` would BROADEN
            #    to write-forms like `git diff --output=<file>` (CR-5). Write-form
            #    git (branch/commit/push) and write-form builtins still prompt.
            #
            # PERMANENTLY OFF THE ALLOWLIST (surface to operator if ever needed):
            #  * python / python3 / uv run / bare pytest (arbitrary code or
            #    missing test-extra deps — tests run via run_tests.sh)
            #  * bash -c / sh -c / eval / compound chains (&& | ; & newline)
            #  * curl / wget / ssh / scp (queue now via queue_submit.py)
            #  * find (-exec), rm/sudo/chmod/dd, installers
            allowed = (
                # Claude-native tools
                "Read Edit Write Grep Glob "
                "ScheduleWakeup TaskCreate TaskUpdate TaskGet TaskList TaskOutput "
                # Path-scoped scripts — the sanctioned shell paths
                "Bash(scripts/poll.py:*) Bash(scripts/atomic_close.py:*) "
                "Bash(scripts/claim.sh:*) Bash(scripts/queue_submit.py:*) "
                "Bash(scripts/run_e2e.sh:*) Bash(./scripts/run_e2e.sh:*) "
                "Bash(scripts/run_tests.sh:*) "
                # Bounded non-interpreter utilities (read-only git + cat/ls/grep
                # are NOT listed — Claude auto-runs read-only builtins regardless)
                "Bash(sleep:*) Bash(date:*) Bash(printf:*)"
            )
```

Then replace the existing PM-8 append (lines 127-133) so operator approval-rules cannot re-admit an interpreter:

```python
            # PM-8: append operator-approved patterns from .fleet/approval-rules.json,
            # but FILTER unbounded patterns first (2026-05-22 tool-surface policy).
            # An operator "approve & remember" must never silently re-admit
            # python/uv-run/curl/compound shells via approval-rules.json — the
            # exact loop that broadened the surface during the v94 dogfood.
            if extra_allowed_tools:
                safe_extra = [p for p in extra_allowed_tools if not _is_unbounded_tool(p)]
                if safe_extra:
                    allowed = allowed + " " + " ".join(safe_extra)
            return ["claude", "--model", model, "--allowedTools", allowed], {}
```

And add this module-level helper near the top of `claude.py` (after the imports, before `class ClaudeAdapter`). The constants are exported so the keystone test asserts against the SAME source of truth (CV-9):

```python
# Forbidden command heads (2026-05-22 tool-surface policy): interpreters, network
# tools, installers, and destructive non-interpreters that policy never auto-
# approves — even via an operator approval-rule. Prefix-matched against the head
# of a Bash(<cmd> ...) pattern; scripts/ paths are bounded by location and exempt.
_FORBIDDEN_HEAD_CMDS = (
    "python", "uv run", "bash", "sh", "eval", "curl", "wget", "ssh", "scp",
    "pip", "npm", "npx", "find", "rm", "sudo", "chmod", "chown", "dd",
    "mv", "tee", "ln",
)
# Compound/background separators Claude Code's Bash matcher recognizes
# (code.claude.com/docs/en/permissions): && || ; | |& & newline — plus command
# substitution. Any presence marks the candidate pattern unbounded.
_COMPOUND_OPERATORS = ("&&", "||", ";", "|", "&", "\n", "$(", "`")


def _is_unbounded_tool(pattern: str) -> bool:
    """True if a candidate --allowedTools pattern names an unbounded interpreter,
    network tool, installer, destructive command, or shell escape. Filters
    operator-supplied PM-8 patterns so 'approve & remember' cannot re-admit them.

    A pattern whose Bash head is a ``scripts/`` path is bounded by location (the
    sanctioned tool dir) — consistent with the threat model (the concern is python
    re-admission and accidental broadening, not malicious scripts). Everything else
    is prefix-matched against the forbidden heads; compound separators anywhere
    also mark the pattern unbounded.
    """
    low = pattern.lower()
    if any(op in low for op in _COMPOUND_OPERATORS):
        return True
    if "bash(" not in low:
        return False  # native-tool patterns (Read/Edit/...) are always bounded
    head = low.split("bash(", 1)[1].strip().lstrip("./").strip()
    if head.startswith("scripts/"):
        return False  # path-scoped script — bounded by location
    return any(head.startswith(cmd) for cmd in _FORBIDDEN_HEAD_CMDS)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra test pytest scripts/tests/test_harness_claude.py -v`
Expected: PASS (both the rewritten contract test and the new regression guard)

- [ ] **Step 5: Commit**

```bash
git add megalodon_ui/harnesses/claude.py scripts/tests/test_harness_claude.py
git commit -m "feat(harness): narrow fleet allowlist to bounded tools; no interpreters"
```

---

## Task 5: Rewrite `launch.md` to use bounded tools only

**Files:**
- Modify: `launch.md` (Step 2 / Step 4 / §5.A / RULE-15 / Python+fcntl carve-out)
- Test: covered by Task 6 (launch-protocol lint)

- [ ] **Step 1: Rewrite Step 2 (agent ID) — `launch.md:26-47`**

```markdown
## Step 2 — Your agent ID is pre-baked

Your agent ID is baked into this launch file at spawn time:

```
{{AGENT_ID}}
```

**Do not run any command to compute it.** This is your agent ID for the entire
mission — write it in your scratch notes, reuse it every tick, never regenerate
it. It persists in this file across crash/recompact, so re-reading recovers the
same ID. (If you ever see a literal `{{AGENT_ID}}` here — an unbaked launch file,
which should not happen via the server spawn path — recover your prior ID from
your existing STATUS.md heartbeat row, per Step 7. Never invent a new one.)
```

- [ ] **Step 1b: Rewrite Step 3 (claim a lane in STATUS.md) — `launch.md:51-63` (CR-3)**

The bootstrap STATUS write must go through the queue (RULE-15), not a direct Edit
that races the applier. Replace the "Edit that single row in place" instructions with:

```markdown
## Step 3 — Claim a lane in STATUS.md (queue-routed)

Find the first row with `Agent = unclaimed` in lane order (AUDIT, ARCHITECT,
BACKEND, FRONTEND, TEST, META). Claim it through the queue applier — never a
direct Edit (RULE-15; a direct Edit races the applier and corrupts STATUS.md):

```bash
scripts/queue_submit.py --mission-dir . --agent {{AGENT_ID}} --lane <LANE> \
  status --state initialized --notes "bootstrap; v8; will claim P1-<X> next tick"
```

The applier stamps `Last UTC` server-side. If the applier heartbeat is stale (read
`queue/.applier.lock/heartbeat.txt` with the Read tool; >30s old), set
`BLOCKED-APPLIER-DOWN` and halt mutations until the operator restarts it. If two
workers race the same row, earlier UTC wins next tick; the loser re-submits for the
next unclaimed row.
```

- [ ] **Step 2: Rewrite Step 4 (claim) — `launch.md:67-77`**

```markdown
## Step 4 — Claim your P1 task and start working

Your P1 task is `P1-<your-lane-letter>` (AUDIT = `P1-A`, BACKEND = `P1-C`, …),
listed in `TASKS.md` under "PHASE 1 — PLAN".

Claim paths — two distinct mechanisms, do not confuse them (CV-5):
- `scripts/claim.sh P1-<X> {{AGENT_ID}}` — the **initial pre-queue P1 directory
  mutex**: atomically creates `claims/P1-<X>/` + `owner.txt`. This is the ONLY
  sanctioned way to *create* a claim dir.
- `scripts/queue_submit.py … claim-done --task P1-<X>` — the **queue-routed
  lifecycle marker** the applier applies on RULE-10 close (also reachable via
  `scripts/atomic_close.py`). The applier owns lifecycle markers; `claim.sh` owns
  the initial create. They never both create the same dir.

Claim it now:

```bash
scripts/claim.sh P1-<X> {{AGENT_ID}}
```

Exit 0 = claimed (or you already own it); exit 3 = another agent holds it (claim
the next unclaimed P1 instead). Then read your task in TASKS.md and **begin work
immediately.** Write your finding to
`findings/<your-agent-id>-<lane-letter>-P1-<topic>-<UTC>.md` with YAML
frontmatter (README.md §3; `lineage: v8` mandatory).
```

- [ ] **Step 3: Rewrite §5.A (fleet ledger) — `launch.md:109-113`**

```markdown
### §5.A Fleet ledger (V9 A9) — operator-run, not agent-run

Fleet-tick telemetry is collected by the **operator** post-mission
(`scripts/aggregate_fleet_perf.py --mission-dir <m>` + token data from
`scripts/parse_session_tokens.py`). Workers do **not** call any telemetry
function during /loop ticks — it required `python` and is dropped from the agent
path (2026-05-22 tool-surface policy).
```

- [ ] **Step 4: Rewrite RULE-15 queue line — `launch.md:121-126`**

Change the second bullet (line 123) from:
```markdown
  - Or `python -m megalodon_ui.queue.queue_client` for direct intent submission.
```
to:
```markdown
  - Or `scripts/queue_submit.py --mission-dir <m> --agent <id> --lane <LANE> <intent> …`
    for direct intent submission (status/claim/done/history/event/claim-dir/claim-done).
    NEVER `python -m megalodon_ui.queue.queue_client` — that is an unbounded `python -m`.
```

Also change the applier-heartbeat verify (line 124) from `cat <mission>/queue/.applier.lock/heartbeat.txt` to:
```markdown
  - **Operator MUST start the applier daemon BEFORE workers via `./scripts/start_applier.sh <mission-dir> &`**.
    Workers verify applier liveness by **reading** `queue/.applier.lock/heartbeat.txt`
    with the Read tool (UTC stamp within last 5s). Use Read, never shell `cat`.
```

- [ ] **Step 5: Remove the dead Python+fcntl carve-out — `launch.md:253-258`**

Delete the `### Python+fcntl reservation (refinement)` section entirely and replace with:

```markdown
### Interpreter reservation — REMOVED (2026-05-22 tool-surface policy)

There is no python carve-out. All shared-state mutations flow through
`scripts/queue_submit.py` or `scripts/atomic_close.py` (queue-routed, serialized
by the applier). The queue removes the CAS-race rationale that previously
justified `python3`+`fcntl` heredocs. `python` is never allowlisted.
```

- [ ] **Step 6: Add the bounded test-runner rule (after RULE-14, `launch.md:240`)**

```markdown
### RULE 14b — Test runs via run_tests.sh

For the full pytest suite (TEST lane, and any lane verifying its own changes),
workers MUST use `scripts/run_tests.sh [pytest args]`. It runs
`uv run --extra test pytest` (the test extra carries freezegun et al.). NEVER run
bare `pytest` (missing test-extra deps) or `uv run …` directly (not allowlisted).
```

- [ ] **Step 7: Verify no forbidden tokens remain, then commit**

The rewrite intentionally KEEPS one `python -m` inside a "NEVER …" prohibition
line, so filter prohibition prose out before checking (CR-8). The authoritative
scan (fenced ```bash blocks only) is Task 6's lint test, which runs next.

```bash
grep -nE 'python3 -c|python -m|mkdir claims|record_tick' launch.md | grep -v NEVER || echo "clean"
git add launch.md
git commit -m "docs(launch): route all agent ops through bounded tools; drop python"
```
Expected output: `clean`

---

## Task 6: Launch-protocol lint test (no interpreters in agent steps)

**Files:**
- Create: `scripts/tests/test_launch_protocol_no_interpreters.py`

- [ ] **Step 1: Write the test**

```python
"""Lint: launch.md must route agent steps through bounded tools only.

Guards the 2026-05-22 tool-surface policy: no python/compound interpreter
invocations in the worker protocol. Fenced 'NEVER …' guard lines are allowed
to NAME a forbidden command (they are prohibitions, not instructions), so we
only scan fenced ```bash blocks for executable invocations.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LAUNCH = REPO / "launch.md"

# Patterns that must not appear as an executable line inside a ```bash block.
FORBIDDEN = [
    re.compile(r"^\s*python3?\s+-[cm]\b"),
    re.compile(r"^\s*python3?\s+-m\b"),
    re.compile(r"^\s*mkdir\s+claims/"),
    re.compile(r"record_tick\s*\("),
    re.compile(r"^\s*curl\b"),
    re.compile(r"&&"),  # no compound chains in fenced agent commands
]


def _bash_block_lines(text: str) -> list[str]:
    lines, in_block = [], False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_block = stripped == "```bash"
            continue
        if in_block:
            lines.append(line)
    return lines


def test_launch_md_has_no_interpreter_invocations():
    text = LAUNCH.read_text(encoding="utf-8")
    offenders = []
    for line in _bash_block_lines(text):
        for pat in FORBIDDEN:
            if pat.search(line):
                offenders.append((pat.pattern, line.strip()))
    assert not offenders, f"forbidden invocations in launch.md bash blocks: {offenders}"


def test_launch_md_references_bounded_tools():
    text = LAUNCH.read_text(encoding="utf-8")
    for tool in ["scripts/claim.sh", "scripts/queue_submit.py",
                 "scripts/run_tests.sh", "{{AGENT_ID}}"]:
        assert tool in text, f"launch.md must reference {tool}"


def test_rendered_per_lane_file_is_interpreter_free(tmp_path):
    """Scan the RENDERED launch-<LANE>.md (header + body) agents actually read,
    not just the template (CV-4/CV-7). Confirms the {{AGENT_ID}} placeholder is
    present PRE-bake (spawn.py bakes it later) and no interpreter invocations leak
    in via the gen_lane_launches header.
    """
    import sys
    sys.path.insert(0, str(REPO))
    from scripts.gen_lane_launches import generate_one

    rendered = generate_one("BACKEND", 2, REPO)
    assert "{{AGENT_ID}}" in rendered, "rendered file lost the pre-bake placeholder"
    offenders = []
    for line in _bash_block_lines(rendered):
        for pat in FORBIDDEN:
            if pat.search(line):
                offenders.append((pat.pattern, line.strip()))
    assert not offenders, f"forbidden invocations in rendered launch-BACKEND.md: {offenders}"
```

- [ ] **Step 2: Run the test**

Run: `uv run --extra test pytest scripts/tests/test_launch_protocol_no_interpreters.py -v`
Expected: PASS (both tests) — depends on Task 5 being complete.

- [ ] **Step 3: Commit**

```bash
git add scripts/tests/test_launch_protocol_no_interpreters.py
git commit -m "test(launch): lint launch.md for interpreter-free agent protocol"
```

---

## Task 7: Guard test — no broad approval-rules seed (spec area 6 reduction)

**Files:**
- Create/extend: add to `scripts/tests/test_new_run.py`

- [ ] **Step 1: Write the test**

Add to `scripts/tests/test_new_run.py`:

```python
def test_new_run_seeds_no_broad_approval_rules(tmp_path):
    """new_run.sh must not seed a broad .fleet/approval-rules.json.

    The bounded allowlist lives in claude.py; a seeded approval-rules file that
    re-broadened the surface would defeat it. Either no file is seeded (current
    behavior — vacuously safe), or any seeded file is interpreter/compound-free.
    """
    import json
    import os
    import subprocess

    repo = Path(__file__).resolve().parents[2]
    # new_run.sh signature: `new_run.sh <slug> [--force]`; RUN_LIB_REPO_ROOT
    # overrides the repo root so the run dir lands under tmp_path.
    env = dict(os.environ, RUN_LIB_REPO_ROOT=str(tmp_path))
    (tmp_path / "runs").mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["bash", str(repo / "scripts" / "new_run.sh"), "guard", "--force"],
        cwd=str(repo), env=env, capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, f"new_run.sh failed: {r.stderr}"
    created = list((tmp_path / "runs").glob("*--guard"))
    assert created, "new_run.sh did not create a run dir under the overridden root"
    for d in created:
        rules = d / ".fleet" / "approval-rules.json"
        if rules.exists():
            patterns = json.dumps(json.loads(rules.read_text())).lower()
            for bad in ["python", "uv run", "bash -c", "curl", "&&"]:
                assert bad not in patterns, f"broad seed pattern leaked: {bad}"
```

- [ ] **Step 2: Run the test**

Run: `uv run --extra test pytest scripts/tests/test_new_run.py -v`
Expected: PASS (no approval-rules.json is seeded today, so the loop is vacuously safe).

- [ ] **Step 3: Commit**

```bash
git add scripts/tests/test_new_run.py
git commit -m "test(new_run): guard against broad approval-rules seed"
```

---

## Task 8: Full validation + documentation

**Files:**
- Modify: `HISTORY.md`, `TASKS.md`, `README.md` (if it documents the allowlist)
- Modify: `docs/superpowers/specs/2026-05-22-agent-tool-surface-policy-design.md` (mark Done)

- [ ] **Step 1: Run the full suite**

Run: `uv run --extra test pytest -q`
Expected: all green. If any pre-existing test asserted the old broad surface elsewhere (grep first), fix it to the new contract.

Pre-check: `grep -rn "Bash(cat:\*)\|Bash(curl\|Bash(git branch\|python -m megalodon_ui.queue" scripts/tests megalodon_ui` — every hit outside `claude.py` history must be reconciled.

- [ ] **Step 2: CI parity check**

Run: `uv run --extra test pytest scripts/tests/test_harness_claude.py scripts/tests/test_claim_sh.py scripts/tests/test_queue_submit.py scripts/tests/test_run_tests_sh.py scripts/tests/test_launch_protocol_no_interpreters.py scripts/tests/test_new_run.py scripts/tests/test_queue_client.py -v`
Expected: all green (the policy's test files).

- [ ] **Step 3: Update docs**

- `HISTORY.md`: record the tool-surface hardening, the failure it fixes (v94-ui-dogfood approval friction), and the regression guard.
- `TASKS.md`: mark the tool-surface policy tasks done; add the re-run dogfood as the next task.
- `README.md`: if it documents the agent allowlist, update to the bounded set.
- Spec: flip Status to `implemented`; check the "Done when" boxes that now hold.

- [ ] **Step 4: Commit LOCALLY (do not push yet — CV-1)**

```bash
git add -A
git commit -m "feat(v9.4): tool-surface policy — bounded fleet allowlist + tools"
```

- [ ] **Step 5: Manual acceptance gate — fresh spawn, zero bootstrap prompts (BEFORE push)**

This is the real acceptance gate (spec "Done when"); it must pass before `main`
moves, so a narrowed allowlist missing a pattern never reaches `main` unverified.
Not automatable here.
- Scaffold a fresh run, start the applier, spawn one lane in `live_repl`, watch the dashboard.
- Confirm the agent completes Step 2 (read baked ID), Step 3 (`queue_submit.py status`),
  Step 4 (`scripts/claim.sh`), and a `run_tests.sh` invocation with **zero permission prompts**.
- **Compound-matcher spot check (CV-2):** in the spawned REPL, confirm a chained tail
  like `scripts/poll.py --brief ; echo x` DOES prompt (proving `Bash(...:*)` does not
  auto-approve compound commands — matches code.claude.com/docs/en/permissions).
- **Builtin-auto-run validation (SR-4):** this gate runs on the actually-deployed
  `claude` CLI (target v2.1.133+). If a future CLI version changed read-only-builtin
  auto-run behavior, the agent would prompt on `cat`/`ls`/`git` here — i.e. this gate
  empirically validates the assumption that let us drop those patterns. Capture the
  CLI version (`claude --version`) in the run notes.
- If any bootstrap step prompts, capture the missed pattern, add it to the bounded set,
  and re-run Task 4's contract test before retrying. Do not push until the gate is green.

- [ ] **Step 6: Push only after the gate is green**

```bash
git push
```

---

## Self-contrarian review applied

Self-pass on the draft before external review: **2 OW + 4 PW + 2 WR**.

**Fixed inline (the plain bugs):**
- OW-1 — `launch.md` Step 2 cited a non-existent `AGENT_ID:` header line → repointed the unbaked-file fallback to STATUS.md heartbeat recovery (Step 7).
- OW-3 — Task 5 assumed a `poll.py` applier-heartbeat feature that doesn't exist → verify via the native Read tool on `queue/.applier.lock/heartbeat.txt`.
- PW-2 — `new_run.sh` invocation was hand-waved → verified signature (`<slug>`, `RUN_LIB_REPO_ROOT` override) and wrote the exact test.
- PW-3 — `test_queue_submit` forwarding test was over-engineered → simplified to a direct import + monkeypatch.

**Operator-resolved at the gate (AskUserQuestion, 2026-05-22):**
- OW-2 — bare `pytest` lacks test-extra deps → **ACCEPT:** add `scripts/run_tests.sh` (Task 3.5), allowlist it, drop `Bash(pytest:*)`.
- PW-1 — PM-8 `extra_allowed_tools` could re-admit `python` at runtime → **ACCEPT:** filter via `_is_unbounded_tool` in `claude.py` + test (Task 4).
- O-1 — agent-ID approach → **adopt existing spawn-time bake**, drop spec area 4 (`gen_lane_launches.py` unchanged).
- O-2 — bounded utilities → **include** `Bash(sleep:*)`/`Bash(date:*)`/`Bash(printf:*)`.

**Acknowledged (real, not mitigated):**
- WR-1 — `claim.sh` sub-millisecond TOCTOU window (dir created before `owner.txt`) → a concurrent claimant gets exit 3 and claims the next task per protocol. Acceptable; no retry added.
- WR-2 — `.claude/settings.json` (operator-machine allowlist) is a separate surface from the fleet harness string → **out of scope**, recorded in Deviations.

**Deferred to external reviewers:** the precision of `_is_unbounded_tool`'s head-matching (false-positive/negative surface), and whether `claude`'s `--allowedTools` matcher enforces the `Bash(scripts/run_tests.sh:*)` boundary against trailing compound operators.

## Warp review applied (2026-05-22)

3 cross-model reviewers (contrarian=GPT-5.5, auditor=Gemini 3.1 Pro, constructive=Opus); full record in `~/Documents/Projects/.plans/megalodon/agent-tool-surface-policy-2026-05-22-synthesis.md`. **19 findings → 17 ACCEPT · 2 ACKNOWLEDGE · 1 REJECT(+substitution) · 1 ESCALATE(resolved).**

**Key changes folded in:**
- **Dropped explicit read-only-git patterns** (CR-5/CR-7, verified vs Claude Code docs): Claude auto-runs read-only git/cat/ls/grep as built-ins, and `Bash(git diff*)` would broaden to `git diff --output=<file>` writes. Allowlist + keystone test updated; "only inspection path" framing corrected.
- **Hardened `_is_unbounded_tool`** (CR-3/CR-4/CV-3/CV-6): added `find`/`rm`/`sudo`/`chmod`/`dd`/`mv`/`tee`/`ln`; full separator set (`&` + newline); `scripts/` paths bounded-by-location; `.strip()` for leading-space bypass; exported constants for DRY (CV-9).
- **Acceptance gate before push** (CV-1) + compound-matcher spot check (CV-2, replacing the rejected hook prescription — Claude's matcher already splits compound).
- **Lint scans rendered per-lane files** (CV-4/CV-7); bootstrap STATUS routed through `queue_submit.py` (CR-3); claim-path boundary documented (CV-5); tests use real direct-exec command shapes + exec-bit assertions (CR-6); grep verification fixed (CR-8); `run_tests.sh` mirrors `run_e2e.sh` (CV-8).

**Acknowledged (out of threat model, operator-confirmed):** CR-1/CR-2 — the surface is not a security sandbox; agents are trusted; goal is friction + anti-re-admission, not hostile-agent isolation. See "Threat model" section.

**Pre-mortem (Kimi K2.5):** 10 failure modes + 4 systemic risks → 3 MITIGATE (TDD import ordering, line-numbers-as-anchors, builtin-auto-run gate validation — folded into "Execution notes" + Task 8) / 9 ACKNOWLEDGE (already-handled) / 2 REJECT (false premise: `scripts/__init__.py` exists; the keystone test is a pure string check needing no on-disk files). No design change. Full record in the synthesis file.
