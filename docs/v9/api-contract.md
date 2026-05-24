---
title: Megalodon v9 API Contract
status: CANONICAL
version: 1.1
utc: 2026-05-20
owner: orchestrator-Claude (v9); revisit v10
---

# Megalodon v9 API Contract

> Canonical source-of-truth for all FE→BE calls. BE startup validates this.
> FE runtime wrapper validates this in test mode. ANY drift fails P3 verify.
>
> See `docs/superpowers/specs/2026-05-16-v9-m2-contract-scan-design.md`.

## Conventions

- Path templates use `{param}` for path parameters (e.g., `/api/v1/findings/{filename}`).
- YAML blocks are extracted verbatim by `megalodon_ui.contract_loader`.
- Response shapes documented as Pydantic class skeletons; canonical models in
  `megalodon_ui/schemas.py`.
- SSE event names MUST match `megalodon_ui.constants.SSE_EVENT_TYPES`.
- Inner dicts (tasks/history/findings entries) stay loose for v9 per spec D6;
  field-level enforcement is a v10 tightening.

### X-CSRF-Token (v9.2+)

All POST and DELETE endpoints introduced in v9.2 or later require the
`X-CSRF-Token` header. The value must match the `content` attribute of
`<meta name="csrf-token">` in the page `<head>`. Comparison is
`secrets.compare_digest` (timing-safe). Missing or mismatched token → **403**.

### Cookie gate (`_V92_GATED_PATH_RE`)

A middleware regex guards paths that require a valid `mui_session` cookie.
Requests without a valid session cookie are rejected with **401**.

Gated prefix pattern (v9.4):
```
^/api/v1/(lane/[^/]+|__fake__|permission_prompts|activity-wall|approval-rules|lanes/stale|_test)(/|$)
```

Additionally, `DELETE /api/v1/fleet` is gated via an exact-match table
(`_V92_GATED_EXACT`).

### Auth bypass (ungated) routes

The following routes do **not** require a session cookie. Any new endpoint
added to this list must be justified in
`ui/tests/integration/test_auth_gate_enumerates_all_routes.py`.

| Route | Reason |
|---|---|
| `GET /api/v1/status` | v9.1 read-only, pre-auth-gate |
| `GET /api/v1/tasks` | v9.1 read-only, pre-auth-gate |
| `GET /api/v1/state` | v9.1 read-only, pre-auth-gate |
| `GET /api/v1/findings` | v9.1 read-only, pre-auth-gate |
| `GET /api/v1/findings/{filename}` | v9.1 read-only, pre-auth-gate |
| `GET /api/v1/events` | v9.1 read-only, pre-auth-gate |
| `GET /api/v1/config` | v9.1 read-only, pre-auth-gate |
| `GET /api/v1/__contract_introspect__` | v9.1 read-only, pre-auth-gate |
| `POST /api/v1/auth/exchange` | entry point — no cookie yet exists |

### File schemas (BE-visible)

**`.fleet/inject-log-YYYY-MM-DD.jsonl`** — daily-rotated audit log. One JSON
object per line. Written by both `/inject` and `/restart-loop`.

```json
{
  "ts": "2026-05-20T15:00:00+00:00",
  "lane": "A",
  "text_sha256": "<hex>",
  "byte_count": 42,
  "enter": true
}
```

Restart-loop entries additionally carry `"source": "restart-loop"`. Inject
entries omit the `source` field (no default value written).

**`.fleet/approval-rules.json`** — flat JSON array of rule objects. Written
atomically (`.tmp` + `os.replace`). No schema version field (per plan §2
non-goals).

```json
[
  {
    "pattern": "Bash(git diff*)",
    "added_at_utc": "2026-05-20T15:00:00+00:00",
    "added_by_session": "<session-id>"
  }
]
```

### `_test/*` namespace

> **WARNING — PRODUCTION MUST NOT EXPOSE THESE ROUTES.**
> All routes under `/api/v1/_test/` are registered **only** when the
> `MEGALODON_FAKE_SPAWNER=1` environment variable is set. They are
> absent from any production `make_app()` invocation. Do not add these
> paths to firewall allowlists or document them in operator runbooks.

---

## Endpoints

### GET /api/v1/state

```yaml
method: GET
path: /api/v1/state
response_model: StateResponse
status: 200
content_type: application/json
fe_consumers:
  - ui/static/js/sse.js:67
description: Returns full mission snapshot for FE bootstrap (sse.js hydrateInitialState).
```

