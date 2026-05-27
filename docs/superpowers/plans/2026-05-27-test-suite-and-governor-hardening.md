# Test-Suite Reliability + Governor Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the megalodon test suite *accurate* (no false/stale claims, no hollow tests), *reliable* (catches real regressions, parallel-safe), and *fast* (parallelized local pre-push gate) — and, as P0, close the live governor sandbox escapes the audit uncovered and lock them with a red-team test matrix.

**Architecture:** Four independently-shippable phases in priority order. **P0 Governor** extends the policy denylists to close the confirmed sandbox escapes and adds a parametrized red-team test matrix. **P1** fixes broken/masked tests and the two confirmed product bugs. **P2** makes the suite parallel-safe and adds the parallelized local pre-push gate (the original ask). **P3** removes hollow/brittle tests, dead code, warning noise, doc inaccuracies, and fills the highest-value coverage gaps. Each phase ends green; phases may ship as separate commits/PRs.

> **CORRECTION (contrarian review, 2026-05-27) — read before P0.** The governor Bash engine is **allow-by-default + deny-matched-dangerous**, NOT deny-by-default (only unknown *non-Bash* tools are deny-by-default). P0 is therefore **incremental denylist extension on an allow-by-default surface**, not an architecture change — there is no single switch. This has two consequences the plan must honor: (1) a denylist cannot enumerate every escape — see "Residual risk" below; (2) the operator's "deny all escape classes" decision is implemented by adding denylist entries/branches, and legitimate commands are NOT re-allowed via an override of a default-deny (there is no default-deny) — they simply continue to match no deny rule. The allow-override (`approval-rules.json`) only matters for commands we *newly* deny that the fleet legitimately needs.

**Tech Stack:** Python 3.14 / pytest / pytest-xdist (to be added) / `uv`; Playwright (`@playwright/test`) with per-project webServers; `node:test` for JS; `fcntl`-based atomic file IO; the governor policy engine (`megalodon_ui/governor/policy.py`).

**Ground-truth baselines (verified 2026-05-27):** `pytest -m "not isolated"` = 1557 passed / 3 xfailed (2m42s); `-m isolated --forked` = 15 passed / 2 xfailed (82s, runs on macOS, tmux 3.6b); JS = 67 tests; line coverage 87.4%. CI/GitHub Actions has been removed; the gate is local-only.

**Policy decision (operator, 2026-05-27):** deny ALL the escape classes below. **The real fleet gate command is `scripts/run_tests.sh`** (allowlisted as `Bash(scripts/run_tests.sh:*)` per every `launch-*.md` RULE 14b) — **NOT** `make test` and **NOT** `uv run pytest` (the latter denies today as an interpreter escape; lanes invoke the script, not pytest directly). P0.9 verifies the *real* gate path survives hardening. The `make`/build-runner denials (P0.8) do not affect the fleet (it never runs make); they close a generic exec surface, overridable per-project via `approval-rules.json`.

### Residual risk / non-goals (P0)

A denylist on an allow-by-default Bash engine **cannot converge** — these remain OPEN after P0 and are explicitly out of scope:

- **Arbitrary script-head execution:** `./deploy.sh`, `scripts/anything.sh`, any executable file head not in a denylist → ALLOW (this is also *why* the legit gate `scripts/run_tests.sh` works). For the v10 external-target threat model an autonomous lane could run `./malicious.sh` *in the target repo* and it would be allowed.
- **Cross-command symlink laundering:** create a dir symlink in one command, write through it in the next — unreachable for a stateless per-command engine.
- **Unknown destructive porcelain:** e.g. `git reset --hard`, `git clean -fdx` (only git config-injection is denied today).

**FLAGGED OPERATOR DECISION (defer to v10, do not resolve in this plan):** the only *converging* design is an **allowlist** for Bash command heads (deny unknown heads, allow a vetted set), which is a larger redesign. P0 closes the enumerated holes and documents these residuals; it does NOT claim the surface is closed. The self-review must not overstate coverage.

---

## Audit findings index (the spec this plan implements)

All findings were produced by an 11-subagent deep-dive + coverage run + firsthand verification on 2026-05-27. Confirmed-by-direct-`decide()` governor escapes and confirmed product bugs are cited inline. Where a finding is an agent claim not yet independently reproduced, the task's first step re-verifies it.

---

# PHASE P0 — Governor security hardening (deny-by-default restored)

**Why first:** the governor is the only constraint on an autonomous fleet writing/executing outside its target. The audit confirmed ~20 live escapes. Per operator priority Security > Correctness > Tests, this ships first.

**Key file:** `megalodon_ui/governor/policy.py` (1430 LOC). Relevant mechanisms (read these before editing): `_DASH_C_SHELLS` (set of shells denied with `-c`), `_INTERPRETER_HEADS`, `_WRAPPER_HEADS` (commands peeled so their inner command is adjudicated, e.g. `timeout`, `nohup`, `env`, `nice`, `ionice`, `stdbuf` — note `timeout rm -rf /` correctly DENIES today), `_MUTATION_HEADS` + `_MUTATION_VALUE_FLAGS`, the `track_target = head_base in ("cp","mv","install")` line (~719) that gates write-target scope-checking, `_adjudicate_write_target`, `_segment_sed_dangerous`, `_segment_awk_dangerous` (has the `-f` deny that sed lacks), `_segment_tar_dangerous`, `_segment_destructive_rm`, and `decide()` (top-level, wraps everything fail-closed). Decision dataclass fields: `permission` ('allow'/'deny'), `reason`, `category`.

