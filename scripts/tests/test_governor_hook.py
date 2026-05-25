"""Governor hook entry-point tests (Task 1.2).

Drives ``megalodon_ui.governor.hook.run()`` directly by feeding a StringIO stdin
and capturing a StringIO stdout.  No subprocess; fast.

Schema refs (Claude Code docs, confirmed 2026-05-25):
  stdin  fields: session_id, transcript_path, cwd, permission_mode,
                 hook_event_name, tool_name, tool_input
  stdout fields: {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                   "permissionDecision": "allow"|"deny",
                   "permissionDecisionReason": "<str>"}}
"""

from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.governor.hook import run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    tool_name: str = "Bash",
    tool_input: dict[str, Any] | None = None,
    cwd: str | None = None,
) -> dict[str, Any]:
    return {
        "session_id": "test-session-001",
        "transcript_path": "/tmp/transcript.jsonl",
        "cwd": cwd or "/tmp/fake-lane",
        "permission_mode": "default",
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input if tool_input is not None else {},
    }


def _run(
    event: dict[str, Any],
    project_dir: Path | None = None,
    *,
    extra_env: dict[str, str] | None = None,
) -> tuple[dict[str, Any], Path | None]:
    """Run hook.run() and return (parsed stdout JSON, fleet_dir or None).

    fleet_dir is only set when project_dir is provided.
    """
    stdin = io.StringIO(json.dumps(event))
    stdout = io.StringIO()
    env: dict[str, str] = {}
    if project_dir is not None:
        env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    if extra_env:
        env.update(extra_env)
    run(stdin=stdin, stdout=stdout, env=env)
    stdout.seek(0)
    return json.loads(stdout.read()), (project_dir / ".fleet" if project_dir else None)


