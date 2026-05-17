### GET /api/v1/events

```yaml
method: GET
path: /api/v1/events
response_model: SSEStream
status: 200
content_type: text/event-stream
fe_consumers:
  - ui/static/js/sse.js:152
sse_events:
  - status-change
  - heartbeat
```
