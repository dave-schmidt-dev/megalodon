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
    """live_repl=True returns a bare REPL argv: NO --print, NO --allowedTools,
    NO positional prompt.

    Task 3.3: the static --allowedTools allowlist was removed. The governor
    PreToolUse hook is the sole permission gate; the live_repl initial prompt is
    delivered via tmux send-keys, not a positional argv arg.
    """
    argv, env = ADAPTER.build_argv(
        "ignored-because-repl-takes-input-via-send-keys",
        model="claude-opus-4-7",
        cwd=Path("/tmp"),
        live_repl=True,
    )
    assert argv == ["claude", "--model", "claude-opus-4-7"]
    assert "--print" not in argv
    assert "--allowedTools" not in argv
    # The (ignored) prompt arg must NOT be present as a positional.
    assert "ignored-because-repl-takes-input-via-send-keys" not in argv
    assert env == {}


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


def test_build_argv_live_repl_carries_settings_no_allowlist():
    """live_repl + governor_settings → --settings present, --allowedTools ABSENT.

    Task 3.3: the governor is the sole gate; the allowlist was removed. The
    live_repl argv is exactly ``claude --model <id> --settings <path>``.
    """
    argv, _ = ADAPTER.build_argv(
        "x",
        model="claude-opus-4-7",
        cwd=Path("/tmp"),
        live_repl=True,
        governor_settings=_GOV,
    )
    assert argv == ["claude", "--model", "claude-opus-4-7", "--settings", str(_GOV)]
    assert "--settings" in argv
    assert argv[argv.index("--settings") + 1] == str(_GOV)
    assert "--allowedTools" not in argv


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
