"""Phase 0 — assert the README-mandated helper-script wildcards are allowlisted.

`.claude/settings.json` is a machine-local, gitignored permissions file, so it is
absent on fresh clones and in CI. This test guards the *operator's* dogfood machine
(where preflight.sh runs it); it skips cleanly when the file is not present rather
than erroring, so the committed test is safe on clones/CI.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SETTINGS = REPO / ".claude/settings.json"
REQUIRED = {
    "Bash(scripts/atomic_close.py:*)",
    "Bash(scripts/poll.py:*)",
    "Bash(scripts/run_e2e.sh:*)",
}


def test_helper_script_wildcards_present():
    if not SETTINGS.exists():
        pytest.skip(
            ".claude/settings.json absent (gitignored; clone/CI) — operator-machine only"
        )
    settings = json.loads(SETTINGS.read_text())
    allow = set(settings["permissions"]["allow"])
    missing = REQUIRED - allow
    assert not missing, f"missing helper-script wildcards: {missing}"
