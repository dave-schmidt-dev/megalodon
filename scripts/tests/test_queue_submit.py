"""Tests for scripts/queue_submit.py — path-scoped wrapper over queue_client.main."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "scripts" / "queue_submit.py"


def test_wrapper_is_executable():
    import os
    import stat

    assert os.stat(WRAPPER).st_mode & stat.S_IXUSR, (
        "queue_submit.py missing executable bit"
    )


def test_help_via_direct_exec_exits_zero():
    """Real command shape: direct exec via shebang (Bash(scripts/queue_submit.py:*)),
    NOT `python wrapper.py` — so a missing chmod/shebang fails here (CR-6)."""
    r = subprocess.run([str(WRAPPER), "--help"], capture_output=True, text=True)
    assert r.returncode == 0
    assert "queue" in r.stdout.lower()


def test_missing_required_args_exit_nonzero():
    # No --mission-dir/--agent/--lane → argparse error (exit 2).
    r = subprocess.run([sys.executable, str(WRAPPER)], capture_output=True, text=True)
    assert r.returncode != 0


def test_forwards_to_queue_client_main(monkeypatch):
    """mod.main forwards its argv verbatim to queue_client.main."""
    sys.path.insert(0, str(REPO))
    import importlib

    qsub = importlib.import_module("scripts.queue_submit")
    import megalodon_ui.queue.queue_client as qc

    seen = {}
    monkeypatch.setattr(qc, "main", lambda argv: (seen.update(argv=argv), 0)[1])
    # queue_submit binds `_qc_main` at import; patch the bound name too.
    monkeypatch.setattr(qsub, "_qc_main", qc.main)

    rc = qsub.main(
        [
            "--mission-dir",
            "/tmp/m",
            "--agent",
            "a",
            "--lane",
            "BACKEND",
            "status",
            "--state",
            "idle",
            "--notes",
            "hb",
        ]
    )
    assert rc == 0
    assert seen["argv"][:6] == [
        "--mission-dir",
        "/tmp/m",
        "--agent",
        "a",
        "--lane",
        "BACKEND",
    ]
