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
        "CI must be enabled at .github/workflows/test.yml (re-enable test.yml.disabled)"
    )
    return yaml.safe_load(WORKFLOW.read_text())


def _triggers(wf: dict) -> dict:
    # YAML 1.1 parses the bare `on:` key as the boolean True (the GitHub
    # trigger key must stay bare, so we read it by that key, not "on").
    on = wf.get(True, wf.get("on"))
    assert on is not None, "workflow has no `on:` trigger block"
    return on


def test_workflow_is_enabled():
    assert WORKFLOW.exists(), (
        "CI workflow disabled — rename .github/workflows/test.yml.disabled to test.yml"
    )


def test_no_macos_runner():
    text = WORKFLOW.read_text().lower()
    assert "macos" not in text, (
        "macOS runners sit queued forever on this repo's plan and were 91% "
        "of the May Actions bill — never reintroduce a macos runner"
    )


def test_concurrency_cancels_superseded_runs():
    wf = _load_workflow()
    conc = wf.get("concurrency")
    assert conc is not None, (
        "missing top-level concurrency block (runaway-run cost guard)"
    )
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
    # stay CI-gated. Parse step `run:` blocks (not raw file text) so a project
    # named only in a comment cannot false-pass this lock.
    wf = _load_workflow()
    runs = "\n".join(
        step.get("run", "")
        for job in wf["jobs"].values()
        for step in job.get("steps", [])
    )
    for project in ("chromium-board", "chromium-default", "chromium-mutations"):
        assert f"--project={project}" in runs, (
            f"{project} dropped from CI run steps — this re-opens INV-3"
        )


def test_inv3_freeze_resolution_recorded():
    assert LEDGER.exists(), "ledger.yaml missing — cannot verify INV-3 resolution"
    ledger = yaml.safe_load(LEDGER.read_text())
    res = (ledger.get("resolutions") or {}).get("INV-3")
    assert res is not None, "INV-3 freeze not cleared: no resolutions.INV-3 entry"
    assert res.get("resolved_at_date"), (
        "INV-3 resolution needs a dated checkpoint to lift the harvest freeze"
    )
