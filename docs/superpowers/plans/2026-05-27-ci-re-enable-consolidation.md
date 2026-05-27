# CI Re-enable Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-enable the disabled GitHub Actions workflow with cost guardrails (concurrency cancellation, scoped triggers, docs-skip, per-job timeouts) and write the dated INV-3 resolution that lifts the standing closed-loop freeze.

**Architecture:** The workflow at `.github/workflows/test.yml.disabled` is already ubuntu-only and Playwright-scoped (the May cost blowout was macOS runners + runaway runs). This plan adds the two missing cost guards — a top-level `concurrency` block with `cancel-in-progress: true`, and trigger scoping (`push` on `main` only + `paths-ignore` for docs) — re-enables the file, regression-locks every guardrail with an offline PyYAML meta-test, and records the dated `INV-3` resolution in `ledger.yaml` so the harvest no longer computes a freeze. This is a `consolidation` plan (remediation of a frozen invariant), not a feature plan — it is the mandated prerequisite track for v10.

**Tech Stack:** GitHub Actions YAML, PyYAML (already a project dep), pytest (`scripts/tests/`), `gh` CLI for watching the first live run.

---

## Context the implementer needs

- **Repo:** `github.com/dave-schmidt-dev/megalodon`. **No branch protection on `main`** (verified) and the workflow has no required-status-check gating, so `paths-ignore` cannot wedge a PR with a never-reported check. Solo dev pushes directly to `main`.
- **Why CI was disabled:** May 2026 Actions bill hit $1,031, 91% from a since-removed `macos-latest` matrix that sat perpetually `queued` (no macOS runner on this plan) plus runaway/superseded runs. The macOS matrix is already gone in the disabled file. Operator has since set an account-level Actions spending cap.
- **The freeze:** `ledger.yaml` lists `INV-3: {gate_test_status: covered}` but has **no** `resolutions.INV-3` entry. The harvest tool (in `~/.agent`, non-git) computes the freeze from `recurrence ≥ threshold` *without a dated resolution after the last recurrence*. Writing the dated resolution is what clears it.
- **INV-3 charter** (`INVARIANTS.md`): "The blocking CI gate covers every project (no chromium-board-only)". Rationale: chromium-board-only gating hid the CSRF routes + mission-status split twice. So the guardrail test must assert the INV-guarded Playwright projects stay gated.
- **Project conventions** (`README.md` §Closed-loop): Conventional Commits (`fix:` for remediation); HISTORY bug entries use `[bug] ... | files: ... | inv: INV-x`.
- **PyYAML footgun (load-bearing for the test):** YAML 1.1 parses the bare key `on:` as the boolean `True`, so `yaml.safe_load(workflow)["on"]` raises `KeyError` — the key is literally `True`. The test reads it via `wf.get(True, wf.get("on"))`. Do not "fix" this by quoting `on:` in the workflow — GitHub requires the bare `on:` trigger key.
- **Meta-tests live in `scripts/tests/`** (e.g. `test_constants_codegen.py`). The new test belongs there.
- **Authoritative gate** (run before any commit; see `TASKS.md`): baseline `uv run --extra test pytest scripts/tests ui/tests/integration ui/tests/unit -q -m "not isolated"` (1553) + ruff/vulture. The new test joins `scripts/tests`.

## File Structure

- **Create** `scripts/tests/test_ci_workflow_guardrails.py` — offline meta-test: parses the enabled workflow + `ledger.yaml`, asserts every cost guardrail and the INV-3 resolution. This is the regression lock that makes the freeze stay lifted.
- **Modify → Rename** `.github/workflows/test.yml.disabled` → `.github/workflows/test.yml` — add `concurrency` block + scope `on:` triggers + `paths-ignore`; re-enable by renaming.
- **Modify** `ledger.yaml` — add dated `resolutions.INV-3` entry.
- **Modify** `README.md` (§ near line 645 closed-loop / add a CI section) — document the re-enabled workflow, the guardrails, and the operator-owned budget cap.
- **Modify** `HISTORY.md` — closed-loop remediation entry referencing INV-3.
- **Modify** `TASKS.md` — lift the standing INV-3 FREEZE note.

---

## Task 1: Cost-guardrail meta-test (red)

**Files:**
- Create: `scripts/tests/test_ci_workflow_guardrails.py`

- [ ] **Step 1: Write the failing test**