**Response shape (`StateResponse`):**
```python
class StateResponse(BaseModel):
    status: dict   # {"lanes": [LaneStatus, ...]}
    tasks: dict    # {"phases": [...]}
    findings: dict # {"list": [...]}
    signals: dict  # {"list": []}  (extractor not yet ported)
    mission: dict  # {"phase": str}
    config: dict   # {"csrf_token": str, "poll_interval_seconds": int}
```

---

### GET /api/v1/config

```yaml
method: GET
path: /api/v1/config
response_model: ConfigResponse
status: 200
content_type: application/json
fe_consumers:
  - ui/static/js/app.js
description: Returns FE bootstrap config (csrf, timing thresholds, CORS origins).
```

**Response shape (`ConfigResponse`):**
```python
class ConfigResponse(BaseModel):
    csrf_token: str
    heartbeat_interval_seconds: int
    poll_interval_seconds: int
    stale_threshold_seconds: int
    allowed_origins: list[str]
```

---

### GET /api/v1/events

```yaml
method: GET
path: /api/v1/events
response_model: SSEStream
status: 200
content_type: text/event-stream
fe_consumers:
  - ui/static/js/sse.js:152
description: Long-lived SSE stream of mission events (sse-starlette EventSourceResponse).
sse_events:
  - status-change
  - task-change
  - phase-flip
  - finding-new
  - history-append
  - claim-create
  - claim-done
  - signal-new
  - lagging
  - heartbeat
  - mission-status
  - sync
```

SSE event names MUST match `megalodon_ui/constants.py` `SSE_EVENT_TYPES`
(M4 dependency). Contract scan cross-validates via `schemas.py` import-time
drift assert.

---

### GET /api/v1/findings

```yaml
method: GET
path: /api/v1/findings
response_model: FindingsListResponse
status: 200
content_type: application/json
fe_consumers:
  - ui/static/js/pages/findings.js
description: List findings filtered by lane/severity/task/scratch query params.
```

**Response shape (`FindingsListResponse`):**
```python
class FindingsListResponse(BaseModel):
    findings: list[dict]  # YAML-frontmatter dicts; filename/severity/lane keys
```

---

### POST /api/v1/reclaim

```yaml
method: POST
path: /api/v1/reclaim
response_model: QueueAcceptResponse
status: 202
content_type: application/json
fe_consumers:
  - ui/static/js/app.js
description: 'Reclaim a lane working task (V9 M1.5 202-async); body {"lane": "A"}. Returns 204 when lane already idle.'
```

**Response shape (`QueueAcceptResponse`):**
```python
class QueueAcceptResponse(BaseModel):
    request_id: str
    intent: str       # "STATUS_UPDATE"
    status: str       # "pending"
```

Headers include `Location: /api/v1/queue/{request_id}` for polling.

---

### POST /api/v1/signal

```yaml
method: POST
path: /api/v1/signal
response_model: QueueAcceptResponse
status: 202
content_type: application/json
fe_consumers:
  - ui/static/js/app.js
description: 'Send signal to target lane (V9 M1.5 202-async); body {"to_lane", "claim", "evidence"}.'
```

**Response shape (`QueueAcceptResponse`):**
```python
class QueueAcceptResponse(BaseModel):
    request_id: str
    intent: str       # "STATUS_UPDATE"
    status: str       # "pending"
```

Headers include `Location: /api/v1/queue/{request_id}` for polling.

---

### POST /api/v1/challenge

```yaml
method: POST
path: /api/v1/challenge
response_model: QueueAcceptResponse
status: 202
content_type: application/json
fe_consumers:
  - ui/static/js/pages/findings.js
description: 'Open CHALLENGE task against a finding (V9 M1.5 202-async); body {"finding_filename", "description"}.'
```

**Response shape (`QueueAcceptResponse`):**
```python
class QueueAcceptResponse(BaseModel):
    request_id: str
    intent: str       # "TASKS_INJECT"
    status: str       # "pending"
```

Headers include `Location: /api/v1/queue/{request_id}` for polling.

---

### POST /api/v1/phase-flip

```yaml
method: POST
path: /api/v1/phase-flip
response_model: ActionResponse
status: 200
content_type: application/json
fe_consumers:
  - ui/static/js/pages/mission.js
description: 'Atomic phase-flip claim; body {"from", "to"}.'
```

**Response shape (`ActionResponse`):**
```python
class ActionResponse(BaseModel):
    ok: bool
    from_: str   # serialized as "from"
    to: str
```

---

### POST /api/v1/mission-status

```yaml
method: POST
path: /api/v1/mission-status
response_model: ActionResponse
status: 200
content_type: application/json
fe_consumers:
  - ui/static/js/pages/mission.js
description: 'Set mission status (IDLE|ACTIVE|DRAINING|COMPLETE); body {"status": "..."}.'
```

