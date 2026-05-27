"""Tests for CodexAdapter (P1.6 — harness adapter contract)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.harnesses.codex import CodexAdapter

ADAPTER = CodexAdapter()

# ---------------------------------------------------------------------------
# 1. name and default_model
# ---------------------------------------------------------------------------


def test_name_and_default_model():
    assert ADAPTER.name == "codex"
    assert ADAPTER.default_model == "gpt-5.5"


# ---------------------------------------------------------------------------
# 2. available_models includes all documented ids
# ---------------------------------------------------------------------------


def test_available_models_include_documented():
    documented = {
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.3-codex",
        "gpt-5.3-codex-spark",
        "gpt-5.2",
    }
    ids = {m.id for m in ADAPTER.available_models}
    assert documented <= ids, f"Missing: {documented - ids}"


# ---------------------------------------------------------------------------
# 3. build_argv — text default
# ---------------------------------------------------------------------------


def test_build_argv_text_default():
    argv, env = ADAPTER.build_argv(
        "hello",
        model="gpt-5.5",
        cwd=Path("/tmp"),
    )
    assert argv == [
        "codex",
        "exec",
        "-m",
        "gpt-5.5",
        "-s",
        "read-only",
        "--skip-git-repo-check",
        "hello",
    ]
    assert env == {}


# ---------------------------------------------------------------------------
# 4. build_argv — stream-json falls back to text shape (no crash, no extra flag)
# ---------------------------------------------------------------------------


def test_build_argv_stream_json_when_supported():
    # Codex does not support stream-json in v9.1; must fall back silently.
    argv, env = ADAPTER.build_argv(
        "hello",
        model="gpt-5.5",
        cwd=Path("/tmp"),
        output_format="stream-json",
    )
    # Should be identical to the text shape — no crash, no --json flag added
    assert argv == [
        "codex",
        "exec",
        "-m",
        "gpt-5.5",
        "-s",
        "read-only",
        "--skip-git-repo-check",
        "hello",
    ]
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
    assert ADAPTER.auth_env_keys() == ["CODEX_API_KEY"]


# ---------------------------------------------------------------------------
# Smoke: --help (skipped if binary absent)
# ---------------------------------------------------------------------------


@pytest.mark.smoke_harness
def test_help_smoke():
    if shutil.which("codex") is None:
        pytest.skip("codex CLI not found")
    result = subprocess.run(["codex", "--help"], capture_output=True, timeout=10)
    assert result.returncode == 0