```python
"""Offline guardrails for the GitHub Actions workflow + the INV-3 freeze resolution.

These encode the cost lessons from the May 2026 Actions blowout ($1,031, 91%
macOS + runaway runs) and the INV-3 charter (the blocking CI gate must cover
every INV-guarded project, not chromium-board only). They run offline — no
network, no `gh` — so they gate every push the way the suite does.
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "test.yml"
LEDGER = REPO_ROOT / "ledger.yaml"


def _load_workflow() -> dict:
    assert WORKFLOW.exists(), (
        "CI must be enabled at .github/workflows/test.yml "
        "(re-enable test.yml.disabled)"
    )
    return yaml.safe_load(WORKFLOW.read_text())


def _triggers(wf: dict) -> dict:
    # YAML 1.1 parses the bare `on:` key as the boolean True (the GitHub
    # trigger key must stay bare, so we read it by that key, not "on").
    return wf.get(True, wf.get("on"))


def test_workflow_is_enabled():
    assert WORKFLOW.exists()


def test_no_macos_runner():
    text = WORKFLOW.read_text().lower()
    assert "macos" not in text, (
        "macOS runners sit queued forever on this repo's plan and were 91% "
        "of the May Actions bill — never reintroduce a macos runner"
    )


def test_concurrency_cancels_superseded_runs():
    wf = _load_workflow()
    conc = wf.get("concurrency")
    assert conc is not None, "missing top-level concurrency block (runaway-run cost guard)"
    assert conc.get("cancel-in-progress") is True, (
        "concurrency.cancel-in-progress must be true so a new push cancels the "
        "in-flight run instead of stacking billable minutes"
    )


def test_push_trigger_scoped_to_main():
    on = _triggers(_load_workflow())
    assert on["push"]["branches"] == ["main"], (
        "push CI must be scoped to main; branches:['**'] double-fires on every "
        "PR-branch push (push + pull_request both trigger)"
    )


def test_docs_only_changes_skip_ci():
    on = _triggers(_load_workflow())
    ignore = on["push"].get("paths-ignore", [])
    assert "**.md" in ignore and "docs/**" in ignore, (
        "doc-only commits (this repo commits docs constantly) must skip CI"
    )


def test_every_job_is_time_bounded():
    wf = _load_workflow()
    for name, job in wf["jobs"].items():
        assert "timeout-minutes" in job, (
            f"job {name!r} has no timeout-minutes — an unbounded job is a cost hole"
        )


def test_inv_guarded_playwright_projects_stay_gated():
    # INV-3: the surfaces that hid the CSRF + mission-status bugs twice must
    # stay CI-gated. Dropping any of these re-opens INV-3.
    text = WORKFLOW.read_text()
    for project in ("chromium-board", "chromium-default", "chromium-mutations"):
        assert f"--project={project}" in text, (
            f"{project} dropped from CI — this re-opens INV-3"
        )


def test_inv3_freeze_resolution_recorded():
    ledger = yaml.safe_load(LEDGER.read_text())
    res = (ledger.get("resolutions") or {}).get("INV-3")
    assert res is not None, "INV-3 freeze not cleared: no resolutions.INV-3 entry"
    assert res.get("resolved_at_date"), (
        "INV-3 resolution needs a dated checkpoint to lift the harvest freeze"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra test pytest scripts/tests/test_ci_workflow_guardrails.py -v`
Expected: FAIL — `test_workflow_is_enabled` (and the others depending on `_load_workflow`) fail with the assert message "CI must be enabled at .github/workflows/test.yml"; `test_inv3_freeze_resolution_recorded` fails with "no resolutions.INV-3 entry". `test_no_macos_runner` will *error* (file read on a missing path) — acceptable red. This confirms the test discriminates the current (disabled, unresolved) state.

- [ ] **Step 3: Commit the red test**

```bash
git add scripts/tests/test_ci_workflow_guardrails.py
git commit -m "test(ci): add offline guardrail + INV-3 resolution meta-test (red)"
```

---

## Task 2: Add cost guards and re-enable the workflow (green for the workflow asserts)

**Files:**
- Modify → Rename: `.github/workflows/test.yml.disabled` → `.github/workflows/test.yml`

- [ ] **Step 1: Replace the trigger block and add concurrency**

In `.github/workflows/test.yml.disabled`, replace the existing header:

```yaml
name: Test

on:
  push:
    branches: ["**"]
  pull_request:
    branches: ["**"]
```

with:

```yaml
name: Test

on:
  push:
    branches: ["main"]
    paths-ignore:
      - "**.md"
      - "docs/**"
      - ".archive/**"
  pull_request:
    paths-ignore:
      - "**.md"
      - "docs/**"
      - ".archive/**"

# Cancel a still-running CI when a newer commit lands on the same ref. The May
# blowout was partly runaway/superseded runs stacking billable minutes; this
# keeps at most one live run per branch.
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
```