def _sha256(tool_input: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(tool_input, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _read_latest_audit_line(fleet_dir: Path) -> dict[str, Any]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = fleet_dir / f"governor-log-{today}.jsonl"
    lines = [
        ln for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()
    ]
    return json.loads(lines[-1])


# ---------------------------------------------------------------------------
# Allow path
# ---------------------------------------------------------------------------


def test_allow_benign_bash(tmp_path: Path) -> None:
    """A safe 'ls' command → permissionDecision == 'allow', audit written."""
    project_dir = tmp_path / "mission"
    project_dir.mkdir()
    event = _make_event(
        tool_name="Bash",
        tool_input={"command": "ls"},
        cwd=str(project_dir),
    )
    result, fleet_dir = _run(event, project_dir)

    # Correct outer structure
    assert "hookSpecificOutput" in result
    hso = result["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "allow"
    assert isinstance(hso.get("permissionDecisionReason"), str)
    assert hso["permissionDecisionReason"]  # non-empty

    # Audit line was written
    assert fleet_dir is not None
    audit = _read_latest_audit_line(fleet_dir)
    assert audit["permission"] == "allow"
    assert audit["tool"] == "Bash"


# ---------------------------------------------------------------------------
# Deny path
# ---------------------------------------------------------------------------


def test_deny_dangerous_bash(tmp_path: Path) -> None:
    """'sudo rm x' → permissionDecision == 'deny', non-empty reason, audit line."""
    project_dir = tmp_path / "mission"
    project_dir.mkdir()
    event = _make_event(
        tool_name="Bash",
        tool_input={"command": "sudo rm x"},
        cwd=str(project_dir),
    )
    result, fleet_dir = _run(event, project_dir)

    hso = result["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny"
    assert hso["permissionDecisionReason"]

    assert fleet_dir is not None
    audit = _read_latest_audit_line(fleet_dir)
    assert audit["permission"] == "deny"


# ---------------------------------------------------------------------------
# Secret hashing — raw secret content must NEVER appear in the audit log
# ---------------------------------------------------------------------------


def test_secret_hashing_not_raw(tmp_path: Path) -> None:
    """Audit line must contain input_sha256, NOT the raw ~/.ssh/id_rsa path."""
    project_dir = tmp_path / "mission"
    project_dir.mkdir()
    tool_input = {"command": "cat ~/.ssh/id_rsa"}
    event = _make_event(
        tool_name="Bash",
        tool_input=tool_input,
        cwd=str(project_dir),
    )
    result, fleet_dir = _run(event, project_dir)

    # Decision is deny (secret read)
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    # Audit file raw text must not contain the secret path
    assert fleet_dir is not None
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = fleet_dir / f"governor-log-{today}.jsonl"
    raw_text = log_path.read_text(encoding="utf-8")
    assert "id_rsa" not in raw_text, "raw secret path must never appear in audit log"
    assert ".ssh" not in raw_text, "ssh dir must never appear in audit log"

    # But hash IS present and correct
    audit = _read_latest_audit_line(fleet_dir)
    assert "input_sha256" in audit
    expected = _sha256(tool_input)
    assert audit["input_sha256"] == expected

    # secret-read is an input-bearing category → audit reason is the bare
    # category (never the raw policy reason, which embeds the path).
    assert audit["reason"] == audit["category"] == "secret-read"


# ---------------------------------------------------------------------------
# Diagnostic-retention: safe categories keep their full reason in the audit
# ---------------------------------------------------------------------------


def test_audit_reason_retains_diagnostic_for_privilege(tmp_path: Path) -> None:
    """A bash-privilege deny ('sudo rm x') keeps diagnostic detail (the head)."""
    project_dir = tmp_path / "mission"
    project_dir.mkdir()
    event = _make_event(
        tool_name="Bash",
        tool_input={"command": "sudo rm x"},
        cwd=str(project_dir),
    )
    _run(event, project_dir)
    audit = _read_latest_audit_line(project_dir / ".fleet")
    assert audit["category"] == "bash-privilege"
    # Not reduced to the bare category — full diagnostic reason retained.
    assert audit["reason"] != audit["category"]
    assert "sudo" in audit["reason"]


def test_audit_reason_retains_diagnostic_for_interpreter(tmp_path: Path) -> None:
    """A bash-interpreter deny ('python3 -c 1') keeps the head for diagnosis."""
    project_dir = tmp_path / "mission"
    project_dir.mkdir()
    event = _make_event(
        tool_name="Bash",
        tool_input={"command": "python3 -c 1"},
        cwd=str(project_dir),
    )
    _run(event, project_dir)
    audit = _read_latest_audit_line(project_dir / ".fleet")
    assert audit["category"] == "bash-interpreter"
    assert audit["reason"] != audit["category"]
    assert "python3" in audit["reason"]


# ---------------------------------------------------------------------------
# Defensive net: a kept-category reason that nonetheless echoes a tool_input
# value (e.g. a misclassified / future input-embedding case) falls back to the
# bare category instead of leaking the value.
# ---------------------------------------------------------------------------


def test_defensive_net_falls_back_to_category() -> None:
    """If a non-listed category's reason embeds a tool_input value, redact it.

    Simulated by a synthetic Decision whose category is NOT in the
    input-bearing set but whose reason quotes a tool_input value. The audit
    writer must fall back to the bare category, never the leaking reason.
    """
    from megalodon_ui.governor.hook import _audit_reason
    from megalodon_ui.governor.policy import Decision

    secret_value = "/home/user/secret-token-value.key"
    tool_input = {"command": f"chmod 600 {secret_value}"}
    # 'bash-privilege' is intentionally NOT in _INPUT_BEARING_CATEGORIES, but
    # the chmod reason embeds the full segment (the secret path).
    leaking = Decision(
        permission="deny",
        reason=f"permission/owner change: 'chmod 600 {secret_value}'",
        category="bash-privilege",
    )
    result = _audit_reason(leaking, tool_input)
    assert result == "bash-privilege", "defensive net must redact to bare category"
    assert secret_value not in result


def test_defensive_net_redacts_home_path_fragment() -> None:
    """A kept-category reason containing a '~' home fragment is redacted."""
    from megalodon_ui.governor.hook import _audit_reason
    from megalodon_ui.governor.policy import Decision

    tool_input = {"command": "chmod 600 ~/secret"}
    leaking = Decision(
        permission="deny",
        reason="permission/owner change: 'chmod 600 ~/secret'",
        category="bash-privilege",
    )
    result = _audit_reason(leaking, tool_input)
    assert result == "bash-privilege"
    assert "~" not in result


def test_defensive_net_catches_repr_escaped_leak() -> None:
    """The net must catch a value embedded in repr()/{!r} form, not just raw.

    A reason built with ``{value!r}`` escapes control chars (``\\n`` becomes a
    literal backslash-n), so a raw-value substring test would MISS it. The
    hardened net tests the repr-escaped fragment too.
    """
    from megalodon_ui.governor.hook import _audit_reason
    from megalodon_ui.governor.policy import Decision

    secret = "token\nLINE2"
    tool_input = {"command": f'chmod 600 "{secret}"'}
    # Reason embeds the segment via repr() — the raw "token\nLINE2" does NOT
    # appear verbatim, but its repr-escaped form "token\\nLINE2" does.
    leaking = Decision(
        permission="deny",
        reason=f"permission/owner change: {tool_input['command']!r}",
        category="bash-privilege",
    )
    # Sanity: the raw value is NOT a literal substring of the reason.
    assert secret not in leaking.reason
    result = _audit_reason(leaking, tool_input)
    assert result == "bash-privilege", "net must catch repr-escaped leak"
    assert "token" not in result


# ---------------------------------------------------------------------------
# REGRESSION (durable-log leak): a chmod/rm target with control chars (\n, \t)
# must NOT land in the written audit log. Before the policy root-fix the reason
# was `... {segment!r}` (kept category) and the repr-escaped fragment leaked.
# These exercise the FULL run()->_write_audit path against the real policy.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cmd,leak_fragment",
    [
        ('chmod 600 "token\nLINE2"', "token"),
        ('chmod 600 "tab\there"', "tab"),
        ('rm -rf "/etc/secretdir\nX"', "secretdir"),
        ('rm -rf "/etc/secret\ttab"', "secret"),
    ],
)
def test_regression_control_char_target_not_in_log(
    tmp_path: Path, cmd: str, leak_fragment: str
) -> None:
    project_dir = tmp_path / "mission"
    project_dir.mkdir()
    event = _make_event(
        tool_name="Bash",
        tool_input={"command": cmd},
        cwd=str(project_dir),
    )
    result, fleet_dir = _run(event, project_dir)
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    raw_text = (fleet_dir / f"governor-log-{today}.jsonl").read_text(encoding="utf-8")
    assert leak_fragment not in raw_text, (
        f"control-char target fragment {leak_fragment!r} leaked into audit log: "
        f"{raw_text!r}"
    )


# ---------------------------------------------------------------------------
# Audit line schema
# ---------------------------------------------------------------------------

REQUIRED_AUDIT_KEYS = {
    "ts",
    "lane",
    "tool",
    "permission",
    "category",
    "reason",
    "input_sha256",
}


def test_audit_line_schema(tmp_path: Path) -> None:
    """Audit line has exactly the required keys; ts parses as UTC ISO8601."""
    project_dir = tmp_path / "mission"
    project_dir.mkdir()
    event = _make_event(
        tool_name="Bash",
        tool_input={"command": "ls"},
        cwd=str(project_dir),
    )
    _run(event, project_dir)

    fleet_dir = project_dir / ".fleet"
    audit = _read_latest_audit_line(fleet_dir)
    assert set(audit.keys()) == REQUIRED_AUDIT_KEYS, (
        f"Extra/missing keys: {set(audit.keys()) ^ REQUIRED_AUDIT_KEYS}"
    )
    # ts must parse as UTC datetime
    ts = datetime.fromisoformat(audit["ts"])
    assert ts.tzinfo is not None, "ts must be timezone-aware"


# ---------------------------------------------------------------------------
# Audit file daily rotation naming
# ---------------------------------------------------------------------------


def test_audit_file_daily_rotation(tmp_path: Path) -> None:
    """Log file is named governor-log-<UTC date>.jsonl under <project_dir>/.fleet/."""
    project_dir = tmp_path / "mission"
    project_dir.mkdir()
    event = _make_event(
        tool_name="Bash",
        tool_input={"command": "ls"},
        cwd=str(project_dir),
    )
    _run(event, project_dir)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fleet_dir = project_dir / ".fleet"
    expected_log = fleet_dir / f"governor-log-{today}.jsonl"
    assert expected_log.exists(), f"Expected log file not found: {expected_log}"


# ---------------------------------------------------------------------------
# Fail-closed: malformed stdin
# ---------------------------------------------------------------------------


def test_fail_closed_bad_json() -> None:
    """Non-JSON stdin → valid deny decision, no exception raised."""
    stdin = io.StringIO("this is not json {{{{")
    stdout = io.StringIO()
    run(stdin=stdin, stdout=stdout, env={})
    stdout.seek(0)
    result = json.loads(stdout.read())
    hso = result["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny"
    assert "governor-error" in hso["permissionDecisionReason"]


def test_fail_closed_missing_tool_name() -> None:
    """JSON stdin missing tool_name → valid deny, no exception."""
    event = {"session_id": "x", "cwd": "/tmp", "tool_input": {}}  # tool_name absent
    stdin = io.StringIO(json.dumps(event))
    stdout = io.StringIO()
    run(stdin=stdin, stdout=stdout, env={})
    stdout.seek(0)
    result = json.loads(stdout.read())
    hso = result["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny"
    assert "governor-error" in hso["permissionDecisionReason"]


# ---------------------------------------------------------------------------
# Audit-write failure: hook still emits decision, does NOT raise
# ---------------------------------------------------------------------------


def test_audit_write_failure_still_emits_decision(tmp_path: Path) -> None:
    """If the audit write fails, the hook still emits a valid decision."""
    project_dir = tmp_path / "mission"
    project_dir.mkdir()
    event = _make_event(
        tool_name="Bash",
        tool_input={"command": "ls"},
        cwd=str(project_dir),
    )

    # Patch the audit writer inside hook to raise
    with patch(
        "megalodon_ui.governor.hook._write_audit", side_effect=OSError("disk full")
    ):
        stdin = io.StringIO(json.dumps(event))
        stdout = io.StringIO()
        run(stdin=stdin, stdout=stdout, env={"CLAUDE_PROJECT_DIR": str(project_dir)})
        stdout.seek(0)
        result = json.loads(stdout.read())

    hso = result["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] in ("allow", "deny")


# ---------------------------------------------------------------------------
# Scope fallback: CLAUDE_PROJECT_DIR unset → use event cwd
# ---------------------------------------------------------------------------


def test_scope_fallback_to_event_cwd(tmp_path: Path) -> None:
    """When CLAUDE_PROJECT_DIR is unset, hook falls back to event cwd.

    Asserted indirectly: the audit log appears under the event cwd's .fleet/,
    not under some other path.  The decision is also valid.
    """
    project_dir = tmp_path / "cwd_based_project"
    project_dir.mkdir()
    event = _make_event(
        tool_name="Bash",
        tool_input={"command": "ls"},
        cwd=str(project_dir),
    )
    stdin = io.StringIO(json.dumps(event))
    stdout = io.StringIO()
    # env has no CLAUDE_PROJECT_DIR
    run(stdin=stdin, stdout=stdout, env={})
    stdout.seek(0)
    result = json.loads(stdout.read())

    assert result["hookSpecificOutput"]["permissionDecision"] in ("allow", "deny")

    # Audit must have been written under project_dir/.fleet/
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fleet_dir = project_dir / ".fleet"
    expected_log = fleet_dir / f"governor-log-{today}.jsonl"
    assert expected_log.exists(), (
        "Audit log should fall under event cwd when CLAUDE_PROJECT_DIR is unset"
    )


# ---------------------------------------------------------------------------
# Valid JSON for both allow and deny paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cmd,expected_decision",
    [
        ("ls", "allow"),
        ("sudo rm x", "deny"),
    ],
)
def test_stdout_is_valid_json_with_required_keys(
    tmp_path: Path, cmd: str, expected_decision: str
) -> None:
    """stdout is parseable JSON with hookSpecificOutput/hookEventName/permissionDecision."""
    project_dir = tmp_path / "mission"
    project_dir.mkdir()
    event = _make_event(
        tool_name="Bash",
        tool_input={"command": cmd},
        cwd=str(project_dir),
    )
    result, _ = _run(event, project_dir)

    assert "hookSpecificOutput" in result
    hso = result["hookSpecificOutput"]
    assert "hookEventName" in hso
    assert "permissionDecision" in hso
    assert "permissionDecisionReason" in hso
    assert hso["permissionDecision"] == expected_decision
