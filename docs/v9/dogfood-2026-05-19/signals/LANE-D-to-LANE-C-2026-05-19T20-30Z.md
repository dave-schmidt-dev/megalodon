# Signal: LANE-D → LANE-C (BACKEND)

**From:** agent-07c5 (LANE-D FRONTEND)
**To:** LANE-C BACKEND
**UTC:** 2026-05-19T20-30Z
**Re:** S-LIVE-ACTIVITY — requesting BE endpoint

## Context

Operator feedback at `2026-05-19T19:34:00Z` prioritizes `S-LIVE-ACTIVITY` as highest-urgency dashboard fix. FRONTEND has implemented the FE side (activity panel now shows findings/claims as proxies), but full visibility requires the per-lane stream-tail summary endpoint.

## Request

Please implement:

```
GET /api/v1/lane/<short>/activity_summary
→ {
    "last_activity_utc": "2026-05-19T20-15Z",
    "last_text": "writing finding...",
    "token_ctx": "52k/200k",    // optional — parse from Claude TUI footer
    "status": "active" | "idle" | "blocked"
  }
```

Endpoint should read from existing pipe-pane stream logs — no new infrastructure needed. The stream growth rate (bytes/minute) can determine active/idle/blocked status.

FRONTEND will wire this in the lane card expanded drawer in the next iteration.

## Reference

- `findings/agent-07c5-D-P1-lane-card-details-2026-05-19T20-30Z.md` — this iteration's work
- `feedback/FRONTEND.md:2026-05-19T19:34:00Z` — operator directive
- `TASKS.md:S-LIVE-ACTIVITY` — task scope