**Response shape (`ActionResponse`):**
```python
class ActionResponse(BaseModel):
    ok: bool
    status: str
```

---

### POST /api/v1/inject-task

```yaml
method: POST
path: /api/v1/inject-task
response_model: QueueAcceptResponse
status: 202
content_type: application/json
fe_consumers:
  - ui/static/js/pages/tasks.js
description: 'Inject a task line into TASKS.md (V9 M1.5 202-async); body {"task_text"} matching `- [bracket] [LANE-X] `id` — desc`.'
```

**Response shape (`QueueAcceptResponse`):**
```python
class QueueAcceptResponse(BaseModel):
    request_id: str
    intent: str       # "TASKS_INJECT"
    status: str       # "pending"
```

Headers include `Location: /api/v1/queue/{request_id}` for polling.

---

### GET /api/v1/queue/{request_id}

```yaml
method: GET
path: /api/v1/queue/{request_id}
response_model: QueueStatusResponse
status: 200
content_type: application/json
fe_consumers:
  - ui/static/js/app.js
description: 'V9 M1.5 — poll status of a queued request. Returns {request_id, status, rejection_reason}.'
```

**Response shape (`QueueStatusResponse`):**
```python
class QueueStatusResponse(BaseModel):
    request_id: str
    status: str            # "pending" | "applied" | "rejected"
    rejection_reason: str | None
```

---

### GET /api/v1/findings/{filename}

```yaml
method: GET
path: /api/v1/findings/{filename}
response_model: FindingDetailResponse
status: 200
content_type: application/json
fe_consumers:
  - ui/static/js/pages/findings.js
description: Fetch a single finding's body + frontmatter by filename.
```

**Response shape (`FindingDetailResponse`):**
```python
class FindingDetailResponse(BaseModel):
    filename: str
    body: str
    frontmatter: dict
```

---

## v9.4 Endpoints

### POST /api/v1/lane/{short}/inject

```yaml
method: POST
path: /api/v1/lane/{short}/inject
response_model: InjectResponse
status: 202
content_type: application/json
auth: cookie (mui_session) + X-CSRF-Token header
fe_consumers:
  - ui/static/js/pages/lane_detail.js
description: Inject keystrokes into a lane's tmux pane (Task 1.3).
```

**Request body (`InjectBody`):**
```python
class InjectBody(BaseModel):
    text: str      # keystrokes to send; max 16384 bytes (UTF-8)
    enter: bool = True  # whether to append Enter keystroke
```

**Required header:** `X-CSRF-Token: <token>`

**Status codes:**

| Code | Condition |
|---|---|
| 202 | Keystrokes sent; audit entry written |
| 403 | Missing or mismatched X-CSRF-Token |
| 404 | Unknown lane or spawner not initialized |
| 413 | `text` exceeds 16384 bytes (UTF-8 encoded) |
| 429 | Rate limit exceeded — 10 calls per 60 s per lane |

**Response shape (202):**
```json
{"ok": true}
```

**Audit log:** `.fleet/inject-log-YYYY-MM-DD.jsonl` — see File schemas section.

**Sample:**
```bash
curl -s -X POST http://localhost:8765/api/v1/lane/A/inject \
  -H "X-CSRF-Token: $CSRF" \
  -H "Content-Type: application/json" \
  -d '{"text": "ls -la", "enter": true}'
# → 202 {"ok": true}
```

---

### POST /api/v1/lane/{short}/restart-loop

```yaml
method: POST
path: /api/v1/lane/{short}/restart-loop
status: 202
content_type: application/json
auth: cookie (mui_session) + X-CSRF-Token header
fe_consumers:
  - ui/static/js/pages/lane_detail.js
description: Restart a lane's /loop cycle using its recorded initial_prompt (Task 2.5).
```

**Request body:** empty object `{}`

**Required header:** `X-CSRF-Token: <token>`

**Status codes:**

| Code | Condition |
|---|---|
| 202 | initial_prompt re-injected; audit entry written |
| 403 | Missing or mismatched X-CSRF-Token |
| 404 | Unknown lane or spawner not initialized |
| 409 | No `initial_prompt` recorded for this lane |

**Response shape (202):**
```json
{"ok": true}
```

**Audit log:** same `.fleet/inject-log-YYYY-MM-DD.jsonl` file as `/inject`;
entry includes `"source": "restart-loop"` and `"enter": true` always.

**Sample:**
```bash
curl -s -X POST http://localhost:8765/api/v1/lane/A/restart-loop \
  -H "X-CSRF-Token: $CSRF" \
  -H "Content-Type: application/json" \
  -d '{}'
# → 202 {"ok": true}
```

