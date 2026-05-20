"""v9.4 T2.1 — unit tests for PermissionWatcher.on_change callback.

Verifies:
- Callback fires on pending-add with (lane, PromptInfo, None).
- Callback fires on clear with (lane, None, None).
- Callback fires on approve / approve_remember / deny with correct action string.
- Backward-compat: clear_lane("A") without action does not crash; callback fires
  with action=None.
- Callback isolation: a raising callback does NOT crash the watcher; subsequent
  events still fire the callback.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.permission_watcher import PermissionWatcher, PromptInfo

# ---------------------------------------------------------------------------
# Shared sample data (reuse the ANSI-rich block from the v9.3 tests)
# ---------------------------------------------------------------------------

SAMPLE_PROMPT_BLOCK = (
    "\x1b[?2026$p\x1b]0;Claude Code\x07"
    "\x1b[2J\x1b[H"
    "Bash command\n"
    "mkdir -p /tmp/test-task && echo agent-42 > /tmp/test-task/owner.txt\n"
    "Claim task atomically\n"
    "\x1b[1mDo you want to proceed?\x1b[0m\n"
    "\xe2\x9d\xaf 1. Yes\n"
    "  2. Yes, and always allow access\n"
    "  3. No\n"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_watcher(tmp_path: Path, callback=None) -> PermissionWatcher:
    """Create a PermissionWatcher with lane A and optionally a callback."""
    fleet_dir = tmp_path / ".fleet"
    fleet_dir.mkdir(exist_ok=True)
    return PermissionWatcher(tmp_path, [("A", "AUDIT")], on_change=callback)


def _write_prompt(tmp_path: Path, lane: str = "A") -> None:
    fleet_dir = tmp_path / ".fleet"
    fleet_dir.mkdir(exist_ok=True)
    log = fleet_dir / f"{lane}.stream.log"
    log.write_bytes(SAMPLE_PROMPT_BLOCK.encode("utf-8"))


def _clear_log(tmp_path: Path, lane: str = "A") -> None:
    log = tmp_path / ".fleet" / f"{lane}.stream.log"
    log.write_bytes(b"agent moved on, no prompt here\n" * 20)


# ---------------------------------------------------------------------------
# Tests: callback fires on pending-add
# ---------------------------------------------------------------------------


def test_callback_fires_on_pending_add(tmp_path):
    """Simulating a new pending prompt: callback called once with (lane, PromptInfo, None)."""
    calls: list[tuple] = []

    def cb(lane, info, action):
        calls.append((lane, info, action))

    _write_prompt(tmp_path)
    watcher = _make_watcher(tmp_path, callback=cb)
    watcher._scan_once()

    assert len(calls) == 1
    lane, info, action = calls[0]
    assert lane == "A"
    assert isinstance(info, PromptInfo)
    assert info.lane_short == "A"
    assert "mkdir" in info.command_preview
    assert action is None


def test_callback_fires_only_once_for_stable_prompt(tmp_path):
    """If the same prompt fingerprint persists across scans, callback fires only once."""
    calls: list[tuple] = []

    def cb(lane, info, action):
        calls.append((lane, info, action))

    _write_prompt(tmp_path)
    watcher = _make_watcher(tmp_path, callback=cb)
    watcher._scan_once()
    watcher._scan_once()  # same content — no new transition

    # Callback fires exactly once (on first detection, not on repeated scans)
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Tests: callback fires on clear
# ---------------------------------------------------------------------------


def test_callback_fires_on_clear_no_action(tmp_path):
    """clear_lane('A') without action fires callback with (lane, None, None)."""
    calls: list[tuple] = []

    def cb(lane, info, action):
        calls.append((lane, info, action))

    _write_prompt(tmp_path)
    watcher = _make_watcher(tmp_path, callback=cb)
    watcher._scan_once()
    calls.clear()  # discard the add event

    watcher.clear_lane("A")

    assert len(calls) == 1
    lane, info, action = calls[0]
    assert lane == "A"
    assert info is None
    assert action is None


# ---------------------------------------------------------------------------
# Tests: callback fires with correct action strings
# ---------------------------------------------------------------------------


def _action_test(tmp_path: Path, action_str: str) -> None:
    calls: list[tuple] = []

    def cb(lane, info, action):
        calls.append((lane, info, action))

    _write_prompt(tmp_path)
    watcher = _make_watcher(tmp_path, callback=cb)
    watcher._scan_once()
    calls.clear()

    watcher.clear_lane("A", action=action_str)

    assert len(calls) == 1
    lane, info, action = calls[0]
    assert lane == "A"
    assert info is None
    assert action == action_str


def test_callback_fires_with_approve(tmp_path):
    _action_test(tmp_path, "approve")


def test_callback_fires_with_approve_remember(tmp_path):
    _action_test(tmp_path, "approve_remember")


def test_callback_fires_with_deny(tmp_path):
    _action_test(tmp_path, "deny")


# ---------------------------------------------------------------------------
# Tests: backward-compat — omitting action does not crash
# ---------------------------------------------------------------------------


def test_backward_compat_clear_without_action(tmp_path):
    """Existing callers using clear_lane('A') with no action must not crash."""
    calls: list[tuple] = []

    def cb(lane, info, action):
        calls.append((lane, info, action))

    _write_prompt(tmp_path)
    watcher = _make_watcher(tmp_path, callback=cb)
    watcher._scan_once()
    calls.clear()

    # Old-style call — no action keyword argument
    watcher.clear_lane("A")

    # Must not raise, callback fires with action=None
    assert len(calls) == 1
    assert calls[0] == ("A", None, None)
    # Watcher state is cleared
    assert watcher.pending_for_lane("A") is None


def test_backward_compat_no_callback_at_all(tmp_path):
    """PermissionWatcher without on_change works exactly as before — no crash."""
    _write_prompt(tmp_path)
    watcher = PermissionWatcher(tmp_path, [("A", "AUDIT")])  # no on_change
    watcher._scan_once()
    assert watcher.pending_for_lane("A") is not None
    watcher.clear_lane("A")  # must not raise
    assert watcher.pending_for_lane("A") is None


# ---------------------------------------------------------------------------
# Tests: callback isolation
# ---------------------------------------------------------------------------


def test_raising_callback_does_not_crash_watcher(tmp_path):
    """A callback that raises must not prevent the watcher from continuing."""
    raise_count = 0
    call_count = 0

    def bad_cb(lane, info, action):
        nonlocal raise_count, call_count
        call_count += 1
        raise_count += 1
        raise RuntimeError("intentional error from callback")

    _write_prompt(tmp_path)
    watcher = _make_watcher(tmp_path, callback=bad_cb)

    # First scan: callback raises — watcher must not propagate the error
    watcher._scan_once()
    assert raise_count == 1

    # Watcher should still be functional (pending state set correctly)
    assert watcher.pending_for_lane("A") is not None

    # clear_lane also uses _fire_change — must also survive a raising callback
    watcher.clear_lane("A")
    assert raise_count == 2
    assert watcher.pending_for_lane("A") is None


def test_raising_callback_subsequent_events_still_fire(tmp_path):
    """After a callback raises on event N, event N+1 still fires the callback."""
    events: list[tuple] = []
    call_count = [0]

    def flaky_cb(lane, info, action):
        call_count[0] += 1
        events.append((lane, info, action))
        if call_count[0] == 1:
            raise ValueError("first call always fails")

    _write_prompt(tmp_path)
    watcher = _make_watcher(tmp_path, callback=flaky_cb)

    # Event 1: pending-add — callback raises but watcher continues
    watcher._scan_once()
    assert call_count[0] == 1

    # Event 2: clear — callback must still fire despite prior failure
    watcher.clear_lane("A", action="deny")
    assert call_count[0] == 2

    # Second event was the clear
    assert events[1] == ("A", None, "deny")
