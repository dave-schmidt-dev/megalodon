"""Tests for CopilotAdapter (P1.7 — experimental harness adapters)."""

from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.harnesses.copilot import CopilotAdapter

ADAPTER = CopilotAdapter()

# ---------------------------------------------------------------------------
# 1. name and default_model
# ---------------------------------------------------------------------------


def test_name_and_default_model():
    assert ADAPTER.name == "copilot"
    assert ADAPTER.default_model == "claude-sonnet-4.6"


# ---------------------------------------------------------------------------
# 2. available_models includes all documented ids
# ---------------------------------------------------------------------------


def test_available_models_include_documented():
    documented = {
        "claude-sonnet-4.6",
        "claude-opus-4.7",
        "gpt-5.2",
        "gpt-5.4",
    }
    ids = {m.id for m in ADAPTER.available_models}
    assert documented <= ids, f"Missing: {documented - ids}"


# ---------------------------------------------------------------------------
# 3. build_argv — text default (full snapshot)
# ---------------------------------------------------------------------------


def test_build_argv_text_default():
    argv, env = ADAPTER.build_argv(
        "hello",
        model="claude-sonnet-4.6",
        cwd=Path("/tmp"),
    )
    assert argv == [
        "copilot",
        "-p",
        "hello",
        "--model",
        "claude-sonnet-4.6",
        "--allow-all-tools",
        "--no-ask-user",
    ]
    assert env == {}


# ---------------------------------------------------------------------------
# 4. supports_autonomous_loop is False
# ---------------------------------------------------------------------------


def test_supports_autonomous_loop_false():
    assert ADAPTER.supports_autonomous_loop is False
    assert ADAPTER.supports().supports_autonomous_loop is False