---

### GET /api/v1/activity-wall/snapshot

```yaml
method: GET
path: /api/v1/activity-wall/snapshot
status: 200
content_type: application/json
auth: cookie (mui_session)
fe_consumers:
  - ui/static/js/components/activity_wall.js
description: Return recent activity-wall events as JSON, newest-first (Task 2.3).
```

**Query parameters:**

| Param | Type | Default | Notes |
|---|---|---|---|
| `limit` | int | 100 | Silently clipped to [1, 500]; no 400 for out-of-range values |

**Response shape (200):**
```json
{
  "events": [
    {
      "type": "finding",
      "lane": "A",
      "ts": "2026-05-20T15:00:00Z",
      "summary": "...",
      "payload": {}
    }
  ]
}
```

Valid `type` values: `finding | signal | history | queue | inject | restart-loop | approval`

`lane` is `null` for events not tied to a specific lane.

If the activity wall is not yet initialized (server startup race), returns
`{"events": []}` with 200 rather than an error.

**Sample:**
```bash
curl -s "http://localhost:8765/api/v1/activity-wall/snapshot?limit=50" \
  -H "Cookie: mui_session=$SID"
```

---

### GET /api/v1/activity-wall

```yaml
method: GET
path: /api/v1/activity-wall
status: 200
content_type: text/event-stream
auth: cookie (mui_session)
fe_consumers:
  - ui/static/js/components/activity_wall.js
description: SSE stream of new activity-wall events as they arrive (Task 2.3).
```

Emits **no backlog**. Clients must hydrate history via
`GET /api/v1/activity-wall/snapshot` first, then open this SSE stream for
live updates.

Each SSE frame is an unnamed event with a JSON-encoded data payload:

```
data: {"type": "finding", "lane": "A", "ts": "...", "summary": "...", "payload": {...}}
```

Keep-alive comment frames (`': ka'`) are emitted every 15 s when no events arrive.

If the activity wall is not initialized: **503** `{"detail": "activity wall not initialized"}`.

**Sample:**
```bash
curl -s -N "http://localhost:8765/api/v1/activity-wall" \
  -H "Cookie: mui_session=$SID"
```

---

### POST /api/v1/approval-rules

```yaml
method: POST
path: /api/v1/approval-rules
status: 201
content_type: application/json
auth: cookie (mui_session) + X-CSRF-Token header
fe_consumers:
  - ui/static/js/pages/approval_rules.js
description: Add an approval-rule pattern (Task 3.1). Idempotent on duplicate pattern.
```

**Request body (`ApprovalRuleBody`):**
```python
class ApprovalRuleBody(BaseModel):
    pattern: str             # e.g. "Bash(git diff*)"
    added_by_session: str    # session ID of the adding operator
```

**Required header:** `X-CSRF-Token: <token>`

**Status codes:**

| Code | Condition |
|---|---|
| 201 | New rule created |
| 200 | Pattern already exists — existing entry returned (dedup, idempotent) |
| 403 | Missing or mismatched X-CSRF-Token |

**Response shape (201 and 200):**
```json
{
  "pattern": "Bash(git diff*)",
  "added_at_utc": "2026-05-20T15:00:00+00:00",
  "added_by_session": "<session-id>"
}
```

**Sample:**
```bash
curl -s -X POST http://localhost:8765/api/v1/approval-rules \
  -H "X-CSRF-Token: $CSRF" \
  -H "Content-Type: application/json" \
  -d '{"pattern": "Bash(git diff*)", "added_by_session": "'"$SID"'"}'
```

---

### GET /api/v1/approval-rules

```yaml
method: GET
path: /api/v1/approval-rules
status: 200
content_type: application/json
auth: cookie (mui_session)
fe_consumers:
  - ui/static/js/pages/approval_rules.js
description: Return all approval-rule patterns (Task 3.1).
```

**Response shape (200):**
```json
{
  "rules": [
    {
      "pattern": "Bash(git diff*)",
      "added_at_utc": "2026-05-20T15:00:00+00:00",
      "added_by_session": "<session-id>"
    }
  ]
}
```

Returns `{"rules": []}` when the file is missing or corrupt (corrupt-file
policy: log WARNING, never 500).

**Sample:**
```bash
curl -s http://localhost:8765/api/v1/approval-rules \
  -H "Cookie: mui_session=$SID"
```

---

### DELETE /api/v1/approval-rules

