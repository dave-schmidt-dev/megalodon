"""Tests for HarnessAdapter.session_log_dir() — Task 3.2 (CR-1).

Plan §6.5: a new Protocol method returning the *parent* directory where a
harness mints its session log entries on disk (one file or one sub-dir per
session). Used by FleetSpawner's before/after snapshot diff (Task 3.3) to
discover the session id of a freshly-spawned harness without racing other
concurrent spawns.

Resume-capable adapters return a Path; harnesses with no on-disk session
manifest return None and degrade gracefully (no resume).
"""

from __future__ import annotations

import pathlib

from megalodon_ui.harnesses.claude import ClaudeAdapter
from megalodon_ui.harnesses.codex import CodexAdapter
from megalodon_ui.harnesses.copilot import CopilotAdapter
from megalodon_ui.harnesses.cursor import CursorAdapter
from megalodon_ui.harnesses.gemini import GeminiAdapter
from megalodon_ui.harnesses.vibe import VibeAdapter


def test_claude_session_log_dir_is_sanitised_cwd_under_projects():
    cwd = pathlib.Path("/tmp/megalodon-fix-medium")
    d = ClaudeAdapter().session_log_dir(cwd)
    assert (
        d == pathlib.Path.home() / ".claude" / "projects" / "tmp-megalodon-fix-medium"
    )


def test_claude_session_log_dir_handles_root_cwd():
    cwd = pathlib.Path("/")
    d = ClaudeAdapter().session_log_dir(cwd)
    assert d == pathlib.Path.home() / ".claude" / "projects" / "root"


def test_codex_session_log_dir_is_sessions_root():
    d = CodexAdapter().session_log_dir(pathlib.Path("/anywhere"))
    assert d == pathlib.Path.home() / ".codex" / "sessions"


def test_gemini_session_log_dir_uses_cwd_basename():
    d = GeminiAdapter().session_log_dir(pathlib.Path("/tmp/mission-X"))
    assert d == pathlib.Path.home() / ".gemini" / "history" / "mission-X"


def test_copilot_session_log_dir_is_none():
    assert CopilotAdapter().session_log_dir(pathlib.Path("/anywhere")) is None


def test_cursor_session_log_dir_is_none():
    assert CursorAdapter().session_log_dir(pathlib.Path("/anywhere")) is None


def test_vibe_session_log_dir_is_none():
    assert VibeAdapter().session_log_dir(pathlib.Path("/anywhere")) is None


def test_all_six_adapters_implement_method():
    """Protocol-compliance: every adapter answers without AttributeError."""
    for ad in (
        ClaudeAdapter(),
        CodexAdapter(),
        GeminiAdapter(),
        CopilotAdapter(),
        CursorAdapter(),
        VibeAdapter(),
    ):
        # Must not raise; result is either Path or None.
        result = ad.session_log_dir(pathlib.Path("/tmp/x"))
        assert result is None or isinstance(result, pathlib.Path)
