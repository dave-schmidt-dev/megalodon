# LANE-C BACKEND Plan — CV-9 Server-Owned Stream Reader
**Agent:** agent-d510  
**Phase:** P1 (PLAN)  
**Task:** P1-C  
**UTC:** 2026-05-19T20-10-00Z

---

## Feedback Acknowledgment

Acknowledging orchestrator message at `2026-05-19T19:55:49Z`: the operator reported a prior LANE-C agent held a claim with no finding written. That agent's session has ended; this is a fresh start. This document is the checkpoint finding. Claim P1-C was taken at iteration start with no predecessor claim in `claims/`.

---

## Summary

This document plans the v9.3 implementation of **CV-9: server-owned stream reader** — the component that reads lane PTY output from `.fleet/<lane>.stream.log` files and makes parsed events available for SSE emission. The deferral is tracked in HISTORY.md (line 552) and in MISSION.md. Build work is task P2-C.

---

## What CV-9 Is (and Is Not)

CV-9 is **not** a visual PTY stream to xterm.js. The v9.2 contrarian review (`docs/superpowers/specs/2026-05-17-megalodon-v9-2-brief.md`) condemned that design: `capture-pane` is snapshot-based, not a byte stream, and is incompatible with `xterm.js term.write()`.

CV-9 is a **structured event reader**: tmux `pipe-pane` records raw PTY bytes to a log file, and a server-side async reader tails that file, calls `adapter.parse_stream_line()` line-by-line, and delivers typed `Event` objects to registered SSE subscribers. The output is machine-readable (not visual), enabling the dashboard to show "last meaningful text" and "last activity timestamp" per lane.

---

## Current State (as-is)

| Component | File | Status |
|---|---|---|
| `tmux.pipe_pane()` | `megalodon_ui/tmux.py:71-83` | Implemented, **not wired** |
| `_spawn_one` TODO | `megalodon_ui/spawn.py:210-212` | Explicit TODO; `session.stream_log` path reserved |
| `parse_stream_line()` | `megalodon_ui/harnesses/base.py:111-117` | Protocol defined, **not called** |
| `LaneSession.stream_log` | `megalodon_ui/spawn.py:34` | Path allocated in dataclass |
| `LaneSession.subscribers_lock` | `megalodon_ui/spawn.py:43` | Forward-hook for SR-3 fan-out; not yet acquired |
| SSE xfail test | `ui/tests/integration/test_sse_stream.py:63-106` | Explicitly blocked on this work |
| `stream_reader.py` | — | **Does not exist** |

---

## Implementation Plan

### Step 1 — New file: `megalodon_ui/stream_reader.py`

Implement `LaneStreamReader`, an async tail-follower:

```python
class LaneStreamReader:
    def __init__(
        self,
        stream_log: Path,
        adapter: HarnessAdapter,
        lane: str,
    ) -> None: ...

    async def run(
        self,
        on_event: Callable[[Event], Awaitable[None]],
        *,
        stop_event: asyncio.Event,
    ) -> None:
        """Tail stream_log, parse lines via adapter, deliver Events."""
```

**Behavior:**
1. Wait for `stream_log` to appear (poll at 200ms, timeout 5s). On timeout: log `WARNING` in degraded-mode and return — stream reading is best-effort, not fatal.
2. Open the file and read line by line using `asyncio`-safe I/O (via `asyncio.to_thread(f.readline)`).
3. On empty read (EOF, no new data): `await asyncio.sleep(0.1)` — 100ms poll.
4. On non-empty read: call `adapter.parse_stream_line(line)`. If result is non-None `Event`, call `await on_event(event)`.
5. Watch for file shrinkage (`tell()` > `stat().st_size`) indicating rotation; re-seek to 0.
6. Respect `stop_event`: check before each poll sleep and exit cleanly when set.

**Numeric constant justification** (per §5.9 lesson):
- 100ms poll: low CPU overhead, sub-second latency acceptable for dashboard "last activity" display
- 200ms file-exist poll: faster than 100ms would waste cycles before pipe-pane starts writing
- 5s existence timeout: enough time for tmux pipe-pane to initialize after session spawn

### Step 2 — Wire pipe_pane in FleetSpawner (`spawn.py:210-212`)