```yaml
method: DELETE
path: /api/v1/approval-rules
status: 204
auth: cookie (mui_session) + X-CSRF-Token header
fe_consumers:
  - ui/static/js/pages/approval_rules.js
description: Remove an approval-rule pattern by exact match (Task 3.1).
```

**Query parameter:** `pattern` (required, exact match)

**Required header:** `X-CSRF-Token: <token>`

**Status codes:**

| Code | Condition |
|---|---|
| 204 | Rule removed; file written atomically |
| 400 | `pattern` query param missing |
| 403 | Missing or mismatched X-CSRF-Token |
| 404 | Pattern not found in rules list |

**Sample:**
```bash
curl -s -X DELETE \
  "http://localhost:8765/api/v1/approval-rules?pattern=Bash(git+diff*)" \
  -H "X-CSRF-Token: $CSRF" \
  -H "Cookie: mui_session=$SID"
# → 204 (no body)
```

---

### GET /api/v1/approval-rules/extract

```yaml
method: GET
path: /api/v1/approval-rules/extract
status: 200
content_type: application/json
auth: cookie (mui_session)
fe_consumers:
  - ui/static/js/pages/approval_rules.js
description: Extract an --allowedTools pattern from a raw command string (Task 3.5).
```

**Query parameter:** `command` (URL-encoded shell command string)

No CSRF required — safe GET method.

**Response shape (200):**
```json
{"pattern": "Bash(git diff*)"}
```

Returns `{"pattern": null}` for compound commands, redirects, empty input,
or any command the heuristic cannot reduce to a single tool pattern.

**Sample:**
```bash
curl -s "http://localhost:8765/api/v1/approval-rules/extract?command=git+diff+HEAD" \
  -H "Cookie: mui_session=$SID"
# → {"pattern": "Bash(git diff*)"}
```

---

### GET /api/v1/lanes/stale

```yaml
method: GET
path: /api/v1/lanes/stale
status: 200
content_type: application/json
auth: cookie (mui_session)
fe_consumers:
  - ui/static/pages/board.js
description: Return lanes silent for ≥ 900 s and not pending approval (Task 2.6).
```

Server-side **5 s cache** (module-level, per `id(app)`). Concurrent operator
polls within the TTL window are served from cache. Cache is bypassed and
cleared when a `_test/stale_override` has been set.

**Response shape (200):**
```json
{
  "stale_lanes": [
    {
      "lane": "A",
      "silent_seconds": 1234.5,
      "pending_approval": false,
      "last_activity_source": "stream-log"
    }
  ],
  "checked_at_utc": "2026-05-20T15:00:00+00:00"
}
```

`silent_seconds` is `null` when no data source provided any timestamp (lane
treated as infinitely stale).

`last_activity_source` values: `"status-md" | "stream-log" | "applier-log" | "none"`

Stale threshold: **900 s** (`_STALE_THRESHOLD_SECONDS`). A lane with
`pending_approval: true` is never included regardless of silence duration.

**Sample:**
```bash
curl -s http://localhost:8765/api/v1/lanes/stale \
  -H "Cookie: mui_session=$SID"
```

---

### POST /api/v1/_test/stale_override

> **TEST ENVIRONMENT ONLY.** This route is registered **only** when
> `MEGALODON_FAKE_SPAWNER=1`. It is absent in production. Do not expose.

```yaml
method: POST
path: /api/v1/_test/stale_override
status: 200
content_type: application/json
auth: cookie (mui_session) + X-CSRF-Token header
description: Populate a one-shot silent_seconds override for the next stale check (Task 2.7).
```

**Query parameters:**

| Param | Type | Required | Notes |
|---|---|---|---|
| `lane` | str | yes | Lane short-code to override |
| `seconds` | float | yes | `silent_seconds` value to inject |

**Request body:** empty or `{}`

**Required header:** `X-CSRF-Token: <token>`

The override is **one-shot**: consumed on the next `GET /api/v1/lanes/stale`
call and then cleared. The stale-lanes cache is also invalidated so the next
GET recomputes with the override applied.

**Status codes:**

| Code | Condition |
|---|---|
| 200 | Override registered |
| 403 | Missing or mismatched X-CSRF-Token |
| 422 | Missing `lane`, missing `seconds`, or `seconds` not a valid float |

**Response shape (200):**
```json
{"ok": true, "lane": "A", "seconds": 1200.0}
```

**Sample:**
```bash
curl -s -X POST \
  "http://localhost:8765/api/v1/_test/stale_override?lane=A&seconds=1200" \
  -H "X-CSRF-Token: $CSRF" \
  -H "Cookie: mui_session=$SID"
# → {"ok": true, "lane": "A", "seconds": 1200.0}
```

---