**Confirmed escapes to close (all currently return `permission='allow'`):**
- Floor bypass: `setsid rm -rf /`, `watch rm -rf /` (these wrappers are not peeled).
- Shells/interpreters with code: `fish/csh/tcsh/nu/pwsh/elvish/xonsh -c "..."`, `php -r "..."`.
- Script exec builtins: `source evil.sh`, `. evil.sh`.
- Exec via tools: `vim -c "!cmd"` (and `vi/ed/ex/nano/emacs`), `make`, `parallel`.
- Write-target escapes: `ln -t /etc`, `ln --target-directory=/etc`, `tar -C /etc -xf`, `tar --directory=/etc`, `mktemp -p /etc`, `mktemp /etc/tmpXXXX`, `split -b 10 in /etc/out`, `csplit -f /etc/out`, `pv -o /etc/x`, `sed -f evil.sed`.
- Out-of-scope delete: plain `rm /etc/passwd`, `rm ~/.ssh/id_rsa` (no `-r`/`-f`).

### Task P0.1: Red-team test matrix (RED — this is the executable spec)

**Files:**
- Create: `scripts/tests/test_governor_redteam.py`

- [ ] **Step 1: Write the parametrized failing test**

```python
"""Red-team matrix for the governor write-scope/exec policy.

Every command here MUST be denied: it would either execute arbitrary code,
bypass a floor (rm -rf /, sudo), or write/delete outside the single allowed
root (project_dir). Confirmed as live escapes 2026-05-27; this matrix is the
spec for the P0 hardening. Legitimate in-scope commands are in ALLOW_OK to
prove the tightening did not over-block.
"""
import tempfile
import pytest
from megalodon_ui.governor.policy import decide

# MUST be under /tmp: policy `_is_temp_path` only honors /tmp & /private/tmp, and
# `_segment_destructive_rm` only allows recursive rm under an explicit temp root.
# mkdtemp() defaults to /var/folders on macOS, which would make in-scope ALLOW_OK
# cases (and recursive rm) wrongly DENY. (contrarian C3)
PROJECT_DIR = tempfile.mkdtemp(prefix="gov-redteam-", dir="/tmp")

# (command, short-reason) — all MUST deny.
DENY_CASES = [
    # floor bypass via unpeeled wrappers
    ("setsid rm -rf /", "wrapper-floor-bypass"),
    ("watch rm -rf /", "wrapper-floor-bypass"),
    ("setsid sudo rm -rf /", "wrapper-floor-bypass"),
    # shells with -c
    ('fish -c "rm -rf /"', "shell-c"),
    ('csh -c "rm -rf /"', "shell-c"),
    ('tcsh -c "rm -rf /"', "shell-c"),
    ('nu -c "rm -rf /"', "shell-c"),
    ('pwsh -c "Remove-Item /"', "shell-c"),
    ('elvish -c "rm -rf /"', "shell-c"),
    ('xonsh -c "rm -rf /"', "shell-c"),
    ('php -r "system(1);"', "interpreter"),
    # env/wrapper to a non-denied shell
    ('env fish -c "rm -rf /"', "wrapper-then-shell"),
    ('nohup fish -c "rm -rf /"', "wrapper-then-shell"),
    # script-exec builtins
    ("source /tmp/evil.sh", "source-exec"),
    (". /tmp/evil.sh", "dot-exec"),
    # editors that shell out / write back
    ('vim -c "!rm -rf /"', "editor-exec"),
    ("vim /etc/passwd", "editor-write"),
    ("ed /etc/passwd", "editor-write"),
    # arbitrary code runners
    ("make", "build-runner"),
    ("make -f /tmp/evil.mk", "build-runner"),
    ("parallel rm ::: /etc/passwd /etc/hosts", "parallel-exec"),
    # write-target escapes
    ("ln -t /etc/ README.md", "ln-target"),
    ("ln --target-directory=/etc/ README.md", "ln-target"),
    ("tar -C /etc -xf a.tar", "tar-dir"),
    ("tar --directory=/etc -xf a.tar", "tar-dir"),
    ("mktemp -p /etc/ XXXXXX", "mktemp-out"),
    ("mktemp /etc/tmpXXXXXX", "mktemp-out"),
    ("split -b 10 infile /etc/out", "split-out"),
    ("csplit -f /etc/out infile 10", "csplit-out"),
    ("pv -o /etc/x infile", "pv-out"),
    ("sed -f /tmp/evil.sed file.txt", "sed-script-file"),
    # plain rm of out-of-scope / secret
    ("rm /etc/passwd", "rm-out-of-scope"),
    ("rm /root/.ssh/id_rsa", "rm-secret"),
]

# Must still ALLOW (do not over-block in-scope work). NOTE: recursive rm is NOT
# here — the engine only allows `rm -rf` under an EXPLICIT temp literal, not a
# relative in-scope path, so `rm -rf subdir` legitimately denies by design
# (contrarian C3). Calibrate this list against ACTUAL current behavior in Step 0.
ALLOW_OK = [
    "echo hello",
    "ls -la",
    "cat README.md",
    "mkdir subdir",
    "cp a.txt b.txt",          # in-scope copy
    "rm a.txt",                # in-scope NON-recursive delete
    "grep -r foo .",
]

# Controls that already deny — must STAY denied (regression guard).
STILL_DENY = [
    "rm -rf /",
    "sudo rm -rf /",
    "timeout 5 rm -rf /",
    "nohup rm -rf /",
    "zsh -c 'rm -rf /'",
    "awk -f /tmp/e.awk f",
    "dd if=a of=/etc/x",
    "cat /etc/shadow",
]


@pytest.mark.parametrize("cmd,why", DENY_CASES, ids=[c[0] for c in DENY_CASES])
def test_redteam_denies(cmd, why):
    d = decide("Bash", {"command": cmd}, project_dir=PROJECT_DIR, lane="AUDIT")
    assert d.permission == "deny", f"ESCAPE [{why}]: {cmd!r} -> {d.permission}/{d.category}"


@pytest.mark.parametrize("cmd", ALLOW_OK, ids=ALLOW_OK)
def test_redteam_allows_in_scope(cmd):
    d = decide("Bash", {"command": cmd}, project_dir=PROJECT_DIR, lane="AUDIT")
    assert d.permission == "allow", f"OVER-BLOCK: {cmd!r} -> {d.permission}/{d.category}"


@pytest.mark.parametrize("cmd", STILL_DENY, ids=STILL_DENY)
def test_redteam_controls_still_deny(cmd):
    d = decide("Bash", {"command": cmd}, project_dir=PROJECT_DIR, lane="AUDIT")
    assert d.permission == "deny", f"REGRESSION: {cmd!r} now {d.permission}/{d.category}"
```

