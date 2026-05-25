"""Governor settings file validity tests (Task 2.1).

Asserts that .claude/governor-settings.json:
  1. Exists and is valid JSON.
  2. Conforms to the Claude Code hooks + permissions schema (confirmed against
     code.claude.com/docs/en/hooks and /en/permissions, 2026-05-25).
  3. The hook command resolves through a fake run-dir symlink (the same
     ``../../scripts`` symlink new_run.sh drops) to an executable file.
  4. The deny floor contains the mandatory catastrophic entries.

(Optional) Smoke-tests the shim end-to-end via subprocess, proving the
sys.path bootstrap actually imports and runs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Repo-root resolution (tests run from arbitrary cwd)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SETTINGS_PATH = _REPO_ROOT / ".claude" / "governor-settings.json"
_SHIM_PATH = _REPO_ROOT / "scripts" / "governor_hook.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_settings() -> dict:
    return json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. File exists and is valid JSON
# ---------------------------------------------------------------------------


def test_settings_file_exists() -> None:
    assert _SETTINGS_PATH.exists(), f"Missing: {_SETTINGS_PATH}"


def test_settings_is_valid_json() -> None:
    data = _load_settings()
    assert isinstance(data, dict), "Top-level must be a JSON object"


# ---------------------------------------------------------------------------
# 2. Schema conformance — hooks.PreToolUse
# ---------------------------------------------------------------------------


def test_hooks_pretooluse_is_list() -> None:
    """hooks.PreToolUse must be a non-empty list."""
    data = _load_settings()
    hooks = data.get("hooks", {})
    assert isinstance(hooks, dict), "hooks must be an object"
    ptu = hooks.get("PreToolUse")
    assert isinstance(ptu, list) and len(ptu) > 0, (
        "hooks.PreToolUse must be a non-empty list"
    )


def test_hooks_pretooluse_entry_shape() -> None:
    """Each PreToolUse entry must have matcher (str) and hooks (list)."""
    data = _load_settings()
    ptu = data["hooks"]["PreToolUse"]
    for i, entry in enumerate(ptu):
        assert isinstance(entry, dict), f"PreToolUse[{i}] must be an object"
        assert "matcher" in entry, f"PreToolUse[{i}] missing 'matcher'"
        assert isinstance(entry["matcher"], str), f"PreToolUse[{i}].matcher must be str"
        assert "hooks" in entry, f"PreToolUse[{i}] missing 'hooks'"
        assert isinstance(entry["hooks"], list) and len(entry["hooks"]) > 0, (
            f"PreToolUse[{i}].hooks must be a non-empty list"
        )


def test_hooks_pretooluse_command_entries() -> None:
    """Each inner hook must be type='command' with a non-empty command string."""
    data = _load_settings()
    ptu = data["hooks"]["PreToolUse"]
    for i, entry in enumerate(ptu):
        for j, h in enumerate(entry["hooks"]):
            assert isinstance(h, dict), f"PreToolUse[{i}].hooks[{j}] must be an object"
            assert h.get("type") == "command", (
                f"PreToolUse[{i}].hooks[{j}].type must be 'command', got {h.get('type')!r}"
            )
            cmd = h.get("command", "")
            assert isinstance(cmd, str) and cmd.strip(), (
                f"PreToolUse[{i}].hooks[{j}].command must be a non-empty string"
            )


def test_hook_command_references_governor_shim() -> None:
    """The hook command must reference scripts/governor_hook.py."""
    data = _load_settings()
    ptu = data["hooks"]["PreToolUse"]
    commands = [h["command"] for entry in ptu for h in entry["hooks"]]
    assert any("scripts/governor_hook.py" in cmd for cmd in commands), (
        f"No hook command references scripts/governor_hook.py; found: {commands}"
    )


def test_all_tools_matcher_present() -> None:
    """At least one PreToolUse entry must use an empty-string matcher (all tools)."""
    data = _load_settings()
    ptu = data["hooks"]["PreToolUse"]
    matchers = [entry["matcher"] for entry in ptu]
    assert "" in matchers, (
        f"No all-tools matcher ('') found in PreToolUse matchers: {matchers}"
    )


# ---------------------------------------------------------------------------
# 3. Hook command resolves through run-dir symlink
# ---------------------------------------------------------------------------


def test_hook_command_resolves_via_run_dir_symlink(tmp_path: Path) -> None:
    """The hook command must reach a real, executable file through a fake run dir.

    new_run.sh does: ln -sfn ../../scripts "$RUN_DIR/scripts"
    With RUN_DIR = <repo>/runs/<slug>, ../../scripts → <repo>/scripts/.
    We simulate this with an absolute symlink (same resolution) in tmp_path.
    """
    # Create a fake run dir and symlink scripts/ into it
    fake_run_dir = tmp_path / "fake-run"
    fake_run_dir.mkdir()
    (fake_run_dir / "scripts").symlink_to(_REPO_ROOT / "scripts")

    # Extract the hook command from settings
    data = _load_settings()
    ptu = data["hooks"]["PreToolUse"]
    governor_commands = [
        h["command"]
        for entry in ptu
        for h in entry["hooks"]
        if "scripts/governor_hook.py" in h["command"]
    ]
    assert governor_commands, "No governor_hook.py command found in settings"

    raw_cmd = governor_commands[0]

    # Expand $CLAUDE_PROJECT_DIR via the real shell-style expander (NOT a naive
    # quote-stripping replace, which would mask a quoting bug). Shell quotes
    # ("/') are removed AFTER expansion since they are shell tokens, not part of
    # the filesystem path the variable expands to.
    old = os.environ.get("CLAUDE_PROJECT_DIR")
    os.environ["CLAUDE_PROJECT_DIR"] = str(fake_run_dir)
    try:
        expanded = os.path.expandvars(raw_cmd)
    finally:
        if old is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = old

    # The variable must actually have expanded (no literal $CLAUDE_PROJECT_DIR left).
    assert "$CLAUDE_PROJECT_DIR" not in expanded, (
        f"$CLAUDE_PROJECT_DIR did not expand: {expanded!r}"
    )
    # Strip shell quoting tokens left around the path.
    path_str = expanded.replace('"', "").replace("'", "")
    resolved = Path(path_str).resolve()

    assert resolved.exists(), (
        f"Hook command path does not exist after run-dir symlink resolution: {resolved}\n"
        f"(expanded from: {raw_cmd!r} with CLAUDE_PROJECT_DIR={fake_run_dir})"
    )
    assert os.access(resolved, os.X_OK), (
        f"Hook command path is not executable: {resolved}"
    )


# ---------------------------------------------------------------------------
# 4. permissions.deny — schema and catastrophic entries
# ---------------------------------------------------------------------------


def test_permissions_deny_is_list() -> None:
    """permissions.deny must be a list of strings."""
    data = _load_settings()
    perms = data.get("permissions", {})
    assert isinstance(perms, dict), "permissions must be an object"
    deny = perms.get("deny")
    assert isinstance(deny, list), "permissions.deny must be a list"
    for i, rule in enumerate(deny):
        assert isinstance(rule, str), f"permissions.deny[{i}] must be a string"


@pytest.mark.parametrize(
    "fragment,description",
    [
        ("sudo", "sudo privilege escalation"),
        ("rm -rf /", "root-destructive rm"),
        ("~/.ssh/", "SSH key secret-path read"),
        ("governor-settings.json", "governor anti-tamper (settings)"),
        ("governor_hook.py", "governor anti-tamper (shim)"),
    ],
)
def test_deny_floor_contains_catastrophic_entry(
    fragment: str, description: str
) -> None:
    """permissions.deny must contain a rule blocking: {description}."""
    data = _load_settings()
    deny_rules = data["permissions"]["deny"]
    matching = [r for r in deny_rules if fragment in r]
    assert matching, (
        f"permissions.deny has no rule covering '{fragment}' ({description}). "
        f"Full deny list: {deny_rules}"
    )


# ---------------------------------------------------------------------------
# (Optional) Smoke test: shim subprocess e2e
# ---------------------------------------------------------------------------


def test_shim_subprocess_e2e_deny(tmp_path: Path) -> None:
    """Pipe a dangerous PreToolUse event into the shim; expect a deny JSON on stdout.

    This proves the sys.path bootstrap actually imports megalodon_ui and runs.
    Skipped if the shim cannot be found.
    """
    if not _SHIM_PATH.exists():
        pytest.skip(f"Shim not found: {_SHIM_PATH}")

    event = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "sudo rm x"},
        "cwd": str(tmp_path),
    }
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(tmp_path)

    result = subprocess.run(
        [sys.executable, str(_SHIM_PATH)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )

    assert result.returncode == 0, (
        f"Shim exited {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )

    stdout = result.stdout.strip()
    assert stdout, f"Shim produced no stdout\nstderr: {result.stderr}"

    try:
        output = json.loads(stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"Shim stdout is not valid JSON: {exc}\nraw: {stdout!r}")

    assert "hookSpecificOutput" in output, f"Missing hookSpecificOutput in: {output}"
    hso = output["hookSpecificOutput"]
    assert hso.get("permissionDecision") == "deny", (
        f"Expected deny for 'sudo rm x', got: {hso.get('permissionDecision')!r}\n"
        f"Full output: {output}"
    )
    assert hso.get("permissionDecisionReason"), (
        "permissionDecisionReason must be non-empty"
    )


# ---------------------------------------------------------------------------
# CRITICAL REGRESSION: the shim must run on STDLIB ALONE.
#
# If the shim (or hook.py) imports the heavy `megalodon_ui` package __init__,
# it drags in yaml (a venv-only third-party dep). Under bare system python3
# (no venv) that ModuleNotFoundError would fail-closed-deny EVERY tool call and
# STALL the lane on call #1. ``python3 -S`` disables site-packages, so yaml is
# unavailable — deterministically simulating bare system python. The shim must
# STILL emit a valid decision, proving the governor runs without third-party deps.
# (The other e2e tests use the venv interpreter and give false confidence here.)
# ---------------------------------------------------------------------------


def test_shim_runs_under_bare_interpreter_no_site_packages(tmp_path: Path) -> None:
    """Shim must emit a valid decision under ``python3 -S`` (no site-packages)."""
    if not _SHIM_PATH.exists():
        pytest.skip(f"Shim not found: {_SHIM_PATH}")

    event = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "sudo rm x"},
        "cwd": str(tmp_path),
    }
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(tmp_path)
    # Belt-and-suspenders: drop any PYTHONPATH that could re-expose site-packages.
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [sys.executable, "-S", str(_SHIM_PATH)],  # -S: no site-packages → no yaml
        input=json.dumps(event),
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )

    assert result.returncode == 0, (
        f"Shim exited {result.returncode} under -S (bare interpreter)\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    stdout = result.stdout.strip()
    assert stdout, (
        f"Shim produced NO stdout under -S — would stall the lane.\nstderr: {result.stderr}"
    )

    try:
        output = json.loads(stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"Shim stdout under -S is not valid JSON: {exc}\nraw: {stdout!r}\n"
            f"stderr: {result.stderr}"
        )

    hso = output.get("hookSpecificOutput", {})
    assert hso.get("permissionDecision") == "deny", (
        f"Expected deny for 'sudo rm x' under bare interpreter, got: "
        f"{hso.get('permissionDecision')!r}\nFull output: {output}\nstderr: {result.stderr}"
    )
    # Negative proof: the failure mode being guarded against (the heavy
    # __init__'s yaml import) must NOT have happened.
    assert "yaml" not in result.stderr.lower(), (
        f"Shim under -S tripped a yaml import — heavy package __init__ leaked in.\n"
        f"stderr: {result.stderr}"
    )


def test_shim_subprocess_e2e_allow(tmp_path: Path) -> None:
    """Pipe a benign PreToolUse event into the shim; expect an allow JSON on stdout."""
    if not _SHIM_PATH.exists():
        pytest.skip(f"Shim not found: {_SHIM_PATH}")

    event = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls -la"},
        "cwd": str(tmp_path),
    }
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(tmp_path)

    result = subprocess.run(
        [sys.executable, str(_SHIM_PATH)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )

    assert result.returncode == 0, (
        f"Shim exited {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )

    output = json.loads(result.stdout.strip())
    hso = output["hookSpecificOutput"]
    assert hso.get("permissionDecision") == "allow", (
        f"Expected allow for 'ls -la', got: {hso.get('permissionDecision')!r}"
    )
