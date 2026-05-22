"""v9.4 — new_run.sh scaffolds a self-contained run dir under runs/."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _run(args, cwd):
    return subprocess.run(
        ["bash", str(REPO / "scripts/new_run.sh"), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
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
    for name in [
        "MISSION.md",
        "STATUS.md",
        "TASKS.md",
        "HISTORY.md",
        "README.md",
        ".mission-config.yaml",
        ".mission-events",
    ]:
        assert (rd / name).exists(), name
    for d in ["findings", "claims", "signals", "queue", ".fleet"]:
        assert (rd / d).is_dir(), d
    # No unresolved placeholders in any scaffolded file.
    for name in [
        "MISSION.md",
        "STATUS.md",
        "TASKS.md",
        "HISTORY.md",
        "README.md",
        ".mission-config.yaml",
    ]:
        assert "{{" not in (rd / name).read_text(), f"Unresolved placeholder in {name}"


def test_refuses_when_live_run_exists(tmp_path, monkeypatch):
    work = tmp_path / "repo"
    work.mkdir()
    (work / "templates").symlink_to(REPO / "templates")
    (work / "scripts").symlink_to(REPO / "scripts")
    monkeypatch.setenv("RUN_LIB_REPO_ROOT", str(work))
    _run(["alpha", "--title", "A", "--summary", "A"], cwd=work)
    res = _run(["beta", "--title", "B", "--summary", "B"], cwd=work)
    assert res.returncode != 0
    assert "live" in (res.stderr + res.stdout).lower()


def test_force_scaffolds_despite_live_run(tmp_path, monkeypatch):
    work = tmp_path / "repo"
    work.mkdir()
    (work / "templates").symlink_to(REPO / "templates")
    (work / "scripts").symlink_to(REPO / "scripts")
    monkeypatch.setenv("RUN_LIB_REPO_ROOT", str(work))
    # First run stays live (RUN-START seeded, never terminated).
    _run(["alpha", "--title", "A", "--summary", "A"], cwd=work)
    # --force scaffolds a second run despite the live one.
    res = _run(["beta", "--title", "B", "--summary", "B", "--force"], cwd=work)
    assert res.returncode == 0, res.stderr
    assert len(list((work / "runs").glob("*--beta"))) == 1


def test_exit_criteria_flag_substituted(tmp_path, monkeypatch):
    work = tmp_path / "repo"
    work.mkdir()
    (work / "templates").symlink_to(REPO / "templates")
    (work / "scripts").symlink_to(REPO / "scripts")
    monkeypatch.setenv("RUN_LIB_REPO_ROOT", str(work))
    res = _run(
        [
            "crit",
            "--title",
            "C",
            "--summary",
            "S",
            "--exit-criteria",
            "CUSTOM-EXIT-XYZ",
        ],
        cwd=work,
    )
    assert res.returncode == 0, res.stderr
    rd = next((work / "runs").glob("*--crit"))
    assert "CUSTOM-EXIT-XYZ" in (rd / "MISSION.md").read_text()
