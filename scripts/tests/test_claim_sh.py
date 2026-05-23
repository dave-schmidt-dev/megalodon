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
    assert (
        tmp_path / "claims" / "P1-A" / "owner.txt"
    ).read_text().strip() == "agent-aaaa"


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