Replace the TODO comment with:
```python
pipe_rc = await tmux.pipe_pane(self.socket, session.name, session.stream_log)
if pipe_rc != 0:
    logger.warning("pipe_pane failed for %s (rc=%d); stream log unavailable", session.lane, pipe_rc)
```

This is the only change to `spawn.py`. If `pipe_pane` fails (tmux unavailable, socket problem), the mission still runs — stream reading falls back to the 5s timeout path in `LaneStreamReader.run()`.

### Step 3 — Integration with the SSE endpoint

The server's `/api/v1/events` endpoint needs to emit `LaneStreamReader` events. Integration point:
- `FleetSpawner` holds `LaneSession` instances; each has `subscribers_lock` (the SR-3 fan-out hook).
- The server lifespan (or the object managing SSE subscriptions) will instantiate one `LaneStreamReader` per `LaneSession`, passing a callback that acquires `subscribers_lock` and delivers the event to all active SSE connections.
- `stop_event` is signaled during graceful shutdown.

The xfail test `test_sse_stream_emits_status_change_on_file_touch` (lines 63-106) exercises exactly this path. Removing the `xfail` mark and passing the test is the P2-C acceptance criterion for SSE integration.

### Step 4 — Fallback to existing tail behavior

No existing "tail" behavior exists in the server today — the queue applier reads `queue/pending/*.json`, not stream logs. The "fallback" is simply: if `stream_log` never appears (pipe_pane unavailable), `LaneStreamReader` logs a warning and exits. The queue applier and all existing mission logic are unaffected. SSE events from the file-watcher path (STATUS.md, TASKS.md) continue to work as before.

---

## File Paths

| New / Changed | Path | Notes |
|---|---|---|
| New | `megalodon_ui/stream_reader.py` | Core implementation |
| Changed | `megalodon_ui/spawn.py:210-212` | Wire `pipe_pane` call |
| New | `scripts/tests/test_stream_reader_unit.py` | Unit tests (mocked file I/O) |
| Changed | `scripts/tests/test_spawn_unit.py` | Add assertion: `pipe_pane` called after session spawn |
| Changed | `ui/tests/integration/test_sse_stream.py:63-106` | Remove xfail, activate test |

---

## Test Plan

**Unit tests (`scripts/tests/test_stream_reader_unit.py`)**:
- `test_run_delivers_parsed_events`: write lines to a temp file, verify `on_event` called for each non-None parse result
- `test_run_skips_none_parse_results`: adapter returns None for some lines; verify callback not called
- `test_run_waits_for_file_existence`: file absent at start, appears after 300ms; verify reader picks it up
- `test_run_handles_file_rotation`: simulate file shrinkage; verify reader re-seeks to 0
- `test_run_exits_on_stop_event`: signal `stop_event` during poll loop; verify clean exit

**Integration test fix (`ui/tests/integration/test_sse_stream.py`)**:
- Remove `xfail`; wire the test to touch the actual `.fleet/<lane>.stream.log` (or mock pipe_pane) and verify SSE emits a `stream-line` event.

**Spawn unit test update (`scripts/tests/test_spawn_unit.py`)**:
- Assert `tmux.pipe_pane` is called once per lane in `start_all`, with correct socket/name/dest args.

---

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| `pipe-pane` flush not line-buffered (§5.8 lesson) | Reader uses `readline()` with 100ms EOF-retry; partial lines are buffered by file I/O, not the reader |
| Log rotation invalidates byte offset (§5.7 lesson) | Reader checks `tell() > stat().st_size` before each read; re-seeks on shrinkage |
| Blocking `readline()` in asyncio loop (§5.5 lesson) | All file reads go through `asyncio.to_thread()` |
| `pipe_pane` failure doesn't surface to operator | Warning logged; no exception raised; stream reader degrades gracefully |

---

## Next Steps

1. **P2-C**: Implement `megalodon_ui/stream_reader.py` per Step 1 above.
2. **P2-C**: Wire `pipe_pane` in `spawn.py` per Step 2.
3. **P2-C**: Wire `LaneStreamReader` into the SSE endpoint per Step 3.
4. **P2-C**: Write unit + integration tests per Test Plan above.
5. **P3-B-to-C**: ARCHITECT verifies stream-reader design matches CV-9 intent.

---

*Findings doc written by agent-d510 at 2026-05-19T20-10-00Z.*
