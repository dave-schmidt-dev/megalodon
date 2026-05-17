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
from megalodon_ui.harnesses.base import Event

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
    result = subprocess.run(
        ["claude", "--help"], capture_output=True, timeout=10
    )
    assert result.returncode == 0
