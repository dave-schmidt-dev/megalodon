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
