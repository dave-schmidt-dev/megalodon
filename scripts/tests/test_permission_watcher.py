"""Unit tests for v9.3 permission_watcher.

Uses synthetic Claude-REPL fragments to verify detection + extraction.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.permission_watcher import (
    PROMPT_MARKER,
    PermissionWatcher,
    _extract_command_preview,
    _strip_ansi,
)


SAMPLE_PROMPT_BLOCK = (
    "\x1b[?2026$p\x1b]0;Claude Code\x07"
    "\x1b[2J\x1b[H"  # CSI clear-screen + home
    "Bash command\n"
    "mkdir -p /Users/dave/Documents/Projects/megalodon-fleet/claims/P1-F && "
    "echo \"agent-5407\" > /Users/dave/Documents/Projects/megalodon-fleet/claims/P1-F/owner.txt\n"
    "Claim task P1-F atomically for this agent\n"
    "\x1b[1mDo you want to proceed?\x1b[0m\n"
    "\xe2\x9d\xaf 1. Yes\n"
    "  2. Yes, and always allow access\n"
    "  3. No\n"
    "Esc to cancel \xc2\xb7 Tab to amend \xc2\xb7 ctrl+e to explain\n"
)


def test_strip_ansi_removes_csi_osc_short():
    raw = "\x1b[2J\x1b]0;title\x07\x1b=hello\x1b[0m"
    out = _strip_ansi(raw)
    assert out == "hello"


def test_extract_command_preview_finds_latest_tool_header():
    stripped = _strip_ansi(SAMPLE_PROMPT_BLOCK)
    idx = stripped.rfind(PROMPT_MARKER)
    assert idx > 0
    preview = _extract_command_preview(stripped, idx)
    assert "Bash command" in preview
    assert "mkdir" in preview
    assert "claims/P1-F" in preview


def test_extract_command_preview_no_header_falls_back():
    """If no recognized tool-header precedes the prompt, surface ~last 260 chars.

    Previously this returned <unknown command>, which left operators blind when
    an agent invoked a tool we hadn't enumerated (e.g. Claude Code's Monitor).
    The fallback now shows the trailing context so the operator can read what's
    being approved.
    """
    fake = "some random tail with no header here\nDo you want to proceed?"
    idx = fake.rfind(PROMPT_MARKER)
    preview = _extract_command_preview(fake, idx)
    # Should be the trimmed tail with the unknown-tool prefix, not the old placeholder.
    assert preview.startswith("[unknown tool]")
    assert "some random tail" in preview


def test_watcher_detects_prompt_in_file(tmp_path):
    fleet_dir = tmp_path / ".fleet"
    fleet_dir.mkdir()
    log = fleet_dir / "A.stream.log"
    log.write_bytes(SAMPLE_PROMPT_BLOCK.encode("utf-8"))

    watcher = PermissionWatcher(tmp_path, [("A", "AUDIT")])
    watcher._scan_once()
    pending = watcher.pending()
    assert len(pending) == 1
    assert pending[0].lane_short == "A"
    assert pending[0].lane_name == "AUDIT"
    assert "mkdir" in pending[0].command_preview
    assert pending[0].fingerprint  # non-empty


def test_watcher_clears_when_prompt_disappears(tmp_path):
    """If the prompt marker is no longer in the tail, pending clears."""
    fleet_dir = tmp_path / ".fleet"
    fleet_dir.mkdir()
    log = fleet_dir / "A.stream.log"
    log.write_bytes(SAMPLE_PROMPT_BLOCK.encode("utf-8"))

    watcher = PermissionWatcher(tmp_path, [("A", "AUDIT")])
    watcher._scan_once()
    assert len(watcher.pending()) == 1

    # Simulate the agent's TUI clearing the prompt — overwrite with content
    # that has no marker.
    log.write_bytes(b"agent moved on, no prompt active here\n" * 50)
    watcher._scan_once()
    assert watcher.pending() == []


def test_watcher_fingerprint_stable_across_scans(tmp_path):
    """Two scans with the same prompt content produce the same fingerprint."""
    fleet_dir = tmp_path / ".fleet"
    fleet_dir.mkdir()
    log = fleet_dir / "A.stream.log"
    log.write_bytes(SAMPLE_PROMPT_BLOCK.encode("utf-8"))

    watcher = PermissionWatcher(tmp_path, [("A", "AUDIT")])
    watcher._scan_once()
    fp1 = watcher.pending()[0].fingerprint
    watcher._scan_once()
    fp2 = watcher.pending()[0].fingerprint
    assert fp1 == fp2


def test_watcher_clear_lane_explicit(tmp_path):
    fleet_dir = tmp_path / ".fleet"
    fleet_dir.mkdir()
    log = fleet_dir / "A.stream.log"
    log.write_bytes(SAMPLE_PROMPT_BLOCK.encode("utf-8"))

    watcher = PermissionWatcher(tmp_path, [("A", "AUDIT")])
    watcher._scan_once()
    assert watcher.pending_for_lane("A") is not None
    watcher.clear_lane("A")
    assert watcher.pending_for_lane("A") is None


def test_watcher_detects_no_space_marker(tmp_path):
    """Claude TUI renders the marker fragmented; ANSI strip removes spaces.

    The agent's pipe-pane stream often shows "Doyouwanttoproceed?" (no
    spaces) once CSI cursor-positioning escapes are removed. The watcher's
    regex matcher must tolerate this. Regression for the smoke-test bug
    where exact-text matching silently missed every real prompt.
    """
    fleet_dir = tmp_path / ".fleet"
    fleet_dir.mkdir()
    log = fleet_dir / "F.stream.log"
    log.write_bytes(
        b"Bash command\npython3 -c \"import secrets\"\n"
        b"This command requires approval\n"
        b"Doyouwanttoproceed?\n"
        b"1. Yes\n2. Yes, and don't ask again\n3. No\n"
    )
    watcher = PermissionWatcher(tmp_path, [("F", "META")])
    watcher._scan_once()
    pending = watcher.pending()
    assert len(pending) == 1
    assert "python3" in pending[0].command_preview


def test_watcher_missing_log_is_silent(tmp_path):
    """No .fleet dir / missing log file → no crash, no pending."""
    watcher = PermissionWatcher(tmp_path, [("A", "AUDIT")])
    watcher._scan_once()
    assert watcher.pending() == []


def test_watcher_suppresses_reflash_after_clear(tmp_path):
    """v9.3.2 — re-flash bug: after clear_lane(), re-scanning the same tail
    must NOT re-populate the same fingerprint.

    Bug repro: operator clicks Approve → clear_lane() resets _pending → next
    1s scan re-reads the same tail (REPL hasn't redrawn yet) → re-matches
    the prompt → prompt re-flashes in dashboard for ~1s. Fix: suppress
    same-fingerprint re-detection per lane for CLEAR_SUPPRESSION_SECONDS.
    """
    import asyncio

    fleet_dir = tmp_path / ".fleet"
    fleet_dir.mkdir()
    log = fleet_dir / "A.stream.log"
    log.write_bytes(SAMPLE_PROMPT_BLOCK.encode("utf-8"))

    import time as _time

    watcher = PermissionWatcher(tmp_path, [("A", "AUDIT")])
    # Tighten suppression window so the test stays fast but still proves
    # the mechanism. 0.3s is more than enough; the assertion runs in < 50ms.
    watcher.CLEAR_SUPPRESSION_SECONDS = 0.3
    watcher._scan_once()
    assert watcher.pending_for_lane("A") is not None
    watcher.clear_lane("A")
    assert watcher.pending_for_lane("A") is None
    # The repro: scan again immediately. Without suppression, this would
    # re-populate _pending. With suppression, stays None.
    watcher._scan_once()
    assert watcher.pending_for_lane("A") is None, (
        "prompt re-flashed after clear — suppression window not honored"
    )
    # After the window expires AND the prompt is still in the tail, a
    # NEW pending should appear (suppression is time-bounded).
    _time.sleep(0.4)
    watcher._scan_once()
    assert watcher.pending_for_lane("A") is not None, (
        "suppression window did not expire — prompts would be lost forever"
    )


def test_watcher_new_prompt_with_different_fingerprint_surfaces_immediately(tmp_path):
    """Suppression must NOT block a NEW prompt with different content.

    If the agent issues a different command immediately after the first
    is approved, that's a brand-new prompt and must surface right away.
    """
    fleet_dir = tmp_path / ".fleet"
    fleet_dir.mkdir()
    log = fleet_dir / "A.stream.log"
    log.write_bytes(SAMPLE_PROMPT_BLOCK.encode("utf-8"))

    watcher = PermissionWatcher(tmp_path, [("A", "AUDIT")])
    watcher.CLEAR_SUPPRESSION_SECONDS = 5.0
    watcher._scan_once()
    first = watcher.pending_for_lane("A")
    assert first is not None
    watcher.clear_lane("A")

    # Simulate the REPL writing a DIFFERENT prompt (different command).
    log.write_bytes(
        b"Bash command\n"
        b"uv run pytest --verbose ui/tests/integration/test_completely_different.py\n"
        b"This command requires approval\n"
        b"Do you want to proceed?\n"
        b"1. Yes\n2. Yes, and don't ask again\n3. No\n"
    )
    watcher._scan_once()
    second = watcher.pending_for_lane("A")
    assert second is not None, "new prompt was suppressed — suppression too greedy"
    assert second.fingerprint != first.fingerprint, (
        "fingerprint did not change despite different command"
    )
