"""Tests for GeminiAdapter (P1.6 — harness adapter contract)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.harnesses.gemini import GeminiAdapter
from megalodon_ui.harnesses.base import Event

ADAPTER = GeminiAdapter()

# ---------------------------------------------------------------------------
# 1. name and default_model
# ---------------------------------------------------------------------------


def test_name_and_default_model():
    assert ADAPTER.name == "gemini"
    assert ADAPTER.default_model == "gemini-3.1-pro-preview"


# ---------------------------------------------------------------------------
# 2. available_models includes all documented ids
# ---------------------------------------------------------------------------


def test_available_models_include_documented():
    documented = {
        "gemini-3.1-pro-preview",
        "gemini-3-flash-preview",
        "gemini-3.1-flash-lite-preview",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemma-4-31b-it",
        "gemma-4-26b-a4b-it",
    }
    ids = {m.id for m in ADAPTER.available_models}
    assert documented <= ids, f"Missing: {documented - ids}"


# ---------------------------------------------------------------------------
# 3. build_argv — text default
# ---------------------------------------------------------------------------


def test_build_argv_text_default():
    argv, env = ADAPTER.build_argv(
        "hello",
        model="gemini-3.1-pro-preview",
        cwd=Path("/tmp"),
    )
    assert argv == [
        "gemini", "-p", "hello",
        "-m", "gemini-3.1-pro-preview",
        "--approval-mode", "plan",
    ]
    assert env == {}


# ---------------------------------------------------------------------------
# 4. build_argv — stream-json falls back to text shape (no crash, no extra flag)
# ---------------------------------------------------------------------------


def test_build_argv_stream_json_when_supported():
    # Gemini does not support stream-json; must produce the same text-shape argv.
    argv, env = ADAPTER.build_argv(
        "hello",
        model="gemini-3.1-pro-preview",
        cwd=Path("/tmp"),
        output_format="stream-json",
    )
    assert argv == [
        "gemini", "-p", "hello",
        "-m", "gemini-3.1-pro-preview",
        "--approval-mode", "plan",
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
    assert ADAPTER.auth_env_keys() == ["GEMINI_API_KEY"]


# ---------------------------------------------------------------------------
# Smoke: --help (skipped in CI or if binary absent)
# ---------------------------------------------------------------------------


@pytest.mark.smoke_harness
def test_help_smoke():
    if shutil.which("gemini") is None:
        pytest.skip("gemini CLI not found")
    if os.environ.get("CI"):
        pytest.skip("smoke tests disabled in CI")
    result = subprocess.run(
        ["gemini", "--help"], capture_output=True, timeout=10
    )
    assert result.returncode == 0
