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


# The socket-budget skip is now provided globally by the autouse
# `_lifespan_test_mode` fixture in conftest.py; the rejection test below opts
# back in via monkeypatch.delenv.


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


def test_scaffold_links_scripts_for_run_dir_cwd(tmp_path, monkeypatch):
    """Run dir must expose scripts/ so bounded tools resolve from the run-dir cwd.

    Agents spawn with cwd = run dir (spawn.py: cwd=self.mission_dir) and invoke
    bounded tools as the allowlisted relative path `scripts/<tool>` (the allowlist
    pattern Bash(scripts/queue_submit.py:*) is a literal-string match). If scripts/
    is absent from the run dir, the relative path file-not-founds and the only
    resolving form (an absolute repo path) misses the allowlist and prompts —
    Finding A from the tsgate acceptance gate (2026-05-24).
    """
    work = tmp_path / "repo"
    work.mkdir()
    (work / "templates").symlink_to(REPO / "templates")
    (work / "scripts").symlink_to(REPO / "scripts")
    monkeypatch.setenv("RUN_LIB_REPO_ROOT", str(work))
    res = _run(["linkcheck", "--title", "L", "--summary", "S"], cwd=work)
    assert res.returncode == 0, res.stderr
    rd = next((work / "runs").glob("*--linkcheck"))
    # Every bounded tool the launch protocol invokes resolves via the relative
    # `scripts/` path from the run-dir cwd.
    for tool in [
        "queue_submit.py",
        "claim.sh",
        "run_tests.sh",
        "atomic_close.py",
        "poll.py",
    ]:
        assert (rd / "scripts" / tool).exists(), (
            f"run-dir scripts/{tool} does not resolve (Finding A)"
        )
    # And it points at the project's real scripts/, not a divergent copy.
    assert (rd / "scripts" / "queue_submit.py").resolve() == (
        REPO / "scripts" / "queue_submit.py"
    ).resolve()


def test_rejects_slug_whose_socket_path_exceeds_budget(tmp_path, monkeypatch):
    """new_run.sh must reject up front when <run>/.fleet/tmux.sock would exceed
    the 100-byte guard, rather than letting launch_fleet.sh --spawn fail late with
    exit 10 (socket-path finding, tsgate gate 2026-05-24). Under the deep pytest
    tmp root the prospective socket path already overflows, so an over-long slug
    is rejected with actionable budget math and no run dir is created.
    """
    monkeypatch.delenv("MEGALODON_SKIP_SOCKET_BUDGET", raising=False)
    work = tmp_path / "repo"
    work.mkdir()
    (work / "templates").symlink_to(REPO / "templates")
    (work / "scripts").symlink_to(REPO / "scripts")
    monkeypatch.setenv("RUN_LIB_REPO_ROOT", str(work))
    res = _run(
        ["this-is-an-overlong-slug-that-blows-the-socket-budget", "--force"],
        cwd=work,
    )
    assert res.returncode != 0
    out = (res.stderr + res.stdout).lower()
    assert "socket path" in out, out
    assert "100" in out, out  # the byte budget appears in the message
    assert not list((work / "runs").glob("*--this-is-an-overlong-slug*"))


def test_socket_budget_limit_matches_product_constant():
    """new_run.sh's hardcoded budget must match the product guard (no drift)."""
    from megalodon_ui._v92_constants import SOCKET_PATH_LIMIT_BYTES

    assert str(SOCKET_PATH_LIMIT_BYTES) in (REPO / "scripts/new_run.sh").read_text()


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


def test_new_run_seeds_no_broad_approval_rules(tmp_path, monkeypatch):
    """new_run.sh must not seed a broad .fleet/approval-rules.json.

    The bounded allowlist lives in claude.py; a seeded approval-rules file that
    re-broadened the surface would defeat it. Either no file is seeded (current
    behavior — vacuously safe), or any seeded file is interpreter/compound-free.
    """
    import json

    work = tmp_path / "repo"
    work.mkdir()
    (work / "templates").symlink_to(REPO / "templates")
    (work / "scripts").symlink_to(REPO / "scripts")
    monkeypatch.setenv("RUN_LIB_REPO_ROOT", str(work))
    res = _run(["guard", "--force"], cwd=work)
    assert res.returncode == 0, f"new_run.sh failed: {res.stderr}"
    created = list((work / "runs").glob("*--guard"))
    assert created, "new_run.sh did not create a run dir under the overridden root"
    for d in created:
        rules = d / ".fleet" / "approval-rules.json"
        if rules.exists():
            patterns = json.dumps(json.loads(rules.read_text())).lower()
            for bad in ["python", "uv run", "bash -c", "curl", "&&"]:
                assert bad not in patterns, f"broad seed pattern leaked: {bad}"