- [ ] **Step 2: Run to confirm RED — and CALIBRATE the baseline (do not assume)**

Run: `uv run --extra test pytest scripts/tests/test_governor_redteam.py -q`
Expected: `test_redteam_denies` FAILs en masse (most return `allow` today). **`test_redteam_allows_in_scope` and `test_redteam_controls_still_deny` MUST be green on this first run** — if any case there fails, the matrix encodes a false assumption (contrarian C3 found exactly this): move the case to the correct list to match the engine's *actual* current behavior. The matrix is only a valid spec once ALLOW_OK + STILL_DENY are green pre-hardening. Record the failing DENY count — that is the P0 work list.

- [ ] **Step 3: Commit the red matrix**

```bash
git add scripts/tests/test_governor_redteam.py
git commit -m "test(governor): red-team escape matrix (red) — spec for P0 hardening"
```

> **Implementation note for P0.2–P0.8:** Each task below makes a subset of `test_redteam_denies` go green by editing `policy.py`. After each, run the FULL matrix plus the existing `scripts/tests/test_governor_policy.py` to ensure no `ALLOW_OK`/`STILL_DENY` regression. Read the cited function before editing; make the minimal denylist/branch change that turns the targeted cases green without breaking controls.

### Task P0.2: Complete the shell/interpreter denylists

**Files:** Modify `megalodon_ui/governor/policy.py` (`_DASH_C_SHELLS`, `_INTERPRETER_HEADS`).

- [ ] **Step 1** Add to `_DASH_C_SHELLS`: `fish`, `csh`, `tcsh`, `ksh` (if absent), `nu`, `pwsh`, `powershell`, `elvish`, `xonsh`. Add `php` to `_INTERPRETER_HEADS` (covers `php -r` and `php script.php`). Ensure the "interpreter head with any args → deny" path covers these.
- [ ] **Step 2** Run: `uv run --extra test pytest scripts/tests/test_governor_redteam.py -q -k "shell or interpreter or wrapper_then_shell"` → those cases PASS; controls still green.
- [ ] **Step 3** Commit: `fix(governor): deny -c on all shells + php interpreter`.

### Task P0.3: Block `source` / `.` script-exec builtins

**Files:** Modify `policy.py` (add a builtin-exec check; category e.g. `bash-interpreter`).

- [ ] **Step 1** Treat a segment whose head is `source` or `.` (with a file operand) as code execution → deny. Place the check where interpreter heads are evaluated.
- [ ] **Step 2** Run matrix `-k "source or dot"` → PASS; `ALLOW_OK` unaffected (none use `.`/`source`).
- [ ] **Step 3** Commit: `fix(governor): deny source/. script execution`.

### Task P0.4: Peel `setsid`/`watch` (close the floor bypass)

**Files:** Modify `policy.py` (`_WRAPPER_HEADS` or equivalent peel set).

- [ ] **Step 1** Add `setsid` and `watch` to the wrapper-peel set so their inner command is adjudicated (mirrors how `timeout`/`nohup rm -rf /` already deny). For `watch`, skip its own flags (`-n N`, `-d`) before the inner command. Verify `setsid sudo rm -rf /` denies (peels to `sudo` → privilege floor).
- [ ] **Step 2** Run matrix `-k "wrapper_floor"` → PASS; `timeout`/`nohup` controls still deny.
- [ ] **Step 3** Commit: `fix(governor): peel setsid/watch so inner command hits the floor`.

### Task P0.5: Write-target scope-checking for ln/tar/mktemp/split/csplit/pv

**Files:** Modify `policy.py` (`track_target` set, `_MUTATION_HEADS`, `_segment_tar_dangerous`, write-target adjudication).

- [ ] **Step 1** Add `ln` to the `track_target` set (line ~719) so its `-t`/`--target-directory` value is scope-checked by `_adjudicate_write_target`. Add `mktemp`, `split`, `csplit`, `pv` to `_MUTATION_HEADS` with their write-destination operand identified (`mktemp`: `-p DIR` value or the template positional; `split`: last positional prefix; `csplit`: `-f` value; `pv`: `-o` value). Extend `_segment_tar_dangerous` to scope-check `-C`/`--directory` value AND the archive output file for create mode.
- [ ] **Step 2** Run matrix `-k "ln_target or tar_dir or mktemp or split or csplit or pv"` → PASS; in-scope `cp a.txt b.txt`, `mkdir subdir` still ALLOW.
- [ ] **Step 3** Commit: `fix(governor): scope-check write targets for ln/tar/mktemp/split/csplit/pv`.

### Task P0.6: `sed -f` external-script deny (parity with `awk -f`)

**Files:** Modify `policy.py` (`_segment_sed_dangerous`).

- [ ] **Step 1** In `_segment_sed_dangerous`, add an early `if a in ("-f", "--file"): return True` (deny) — matching `_segment_awk_dangerous`'s treatment, because `sed -f` runs an unread external script that can shell-exec via the `e` command.
- [ ] **Step 2** Run matrix `-k "sed_script_file"` → PASS; an in-scope `sed 's/a/b/' f.txt` (inline, no `-f`) still ALLOW (add to ALLOW_OK if needed).
- [ ] **Step 3** Commit: `fix(governor): deny sed -f external script (awk -f parity)`.

