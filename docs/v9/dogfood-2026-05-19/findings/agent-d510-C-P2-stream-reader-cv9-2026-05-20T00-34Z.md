# P2-C Finding: CV-9 Server-Owned Stream Reader

**Lane**: C | **Agent**: agent-d510 | **Task**: P2-C | **UTC**: 2026-05-20T00-34Z

## Summary

Implemented `LaneStreamReader` (CV-9) — server-owned async tail of per-lane
`.fleet/<LANE>.stream.log` files. Wired `pipe_pane` into `FleetSpawner._spawn_one()`
to produce those log files. Added 9 new unit tests; all pass. Zero regressions.

## Changes

### New: `megalodon_ui/stream_reader.py`

`LaneStreamReader` tails a stream log, parses lines through the lane's
`HarnessAdapter`, and delivers `Event` objects to a caller-supplied async
callback. Design constants:

- `EOF_POLL_S = 0.1` — 100ms poll at EOF (sub-second latency)
- `FILE_EXIST_POLL_S = 0.2` — 200ms existence poll
- `FILE_EXIST_TIMEOUT_S = 5.0` — 5s timeout before degraded exit

Key implementation details:

- `asyncio.to_thread(fh.readline)` for all blocking I/O — event loop stays live
- File rotation: `fh.tell() > log_path.stat().st_size` → re-seek to 0
- Best-effort: file never appears → WARNING logged, reader exits cleanly
- All exceptions from `parse_stream_line` and `on_event` caught and logged

### Modified: `megalodon_ui/spawn.py` — `_spawn_one()`

Replaced `TODO(P3.1)` comment with actual `tmux.pipe_pane()` call.

Critical ordering fix (OW-3 safety): `session.running = True` and
`spawned.append(session)` are placed **before** `await tmux.pipe_pane(...)`.
This ensures that if `pipe_pane` is cancelled mid-flight (e.g., another lane
raises `SpawnError` and `asyncio.gather` cancels peers), the session is already
in `spawned` and will be cleaned up by the OW-3 handler.

### New: `scripts/tests/test_stream_reader_unit.py`

6 unit tests using real tmpdir files (no mocking):

1. `test_run_delivers_parsed_events` — events delivered via callback
2. `test_run_skips_none_parse_results` — None returns silently skipped
3. `test_run_waits_for_file_existence` — waits then processes file
4. `test_run_handles_file_rotation` — re-seeks on shrinkage
5. `test_run_exits_on_stop_event` — exits promptly when signaled
6. `test_run_returns_gracefully_when_file_never_appears` — timeout + graceful exit

### Modified: `scripts/tests/test_spawn_unit.py`

- Added `pipe_pane` mock to `test_start_all_calls_new_session_once_per_lane`
- New: `test_start_all_calls_pipe_pane_once_per_lane` (CV-9 wire-up)
- New: `test_pipe_pane_failure_is_non_fatal` (rc=1 → degraded, not fatal)

## Test Results

Full suite: **511 passed, 8 failed** (7 pre-existing tmux socket failures +
1 confirmed-flaky queue applier test). Zero regressions from P2-C changes.

```
scripts/tests/test_stream_reader_unit.py  6 passed  3.45s
scripts/tests/test_spawn_unit.py          6 passed  (including 3 new)
ui/tests/integration/                    11 passed
```

## Deferred (out of scope per P1-C plan)

SSE integration: wiring `LaneStreamReader` into the SSE fan-out endpoint and
fixing `test_sse_stream_emits_status_change_on_file_touch` (xfail) — deferred
to P3 per explicit P1-C plan constraint.
