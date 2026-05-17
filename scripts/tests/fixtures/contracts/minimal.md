# Minimal Test Contract

## Endpoints

### GET /api/v1/state

```yaml
method: GET
path: /api/v1/state
response_model: StateResponse
status: 200
content_type: application/json
fe_consumers:
  - ui/static/js/sse.js:56
description: Returns full mission snapshot.
```
