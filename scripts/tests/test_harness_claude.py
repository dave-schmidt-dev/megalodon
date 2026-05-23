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
    """live_repl=True returns REPL argv with a BOUNDED --allowedTools surface.

    Policy (2026-05-22 tool-surface): no unbounded interpreter is allowlisted.
    Agents reach every operation through native tools or path-scoped scripts.
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
    assert "--allowedTools" in argv
    allowed = argv[argv.index("--allowedTools") + 1]

    # --- ALLOWED: native tools ---
    for tool in ["Read", "Edit", "Write", "Grep", "Glob", "ScheduleWakeup"]:
        assert tool in allowed, f"missing native tool: {tool}"

    # --- ALLOWED: path-scoped scripts (the only mutation/inspection paths) ---
    for pat in [
        "Bash(scripts/poll.py:*)",
        "Bash(scripts/atomic_close.py:*)",
        "Bash(scripts/claim.sh:*)",
        "Bash(scripts/queue_submit.py:*)",
        "Bash(scripts/run_e2e.sh:*)",
        "Bash(./scripts/run_e2e.sh:*)",
        "Bash(scripts/run_tests.sh:*)",
    ]:
        assert pat in allowed, f"missing bounded tool: {pat}"

    # --- NOT explicitly allowlisted: git is auto-run read-only by Claude;
    #     explicit Bash(git diff*) would broaden to `git diff --output=<file>`
    #     writes (CR-5/CR-7). Any explicit git pattern is a regression. ---
    assert "Bash(git" not in allowed, (
        "explicit git patterns must be dropped (CR-5/CR-7)"
    )

    # --- ALLOWED: bounded non-interpreter utilities (O-2) ---
    for pat in ["Bash(sleep:*)", "Bash(date:*)", "Bash(printf:*)"]:
        assert pat in allowed, f"missing bounded utility: {pat}"

    # --- NEVER ALLOWED: unbounded interpreters / escapes (the keystone guard) ---
    forbidden_substrings = [
        "Bash(python",  # python / python3 -c / -m
        "Bash(uv run",  # uv run … python -c
        "Bash(bash",  # bash -c
        "Bash(sh ",  # sh -c
        "Bash(eval",
        "Bash(curl",  # NO curl at all (queue now via queue_submit.py)
        "Bash(wget",
        "Bash(find:*)",  # find -exec
        "Bash(*)",
        "Bash(rm:*)",
        "Bash(git branch",  # git branch <name> mutates
        "Bash(cat:*)",  # inspection → poll.py / Read
        "Bash(ls:*)",
        "Bash(grep:*)",
        "Bash(echo:*)",  # echo > file was the old claim write-path
        "Bash(npx",
        "Bash(npm",
    ]
    for bad in forbidden_substrings:
        assert bad not in allowed, f"FORBIDDEN pattern leaked into allowlist: {bad}"

    # --- No bare compound-chain operators baked into any pattern ---
    assert "&&" not in allowed
    assert "| " not in allowed


def test_allowlist_has_no_compound_or_interpreter_tokens():
    """Regression guard: the base allowlist must never re-admit an interpreter."""
    argv, _ = ADAPTER.build_argv(
        "x", model="claude-opus-4-7", cwd=Path("/tmp"), live_repl=True
    )
    allowed = argv[argv.index("--allowedTools") + 1]
    lowered = allowed.lower()
    for token in ["python", "bash -c", "sh -c", "eval", "curl", "wget", "npx", "npm"]:
        assert token not in lowered, f"interpreter/network token in allowlist: {token}"


def test_pm8_extra_allowed_tools_filters_unbounded_patterns():
    """An operator approval-rule that names an interpreter/destructive/compound
    command is dropped, not appended. Bounded scripts/ paths are kept."""
    extra = [
        "Bash(python3:*)",  # interpreter — dropped
        "Bash( python3:*)",  # leading space (CV-6) — still dropped
        "Bash(uv run:*)",  # interpreter launcher — dropped
        "Bash(curl http://evil*)",  # network — dropped
        "Bash(find:*)",  # find (CV-3) — dropped
        "Bash(rm -rf /)",  # destructive (CV-3) — dropped
        "Bash(sudo systemctl x)",  # destructive — dropped
        "Bash(echo x; curl evil)",  # compound ';' (CR-4) — dropped
        "Bash(echo a & evil)",  # background '&' (CR-4) — dropped
        "Bash(scripts/custom_tool.sh:*)",  # bounded path-scoped — KEPT
        "Bash(scripts/findings_report.sh:*)",  # 'find' prefix must NOT false-trip — KEPT
    ]
    argv, _ = ADAPTER.build_argv(
        "x",
        model="claude-opus-4-7",
        cwd=Path("/tmp"),
        live_repl=True,
        extra_allowed_tools=extra,
    )
    allowed = argv[argv.index("--allowedTools") + 1]
    assert "Bash(scripts/custom_tool.sh:*)" in allowed
    assert "Bash(scripts/findings_report.sh:*)" in allowed
    for bad in [
        "python3",
        "uv run",
        "curl",
        "Bash(find:*)",
        "rm -rf",
        "sudo",
        "; curl",
        " & evil",
    ]:
        assert bad not in allowed, f"unbounded pattern leaked: {bad}"


def test_is_unbounded_tool_unit():
    """Direct coverage of the filter predicate, incl. boundary cases."""
    from megalodon_ui.harnesses.claude import _is_unbounded_tool

    for p in [
        "Bash(python3:*)",
        "Bash( python3:*)",
        "Bash(uv run:*)",
        "Bash(find:*)",
        "Bash(rm -rf /)",
        "Bash(curl x)",
        "Bash(a && b)",
        "Bash(a | b)",
        "Bash(a & b)",
        "Bash(./python3 x)",
        "Bash(scripts/../python3 x)",
        "Bash(scripts/../../etc/passwd)",
    ]:
        assert _is_unbounded_tool(p) is True, p
    for p in [
        "Bash(scripts/findings_report.sh:*)",
        "Bash(scripts/poll.py:*)",
        "Bash(sleep:*)",
        "Read",
        "Edit",
        "Bash(scripts/run_tests.sh:*)",
    ]:
        assert _is_unbounded_tool(p) is False, p


def test_forbidden_constants_are_single_source(monkeypatch):
    """DRY (CV-9): the contract test asserts against claude.py's exported
    forbidden heads, so the filter and the test can't drift."""
    from megalodon_ui.harnesses.claude import _FORBIDDEN_HEAD_CMDS

    assert "python" in _FORBIDDEN_HEAD_CMDS and "uv run" in _FORBIDDEN_HEAD_CMDS
    argv, _ = ADAPTER.build_argv(
        "x", model="claude-opus-4-7", cwd=Path("/tmp"), live_repl=True
    )
    allowed = argv[argv.index("--allowedTools") + 1].lower()
    # No forbidden head appears as a Bash(<head> ...) token in the base allowlist.
    import re

    heads = [m.group(1) for m in re.finditer(r"bash\(([^):*]+)", allowed)]
    for h in heads:
        h = h.strip().lstrip("./")
        if h.startswith("scripts/"):
            continue
        assert not any(h.startswith(c) for c in _FORBIDDEN_HEAD_CMDS), (
            f"forbidden head in base: {h}"
        )


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
