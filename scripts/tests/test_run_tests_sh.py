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
    assert '"$@"' in text  # forwards args, execs (no trailing commands)


def test_collect_only_smoke():
    """Wrapper drives pytest collection via its REAL command shape (direct exec)."""
    r = subprocess.run(
        [str(RUN_TESTS), "--collect-only", "-q", "scripts/tests/test_run_tests_sh.py"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert r.returncode == 0, r.stderr
    assert "test_invokes_uv_directory_extra_test_pytest" in r.stdout
