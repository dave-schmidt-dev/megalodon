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
# Governor --settings wiring (Task 2.2 — ADDITIVE; allowlist untouched)
# ---------------------------------------------------------------------------

_GOV = Path("/repo/.claude/governor-settings.json")


def test_build_argv_live_repl_carries_settings_keeps_allowlist():
    """live_repl + governor_settings → --settings present, --allowedTools intact."""
    argv, _ = ADAPTER.build_argv(
        "x",
        model="claude-opus-4-7",
        cwd=Path("/tmp"),
        live_repl=True,
        governor_settings=_GOV,
    )
    assert "--settings" in argv
    assert argv[argv.index("--settings") + 1] == str(_GOV)
    # Allowlist STILL present (additive change must not remove it).
    assert "--allowedTools" in argv
    allowed = argv[argv.index("--allowedTools") + 1]
    assert "Read" in allowed and "Bash(scripts/poll.py:*)" in allowed


def test_build_argv_print_carries_settings():
    """non-live --print branch + governor_settings → --settings present."""
    argv, _ = ADAPTER.build_argv(
        "hello",
        model="claude-opus-4-7",
        cwd=Path("/tmp"),
        governor_settings=_GOV,
    )
    assert "--settings" in argv
    assert argv[argv.index("--settings") + 1] == str(_GOV)


def test_build_argv_omits_settings_when_none():
    """governor_settings=None (default) → argv unchanged from today."""
    argv_repl, _ = ADAPTER.build_argv(
        "x", model="claude-opus-4-7", cwd=Path("/tmp"), live_repl=True
    )
    argv_print, _ = ADAPTER.build_argv(
        "hello", model="claude-opus-4-7", cwd=Path("/tmp")
    )
    assert "--settings" not in argv_repl
    assert "--settings" not in argv_print
    # Back-compat: the historical text-default argv is byte-identical.
    assert argv_print == ["claude", "--print", "--model", "claude-opus-4-7", "hello"]


def test_build_argv_settings_positioned_after_model_before_prompt():
    """--settings must come after --model <id> and before any trailing prompt."""
    # non-live --print: trailing positional prompt must be LAST.
    argv, _ = ADAPTER.build_argv(
        "the-prompt",
        model="claude-opus-4-7",
        cwd=Path("/tmp"),
        governor_settings=_GOV,
    )
    assert argv[-1] == "the-prompt"
    i_model = argv.index("--model")
    i_settings = argv.index("--settings")
    assert i_settings > i_model  # after --model
    assert i_settings < len(argv) - 1  # before the trailing prompt
    # live_repl: --settings after --model (no trailing positional prompt here).
    argv_repl, _ = ADAPTER.build_argv(
        "x",
        model="claude-opus-4-7",
        cwd=Path("/tmp"),
        live_repl=True,
        governor_settings=_GOV,
    )
    assert argv_repl.index("--settings") > argv_repl.index("--model")


def test_build_followup_argv_carries_settings_with_resume():
    """build_followup_argv + governor_settings → --settings present; --resume works."""
    argv, _ = ADAPTER.build_followup_argv(
        "follow up",
        prior_session_id="sess-123",
        model="claude-opus-4-7",
        cwd=Path("/tmp"),
        governor_settings=_GOV,
    )
    assert "--settings" in argv
    assert argv[argv.index("--settings") + 1] == str(_GOV)
    assert "--resume" in argv
    assert argv[argv.index("--resume") + 1] == "sess-123"
    assert argv[-1] == "follow up"  # prompt still last
    assert argv.index("--settings") > argv.index("--model")
    assert argv.index("--settings") < len(argv) - 1


def test_build_followup_argv_settings_positioned_after_model_before_resume():
    """--settings must sit after --model <id> and before --resume / the prompt.

    Mirrors the build_argv positioning guard so a malformed followup argv can't
    ship (the /followup respawn path is otherwise only covered at the helper
    level via governor_kwargs).
    """
    argv, _ = ADAPTER.build_followup_argv(
        "the-prompt",
        prior_session_id="sess-123",
        model="claude-opus-4-7",
        cwd=Path("/tmp"),
        governor_settings=_GOV,
    )
    i_model = argv.index("--model")
    i_settings = argv.index("--settings")
    i_resume = argv.index("--resume")
    assert i_settings == i_model + 2  # immediately after --model <id>
    assert i_settings < i_resume  # before --resume
    assert argv[-1] == "the-prompt"  # before the trailing prompt
    # No --resume case: --settings still after --model, before the prompt.
    argv2, _ = ADAPTER.build_followup_argv(
        "p2",
        prior_session_id=None,
        model="claude-opus-4-7",
        cwd=Path("/tmp"),
        governor_settings=_GOV,
    )
    assert argv2.index("--settings") == argv2.index("--model") + 2
    assert "--resume" not in argv2
    assert argv2[-1] == "p2"


def test_build_followup_argv_omits_settings_when_none():
    argv, _ = ADAPTER.build_followup_argv(
        "follow up",
        prior_session_id="sess-123",
        model="claude-opus-4-7",
        cwd=Path("/tmp"),
    )
    assert "--settings" not in argv
    assert argv == [
        "claude",
        "--print",
        "--model",
        "claude-opus-4-7",
        "--resume",
        "sess-123",
        "follow up",
    ]


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
