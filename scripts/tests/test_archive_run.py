"""v9.4 — archive_run.sh moves a terminal run dir to .archive/ via git mv."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _make_repo(tmp_path):
    work = tmp_path / "repo"
    work.mkdir()
    _git(["init", "-q"], work)
    _git(["config", "user.email", "t@t"], work)
    _git(["config", "user.name", "t"], work)
    (work / "scripts").symlink_to(REPO / "scripts")
    (work / "templates").symlink_to(REPO / "templates")
    # Reproduce the real repo: .archive/ is gitignored (local cold storage).
    (work / ".gitignore").write_text(".archive/\n")
    (work / ".archive").mkdir()
    (work / ".archive" / "INDEX.md").write_text(
        "# Index\n\n| Run ID | Mission | Started | Completed | Wall clock | Outputs |\n|---|---|---|---|---|---|\n"
    )
    return work


def _scaffold_terminal_run(work):
    subprocess.run(
        ["bash", "scripts/new_run.sh", "demo", "--title", "T", "--summary", "S"],
        cwd=work,
        env={**__import__("os").environ, "RUN_LIB_REPO_ROOT": str(work)},
        check=True,
        capture_output=True,
        text=True,
    )
    rd = next((work / "runs").glob("*--demo"))
    (rd / "findings" / "f1.md").write_text("finding\n")
    # Write a terminal event.
    (rd / ".mission-events").write_text("RUN-START ...\nCOMPLETE done\n")
    _git(["add", "-A"], work)
    _git(["commit", "-qm", "run"], work)
    return rd


def test_archive_moves_and_registers(tmp_path):
    import os

    work = _make_repo(tmp_path)
    rd = _scaffold_terminal_run(work)
    res = subprocess.run(
        ["bash", "scripts/archive_run.sh", str(rd)],
        cwd=work,
        env={**os.environ, "RUN_LIB_REPO_ROOT": str(work)},
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    assert not rd.exists()  # moved out of runs/
    archived = list((work / ".archive").glob("*--demo"))
    assert len(archived) == 1
    assert (archived[0] / "findings" / "f1.md").exists()
    idx = (work / ".archive" / "INDEX.md").read_text()
    assert "--demo" in idx
    # .archive is local-only cold storage: the archived run must NOT be tracked.
    tracked = _git(["ls-files", ".archive/"], work).stdout.strip()
    assert tracked == "", f"archive should be untracked, got: {tracked}"
    # And the run must be untracked from runs/ (staged deletion after git rm --cached).
    still_tracked = _git(["ls-files", f"runs/{rd.name}"], work).stdout.strip()
    assert still_tracked == "", (
        f"run should be untracked after archive: {still_tracked}"
    )


def test_refuses_live_run(tmp_path):
    import os

    work = _make_repo(tmp_path)
    rd = _scaffold_terminal_run(work)
    (rd / ".mission-events").write_text("RUN-START still going\n")  # make it live
    _git(["add", "-A"], work)
    _git(["commit", "-qm", "live"], work)
    res = subprocess.run(
        ["bash", "scripts/archive_run.sh", str(rd)],
        cwd=work,
        env={**os.environ, "RUN_LIB_REPO_ROOT": str(work)},
        capture_output=True,
        text=True,
    )
    assert res.returncode != 0
    assert rd.exists()


def test_index_row_uses_template_shape(tmp_path):
    import os

    work = _make_repo(tmp_path)
    rd = _scaffold_terminal_run(work)
    name = rd.name  # <UTC>--demo
    res = subprocess.run(
        ["bash", "scripts/archive_run.sh", str(rd)],
        cwd=work,
        env={**os.environ, "RUN_LIB_REPO_ROOT": str(work)},
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    idx = (work / ".archive" / "INDEX.md").read_text()
    # Exactly one rendered row for this run, six pipe-delimited columns, and
    # no leftover template placeholders.
    rows = [ln for ln in idx.splitlines() if f"`{name}`" in ln]
    assert len(rows) == 1, idx
    row = rows[0]
    assert "{{" not in row
    assert row.count("|") == 7  # 6 columns => 7 pipes
    assert "T" in row  # mission title rendered ("T")
    assert f"see {work}/.archive/{name}/README.md" in row


def test_recovers_when_dest_exists_but_index_missing(tmp_path):
    """Crash-recovery: DEST moved but INDEX append never happened.

    Re-running must register the row, not silently no-op without it.
    """
    import os

    work = _make_repo(tmp_path)
    rd = _scaffold_terminal_run(work)
    name = rd.name
    dest = work / ".archive" / name
    # Simulate the gap: move the run dir into .archive but leave INDEX untouched.
    rd.rename(dest)
    assert dest.exists()
    assert f"`{name}`" not in (work / ".archive" / "INDEX.md").read_text()
    res = subprocess.run(
        ["bash", "scripts/archive_run.sh", str(dest)],
        cwd=work,
        env={**os.environ, "RUN_LIB_REPO_ROOT": str(work)},
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    idx = (work / ".archive" / "INDEX.md").read_text()
    assert f"`{name}`" in idx
    assert len([ln for ln in idx.splitlines() if f"`{name}`" in ln]) == 1


def test_aborts_when_archive_dir_absent(tmp_path):
    import os

    work = _make_repo(tmp_path)
    rd = _scaffold_terminal_run(work)
    # Remove .archive to trigger the preflight abort.
    (work / ".archive" / "INDEX.md").unlink()
    (work / ".archive").rmdir()
    res = subprocess.run(
        ["bash", "scripts/archive_run.sh", str(rd)],
        cwd=work,
        env={**os.environ, "RUN_LIB_REPO_ROOT": str(work)},
        capture_output=True,
        text=True,
    )
    assert res.returncode != 0
    assert "not found" in (res.stderr + res.stdout).lower()
    assert rd.exists()


def test_force_archives_live_run(tmp_path):
    import os

    work = _make_repo(tmp_path)
    rd = _scaffold_terminal_run(work)
    (rd / ".mission-events").write_text("RUN-START still going\n")  # make it live
    _git(["add", "-A"], work)
    _git(["commit", "-qm", "live"], work)
    res = subprocess.run(
        ["bash", "scripts/archive_run.sh", str(rd), "--force"],
        cwd=work,
        env={**os.environ, "RUN_LIB_REPO_ROOT": str(work)},
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    assert not rd.exists()
    archived = list((work / ".archive").glob("*--demo"))
    assert len(archived) == 1
    assert "--demo" in (work / ".archive" / "INDEX.md").read_text()
