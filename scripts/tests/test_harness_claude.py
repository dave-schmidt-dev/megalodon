"""Tests for ClaudeAdapter (P1.6 — harness adapter contract)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.harnesses.claude import ClaudeAdapter

ADAPTER = ClaudeAdapter()

# ---------------------------------------------------------------------------
# 1. name and default_model
# ---------------------------------------------------------------------------


def test_name_and_default_model():
    assert ADAPTER.name == "claude"
    assert ADAPTER.default_model == "claude-opus-4-7"


# ---------------------------------------------------------------------------
# 2. available_models includes all documented ids
# ---------------------------------------------------------------------------


def test_available_models_include_documented():
    documented = {
        "claude-opus-4-7",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
        "claude-opus-4-6",
        "claude-opus-4-5-20251101",
        "claude-sonnet-4-5-20250929",
    }
    ids = {m.id for m in ADAPTER.available_models}
    assert documented <= ids, f"Missing: {documented - ids}"


# ---------------------------------------------------------------------------
# 3. build_argv — text default
# ---------------------------------------------------------------------------


def test_build_argv_text_default():
    argv, env = ADAPTER.build_argv(
        "hello",
        model="claude-opus-4-7",
        cwd=Path("/tmp"),
    )
    assert argv == ["claude", "--print", "--model", "claude-opus-4-7", "hello"]
    assert env == {}


# ---------------------------------------------------------------------------
# 4. build_argv — stream-json adds --output-format flag
# ---------------------------------------------------------------------------


def test_build_argv_live_repl_omits_print_and_prompt():
    """v9.3 dogfood: live_repl=True returns REPL argv (no --print, no prompt).

    Now also injects a tight --allowedTools so the agent doesn't prompt for
    every protocol primitive (claim mkdir, claim rm, Read/Edit/Write); but
    Python and general Bash are explicitly NOT auto-approved — those surface
    to the operator via the dashboard's permission_watcher.
    """
    argv, env = ADAPTER.build_argv(
        "ignored-because-repl-takes-input-via-send-keys",
        model="claude-opus-4-7",
        cwd=Path("/tmp"),
        live_repl=True,
    )
    assert argv[:2] == ["claude", "--model"]
    assert "claude-opus-4-7" in argv
    assert "--print" not in argv
    assert env == {}
    # allowedTools must be present and contain protocol primitives only
    assert "--allowedTools" in argv
    allowed_idx = argv.index("--allowedTools") + 1
    allowed = argv[allowed_idx]
    # Protocol primitives auto-approved
    assert "Bash(mkdir claims/*)" in allowed
    assert "Bash(rm -rf claims/*)" in allowed
    assert "ScheduleWakeup" in allowed
    assert "Read" in allowed and "Edit" in allowed and "Write" in allowed
    # Read-only workspace shell ops auto-approved (v9.3 — "read from project
    # workspace" is a basic capability, not a per-command decision).
    for safe in [
        "Bash(ls:*)",
        "Bash(grep:*)",
        "Bash(cat:*)",
        "Bash(echo:*)",
        "Bash(head:*)",
        "Bash(tail:*)",
        "Bash(wc:*)",
        "Bash(rg:*)",
    ]:
        assert safe in allowed, f"missing safe shell op: {safe}"
    # Read-only git auto-approved.
    assert "Bash(git status*)" in allowed
    assert "Bash(git diff*)" in allowed
    assert "Bash(git log*)" in allowed
    # Bare `python3 -c "..."` NOT auto-approved (arbitrary code injection).
    assert "Bash(python" not in allowed
    # find NOT auto-approved (has -exec arbitrary-command-execution).
    assert "Bash(find:*)" not in allowed
    # v9.3.3 — test runners auto-approved (operator-authorized at-launch).
    # NARROW SCOPE (option 1): `uv run` is gated to invocations that DECLARE
    # pytest in --with deps, so `uv run --with pytest ... pytest ...` from
    # the launch template auto-approves but `uv run --with badpkg python -c
    # "..."` still prompts.
    assert "Bash(pytest:*)" in allowed
    assert "Bash(uv run --with pytest*)" in allowed
    assert "Bash(./scripts/run_e2e.sh*)" in allowed
    assert "Bash(npx playwright:*)" in allowed
    # Crucially the BROAD `Bash(uv run:*)` pattern is NOT present — that was
    # the medium-risk surface the operator explicitly rejected.
    assert "Bash(uv run:*)" not in allowed
    # Wildcard / dangerous-rm NOT auto-approved.
    assert "Bash(rm:*)" not in allowed
    assert "Bash(*)" not in allowed
    # Network ops NOT auto-approved EXCEPT localhost-scoped curl for queue endpoints.
    assert "Bash(wget" not in allowed
    # General-purpose curl (no host scope) must NOT be auto-approved.
    assert "Bash(curl:*)" not in allowed
    assert "Bash(curl -X*)" not in allowed
    # But localhost-scoped curl IS allowed (agents call queue endpoints).
    assert "Bash(curl -s http://127.0.0.1*)" in allowed
    assert "Bash(curl -s -X POST http://127.0.0.1*)" in allowed


def test_build_argv_live_repl_ignores_output_format():
    """live_repl mode is REPL-only; output-format is meaningless."""
    argv, _ = ADAPTER.build_argv(
        "hello",
        model="claude-sonnet-4-6",
        cwd=Path("/tmp"),
        output_format="stream-json",
        live_repl=True,
    )
    assert "--output-format" not in argv
    assert "stream-json" not in argv


def test_build_argv_stream_json_when_supported():
    argv, env = ADAPTER.build_argv(
        "hello",
        model="claude-opus-4-7",
        cwd=Path("/tmp"),
        output_format="stream-json",
    )
    assert "--output-format" in argv
    assert "stream-json" in argv
    assert env == {}


# ---------------------------------------------------------------------------
# 5. parse_stream_line — plain text
# ---------------------------------------------------------------------------


def test_parse_stream_line_handles_plain_text():
    result = ADAPTER.parse_stream_line("hello world\n")
    assert result is not None
    assert result.kind == "text"
    assert result.text == "hello world"


# ---------------------------------------------------------------------------
# 6. auth_env_keys
# ---------------------------------------------------------------------------


def test_auth_env_keys_listed():
    assert ADAPTER.auth_env_keys() == ["ANTHROPIC_API_KEY"]


# ---------------------------------------------------------------------------
# Smoke: --help (skipped in CI or if binary absent)
# ---------------------------------------------------------------------------


@pytest.mark.smoke_harness
def test_help_smoke():
    if shutil.which("claude") is None:
        pytest.skip("claude CLI not found")
    if os.environ.get("CI"):
        pytest.skip("smoke tests disabled in CI")
    result = subprocess.run(["claude", "--help"], capture_output=True, timeout=10)
    assert result.returncode == 0
