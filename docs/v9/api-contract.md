---
title: Megalodon v9 API Contract
status: CANONICAL
version: 1.0
utc: 2026-05-16
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
