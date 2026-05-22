"""v9.4 — preflight.sh checks (each emits a CHECK line; exits non-zero on any fail)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_preflight_reports_all_checks():
    # PREFLIGHT_SKIP_HEAVY=1 skips check 2's full suite run so this unit test
    # stays fast and cannot recurse (test_preflight -> preflight.sh -> pytest).
    res = subprocess.run(
        ["bash", "scripts/preflight.sh", "--dry-run"],
        cwd=REPO,
        capture_output=True,
        text=True,
        env={**os.environ, "PREFLIGHT_SKIP_HEAVY": "1"},
    )
    out = res.stdout + res.stderr
    for label in [
        "CHECK pytest-scope",
        "CHECK test-deps",
        "CHECK friction-allowlist",
        "CHECK lifecycle-scripts",
    ]:
        assert label in out, f"missing {label}\n{out}"
