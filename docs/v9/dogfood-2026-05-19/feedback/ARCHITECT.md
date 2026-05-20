# Operator feedback — LANE-B ARCHITECT

## 2026-05-19T22:37:00Z — STOP writing compound-bash self-snapshots

You've been blocking on a permission prompt that looks like:
```
date -u +"..."; echo "---"; grep -E "..." STATUS.md | head -5; echo "---"; curl -s -o /dev/null -w "queue health: %{http_code}\n" http://127.0.0.1:8765/api/v1/health 2>&1 || echo "queue unreachable"
```

The static allowlist matcher in the spawn config CANNOT authorize compound bash (`;`, `&&`, `||`, `|`, command substitution `$(...)`, for/while/if blocks). Every compound chain triggers an operator-approval prompt — and you've been retrying the same one in a loop.

**Use these primitives instead (all auto-approved, NO prompts):**

1. **Get UTC**: a single `date -u +%Y-%m-%dT%H-%M-%SZ` call (no chaining)
2. **Inspect your STATUS.md row**: `Read` tool on `STATUS.md` (auto-approved Claude file tool)
3. **Check queue health**: a single `curl -s http://127.0.0.1:8765/api/v1/state` (localhost curl is auto-approved). There's no `/api/v1/health` endpoint — use `/api/v1/state` instead.

**Updated launch file** — re-read `launch-ARCHITECT.md`. It now explicitly forbids compound bash and shows the correct primitives.

**Queue endpoints now sync** — POST `/api/v1/{task/claim,task/done,status/update,history/append,mission-event}?wait=true` blocks ~5s for the applier and returns the final status in ONE curl. NEVER write `for i in 1..5; do curl ...; done` polling loops; the `?wait=true` query parameter eliminates the need.

Acknowledge by referencing this timestamp (`2026-05-19T22:37:00Z`) in your next finding.
