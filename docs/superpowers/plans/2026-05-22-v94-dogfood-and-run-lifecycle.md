# v9.4 Dogfood + Run Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a uniform run lifecycle (templates + scaffold/archive scripts), a pre-flight gate, and an instrumented dashboard-visibility harness, then kick off the v9.4 dogfood (plan task T4.3) on top of it.

**Architecture:** A run is a self-contained subdir `runs/<UTC>--<slug>/` holding all mission ephemera (the fleet's code edits still land in repo source as normal commits). `new_run.sh` scaffolds it from `templates/run/`; `archive_run.sh` moves it to `.archive/<UTC>--<slug>/` via tracked `git mv` and registers it in `INDEX.md`. A pre-flight gate (`preflight.sh`) blocks kickoff until tests pass, the approval-friction allowlist is fixed, the scripts are smoke-proven, and loops are armed. A stimulus harness (`runs_harness/`) forces each prior failure-mode condition and asserts the dashboard reflects it.

**Tech Stack:** Bash (lifecycle scripts), Python 3 + pytest (tests + harness), existing FastAPI server (`megalodon_ui/server.py`) with `__fake__/emit`, `__fake__/set_state`, `_test/stale_override` endpoints, `@playwright/test` for interaction-fidelity checks.

**Spec:** `docs/superpowers/specs/2026-05-22-v94-dogfood-and-run-lifecycle-design.md` (rev 2).

---

## File Structure

**Created:**
- `templates/run/MISSION.md.tmpl`, `STATUS.md.tmpl`, `TASKS.md.tmpl`, `HISTORY.md.tmpl`, `README.md.tmpl`, `.mission-config.yaml.tmpl`, `INDEX-entry.tmpl` — run-doc templates with `{{placeholder}}`s.
- `scripts/run_lib.sh` — shared bash helpers (UTC stamp, placeholder substitution, liveness check, path guard).
- `scripts/new_run.sh` — scaffold a run dir.
- `scripts/archive_run.sh` — archive a run dir (transactional `git mv` + INDEX register).
- `scripts/preflight.sh` — pre-flight gate runner.
- `scripts/_run_liveness.py` — liveness-grammar parser (terminal-token detection), importable + CLI.
- `runs_harness/stimulus.py` — forces each failure-mode stimulus and asserts dashboard reflection.
- `runs_harness/__init__.py`
- `ui/tests/e2e/visibility.spec.ts` — Playwright interaction-fidelity (snap-back, tab highlight).
- `scripts/tests/test_run_liveness.py`, `test_new_run.py`, `test_archive_run.py`, `test_preflight.py`, `test_stimulus_harness.py` — pytest coverage.
- `docs/v9/v9-4-RUN-LIFECYCLE.md` — convention doc.

**Modified:**
- `pytest.ini` — add `testpaths` + `norecursedirs`.
- `pyproject.toml` (create if absent) — pin test deps.
- `.claude/settings.json` — add helper-script wildcards to `allow`.
- `README.md`, `HISTORY.md`, `TASKS.md`, `.archive/INDEX.md` — doc updates (Part C).

---

## Phase 0 — Pre-flight foundation fixes

### Task 0.1: Scope pytest collection

**Files:**
- Modify: `pytest.ini`

- [ ] **Step 1: Write the failing test**

Create `scripts/tests/test_pytest_collection_scope.py`:

```python
"""Phase 0 — guard against pytest collecting non-source test files.

A bare `pytest` from the repo root must NOT recurse into docs/ or .archive/,
where agent-draft test_*.py files live (they break collection).
"""
from __future__ import annotations

import configparser
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_pytest_ini_excludes_docs_and_archive():
    cfg = configparser.ConfigParser()
    cfg.read(REPO / "pytest.ini")
    norecurse = cfg.get("pytest", "norecursedirs", fallback="")
    assert "docs" in norecurse
    assert ".archive" in norecurse
    assert "runs" in norecurse
    testpaths = cfg.get("pytest", "testpaths", fallback="")
    assert "scripts/tests" in testpaths
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest scripts/tests/test_pytest_collection_scope.py -v`
Expected: FAIL (no `norecursedirs`/`testpaths` keys).

- [ ] **Step 3: Edit `pytest.ini`**

Add under `[pytest]`:

```ini
testpaths = scripts/tests ui/tests/unit ui/tests/integration
norecursedirs = docs .archive runs .git node_modules .venv ui/tests/fixtures
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest scripts/tests/test_pytest_collection_scope.py -v`
Expected: PASS.

- [ ] **Step 5: Verify bare collection no longer breaks**

Run: `uv run pytest --collect-only -q 2>&1 | tail -5`
Expected: no `docs/v9/dogfood-2026-05-19/agent-code-drafts/test_*.py` in the collected set; no collection errors.

- [ ] **Step 6: Commit**

```bash
git add pytest.ini scripts/tests/test_pytest_collection_scope.py
git commit -m "test(v9.4): scope pytest collection — exclude docs/.archive/runs"
```

### Task 0.2: Pin test dependencies

**Files:**
- Create: `pyproject.toml` (only if absent; else add the optional-deps group)

- [ ] **Step 1: Check for existing pyproject**

Run: `ls pyproject.toml 2>&1`
If present, add the group below to it; if absent, create it.

- [ ] **Step 2: Create/extend `pyproject.toml`**

```toml
[project]
name = "megalodon"
version = "9.4.0"
requires-python = ">=3.11"

[project.optional-dependencies]
test = [
    "pytest",
    "pytest-asyncio",
    "freezegun",
    "pytest-forked",
    "fastapi",
    "uvicorn[standard]",
    "sse-starlette",
    "pyyaml",
    "httpx",
]
```

- [ ] **Step 3: Verify the canonical test command resolves all deps**

Run: `uv run --extra test pytest scripts/tests -q 2>&1 | tail -15`
Expected: collection succeeds with no `ModuleNotFoundError`. (Record the pass/fail tally — this is the Task 4.2 Python third.)

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "build(v9.4): pin test deps via [project.optional-dependencies] test"
```

### Task 0.3: Fix the approval-friction allowlist

**Files:**
- Modify: `.claude/settings.json`

- [ ] **Step 1: Write the failing test**

Create `scripts/tests/test_settings_friction_allowlist.py`:

```python
"""Phase 0 — assert the README-mandated helper-script wildcards are allowlisted.

README.md (§5) says workers MUST use atomic_close.py / poll.py / run_e2e.sh to
avoid per-tick permission prompts. Those wildcards must be in settings.json,
or prior failure mode #2 (approval storm) recurs.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REQUIRED = {
    "Bash(scripts/atomic_close.py:*)",
    "Bash(scripts/poll.py:*)",
    "Bash(scripts/run_e2e.sh:*)",
}


def test_helper_script_wildcards_present():
    settings = json.loads((REPO / ".claude/settings.json").read_text())
    allow = set(settings["permissions"]["allow"])
    missing = REQUIRED - allow
    assert not missing, f"missing helper-script wildcards: {missing}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest scripts/tests/test_settings_friction_allowlist.py -v`
Expected: FAIL (all three missing).

- [ ] **Step 3: Edit `.claude/settings.json`**

Add these three entries to `permissions.allow` (after `"Bash(uv run pytest:*)"`):

```json
      "Bash(scripts/atomic_close.py:*)",
      "Bash(scripts/poll.py:*)",
      "Bash(scripts/run_e2e.sh:*)",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest scripts/tests/test_settings_friction_allowlist.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add .claude/settings.json scripts/tests/test_settings_friction_allowlist.py
git commit -m "fix(v9.4): allowlist helper-script wildcards (kills approval-storm failure mode)"
```

---

## Phase 1 — Run lifecycle (templates + scripts)

### Task 1.1: Liveness-grammar parser

**Files:**
- Create: `scripts/_run_liveness.py`
- Test: `scripts/tests/test_run_liveness.py`

- [ ] **Step 1: Write the failing test**

```python
"""v9.4 — run liveness grammar.

A run is LIVE iff the first whitespace-token of the last .mission-events line
is not a terminal token. Terminal tokens: COMPLETE | ABORTED | DEGRADED-CLOSE.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _run_liveness import TERMINAL_TOKENS, is_live, last_token  # noqa: E402


def test_terminal_tokens_frozen():
    assert TERMINAL_TOKENS == {"COMPLETE", "ABORTED", "DEGRADED-CLOSE"}


def test_run_start_is_live(tmp_path: Path):
    ev = tmp_path / ".mission-events"
    ev.write_text("RUN-START 2026-05-22T16-30Z slug=demo\n")
    assert is_live(ev) is True
    assert last_token(ev) == "RUN-START"


def test_complete_is_not_live(tmp_path: Path):
    ev = tmp_path / ".mission-events"
    ev.write_text("RUN-START ...\nCOMPLETE 2026-05-22T20-00Z all lanes drained\n")
    assert is_live(ev) is False


def test_missing_file_is_not_live(tmp_path: Path):
    assert is_live(tmp_path / "nope") is False


def test_blank_trailing_lines_ignored(tmp_path: Path):
    ev = tmp_path / ".mission-events"
    ev.write_text("ABORTED operator killed run\n\n  \n")
    assert is_live(ev) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest scripts/tests/test_run_liveness.py -v`
Expected: FAIL (`_run_liveness` not importable).

- [ ] **Step 3: Implement `scripts/_run_liveness.py`**

```python
"""Liveness grammar for Megalodon runs (v9.4 run lifecycle).

`.mission-events` lines start with a structured first token. A run is LIVE
until a terminal token is written as the first token of the last non-blank
line. Used by new_run.sh (refuse to scaffold over a live run) and
archive_run.sh (refuse to archive a live run without --force).
"""
from __future__ import annotations

import sys
from pathlib import Path

TERMINAL_TOKENS = {"COMPLETE", "ABORTED", "DEGRADED-CLOSE"}


def last_token(events_path: Path) -> str | None:
    """First whitespace-delimited token of the last non-blank line, or None."""
    if not events_path.exists():
        return None
    last = None
    for line in events_path.read_text().splitlines():
        if line.strip():
            last = line.strip()
    if last is None:
        return None
    return last.split()[0]


def is_live(events_path: Path) -> bool:
    """True iff the run has events and the last token is not terminal."""
    tok = last_token(events_path)
    if tok is None:
        return False
    return tok not in TERMINAL_TOKENS


def main(argv: list[str]) -> int:
    """CLI: exit 0 if live, 1 if not-live/missing. Path is argv[1]."""
    if len(argv) != 2:
        print("usage: _run_liveness.py <path-to-.mission-events>", file=sys.stderr)
        return 2
    return 0 if is_live(Path(argv[1])) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest scripts/tests/test_run_liveness.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/_run_liveness.py scripts/tests/test_run_liveness.py
git commit -m "feat(v9.4): run liveness grammar (terminal-token parser)"
```

### Task 1.2: Run-doc templates

**Files:**
- Create: `templates/run/{MISSION,STATUS,TASKS,HISTORY,README}.md.tmpl`, `.mission-config.yaml.tmpl`, `INDEX-entry.tmpl`

- [ ] **Step 1: Create `templates/run/MISSION.md.tmpl`**

```markdown
# Mission — {{MISSION_TITLE}}

- **Slug:** {{SLUG}}
- **Started:** {{UTC}}
- **Lanes:** {{LANES}}

## Scope

{{MISSION_SUMMARY}}

## Exit criteria

{{EXIT_CRITERIA}}

## Phase progression

INIT → PHASE-PLAN → PHASE-CHALLENGE → PHASE-BUILD → PHASE-VERIFY → PHASE-RUN →
PHASE-HEAL → PHASE-OPERATOR-ACCEPTANCE → DRAINING → COMPLETE
```

- [ ] **Step 2: Create `templates/run/STATUS.md.tmpl`**

```markdown
# Status board — {{SLUG}}

States: `unclaimed | initialized | working: <task-id> | idle | BLOCKED | awaiting OPERATOR-ACK`

| Lane | Agent | State | Last UTC | Notes |
|---|---|---|---|---|
| AUDIT     | unclaimed | unclaimed |  |  |
| ARCHITECT | unclaimed | unclaimed |  |  |
| BACKEND   | unclaimed | unclaimed |  |  |
| FRONTEND  | unclaimed | unclaimed |  |  |
| TEST      | unclaimed | unclaimed |  |  |
| META      | unclaimed | unclaimed |  |  |
```

- [ ] **Step 3: Create `templates/run/TASKS.md.tmpl`**

```markdown
# Tasks — {{SLUG}}

Format: `[ ] [LANE-X] <task-id> — <description>`
States: `[ ]` open · `[claimed: <agent-id> @ <UTC>]` · `[done: <agent-id> @ <UTC>]`

## PHASE 1 — PLAN

## PHASE 2 — BUILD

## PHASE 3 — VERIFY

## OPERATOR-ACCEPTANCE TASKS

## CROSS-LANE / SECONDARY TASK POOL
```

- [ ] **Step 4: Create `templates/run/HISTORY.md.tmpl`**

```markdown
# History — {{SLUG}}

Run started {{UTC}}. All meaningful changes appended below (newest last).
```

- [ ] **Step 5: Create `templates/run/README.md.tmpl`**

```markdown
# Run — {{SLUG}} ({{DATE}})

{{MISSION_SUMMARY}}

## Lanes

{{LANES}}

## What's here

| Path | Purpose |
|------|---------|
| `findings/` | Per-agent finding files (primary output) |
| `signals/` | Inter-lane messages |
| `claims/` | Task claim mutex dirs |
| `queue/` | Queue applier intents + journal |
| `.fleet/` | Stream logs, tokens, applier log |
| `MISSION.md` `STATUS.md` `TASKS.md` `HISTORY.md` | Final mission state |
| `.mission-config.yaml` | Config that drove the spawn |
```

- [ ] **Step 6: Create `templates/run/.mission-config.yaml.tmpl`**

Copy the six-lane v9.1 schema from `docs/v9/dogfood-2026-05-19/.mission-config.yaml`, replacing the `mission:` block header with placeholders:

```yaml
schema_version: 1
mission:
  id: {{SLUG}}
  utc_started: '{{UTC_ISO}}'
  type: software-engineering
  description: "{{MISSION_TITLE}}"
lanes:
- name: AUDIT
  short: A
  role: "AUDIT — scrutinize protocol adherence, race conditions, security"
  harness: {cli: claude, model: claude-opus-4-7, extra_args: [], auth_env: []}
  cadence_seconds: 300
  tick_offset_seconds: 0
  live_repl: true
  initial_prompt: /loop Read launch-AUDIT.md and execute one iteration.
- name: ARCHITECT
  short: B
  role: "ARCHITECT — design specs, ADRs, integration shapes"
  harness: {cli: claude, model: claude-opus-4-7, extra_args: [], auth_env: []}
  cadence_seconds: 300
  tick_offset_seconds: 0
  live_repl: true
  initial_prompt: /loop Read launch-ARCHITECT.md and execute one iteration.
- name: BACKEND
  short: C
  role: "BACKEND — implement server/primitives/adapters in megalodon_ui/"
  harness: {cli: claude, model: claude-sonnet-4-6, extra_args: [], auth_env: []}
  cadence_seconds: 300
  tick_offset_seconds: 0
  live_repl: true
  initial_prompt: /loop Read launch-BACKEND.md and execute one iteration.
- name: FRONTEND
  short: D
  role: "FRONTEND — implement UI in ui/static/, wire dashboard forms"
  harness: {cli: claude, model: claude-sonnet-4-6, extra_args: [], auth_env: []}
  cadence_seconds: 300
  tick_offset_seconds: 0
  live_repl: true
  initial_prompt: /loop Read launch-FRONTEND.md and execute one iteration.
- name: TEST
  short: E
  role: "TEST — write/run pytest + playwright suites, eliminate skipped/xfail"
  harness: {cli: claude, model: claude-sonnet-4-6, extra_args: [], auth_env: []}
  cadence_seconds: 300
  tick_offset_seconds: 0
  live_repl: true
  initial_prompt: /loop Read launch-TEST.md and execute one iteration.
- name: META
  short: F
  role: "META — observe agent behavior, track tick activity, mid/final reports"
  harness: {cli: claude, model: claude-haiku-4-5-20251001, extra_args: [], auth_env: []}
  cadence_seconds: 300
  tick_offset_seconds: 0
  live_repl: true
  initial_prompt: /loop Read launch-META.md and execute one iteration.
phases: [INIT, PHASE-PLAN, PHASE-CHALLENGE, PHASE-BUILD, PHASE-VERIFY, PHASE-RUN, PHASE-HEAL, PHASE-OPERATOR-ACCEPTANCE, DRAINING, COMPLETE]
task_id_patterns:
  patterns:
  - ^(P\d+(\.\d+)?(-[A-F](-to-[A-F])?)?|P\d+-RUN-[A-Z0-9_-]+|REPAIR-[A-Z0-9_-]+|OPERATOR-[A-Z_-]+|S-\d+|TEST-\d+|CHALLENGE-[A-Z0-9_-]+|OA-[A-Z0-9_-]+)$
  description: ''
harness_rebinding_reserved: {}
orchestrator_pseudo_lane: META
task_sections: ["PHASE 1 — PLAN", "PHASE 2 — BUILD", "PHASE 3 — VERIFY", "OPERATOR-ACCEPTANCE TASKS", "CROSS-LANE / SECONDARY TASK POOL"]
```

- [ ] **Step 7: Create `templates/run/INDEX-entry.tmpl`**

```markdown
| `{{UTC}}--{{SLUG}}` | {{MISSION_TITLE}} | {{DATE}} | {{COMPLETED}} | {{WALL_CLOCK}} | {{OUTPUTS}} |
```

- [ ] **Step 8: Commit**

```bash
git add templates/run/
git commit -m "feat(v9.4): run-doc templates (MISSION/STATUS/TASKS/HISTORY/README/config/index)"
```

### Task 1.3: Shared bash helpers

**Files:**
- Create: `scripts/run_lib.sh`

- [ ] **Step 1: Implement `scripts/run_lib.sh`**

```bash
#!/usr/bin/env bash
# Shared helpers for the v9.4 run lifecycle (new_run.sh / archive_run.sh).
# Source this file; do not execute directly.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# UTC stamp for run dir names: 2026-05-22T16-30Z  (filesystem-safe).
run_utc()      { date -u +%Y-%m-%dT%H-%MZ; }
# ISO stamp for inside config files: 2026-05-22T16:30:00Z
run_utc_iso()  { date -u +%Y-%m-%dT%H:%M:%SZ; }

# Substitute {{KEY}} placeholders in a file, in place.
# Usage: subst_file <file> KEY=VALUE [KEY=VALUE ...]
subst_file() {
  local f="$1"; shift
  local kv key val
  for kv in "$@"; do
    key="${kv%%=*}"; val="${kv#*=}"
    # Use python for safe replacement (handles slashes/newlines in val).
    python3 - "$f" "$key" "$val" <<'PY'
import sys, pathlib
f, key, val = sys.argv[1], sys.argv[2], sys.argv[3]
p = pathlib.Path(f)
p.write_text(p.read_text().replace("{{%s}}" % key, val))
PY
  done
}

# Guard: refuse to operate on a path outside runs/ or .archive/.
# Usage: assert_under_runs_or_archive <abs-path>
assert_under_runs_or_archive() {
  local p; p="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
  case "$p" in
    "$REPO_ROOT"/runs/*|"$REPO_ROOT"/.archive/*) return 0 ;;
    *) echo "REFUSING: $p is outside runs/ or .archive/" >&2; return 1 ;;
  esac
}

# Liveness check via the python parser. Returns 0 if live.
run_is_live() {
  uv run python3 "$REPO_ROOT/scripts/_run_liveness.py" "$1/.mission-events"
}
```

- [ ] **Step 2: Smoke-check it sources cleanly**

Run: `bash -c 'source scripts/run_lib.sh && run_utc && run_utc_iso'`
Expected: two timestamps printed, no error.

- [ ] **Step 3: Commit**

```bash
git add scripts/run_lib.sh
git commit -m "feat(v9.4): run_lib.sh shared lifecycle helpers"
```

### Task 1.4: `new_run.sh`

**Files:**
- Create: `scripts/new_run.sh`
- Test: `scripts/tests/test_new_run.py`

- [ ] **Step 1: Write the failing test**

```python
"""v9.4 — new_run.sh scaffolds a self-contained run dir under runs/."""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _run(args, cwd):
    return subprocess.run(
        ["bash", str(REPO / "scripts/new_run.sh"), *args],
        cwd=cwd, capture_output=True, text=True,
    )


def test_scaffold_creates_run_dir(tmp_path, monkeypatch):
    # Run against a throwaway REPO_ROOT copy so we don't touch the real runs/.
    work = tmp_path / "repo"
    work.mkdir()
    (work / "templates").symlink_to(REPO / "templates")
    (work / "scripts").symlink_to(REPO / "scripts")
    monkeypatch.setenv("RUN_LIB_REPO_ROOT", str(work))
    res = _run(["smoketest", "--title", "Smoke", "--summary", "S"], cwd=work)
    assert res.returncode == 0, res.stderr
    run_dirs = list((work / "runs").glob("*--smoketest"))
    assert len(run_dirs) == 1
    rd = run_dirs[0]
    for name in ["MISSION.md", "STATUS.md", "TASKS.md", "HISTORY.md",
                 "README.md", ".mission-config.yaml", ".mission-events"]:
        assert (rd / name).exists(), name
    for d in ["findings", "claims", "signals", "queue", ".fleet"]:
        assert (rd / d).is_dir(), d
    # No unresolved placeholders.
    assert "{{" not in (rd / "MISSION.md").read_text()


def test_refuses_when_live_run_exists(tmp_path, monkeypatch):
    work = tmp_path / "repo"; work.mkdir()
    (work / "templates").symlink_to(REPO / "templates")
    (work / "scripts").symlink_to(REPO / "scripts")
    monkeypatch.setenv("RUN_LIB_REPO_ROOT", str(work))
    _run(["alpha", "--title", "A", "--summary", "A"], cwd=work)
    res = _run(["beta", "--title", "B", "--summary", "B"], cwd=work)
    assert res.returncode != 0
    assert "live" in (res.stderr + res.stdout).lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest scripts/tests/test_new_run.py -v`
Expected: FAIL (`new_run.sh` missing).

- [ ] **Step 3: Implement `scripts/new_run.sh`**

```bash
#!/usr/bin/env bash
# Scaffold a self-contained run dir under runs/<UTC>--<slug>/ from templates/run/.
#
# Usage: scripts/new_run.sh <slug> [--title T] [--summary S] [--force]
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/run_lib.sh"
# Allow tests to override the repo root.
REPO_ROOT="${RUN_LIB_REPO_ROOT:-$REPO_ROOT}"

SLUG=""; TITLE=""; SUMMARY=""; FORCE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --title)   TITLE="$2"; shift 2 ;;
    --summary) SUMMARY="$2"; shift 2 ;;
    --force)   FORCE=1; shift ;;
    -*)        echo "unknown flag: $1" >&2; exit 2 ;;
    *)         SLUG="$1"; shift ;;
  esac
done
[[ -n "$SLUG" ]] || { echo "usage: new_run.sh <slug> [--title T] [--summary S]" >&2; exit 2; }

# Refuse if any existing run under runs/ is still live.
if [[ -d "$REPO_ROOT/runs" && $FORCE -eq 0 ]]; then
  for ev in "$REPO_ROOT"/runs/*/.mission-events; do
    [[ -e "$ev" ]] || continue
    if uv run python3 "$HERE/_run_liveness.py" "$ev"; then
      echo "REFUSING: live run at $(dirname "$ev"). Archive it first (scripts/archive_run.sh) or pass --force." >&2
      exit 1
    fi
  done
fi

UTC="$(run_utc)"; UTC_ISO="$(run_utc_iso)"; DATE="$(date -u +%Y-%m-%d)"
RUN_DIR="$REPO_ROOT/runs/${UTC}--${SLUG}"
[[ -e "$RUN_DIR" && $FORCE -eq 0 ]] && { echo "REFUSING: $RUN_DIR exists (use --force)" >&2; exit 1; }
assert_under_runs_or_archive "$RUN_DIR"

LANES="AUDIT, ARCHITECT, BACKEND, FRONTEND, TEST, META"
mkdir -p "$RUN_DIR"/{findings,claims,signals,queue/pending,queue/applied,queue/rejected,.fleet}
for d in findings claims signals queue .fleet; do touch "$RUN_DIR/$d/.gitkeep"; done

# Copy + substitute doc templates.
for t in MISSION STATUS TASKS HISTORY README; do
  cp "$REPO_ROOT/templates/run/${t}.md.tmpl" "$RUN_DIR/${t}.md"
  subst_file "$RUN_DIR/${t}.md" \
    "SLUG=$SLUG" "UTC=$UTC" "DATE=$DATE" "LANES=$LANES" \
    "MISSION_TITLE=$TITLE" "MISSION_SUMMARY=$SUMMARY" \
    "EXIT_CRITERIA=See docs/superpowers/specs/2026-05-22-v94-dogfood-and-run-lifecycle-design.md"
done
cp "$REPO_ROOT/templates/run/.mission-config.yaml.tmpl" "$RUN_DIR/.mission-config.yaml"
subst_file "$RUN_DIR/.mission-config.yaml" "SLUG=$SLUG" "UTC_ISO=$UTC_ISO" "MISSION_TITLE=$TITLE"

# Seed structured RUN-START event.
echo "RUN-START $UTC slug=$SLUG" > "$RUN_DIR/.mission-events"

# Generate per-lane launch files.
uv run python3 "$HERE/gen_lane_launches.py" --mission-dir "$RUN_DIR" --out-dir "$RUN_DIR" \
  || echo "WARN: gen_lane_launches.py failed; generate launch files manually" >&2

echo "Scaffolded run: $RUN_DIR"
echo
echo "Launch:"
echo "  ./scripts/start_applier.sh $RUN_DIR &"
echo "  ./scripts/launch_fleet.sh --mission-dir $RUN_DIR --spawn --port 8765"
echo "  open http://localhost:8765/  (token in $RUN_DIR/.fleet/ui.token)"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest scripts/tests/test_new_run.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/new_run.sh scripts/tests/test_new_run.py
git commit -m "feat(v9.4): new_run.sh — scaffold runs/<UTC>--<slug>/ from templates"
```

### Task 1.5: `archive_run.sh`

**Files:**
- Create: `scripts/archive_run.sh`
- Test: `scripts/tests/test_archive_run.py`

- [ ] **Step 1: Write the failing test**

```python
"""v9.4 — archive_run.sh moves a terminal run dir to .archive/ via git mv."""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _make_repo(tmp_path):
    work = tmp_path / "repo"; work.mkdir()
    _git(["init", "-q"], work)
    _git(["config", "user.email", "t@t"], work)
    _git(["config", "user.name", "t"], work)
    (work / "scripts").symlink_to(REPO / "scripts")
    (work / "templates").symlink_to(REPO / "templates")
    (work / ".archive").mkdir()
    (work / ".archive" / "INDEX.md").write_text(
        "# Index\n\n| Run ID | Mission | Started | Completed | Wall clock | Outputs |\n|---|---|---|---|---|---|\n"
    )
    return work


def _scaffold_terminal_run(work):
    subprocess.run(["bash", "scripts/new_run.sh", "demo", "--title", "T", "--summary", "S"],
                   cwd=work, env={**__import__("os").environ, "RUN_LIB_REPO_ROOT": str(work)},
                   check=True, capture_output=True, text=True)
    rd = next((work / "runs").glob("*--demo"))
    (rd / "findings" / "f1.md").write_text("finding\n")
    # Write a terminal event.
    (rd / ".mission-events").write_text("RUN-START ...\nCOMPLETE done\n")
    _git(["add", "-A"], work); _git(["commit", "-qm", "run"], work)
    return rd


def test_archive_moves_and_registers(tmp_path):
    import os
    work = _make_repo(tmp_path)
    rd = _scaffold_terminal_run(work)
    res = subprocess.run(["bash", "scripts/archive_run.sh", str(rd)], cwd=work,
                         env={**os.environ, "RUN_LIB_REPO_ROOT": str(work)},
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert not rd.exists()  # moved out of runs/
    archived = list((work / ".archive").glob("*--demo"))
    assert len(archived) == 1
    assert (archived[0] / "findings" / "f1.md").exists()
    idx = (work / ".archive" / "INDEX.md").read_text()
    assert "--demo" in idx


def test_refuses_live_run(tmp_path):
    import os
    work = _make_repo(tmp_path)
    rd = _scaffold_terminal_run(work)
    (rd / ".mission-events").write_text("RUN-START still going\n")  # make it live
    _git(["add", "-A"], work); _git(["commit", "-qm", "live"], work)
    res = subprocess.run(["bash", "scripts/archive_run.sh", str(rd)], cwd=work,
                         env={**os.environ, "RUN_LIB_REPO_ROOT": str(work)},
                         capture_output=True, text=True)
    assert res.returncode != 0
    assert rd.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest scripts/tests/test_archive_run.py -v`
Expected: FAIL (`archive_run.sh` missing).

- [ ] **Step 3: Implement `scripts/archive_run.sh`**

```bash
#!/usr/bin/env bash
# Archive a run dir: git mv runs/<UTC>--<slug>/ -> .archive/<UTC>--<slug>/,
# then append one INDEX.md row. Transactional + idempotent.
#
# Usage: scripts/archive_run.sh <run-dir> [--force]
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/run_lib.sh"
REPO_ROOT="${RUN_LIB_REPO_ROOT:-$REPO_ROOT}"

RUN_DIR=""; FORCE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    *)       RUN_DIR="$1"; shift ;;
  esac
done
[[ -n "$RUN_DIR" && -d "$RUN_DIR" ]] || { echo "usage: archive_run.sh <run-dir>" >&2; exit 2; }
RUN_DIR="$(cd "$RUN_DIR" && pwd)"
assert_under_runs_or_archive "$RUN_DIR"

NAME="$(basename "$RUN_DIR")"            # <UTC>--<slug>
DEST="$REPO_ROOT/.archive/$NAME"

# Idempotent: already archived?
if [[ -d "$DEST" ]]; then
  echo "Already archived: $DEST (no-op)"; exit 0
fi

# Refuse a live run.
if [[ $FORCE -eq 0 ]] && uv run python3 "$HERE/_run_liveness.py" "$RUN_DIR/.mission-events"; then
  echo "REFUSING: $RUN_DIR is still live (last event non-terminal). Pass --force to override." >&2
  exit 1
fi

SRC_COUNT="$(find "$RUN_DIR" -type f | wc -l | tr -d ' ')"
assert_under_runs_or_archive "$DEST"
git -C "$REPO_ROOT" mv "$RUN_DIR" "$DEST"
DEST_COUNT="$(find "$DEST" -type f | wc -l | tr -d ' ')"
[[ "$SRC_COUNT" == "$DEST_COUNT" ]] || { echo "ABORT: file count mismatch ($SRC_COUNT != $DEST_COUNT)" >&2; exit 1; }
touch "$DEST/.archived"

# Register one INDEX row (dedup by run ID).
if ! grep -q "\`$NAME\`" "$REPO_ROOT/.archive/INDEX.md"; then
  TITLE="$(grep -m1 '^# Mission' "$DEST/MISSION.md" 2>/dev/null | sed 's/^# Mission — //' || echo "$NAME")"
  printf '| `%s` | %s | %s | %s | %s | %s |\n' \
    "$NAME" "$TITLE" "n/a" "$(run_utc_iso)" "n/a" "see $DEST/README.md" \
    >> "$REPO_ROOT/.archive/INDEX.md"
fi

echo "Archived: $DEST"
echo "Registered in .archive/INDEX.md"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest scripts/tests/test_archive_run.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/archive_run.sh scripts/tests/test_archive_run.py
git commit -m "feat(v9.4): archive_run.sh — transactional git mv to .archive/ + INDEX register"
```

---

## Phase 2 — Pre-flight gate

### Task 2.1: `preflight.sh`

**Files:**
- Create: `scripts/preflight.sh`
- Test: `scripts/tests/test_preflight.py`

- [ ] **Step 1: Write the failing test**

```python
"""v9.4 — preflight.sh checks (each emits a CHECK line; exits non-zero on any fail)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_preflight_reports_all_checks():
    res = subprocess.run(["bash", "scripts/preflight.sh", "--dry-run"],
                         cwd=REPO, capture_output=True, text=True,
                         env={**os.environ})
    out = res.stdout + res.stderr
    for label in ["CHECK pytest-scope", "CHECK test-deps", "CHECK friction-allowlist",
                  "CHECK lifecycle-scripts"]:
        assert label in out, f"missing {label}\n{out}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest scripts/tests/test_preflight.py -v`
Expected: FAIL (`preflight.sh` missing).

- [ ] **Step 3: Implement `scripts/preflight.sh`**

```bash
#!/usr/bin/env bash
# Pre-flight gate for a dogfood run. Each check prints "CHECK <name> PASS|FAIL".
# Exits non-zero if any check fails. --dry-run skips the live loops-armed check.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
DRY=0; [[ "${1:-}" == "--dry-run" ]] && DRY=1
FAIL=0
ok()  { echo "CHECK $1 PASS"; }
bad() { echo "CHECK $1 FAIL: $2" >&2; FAIL=1; }

# 1. pytest collection scope.
if uv run pytest scripts/tests/test_pytest_collection_scope.py -q >/dev/null 2>&1; then
  ok pytest-scope; else bad pytest-scope "testpaths/norecursedirs not set"; fi

# 2. test deps + canonical green command (Python third of Task 4.2).
if uv run --extra test pytest scripts/tests -q >/dev/null 2>&1; then
  ok test-deps; else bad test-deps "scripts/tests not green under --extra test"; fi

# 3. friction allowlist.
if uv run pytest scripts/tests/test_settings_friction_allowlist.py -q >/dev/null 2>&1; then
  ok friction-allowlist; else bad friction-allowlist "helper-script wildcards missing"; fi

# 4. lifecycle scripts smoke (new_run -> archive_run on throwaway, in a temp git repo).
TMP="$(mktemp -d)"
(
  cd "$TMP"
  git init -q && git config user.email t@t && git config user.name t
  ln -s "$REPO_ROOT/scripts" scripts; ln -s "$REPO_ROOT/templates" templates
  mkdir .archive
  printf '# Index\n\n| Run ID | Mission | Started | Completed | Wall clock | Outputs |\n|---|---|---|---|---|---|\n' > .archive/INDEX.md
  RUN_LIB_REPO_ROOT="$TMP" bash scripts/new_run.sh smoke --title S --summary S >/dev/null
  rd="$(ls -d runs/*--smoke)"
  printf 'RUN-START x\nCOMPLETE x\n' > "$rd/.mission-events"
  git add -A && git commit -qm run
  RUN_LIB_REPO_ROOT="$TMP" bash scripts/archive_run.sh "$TMP/$rd" >/dev/null
  [[ -d "$TMP/.archive/"*"--smoke" ]] && [[ ! -d "$TMP/$rd" ]]
) && ok lifecycle-scripts || bad lifecycle-scripts "smoke round-trip failed"
rm -rf "$TMP"

# 5. loops armed (live only).
if [[ $DRY -eq 0 ]]; then
  echo "CHECK loops-armed MANUAL: confirm 6 lanes show >=2 STATUS heartbeats within 10 min"
fi

[[ $FAIL -eq 0 ]] && echo "PREFLIGHT: PASS" || { echo "PREFLIGHT: FAIL" >&2; exit 1; }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest scripts/tests/test_preflight.py -v`
Expected: PASS.

- [ ] **Step 5: Run the gate for real (dry-run)**

Run: `bash scripts/preflight.sh --dry-run`
Expected: four `CHECK ... PASS` lines + `PREFLIGHT: PASS`. If any FAIL, fix before proceeding (this IS Task 4.2's gate).

- [ ] **Step 6: Commit**

```bash
git add scripts/preflight.sh scripts/tests/test_preflight.py
git commit -m "feat(v9.4): preflight.sh gate (pytest-scope, deps, friction, lifecycle smoke)"
```

---

## Phase 3 — Stimulus harness

### Task 3.1: Stimulus harness skeleton + first assertion (stale lane)

**Files:**
- Create: `runs_harness/__init__.py`, `runs_harness/stimulus.py`
- Test: `scripts/tests/test_stimulus_harness.py`

- [ ] **Step 1: Write the failing test**

```python
"""v9.4 — stimulus harness asserts the dashboard reflects forced events.

Runs against a fake-spawner test server (no real fleet). Uses the existing
__fake__/emit, __fake__/set_state, _test/stale_override endpoints.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from runs_harness.stimulus import StimulusResult, run_stale_lane_check  # noqa: E402


def test_stimulus_result_shape():
    r = StimulusResult(name="x", passed=True, detail="ok", latency_ms=12.0)
    assert r.passed and r.name == "x" and r.latency_ms == 12.0


@pytest.mark.asyncio
async def test_stale_lane_check_against_fake_server(fake_server_base_url):
    # fake_server_base_url fixture: a running server with fake-spawner enabled.
    res = await run_stale_lane_check(fake_server_base_url, lane_short="A", deadline_s=5.0)
    assert isinstance(res, StimulusResult)
    assert res.passed, res.detail
```

(If a `fake_server_base_url` fixture does not exist, add it to `scripts/tests/conftest.py` spinning up `make_app(mission_dir=...)` with the fake-spawner flag, mirroring `test_activity_wall.py`'s server setup.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest scripts/tests/test_stimulus_harness.py -v`
Expected: FAIL (`runs_harness.stimulus` missing).

- [ ] **Step 3: Implement `runs_harness/__init__.py` + `runs_harness/stimulus.py`**

`runs_harness/__init__.py`: empty.

`runs_harness/stimulus.py`:

```python
"""Deterministic dashboard-visibility stimulus harness (v9.4 T4.3 gate).

For each prior failure mode, force a known condition via the server's test
endpoints, then assert the dashboard surface reflects it within a deadline.
Each check returns a StimulusResult; the harness exit code is the gate.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx


@dataclass
class StimulusResult:
    name: str
    passed: bool
    detail: str
    latency_ms: float


async def _wait_until(predicate, deadline_s: float, poll_s: float = 0.2):
    """Poll predicate() until truthy or deadline; return (ok, elapsed_ms)."""
    start = time.monotonic()
    while time.monotonic() - start < deadline_s:
        if await predicate():
            return True, (time.monotonic() - start) * 1000
        time.sleep(poll_s)
    return False, deadline_s * 1000


async def run_stale_lane_check(base_url: str, lane_short: str, deadline_s: float) -> StimulusResult:
    """Force a stale lane via _test/stale_override; assert it shows in /lanes/stale."""
    async with httpx.AsyncClient(base_url=base_url, timeout=10) as c:
        await c.post("/api/v1/_test/stale_override", json={"lane": lane_short, "stale": True})

        async def shows_stale():
            r = await c.get("/api/v1/lanes/stale")
            r.raise_for_status()
            stale = {x.get("short") or x.get("lane") for x in r.json().get("stale", r.json())}
            return lane_short in stale

        ok, ms = await _wait_until(shows_stale, deadline_s)
        return StimulusResult("stale-lane", ok,
                              "stale badge surfaced" if ok else "stale lane not surfaced before deadline",
                              ms)
```

(Adjust the `/api/v1/lanes/stale` and `_test/stale_override` payload shapes to match `megalodon_ui/server.py` exactly — read `server.py:2490` for the override body and the stale endpoint near `lanes/stale` for the response shape.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest scripts/tests/test_stimulus_harness.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runs_harness/ scripts/tests/test_stimulus_harness.py scripts/tests/conftest.py
git commit -m "feat(v9.4): stimulus harness skeleton + stale-lane visibility assertion"
```

### Task 3.2: Remaining data-fidelity assertions

**Files:**
- Modify: `runs_harness/stimulus.py`
- Test: `scripts/tests/test_stimulus_harness.py`

- [ ] **Step 1: Add failing tests** for each of: `run_activity_emit_check` (force `__fake__/emit` of a finding → assert activity wall shows it), `run_signal_fidelity_check` (write a signal file → assert signals surface matches the disk parse), `run_empty_state_check` (set_state empty → assert empty-state testid renders, no placeholder fallthrough). One test per check, asserting `.passed`.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest scripts/tests/test_stimulus_harness.py -v`
Expected: the 3 new tests FAIL.

- [ ] **Step 3: Implement the three check functions** in `stimulus.py`, each mirroring `run_stale_lane_check`'s structure: POST the stimulus to the relevant `__fake__`/test endpoint, then `_wait_until` the corresponding `GET` reflects it, returning a `StimulusResult`. Use the exact endpoint paths/shapes from `server.py` (`__fake__/emit` at `:1779`, `__fake__/set_state` at `:1807`).

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest scripts/tests/test_stimulus_harness.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add runs_harness/stimulus.py scripts/tests/test_stimulus_harness.py
git commit -m "feat(v9.4): activity/signal/empty-state visibility assertions"
```

### Task 3.3: Harness CLI runner

**Files:**
- Modify: `runs_harness/stimulus.py` (add `main()`)

- [ ] **Step 1: Add a `main(argv)`** that takes `--base-url` and `--json-out PATH`, runs every check sequentially against a live server, prints a `CHECK <name> PASS|FAIL <latency>ms` line each, writes a JSON summary, and exits non-zero if any check failed.

- [ ] **Step 2: Manual smoke** (documented, run during the dogfood, not in CI):

```bash
uv run python3 -m runs_harness.stimulus --base-url http://localhost:8765 --json-out /tmp/harness.json
```
Expected: one `CHECK ... PASS` line per assertion + `HARNESS: PASS`.

- [ ] **Step 3: Commit**

```bash
git add runs_harness/stimulus.py
git commit -m "feat(v9.4): stimulus harness CLI runner (--base-url, --json-out)"
```

### Task 3.4: Playwright interaction-fidelity specs

**Files:**
- Create: `ui/tests/e2e/visibility.spec.ts`

- [ ] **Step 1: Write the spec** with two tests:
  - *snap-back*: navigate to each of the 7 routes while triggering a slow `loadConfig()`; assert the active page/URL stays on the clicked tab (does not revert to dashboard).
  - *tab highlight*: visit each route; assert the nav element for that route has `aria-current` (the value `app.js` sets) and no other nav element does.

Use `@playwright/test`, `playwright.config.ts`'s existing `webServer`. Mirror selectors from `ui/static/js/app.js` (the router that sets `aria-current`).

- [ ] **Step 2: Run** `./scripts/run_e2e.sh ui/tests/e2e/visibility.spec.ts`
Expected: 2 specs PASS (or documented FAIL → file a finding; this is what the dogfood validates).

- [ ] **Step 3: Commit**

```bash
git add ui/tests/e2e/visibility.spec.ts
git commit -m "test(v9.4): playwright snap-back + tab-highlight interaction fidelity"
```

---

## Phase 4 — Doc updates (5 files, before kickoff)

### Task 4.1: Run-lifecycle convention doc

**Files:**
- Create: `docs/v9/v9-4-RUN-LIFECYCLE.md`

- [ ] **Step 1: Write the doc** covering: the `runs/<UTC>--<slug>/` layout, `new_run.sh`/`archive_run.sh`/`preflight.sh` usage, the liveness grammar (terminal tokens), the uniformity rule (everything to `.archive/` + `INDEX.md`; the v9.3 dogfood is a grandfathered exception with a back-filled entry), and the stimulus harness.

- [ ] **Step 2: Commit**

```bash
git add docs/v9/v9-4-RUN-LIFECYCLE.md
git commit -m "docs(v9.4): run-lifecycle convention"
```

### Task 4.2: README + HISTORY + root TASKS + INDEX back-fill

**Files:**
- Modify: `README.md`, `HISTORY.md`, `TASKS.md`, `.archive/INDEX.md`

- [ ] **Step 1: `README.md`** — add a "v9.4 dashboard" section (the new pages/endpoints, link to `docs/v9/api-contract.md`) and a "Run lifecycle" section linking `docs/v9/v9-4-RUN-LIFECYCLE.md`.

- [ ] **Step 2: `HISTORY.md`** — append a "V9.4 — run lifecycle + dogfood prep" entry summarizing the lifecycle scripts, pre-flight gate, friction-allowlist fix, and the stimulus harness.

- [ ] **Step 3: `TASKS.md`** (root) — update the header block: T4.3 `in progress` (dogfood lifecycle ready), point at this plan + `docs/v9/v9-4-RUN-LIFECYCLE.md`.

- [ ] **Step 4: `.archive/INDEX.md`** — back-fill one row for `2026-05-19--dogfood` pointing at `docs/v9/dogfood-2026-05-19/` (note: grandfathered location). Fix the "Megaladon" typo in the H1 while here.

- [ ] **Step 5: Commit**

```bash
git add README.md HISTORY.md TASKS.md .archive/INDEX.md
git commit -m "docs(v9.4): README/HISTORY/TASKS + INDEX back-fill for v9.3 dogfood"
```

---

## Phase 5 — Dogfood kickoff (operator-driven; NOT auto-run)

> This phase is an execution procedure for the operator (David), not code. Each step is a manual gate. Do not automate the 6-lane spawn from within this plan's execution.

### Task 5.1: Scaffold + pre-flight

- [ ] **Step 1:** `bash scripts/new_run.sh v94-ui-dogfood --title "v9.4 UI self-observation dogfood" --summary "Harden v9.4 dashboard + clear v9.x backlog + scope v10; each lane validates one dashboard surface against disk."`
- [ ] **Step 2:** Edit the scaffolded `runs/<UTC>--v94-ui-dogfood/TASKS.md` to seed the dual-charter tasks + the surface-ownership matrix (from spec Part B).
- [ ] **Step 3:** `bash scripts/preflight.sh --dry-run` → must print `PREFLIGHT: PASS`.

### Task 5.2: Spawn + arm

- [ ] **Step 1:** `./scripts/start_applier.sh runs/<UTC>--v94-ui-dogfood &` then verify `cat runs/<…>/queue/.applier.lock/heartbeat.txt` is fresh.
- [ ] **Step 2:** `./scripts/launch_fleet.sh --mission-dir runs/<UTC>--v94-ui-dogfood --spawn --port 8765`
- [ ] **Step 3:** Open `http://localhost:8765/` (token in `.fleet/ui.token`). Confirm loops-armed: all 6 lanes show ≥2 STATUS heartbeats within 10 min.

### Task 5.3: Run the gate

- [ ] **Step 1:** `uv run python3 -m runs_harness.stimulus --base-url http://localhost:8765 --json-out runs/<…>/.fleet/harness.json` → must print `HARNESS: PASS`.
- [ ] **Step 2:** `./scripts/run_e2e.sh ui/tests/e2e/visibility.spec.ts` → snap-back + tab-highlight PASS.
- [ ] **Step 3:** Soak ≥2h alongside the fleet; operator corroborates the 4 qualitative claims don't recur. Checkpoint-commit source edits every ~30 min.

### Task 5.4: Archive + close

- [ ] **Step 1:** Write a terminal `.mission-events` line (`COMPLETE <utc> ...` or `DEGRADED-CLOSE ...`).
- [ ] **Step 2:** `bash scripts/archive_run.sh runs/<UTC>--v94-ui-dogfood` → moved to `.archive/`, `INDEX.md` row added.
- [ ] **Step 3:** Hand off to T5.1 (final v9.4 docs, referencing the archived dogfood + `harness.json`).

---

## Self-Review

**Spec coverage:**
- Part A (lifecycle/templates) → Tasks 1.1–1.5 ✓
- Part A.5 (pre-flight gate) → Phase 0 + Task 2.1 ✓ (Task 4.2 execution = preflight check 2 + step 5)
- Part B (dogfood + harness + surface matrix) → Phase 3 + Phase 5 ✓
- Part C (5-file doc updates) → Phase 4 ✓
- Liveness grammar → Task 1.1 ✓
- `queue/` in lifecycle → `new_run.sh` init (Task 1.4) + moved wholesale by `git mv` (Task 1.5) ✓
- Transactional archive → Task 1.5 (git mv + count verify + sentinel + idempotent) ✓

**Placeholder scan:** Tasks 3.2 / 3.4 / 4.x describe content rather than full code where the exact server payload shapes / nav selectors must be read from source at implementation time — flagged explicitly with the file:line to read. All bash/python scripts have complete code.

**Type consistency:** `StimulusResult(name, passed, detail, latency_ms)` used identically across 3.1–3.3. `is_live`/`last_token`/`TERMINAL_TOKENS` consistent between `_run_liveness.py` and `run_lib.sh` (bash calls the python CLI, single source of truth). `RUN_LIB_REPO_ROOT` override consistent across `run_lib.sh`/`new_run.sh`/`archive_run.sh`/tests.

**Known follow-up for implementer:** confirm `gen_lane_launches.py --out-dir` writes `launch-<LANE>.md` into the run dir (Task 1.4 step 3); if its output naming differs, adjust the launch-file references in `new_run.sh`.