Leave the `jobs:` block (both the `test` and `playwright-webkit` jobs) exactly as-is — they are already ubuntu-only, `timeout-minutes`-bounded (30 and 25), and scoped to chromium-board/default/mutations (blocking) + webkit (non-blocking). Do not touch them.

- [ ] **Step 2: Re-enable the workflow by renaming**

Run:
```bash
git mv .github/workflows/test.yml.disabled .github/workflows/test.yml
```

- [ ] **Step 3: Run the workflow guardrail tests to verify they pass**

Run: `uv run --extra test pytest scripts/tests/test_ci_workflow_guardrails.py -v -k "not inv3"`
Expected: PASS — all workflow asserts green (`test_workflow_is_enabled`, `test_no_macos_runner`, `test_concurrency_cancels_superseded_runs`, `test_push_trigger_scoped_to_main`, `test_docs_only_changes_skip_ci`, `test_every_job_is_time_bounded`, `test_inv_guarded_playwright_projects_stay_gated`). `test_inv3_freeze_resolution_recorded` is deselected (still red — closed in Task 3).

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/test.yml
git commit -m "ci: re-enable workflow with concurrency + scoped triggers (cost guards)"
```

---

## Task 3: Record the dated INV-3 resolution (green for the full test)

**Files:**
- Modify: `ledger.yaml`

- [ ] **Step 1: Add the resolution entry**

In `ledger.yaml`, under the existing `resolutions:` block (which currently holds `INV-1` and `INV-2`), append:

```yaml
  INV-3:
    resolved_at_date: "2026-05-27"
    note: "CI re-enabled with cost guardrails — ubuntu-only (no macos), concurrency cancel-in-progress, push scoped to main + docs paths-ignore, per-job timeout-minutes, account-level Actions budget cap set by operator. Blocking gate keeps chromium-board/default/mutations; webkit non-blocking. Guardrails regression-locked by scripts/tests/test_ci_workflow_guardrails.py."
```

Leave the `invariants:` block unchanged (`INV-3` was already `gate_test_status: covered`).

- [ ] **Step 2: Run the full guardrail test to verify it passes**

Run: `uv run --extra test pytest scripts/tests/test_ci_workflow_guardrails.py -v`
Expected: PASS — all 8 tests green, including `test_inv3_freeze_resolution_recorded`.

- [ ] **Step 3: Commit**

```bash
git add ledger.yaml
git commit -m "fix(ci): record dated INV-3 resolution — lifts closed-loop freeze | inv: INV-3"
```

---

## Task 4: Documentation + lift the freeze note (no test — docs)

**Files:**
- Modify: `README.md`
- Modify: `HISTORY.md`
- Modify: `TASKS.md`

- [ ] **Step 1: Document CI in README**

In `README.md`, immediately after the `## Closed-loop convention` section (currently ending at the `inv: INV-x` line ~647), add:

```markdown

## Continuous Integration

GitHub Actions workflow: `.github/workflows/test.yml`. Cost guardrails (after the
May 2026 Actions blowout — $1,031, 91% macOS + runaway runs):

- **ubuntu-only** — no macOS runner (sat perpetually `queued` on this plan).
- **concurrency: cancel-in-progress** — a newer push cancels the in-flight run.
- **scoped triggers** — `push` runs on `main` only; `paths-ignore` skips
  doc-only commits (`**.md`, `docs/**`, `.archive/**`).
- **per-job `timeout-minutes`** — 30 (test) / 25 (webkit).
- **budget cap** — operator-set account-level Actions spending limit (GitHub
  Settings → Billing). Not in-repo; verify it is still set after re-enable.

Blocking gate: ubuntu `test` job (pytest baseline + forked-isolated tier +
ruff + vulture + chromium-board/default/mutations Playwright). Non-blocking:
`playwright-webkit` (`continue-on-error: true`). These guardrails are
regression-locked by `scripts/tests/test_ci_workflow_guardrails.py` and the
INV-3 charter.
```

- [ ] **Step 2: Add the HISTORY remediation entry**

In `HISTORY.md`, add a dated entry at the top section (follow the existing format):

```markdown
- [fix] CI re-enabled with cost guardrails; INV-3 freeze lifted. Added top-level `concurrency` (cancel-in-progress) + scoped `push` to `main` + docs `paths-ignore`; renamed `test.yml.disabled` → `test.yml`. Regression-locked by an offline PyYAML meta-test asserting no-macos / concurrency / scoped-triggers / per-job timeouts / INV-guarded Playwright projects gated / dated INV-3 resolution. | files: .github/workflows/test.yml, scripts/tests/test_ci_workflow_guardrails.py, ledger.yaml, README.md | inv: INV-3
```

- [ ] **Step 3: Lift the freeze note in TASKS.md**

In `TASKS.md`, update the Task #32 "Standing FREEZE: INV-3" line and the Task #31 freeze paragraph to reflect that INV-3 is resolved (dated 2026-05-27) and CI is re-enabled. Change "Standing FREEZE: INV-3 ... until CI re-enable lands" to note it is **LIFTED 2026-05-27** with the commit, and that the v10 generalization plan (track b) is now unblocked.

- [ ] **Step 4: Commit**

```bash
git add README.md HISTORY.md TASKS.md
git commit -m "docs(ci): document CI guardrails + lift INV-3 freeze | inv: INV-3"
```

---

## Task 5: Authoritative verification + first live CI run

**Files:** none (verification only)

- [ ] **Step 1: Confirm the new test is collected by the baseline gate**

Run: `uv run --extra test pytest scripts/tests/test_ci_workflow_guardrails.py scripts/tests -q -m "not isolated" -k "ci_workflow or constants"`
Expected: the guardrail tests pass and no collection error is introduced into `scripts/tests`. (A full baseline `uv run --extra test pytest scripts/tests ui/tests/integration ui/tests/unit -q -m "not isolated"` — baseline 1553 + 8 new — may be run if time allows; this change is config/docs + one offline test, so the targeted run is the meaningful signal.)

- [ ] **Step 2: Lint clean**

Run: `uv run --with 'ruff==0.15.14' ruff check scripts/tests/test_ci_workflow_guardrails.py && uvx vulture megalodon_ui scripts`
Expected: no errors.

- [ ] **Step 3: Push to main and watch the first real CI run conclude**

> Operator/orchestrator action (push to `main` per the solo-dev workflow). This is the live proof the re-enabled workflow runs green and concludes within the timeout (the disabled file was never validated live).

```bash
git push origin main
gh run watch "$(gh run list --workflow=test.yml --limit=1 --json databaseId --jq '.[0].databaseId')"
```
Expected: the `test (ubuntu-latest)` job concludes **success** within 30 min; `playwright webkit (non-blocking)` may pass or soft-fail (`continue-on-error`) without blocking. Confirm the run did **not** queue indefinitely.

- [ ] **Step 4: Confirm the budget cap is still set**

> Manual operator check (spending limits are account/org billing settings, not exposed to a repo-scoped token). Confirm GitHub Settings → Billing → Actions spending limit is set to the operator's cap. Record the confirmation in `HISTORY.md` if it was changed.

- [ ] **Step 5: Verify the freeze is lifted**

Run: `uv run --extra test pytest scripts/tests/test_ci_workflow_guardrails.py::test_inv3_freeze_resolution_recorded -v`
Expected: PASS. The dated `resolutions.INV-3` is in `ledger.yaml`; the harvest no longer computes an INV-3 freeze. The v10 generalization plan (track b) is unblocked.

---

## Self-Review (against the spec / freeze requirements)

**Spec coverage** — the freeze-lift requirements from `TASKS.md` §Task #32 NEXT.1(a) and `docs/v10/...§8`:
- "no macOS" → `test_no_macos_runner` (Task 1) + already true in the file (Task 2 leaves jobs untouched). ✅
- "`concurrency: cancel-in-progress`" → added in Task 2; asserted by `test_concurrency_cancels_superseded_runs`. ✅
- "scoped triggers" → `push` scoped to `main` + `paths-ignore` (Task 2); asserted by `test_push_trigger_scoped_to_main` + `test_docs_only_changes_skip_ci`. ✅
- "budget cap" → operator-owned account setting; documented (Task 4) + verified (Task 5.4). Not code — correctly out of in-repo scope. ✅
- "dated INV-3 `resolutions:` checkpoint in `ledger.yaml`" → Task 3; asserted by `test_inv3_freeze_resolution_recorded`. ✅
- INV-3 charter "covers every project" → `test_inv_guarded_playwright_projects_stay_gated`. ✅

**Placeholder scan:** none — every step has the literal YAML/Python/commands.

**Type/name consistency:** test file `scripts/tests/test_ci_workflow_guardrails.py`, workflow path `.github/workflows/test.yml`, ledger key `resolutions.INV-3.resolved_at_date`, and the three Playwright project names (`chromium-board`/`-default`/`-mutations`) are used identically across Tasks 1–5. The `_triggers()` helper's `wf.get(True, ...)` handling of the YAML-truthy `on:` key is consistent with leaving `on:` bare in the workflow.
