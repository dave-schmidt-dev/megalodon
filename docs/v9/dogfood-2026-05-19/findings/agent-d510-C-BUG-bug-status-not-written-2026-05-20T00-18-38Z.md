---
agent: agent-d510
lane: C
task: BUG-STATUS-NOT-WRITTEN
severity: INFO
utc: 2026-05-20T00-18-38Z
---

# BUG-STATUS-NOT-WRITTEN: Agent-Facing Queue Proxy Endpoints Added

## Summary

Added four agent-facing HTTP endpoints to `megalodon_ui/server.py` so agents
can update STATUS.md, claim/release tasks, and append to HISTORY.md via
`curl` without direct file edits. Also added `POST /api/v1/auth/exchange` for
session cookie authentication.

## Root Cause

The live server (pid 53741, started before the dogfood mission commits) had
these endpoints from a prior `server.py` version. The current working-tree
`server.py` was missing them. A server restart would have dropped the endpoints
because they weren't in the file on disk.

## Changes

### `megalodon_ui/server.py`

Added module-level helper:

```python
async def _wait_for_queue_result(mission_dir, rid, timeout=5.0) -> tuple[str, str|None]
```

Polls `queue/applied/` and `queue/rejected/` every 0.25 s (asyncio.sleep, not
blocking) for up to `timeout` seconds. Returns `("applied", None)`,
`("rejected", reason)`, or `("pending", None)`.

Added endpoints (all inside `_register_routes`):

| Endpoint | Body fields | Notes |
|---|---|---|
| `POST /api/v1/auth/exchange` | `{token}` | Sets `megalodon_session` cookie |
| `POST /api/v1/status/update` | `{lane, agent, new_state, new_utc?, new_notes?}` | Queue → STATUS.md |
| `POST /api/v1/task/claim` | `{lane, task_id, agent}` | Queue → TASKS.md bracket |
| `POST /api/v1/task/done` | `{lane, task_id, agent}` | Queue → TASKS.md bracket |
| `POST /api/v1/history/append` | `{lane, agent, task_id, finding_path, severity?}` | Queue → HISTORY.md |

All four queue-proxy endpoints:
- Check session cookie (401 if missing or wrong)
- Return 422 on missing required fields
- Without `?wait=true`: submit to queue, return 202 + `Location` header
- With `?wait=true`: poll up to 5 s, return 200 (applied) / 409 (rejected) / 202 (still pending)

### `ui/tests/integration/test_agent_queue_endpoints.py` (new)

11 integration tests covering auth, validation, and queue-apply behavior for
all five endpoints. All pass; 504 existing tests also pass (7 pre-existing tmux
socket failures, not related to this change).

## Evidence

```
11 passed in 0.15s
504 passed in 75.80s (full suite minus isolated)
```

## Next Steps

- Server should be restarted to pick up the new server.py (operator action).
- Other endpoints still missing from working tree vs live server:
  - `GET /api/v1/lane/{lane}/state`
  - `GET /api/v1/lane/{lane}/pane-stream`
  - `GET /api/v1/permission_prompts`
  - `POST /api/v1/permission_prompts/{lane}/respond`
  - `POST /api/v1/lane/{lane}/feedback`
  - `POST /api/v1/lane/{lane}/followup`
  - `POST /api/v1/mission-event`
  - `DELETE /api/v1/fleet`
  These belong to other tasks (S-ORCHESTRATOR-AUTO-LOOP, S-LIVE-ACTIVITY, etc.).
