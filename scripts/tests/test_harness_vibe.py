"""Tests for VibeAdapter (P1.7 — experimental harness adapters)."""

from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.harnesses.vibe import VibeAdapter

ADAPTER = VibeAdapter()

# ---------------------------------------------------------------------------
# 1. name and default_model
# ---------------------------------------------------------------------------


def test_name_and_default_model():
    assert ADAPTER.name == "vibe"
    assert ADAPTER.default_model == "mistral-medium-3.5"


# ---------------------------------------------------------------------------
# 2. available_models includes all documented ids
# ---------------------------------------------------------------------------


def test_available_models_include_documented():
    documented = {
        "mistral-medium-3.5",
        "mistral-large-2",
        "codestral-25.08",
        "devstral-2-large",
        "devstral-2-small",
    }
    ids = {m.id for m in ADAPTER.available_models}
    assert documented <= ids, f"Missing: {documented - ids}"


# ---------------------------------------------------------------------------
# 3. build_argv — text default (full snapshot); assert no --model flag
# ---------------------------------------------------------------------------


def test_build_argv_text_default():
    argv, env = ADAPTER.build_argv(
        "hello",
        model="mistral-medium-3.5",
        cwd=Path("/tmp"),
    )
    assert argv == [
        "vibe",
        "--prompt",
        "hello",
        "--agent",
        "auto-approve",
        "--output",
        "json",
    ]
    assert env == {}
    # vibe ignores the model arg — no --model flag must appear in argv
    assert "--model" not in argv


# ---------------------------------------------------------------------------
# 4. supports_autonomous_loop is False
# ---------------------------------------------------------------------------


def test_supports_autonomous_loop_false():
    assert ADAPTER.supports_autonomous_loop is False
    assert ADAPTER.supports().supports_autonomous_loop is False
