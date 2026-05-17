"""Tests for CursorAdapter (P1.7 — experimental harness adapters)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.harnesses.cursor import CursorAdapter

ADAPTER = CursorAdapter()

# ---------------------------------------------------------------------------
# 1. name and default_model
# ---------------------------------------------------------------------------


def test_name_and_default_model():
    assert ADAPTER.name == "cursor"
    assert ADAPTER.default_model == "auto"


# ---------------------------------------------------------------------------
# 2. available_models includes all documented ids
# ---------------------------------------------------------------------------


def test_available_models_include_documented():
    documented = {
        "auto",
        "composer-2-fast",
        "composer-2",
        "gpt-5.5-high",
        "gpt-5.4-high",
        "gpt-5.3-codex-xhigh",
        "claude-opus-4-7-thinking-high",
        "claude-4.6-opus-high-thinking",
        "sonnet-4-thinking",
        "kimi-k2.5",
    }
    ids = {m.id for m in ADAPTER.available_models}
    assert documented <= ids, f"Missing: {documented - ids}"


# ---------------------------------------------------------------------------
# 3. build_argv — text default (full snapshot)
# ---------------------------------------------------------------------------


def test_build_argv_text_default():
    argv, env = ADAPTER.build_argv(
        "hello",
        model="auto",
        cwd=Path("/tmp"),
    )
    assert argv == [
        "cursor-agent",
        "-p",
        "--model", "auto",
        "--force",
        "--trust",
        "hello",
    ]
    assert env == {}


# ---------------------------------------------------------------------------
# 4. supports_autonomous_loop is False
# ---------------------------------------------------------------------------


def test_supports_autonomous_loop_false():
    assert ADAPTER.supports_autonomous_loop is False
    assert ADAPTER.supports().supports_autonomous_loop is False
