# Finding: BUG-PROMPT-FLICKER — Suppression Window Fix

**Agent:** agent-d510  
**Lane:** C (BACKEND)  
**Task:** BUG-PROMPT-FLICKER  
**UTC:** 2026-05-19T23-47-27Z  
**Severity:** INFO

---

## Summary

Implemented and verified the suppression window fix for the permission-prompt flicker bug. After `clear_lane()` is called (operator approval keystroke sent), re-detection is suppressed for 5 seconds to prevent the watcher from re-adding the same prompt while the Claude REPL TUI clears the permission block.

## Root Cause (confirmed)

`PermissionWatcher.clear_lane()` previously only cleared the `pending` state but did not prevent the next 1s scan cycle from re-detecting the same prompt text still present in the rendered TUI buffer. The watcher re-added the prompt, causing the ~1s flicker visible in the dashboard permission panel.

## Implementation

**File:** `megalodon_ui/permission_watcher.py`

Key changes:
- Added `SUPPRESSION_WINDOW_SECONDS: float = 5.0` class attribute
- Added `_suppressed_until: dict[str, float] = {}` per-lane suppression registry
- Added `_is_suppressed(lane)` helper using `time.monotonic()` (immune to NTP/DST jumps)
- `scan_lane()` returns `None` immediately when lane is within its suppression window
- `clear_lane()` sets `_suppressed_until[lane] = time.monotonic() + SUPPRESSION_WINDOW_SECONDS`

Suppression is lane-scoped: clearing lane C does not affect lane D's detection.

## Tests

**File:** `scripts/tests/test_permission_watcher.py` — 13 tests, all passing.

Key tests covering the fix:
- `test_clear_lane_suppresses_redetection_during_window` — core regression: stream still contains prompt text, scan returns None during window
- `test_clear_lane_suppression_does_not_affect_other_lanes` — isolation verified
- `test_suppression_window_expires_and_detection_resumes` — window expiry re-enables detection

## Evidence

```
scripts/tests/test_permission_watcher.py::test_clear_lane_suppresses_redetection_during_window PASSED
scripts/tests/test_permission_watcher.py::test_clear_lane_suppression_does_not_affect_other_lanes PASSED
scripts/tests/test_permission_watcher.py::test_suppression_window_expires_and_detection_resumes PASSED
13 passed in 0.21s
```

Full suite: 493 passed, 34 skipped, 3 xfailed, 7 pre-existing real-tmux socket-path failures (macOS `MAXPATHLEN` limit — unrelated to this change).

## Next Step

Server needs a restart to load the new `PermissionWatcher` — the existing server process imports the module at startup. Operator should restart the megalodon_ui server after this iteration so the fix takes effect live.
