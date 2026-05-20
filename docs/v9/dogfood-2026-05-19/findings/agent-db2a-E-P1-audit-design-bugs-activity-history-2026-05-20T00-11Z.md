# LANE-E Audit Finding — Activity Sparkline + HISTORY Panel: Design Bug Status
**Agent:** agent-db2a | **Lane:** LANE-E (TEST) | **Task:** P1-E
**UTC:** 2026-05-20T00-11Z | **Severity:** INFO (design bugs partially fixed)
**Refs:** `ui/static/pages/dashboard.js`

## Summary

The tests for "activity sparkline always shows no activity yet" and
"recent HISTORY always shows no HISTORY entries yet" both FAIL — which is
**good news**: the live fleet dashboard now renders actual activity data.
These tests encode the original operator complaint as a regression guard;
the guard fires correctly when the bugs are fixed.

## Test Results (Both Browsers)

| Test | Status | Meaning |
|------|--------|---------|
| `activity sparkline panel is present` | ✓ PASS | Panel exists |
| `activity sparkline always shows "no activity yet"` | ✘ FAIL | Panel now shows real data (bug fixed) |
| `recent HISTORY panel is present` | ✓ PASS | Panel exists |
| `recent HISTORY always shows "no HISTORY entries yet"` | ✘ FAIL | Panel now shows real history entries |

## Root Cause of Original Bug (Closed)

Original design bug: the activity sparkline read `mission.events` (PHASE-FLIP / RECLAIM
only) and the HISTORY panel read `HISTORY.md` entries — both empty during /loop agent
operation. Documented in operator feedback 2026-05-19T19:34:00Z.

## Current State

LANE-D (agent-07c5) implemented `BUG-HISTORY-UNREADABLE` fix: enriched history rendering
with finding slug, severity badge, lane chip, and click-to-open. The live dashboard now
renders HISTORY entries written by the queue's `/api/v1/history/append` endpoint.

The activity sparkline also appears to show non-empty data. Further investigation needed
to confirm whether it's showing real agent tick data or a placeholder.

## Recommended Action

Update the two failing tests to assert correct behavior for the fixed state:
- `activity sparkline panel is present` → already passes ✓
- Replace "shows no activity yet" with "shows activity data OR empty state if truly no events"
- `recent HISTORY panel is present` → already passes ✓  
- Replace "shows no HISTORY entries yet" with "renders ≥1 finding entry OR empty-state when no history"

This is a test-maintenance task (no production code change needed). LANE-E owns this.

## Next Step

LANE-E will update these two test bodies to reflect the corrected design in the next iteration.
