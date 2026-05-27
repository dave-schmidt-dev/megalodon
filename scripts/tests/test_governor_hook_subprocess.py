"""Real-subprocess hook test (Task P0.9).

The other hook tests drive ``hook.run()`` in-process via StringIO. This one runs
``scripts/governor_hook.py`` as an ACTUAL subprocess — the same path the fleet
uses at runtime (``$CLAUDE_PROJECT_DIR/scripts/governor_hook.py`` reading a JSON
event on stdin, writing a JSON decision on stdout, appending an audit line). It
closes the matrix by proving a representative NEW escape (``setsid rm -rf /``) is
denied via the real subprocess path, not just via in-process ``decide()``.

stdin schema (Claude Code PreToolUse, confirmed in hook.py):
  {"session_id", "transcript_path", "cwd", "permission_mode",
   "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {...}}
stdout schema:
  {"hookSpecificOutput": {"hookEventName": "PreToolUse",
   "permissionDecision": "allow"|"deny", "permissionDecisionReason": "<str>"}}

The audit log is isolated to ``tmp_path`` by pointing CLAUDE_PROJECT_DIR at it;
hook.py writes ``<project_dir>/.fleet/governor-log-<UTC date>.jsonl``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / "scripts" / "governor_hook.py"


def _make_event(command: str, cwd: str) -> dict:
    return {
        "session_id": "test-subproc-001",
        "transcript_path": "/tmp/transcript.jsonl",
        "cwd": cwd,
        "permission_mode": "default",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }


def test_hook_subprocess_denies_new_escape_and_audits(tmp_path: Path) -> None:
    """`setsid rm -rf /` via the REAL subprocess: stdout denies + audit line written."""
    project_dir = tmp_path / "mission"
    project_dir.mkdir()
    event = _make_event("setsid rm -rf /", cwd=str(project_dir))

    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        # Point the audit log at tmp_path (isolated) and keep PATH minimal.
        env={"CLAUDE_PROJECT_DIR": str(project_dir), "PATH": "/usr/bin:/bin"},
    )

    assert proc.returncode == 0, (
        f"hook exited nonzero: {proc.returncode}\n{proc.stderr}"
    )

    # (a) stdout JSON decision denies, per the hook output schema.
    result = json.loads(proc.stdout)
    hso = result["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny", (
        f"real subprocess did not deny the escape: {hso!r}"
    )
    assert hso["permissionDecisionReason"], "deny reason must be non-empty"

    # (b) an audit line was written recording the deny, under the isolated dir.
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = project_dir / ".fleet" / f"governor-log-{today}.jsonl"
    assert log_path.exists(), f"audit log not written: {log_path}"
    lines = [
        ln for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()
    ]
    assert lines, "audit log is empty"
    audit = json.loads(lines[-1])
    assert audit["permission"] == "deny", f"audit did not record a deny: {audit!r}"
    assert audit["tool"] == "Bash"