### Task P0.7: Plain `rm` of out-of-scope / secret paths

**Files:** Modify `policy.py` (`_segment_destructive_rm`).

- [ ] **Step 1** When `rm` is NOT recursive/force, still scope-check each path operand: deny if any target resolves outside `project_dir` or matches the secret-path patterns. Keep in-scope `rm file.txt` / `rm -rf subdir` ALLOW.
- [ ] **Step 2** Run matrix `-k "rm_out_of_scope or rm_secret"` → PASS; `rm -rf subdir` (ALLOW_OK) still allows.
- [ ] **Step 3** Commit: `fix(governor): scope-check plain rm targets`.

### Task P0.8: Deny exec-via-tools (editors, make/build-runners, parallel) — deny-by-default

**Files:** Modify `policy.py` (new `_EDITOR_HEADS`, `_BUILD_RUNNER_HEADS`, `_EXEC_WRAPPER_HEADS`).

- [ ] **Step 1** Deny: editors `vim/vi/ex/ed/nano/emacs` (category `bash-editor`); build/task runners `make/just/task/rake/gradle/mvn/ant` and `parallel`/`xargs`-with-command (category `bash-exec-runner`). These are arbitrary-code surfaces. Legitimate gate commands are handled by the allow-override in P0.9 — do NOT special-case `make test` here.
- [ ] **Step 2** Run matrix `-k "editor or build_runner or parallel"` → PASS.
- [ ] **Step 3** Commit: `fix(governor): deny editors/build-runners/parallel exec (deny-by-default)`.

### Task P0.9: Don't break the fleet — verify the REAL gate path + override-ability of new denials

**Files:** Test `scripts/tests/test_governor_redteam.py` (extend); read `policy.py` allow-override path (`_load_override_patterns` / approval-rules consumer), `_FLOOR_CATEGORIES`, and `scripts/run_tests.sh` / `scripts/run_e2e.sh`.

- [ ] **Step 1: The real gate must keep working.** Add tests asserting the actual fleet gate commands still ALLOW after P0.2–P0.8:

```python
GATE_CMDS = ["scripts/run_tests.sh", "scripts/run_tests.sh -q", "scripts/run_e2e.sh"]

@pytest.mark.parametrize("cmd", GATE_CMDS, ids=GATE_CMDS)
def test_fleet_gate_commands_still_allow(cmd):
    d = decide("Bash", {"command": cmd}, project_dir=PROJECT_DIR, lane="TEST")
    assert d.permission == "allow", f"P0 broke the fleet gate: {cmd!r} -> {d.permission}/{d.category}"
```
(These are allowlisted script heads — they should remain `allow`. If P0.8's editor/exec denials accidentally catch them, fix the denial scope.)

- [ ] **Step 2: New denials must be OVERRIDABLE (non-floor).** Every category introduced in P0.2–P0.8 (`bash-editor`, `bash-exec-runner`, the new write-target denials, etc.) MUST NOT be added to `_FLOOR_CATEGORIES` — otherwise `approval-rules.json` cannot lift them per-project (forgotten-item #4). Add a test that an `approval-rules.json` entry granting e.g. `Bash(make:*)` flips a P0.8-denied `make` to `allow` with an override category, proving the new denials are overridable (use the real override mechanism `decide()` reads).

- [ ] **Step 3: Close the matrix via the HOOK subprocess, not just `decide()`.** Extend `scripts/tests/test_governor_hook.py` (or `test_governor_settings_valid.py`) with a subprocess case piping one representative new-escape event (e.g. `setsid rm -rf /`) through `scripts/governor_hook.py` and asserting the hook emits a deny decision + audit line — the matrix tests `decide()` in-process, but the fleet uses the hook subprocess (forgotten-item #3).

- [ ] **Step 4** Document in `policy.py` + `README.md` governor section: the Bash engine is allow-by-default with a dangerous-command denylist; newly-denied generic exec commands (`make`, editors, build-runners) are overridable per-project via `approval-rules.json`; the fleet gate runs via the allowlisted `scripts/run_tests.sh`. Commit: `feat(governor): keep real gate path working; new denials overridable`.

### Task P0.10: Full governor green + matrix all-deny

- [ ] **Step 1** Run: `uv run --extra test pytest scripts/tests/test_governor_redteam.py scripts/tests/test_governor_policy.py scripts/tests/test_governor_hook.py -q`
Expected: ALL pass (every `DENY_CASES` denies, `ALLOW_OK` allows, controls hold, existing policy tests green).
- [ ] **Step 2** Also fix the two governor test-quality issues found in the audit: tighten `test_governor_hook_e2e.py:230` category assertion (remove the `or deny_categories` fallback); un-patch or assert-called `governor_canary_selftest` in `test_governor_canary.py:261`.
- [ ] **Step 3** Commit: `test(governor): tighten e2e category + canary assertions`.

---

# PHASE P1 — Broken/masked tests + confirmed product bugs

### Task P1.1: Fix `test_startup_timeout` (un-mask the unverified feature)

**Files:** Modify `ui/tests/integration/test_startup_timeout_cleans_up_token_and_listener.py`; possibly `megalodon_ui/__main__.py` / `server.py` lifespan.

- [ ] **Step 1** Reproduce: `uv run --extra test pytest <file> --runxfail -q` → fails exit 10 ("socket path exceeds 100 bytes"), not the xfail's claimed exit 0. Root cause: the subprocess mission dir is a deep pytest `tmp_path`, tripping the pre-lifespan socket guard before the startup-timeout logic runs.
- [ ] **Step 2** Fix the test to use a short mission dir (mirror the `short_mission_dir` conftest fixture / `/tmp` root) OR set `MEGALODON_SKIP_SOCKET_BUDGET=1` for this subprocess. NOTE (contrarian M1): this test lives under `ui/tests/integration/`, whose conftest scope differs from `scripts/tests/conftest.py` (which already sets `MEGALODON_SKIP_SOCKET_BUDGET=1`); verify what env the spawned subprocess actually inherits and set it explicitly in the test's `subprocess` call rather than assuming. Remove the `@pytest.mark.xfail`.
- [ ] **Step 3** Run → the test now exercises the lifespan-timeout path. **Two outcomes, two tasks:**
  - **(3a) If it passes** (exit 11 + token/listener cleanup): done — commit `fix(test): un-mask startup-timeout test; verify exit 11 cleanup`.
  - **(3b) If exit 11 does NOT occur**, the feature has a real bug. **Do NOT fix it inline** — that is unbounded lifespan debugging. Re-mark the test `xfail` with a CORRECT reason (cite the real observed exit code / behavior, not the false "no-op exits 0"), file a separate `TASKS.md` item "lifespan startup-timeout (exit 11) not honored — repro + fix" with the reproduction, and commit `fix(test): correct startup-timeout xfail reason (real bug filed separately)`. The lifespan fix is its own estimated task, gated on reproduction.

### Task P1.2: Remove the hollow `assert False` SIGTERM test

**Files:** Modify `scripts/tests/test_launch_fleet_v92.py` (delete `test_sigterm_propagation_in_spawn_mode`, lines ~239-262).

- [ ] **Step 1** Delete the permanently-xfailing `assert False` placeholder. If real SIGTERM coverage is wanted, add a follow-up TODO in `TASKS.md` to write it as an `@pytest.mark.isolated` real-tmux test; do not leave a hollow stub.
- [ ] **Step 2** Run the file → still green (one fewer xfail). Commit: `test: delete hollow assert-False SIGTERM placeholder`.

### Task P1.3: Fix applier `submitting_lane` → `lane` logging bug

**Files:** Modify `megalodon_ui/queue/applier.py` (lines ~335/348/365); Test `scripts/tests/test_queue_applier.py`.

- [ ] **Step 1: Failing test** — submit a request with `lane="A"`, drain, assert the APPLIED log line contains `lane=A` (not `lane=?`).
- [ ] **Step 2** Run → fails (`lane=?`).
- [ ] **Step 3** Change the three `req.get("submitting_lane", "?")` to `req.get("lane", "?")`.
- [ ] **Step 4** Run → pass. Commit: `fix(applier): log real submitting lane (was always '?')`.

### Task P1.4: MISSION.md atomic write + concurrent-read test

**Files:** Modify `megalodon_ui/server.py` (~3810 `post_v1_mission_status` write); Test `ui/tests/integration/test_state_source_of_truth.py`.

- [ ] **Step 1: Failing test** — assert the mission-status write is atomic: patch/observe that the write goes to a temp file + `os.replace` (no truncated-read window). A practical test: monkeypatch `Path.write_text` to fail mid-write and assert MISSION.md is either fully old or fully new, never empty/partial.
- [ ] **Step 2** Run → fails (current `write_text` is non-atomic).
- [ ] **Step 3** Replace with atomic write (temp in same dir → `os.replace`), reusing the project's existing atomic-write helper if present.
- [ ] **Step 4** Run → pass. Commit: `fix(server): atomic MISSION.md write`.

### Task P1.5: Fix Playwright specs asserting dead testids

**Files:** Modify `ui/tests/e2e/test_fe_renders_with_custom_3_lane_config.spec.ts`, `ui/tests/e2e/test_fe_phase_navigator_custom_config.spec.ts`.

- [ ] **Step 0 (contrarian, promoted):** First confirm these specs are actually wired to a project's `testMatch` in `playwright.config.ts`. `BOARD_SPEC_PATTERN` may NOT cover `test_fe_renders_with_custom_3_lane_config` / `test_fe_phase_navigator_custom_config`. If they match NO project, they are dead/never-run — then the fix is to either route them to a project (with the right fixture) or delete them; fixing testids on a never-executed spec is pointless. Decide route-vs-delete before Step 1.
- [ ] **Step 1** If kept: replace every `lane-row-*` / `[data-testid^="lane-row-"]` assertion with the real testid `board-row-*` (emitted by `board.js:505`). Remove the unused `board-now-A` locator in `test_board_fix_round3.spec.ts:127`.
- [ ] **Step 2** Run those specs against their fixture project and confirm they actually execute and pass (not silently skip). Commit: `fix(e2e): route+fix (or delete) dead custom-config specs`.

### Task P1.6: De-hollow the highest-value hollow tests

**Files:** `ui/static/js/signals.js` + `ui/tests/unit/test_signal_merge_dedupe.test.js`; `ui/static/js/app.js` + `ui/tests/unit/test_router_path_params.test.js`; `scripts/tests/test_queue_applier.py` (T4); `scripts/tests/test_spawn_unit.py` (argv assertion).

- [ ] **Step 1** Export `mergedSignals` from `signals.js` and `_mountSeq`/the mount-guard from `app.js`; change the two JS tests to import and exercise the REAL functions (delete the local reimplementations).
- [ ] **Step 2** Fix queue T4 disk-full test: make only the HISTORY.md fsync fail (not the journal), and assert REJECTED is journaled AND the target file is unmodified (current assertion `"PENDING" in log or "REJECTED" in log` is trivially true).
- [ ] **Step 3** Fix `test_spawn_unit.py` argv assertion to check `build_argv` was called with the right prompt/model/cwd (not just the stub's constant return). Add `adapter.session_log_dir = MagicMock(return_value=None)` so the 4 affected tests stop stalling 5s on real-subprocess discovery (also a speed win).
- [ ] **Step 4** Run each touched test → pass. Commit per file group.

---

# PHASE P2 — Parallel-safety → fast local pre-push gate (the original ask)

**Goal:** make the suite safe under `pytest-xdist -n auto` and Playwright 8–12 workers, then wire a blocking, parallelized pre-push hook. **Trigger model (operator decision): auto pre-push hook, blocking, full gate, parallelized.** Real-tmux isolated tier runs natively on macOS (verified) — no OrbStack needed.

### Task P2.1: Add pytest-xdist

**Files:** Modify `pyproject.toml` (`test` extra).

- [ ] **Step 1** Add `pytest-xdist` to the `test` extra. Run `uv run --extra test pytest scripts/tests ui/tests/integration ui/tests/unit -m "not isolated" -n auto -q`. Record failures (these are the parallel-unsafe tests P2.2–P2.4 fix). Commit: `build(test): add pytest-xdist`.

### Task P2.2: Fixture-isolate hardcoded shared paths

**Files:** Modify (replace module-level `/tmp/...` constants with per-test `tmp_path`-derived paths): `scripts/tests/test_spawn_unit.py`, `test_spawn_pipe_pane_wiring.py`, `test_spawn_error_propagates.py`, `test_spawn_subscribe_unsubscribe.py`, `test_spawn_tail_fanout.py`, `test_session_id_discovery.py`, `test_tmux.py`, `test_tmux_display_pane_dead.py`, `test_governor_reattach.py`, `test_governor_canary.py`, `test_governor_wiring.py`, `test_logging.py`.

- [ ] **Step 1** Canonical transformation (apply to each file): replace `SOCKET = Path("/tmp/test-...sock")` / `MISSION_DIR = Path("/tmp/test-mission")` module constants with a fixture:

```python
@pytest.fixture
def socket_path(tmp_path):
    return tmp_path / ".fleet" / "tmux.sock"
```
and thread `tmp_path`/`socket_path` through each test. For `test_logging.py`, redirect the logger to `tmp_path` via `monkeypatch` instead of asserting/writing the global `/tmp/megalodon-scripts.log` (keep one assertion that the *configured* path constant is `/tmp/megalodon-scripts.log`, but do not write to it).
- [ ] **Step 2** Run each file under `-n auto` → pass. Commit per cluster (spawn/tmux, governor sockets, logging).

### Task P2.3: Convert module-level mutable globals to fixtures

**Files:** `scripts/tests/test_event_tail.py`, `test_inject_log_date_rollover.py`, `test_activity_wall.py`, `test_activity_wall_signals.py`, `ui/tests/integration/test_activity_wall_signal_id.py` (the `et.POLL_INTERVAL_S = 0.05` import-time mutations).

- [ ] **Step 1** Replace the bare module-level `POLL_INTERVAL_S` assignment with an `autouse` fixture using `monkeypatch.setattr(et, "POLL_INTERVAL_S", 0.05)` (restores cleanly, xdist-safe).
- [ ] **Step 2** Run under `-n auto` → pass. Commit: `test: monkeypatch POLL_INTERVAL_S (xdist-safe)`.

### Task P2.4: Move server stale-state globals onto the app context

**Files:** Modify `megalodon_ui/server.py` (`_stale_cache`, `_TEST_STALE_OVERRIDES` — move onto the `MissionContext`/`ctx` so they are app-instance-scoped); Tests `test_lanes_stale.py`, `test_stale_override.py` (drop the manual `_stale_cache.pop(id(app))` teardown).

- [ ] **Step 1: Failing test** — two app instances in one process must not share stale-override state (simulates xdist same-process workers).
- [ ] **Step 2** Note the two globals differ (contrarian M2): `_stale_cache` is already keyed by `id(app)` (hazard = `id()` reuse across xdist workers); `_TEST_STALE_OVERRIDES` (server.py:~154) is keyed by **lane string with NO app scoping** and is **consume-on-read** (`.pop()` in the read path). Move BOTH onto `ctx` (the `MissionContext`); preserve the consume-on-read `.pop()` semantics. Update ALL call sites — the `/_test/stale_override` writer, the stale-read consumer (~436-437), and the `id(app)` reader/pop (~4043-4044). Drop the manual `_stale_cache.pop(id(app))` teardown from `test_lanes_stale.py` / `test_stale_override.py` once app-scoped.
- [ ] **Step 3** Run the two test files + the new isolation test under `-n auto` → pass. Commit: `fix(server): app-scope stale cache/overrides (parallel-safe)`.

### Task P2.5: Replace flaky sleep-based waits with polling

**Files:** `scripts/tests/test_inject_log_date_rollover.py:160` (the `sleep(1.5)`), `test_activity_wall.py` / `test_activity_wall_signals.py` setup sleeps, `test_tmux_real.py` (3 sleeps), `test_applier_log_timestamps_utc.py` (drop unnecessary `sleep(0.05)`).

- [ ] **Step 1** Canonical transformation: replace `await asyncio.sleep(N)` "wait for event" barriers with a polling helper bounded by a generous deadline (e.g. `await _wait_for(predicate, timeout_s=4.0)`); replace `asyncio.get_event_loop()` with `asyncio.get_running_loop()` (see P3.3 — can be done together). For RotatingFileHandler tests, drop the sleep (handler flushes on emit).
- [ ] **Step 2** Run each touched file twice (`--count=2` via pytest-repeat if available, else twice manually) under `-n auto` to shake out flakiness → pass. Commit per file.

### Task P2.6: Playwright project split for parallelism

**Files:** Modify `ui/tests/e2e/playwright.config.ts`; the 3 disk-writing board specs (`test_board_activity_autoscroll.spec.ts`, `test_board_signals_live.spec.ts`, `test_activity_wall.spec.ts`).

- [ ] **Step 1** Split `chromium-board`/`webkit-board` into `*-board-ro` (read-only specs: `fullyParallel: true`, `workers: 12`, own fixture tmpdir) and `*-board-mut` (mutation/file-writing specs: `workers: 1`, own tmpdir). Same split for `chromium-v92-dashboard` → `*-v92-ro` (dashboard-loads, auth-redirect) and `*-v92-mut`. Route specs via test-path patterns. (See the e2e audit's concrete spec-by-spec partition.)
- [ ] **Step 2** Make the 3 disk-writing specs write into a per-test subdir keyed by `testInfo.testId` so they're safe even within their project.
- [ ] **Step 3** Run the full chromium matrix → green; record wall-time improvement. Commit: `test(e2e): split read-only vs mutation projects for parallelism`.

### Task P2.7: Makefile + pre-push gate (MEASURE FIRST; preserve the harvest hook)

**Files:** Create `Makefile`; **APPEND to** existing `hooks/pre-push` (do NOT overwrite — see warning).

> ⚠️ **Contrarian M3 — two hazards:**
> 1. **`hooks/pre-push` already exists** and runs the closed-loop harvest (`~/.agent/bin/harvest`), which the operator's CLAUDE.md invariant loop depends on. **APPEND the gate; never replace.** Read the current hook first.
> 2. The operator's CLAUDE.md says *"don't wire the heavy suite into a pre-push hook unless I ask"* and *"the heavy suite runs in CI."* CI was removed and the operator explicitly chose a blocking pre-push gate this session — but per M4/M5 a multi-minute blocking gate gets `--no-verify`'d, defeating the goal. So the gate must be **fast or it isn't blocking.**

- [ ] **Step 1: Create `Makefile`** with targets:
  - `test-py`: `uv run --extra test pytest scripts/tests ui/tests/integration ui/tests/unit -m "not isolated" -n auto -q`
  - `test-isolated`: `uv run --extra test pytest scripts/tests ui/tests -m isolated --forked -q`
  - `test-js`: `node --test ui/tests/unit/*.test.js`
  - `lint`: `uv run --with 'ruff==0.15.14' ruff check megalodon_ui/ scripts/ && uvx vulture megalodon_ui scripts`
  - `test-e2e`: the chromium matrix with the split projects (P2.6)
  - `gate-fast`: `test-py test-js lint` run concurrently (background + `wait`)
  - `gate-full`: `gate-fast` then `test-isolated` then `test-e2e`
- [ ] **Step 2: MEASURE before choosing what blocks.** Run `time make gate-fast` and `time make gate-full`. Record both.
- [ ] **Step 3: Choose the pre-push tier by the measurement.** If `gate-fast` is ≲90s, the **appended** pre-push block runs `make gate-fast` (blocking); `gate-full` stays a manual command the operator runs before a fleet launch. If even `gate-fast` is too slow, drop e2e/isolated entirely from pre-push. Do NOT put the full Playwright matrix in a blocking pre-push. Verify `test-isolated` and `test-py -n auto` can run concurrently without tmux-socket/port collisions (contrarian M5) — if not, sequence them in `gate-full`.
- [ ] **Step 4** Append the chosen gate invocation to `hooks/pre-push` AFTER the existing harvest call (harvest stays best-effort; gate failure blocks the push). Document the real commands + measured times in `README.md` (replace the "planned successor" note). Commit: `build: Makefile + appended pre-push fast gate (harvest preserved)`.

### Task P2.8: Measure + record the gate wall-time

- [ ] **Step 1** Run `time make gate`. Record the wall-time in `README.md` Testing section. Confirm full suite green under parallelism. Commit: `docs: record local gate wall-time`.

---

# PHASE P3 — Hollow/brittle cleanup, dead code, warnings, docs, missing tests

### Task P3.1: Delete dead `schemas.py`

**Files:** Delete `megalodon_ui/schemas.py`; if the `SSEEventName` drift-assert is valuable, move it to a test `scripts/tests/test_sse_event_name_parity.py`.

- [ ] **Step 1** Confirm zero importers (`grep -rn "megalodon_ui.schemas\|from .schemas\|import schemas"`). Extract the drift assert to a test if present. Delete the file.
- [ ] **Step 2** Full baseline green. Commit: `chore: delete unused schemas.py (extract SSEEventName parity to test)`.

### Task P3.2: Remove stale skips/guards

**Files:** `scripts/tests/test_harness_claude.py`/`_codex.py`/`_gemini.py` (the `if os.environ.get("CI")` dead branches); `ui/tests/unit/test_protocol_primitives.py` + `scripts/tests/test_mission_config_*.py` (`skipif(not BACKEND_AVAILABLE, "awaits P3-C")` ×many — backend is available); `ui/tests/integration/test_sse_stream.py` (`skipif(not BACKEND_AVAILABLE)`).

- [ ] **Step 1** Delete the dead `if CI` branches; replace `BACKEND_AVAILABLE` skipif guards with a module-level `pytest.importorskip("megalodon_ui.primitives")` (or just let the import error surface). 
- [ ] **Step 2** Run the touched files → same pass count, zero skips. Commit: `test: remove stale CI/BACKEND_AVAILABLE skip guards`.

### Task P3.3: Kill warning noise + asyncio deprecation

**Files:** Modify `pytest.ini` (`filterwarnings`); replace `asyncio.get_event_loop()` → `asyncio.get_running_loop()` across the ~14 callsites (`test_inject_log_date_rollover.py`, `test_activity_wall.py`, `test_back_compat_shape.py`, `test_pipe_pane_preserves_ansi_escapes.py`, `test_spawn_tail_realfile.py`, `test_spawn_tail_fanout.py`, `ui/tests/integration/conftest.py`, `test_activity_wall_signal_id.py`).

- [ ] **Step 1** Add to `pytest.ini`: `filterwarnings = ignore::DeprecationWarning:uvicorn` and `ignore::DeprecationWarning:websockets` and `ignore:.*iscoroutinefunction.*:DeprecationWarning` (the 661-warning flood from `test_stimulus_harness.py`). Keep other DeprecationWarnings visible.
- [ ] **Step 2** `sed`-style replace `get_event_loop()` → `get_running_loop()` in the listed files (these are inside running coroutines, so it's safe and future-proofs Py3.16).
- [ ] **Step 3** Run baseline → warnings near zero, all green. Commit: `test: silence external deprecation flood + use get_running_loop`.

### Task P3.4: De-brittle log-substring assertions

**Files:** `scripts/tests/test_autorecover_supervisor.py:120-121,231`; optionally have the supervisor emit structured JSONL.

- [ ] **Step 1** Either (preferred) have the autorecover supervisor write a structured JSONL event per restart and assert on parsed fields (`restart_count`, `reason`), or assert on supervisor state rather than the exact log string. Keep behavior identical.
- [ ] **Step 2** Run → pass. Commit: `test(autorecover): assert structured state not log substrings`.

### Task P3.5: Fix the 30s SSE tests + add real SSE-delivery coverage

**Files:** `ui/tests/integration/test_sse_stream.py`, `scripts/tests/test_back_compat_shape.py:232`.

- [ ] **Step 1** Replace the ASGITransport-buffered SSE tests (30s each) with a **generator-unit test** that drives `event_generator()` directly (no HTTP) and asserts it yields `sync` then `status-change` on a STATUS.md touch within a bounded loop — fast and actually verifies incremental delivery. Remove/repurpose the xfard `test_sse_stream_emits_status_change_on_file_touch` (now covered by the generator unit test). 
- [ ] **Step 2** Run → pass, and the two 30s tests no longer take 30s. Commit: `test(sse): unit-test event_generator (kills 2×30s + covers delivery)`.

### Task P3.6: Documentation/config accuracy sweep

**Files:** `README.md` (counts, intro Linux-only line at ~21), `TASKS.md` (1553→1557, "14 isolated"→15, JS-glob note, CI-removed language), `ui/tests/e2e/playwright.config.ts` (header "8 projects"→actual count, `reuseExistingServer` rationale, `__main__.py:106` ref→correct line), `pytest.ini` (the `destructive` marker "skip by default" claim — either wire `-m "not destructive"` in addopts or delete the unused marker), stale `chromium-grid` comments in `test_lane_detail.spec.ts`/`test_approval_rules.spec.ts`.

- [ ] **Step 1** Correct each stale assertion to match verified reality (use the audit's accuracy table). For the `destructive` marker: it has zero usages — delete it and its claim.
- [ ] **Step 2** Commit: `docs: correct stale test/CI assertions across README/TASKS/config`.

### Task P3.7: Fill highest-value coverage gaps

**Files:** new tests — `scripts/tests/test_gen_lane_launches.py` (extend), `ui/tests/integration/test_followup_endpoint.py` (CSRF/404/422 if missing), `scripts/tests/test_csrf_canonical_routes.py` (add `/api/tasks`, `/api/lanes/{lane}/signal`, `/api/v1/phase-flip`, `/api/v1/lane/{lane}/feedback`), `scripts/tests/test_watchdog_run.py`, `scripts/tests/test_preflight_main.py`, `scripts/tests/test_queue_applier.py` (corrupt-pending, phase-mismatch).

- [ ] **Step 1 (v10-critical first):** `gen_lane_launches.py` (31.6% cov) — add direct-import tests for `generate_from_config`, `_find_launch_md` (both lookup paths), `_write_atomic` exception/cleanup, and the config-absent CLI fallback. This module is generalized by v10; lock it now.
- [ ] **Step 2** Add the missing CSRF-negative cases (missing + wrong header → 403) for the 4 uncovered mutation routes to `test_csrf_canonical_routes.py`'s `ROUTES`.
- [ ] **Step 3** Add `watchdog.run()` loop test (short poll + stop), `preflight.__main__` entry test (`MOCK_CLAUDE=1`), and applier corrupt-pending (`json-decode-error` → reject) + phase-mismatch tests.
- [ ] **Step 4** Run each new test → pass; re-run coverage and confirm the targeted files climbed. Commit per group.

---

## Self-Review

**Spec coverage:** P0 closes every *enumerated* governor escape (matrix = spec) and keeps the real gate path (`scripts/run_tests.sh`) working — but does NOT close the class (arbitrary script-head exec remains open by design; see "Residual risk / non-goals"). An allowlist redesign is a flagged v10 operator decision, deliberately out of scope here. P1 fixes the 2 broken/masked tests + 2 product bugs + dead-testid specs + the worst hollow tests. P2 delivers the parallel-safe fast gate (the original ask) including the real-tmux tier (runs on macOS). P3 clears dead code, warning noise, brittle/slow tests, doc inaccuracies, and the top coverage gaps incl. the v10-critical `gen_lane_launches.py`. The first-audit accuracy findings are folded into P3.6.

**Placeholder scan:** the only deliberately-deferred specifics are inside P0.9 (the override-wiring test body) and P2.6 (per-spec routing) — both point the implementer at the exact existing mechanism to read; the red-team matrix and per-task assertions are concrete. No TBD/"handle edge cases" placeholders remain.

**Type/name consistency:** `decide()` signature `decide(tool_name, tool_input, *, project_dir, lane)` and `Decision.permission/reason/category` are used consistently. Test file names and the `DENY_CASES/ALLOW_OK/STILL_DENY` matrix names are consistent across P0 tasks.

**Known risk to flag in contrarian review:** P0.8 deny-by-default of `make`/build-runners depends on P0.9's allow-override actually working for `target.gates`; if the override path is weaker than assumed, the fleet's gate-running breaks. This is the single highest-risk coupling and must be stress-tested in the contrarian review and Task P0.9.
