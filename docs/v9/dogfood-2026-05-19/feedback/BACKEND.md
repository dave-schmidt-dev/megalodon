
## 2026-05-19T19:55:49Z — orchestrator (auto-monitor)

You've held your claim for 33+ minutes with no finding written. The pipe-pane stream shows you're actively thinking, not blocked on a prompt. Two possibilities:

1. **Heavy iteration** — fine, but please write an intermediate checkpoint finding (`findings/<agent-id>-<LANE>-P1-checkpoint-2026-05-19T19:55:49Z.md`) describing what you've done so far + what's left, so the operator has visibility.
2. **Blocked silently** — if you're spinning on something (e.g. trying a tool that prompts and then bouncing off, deep nested research, etc.), STOP, release the claim, write a finding describing what blocked you, then re-claim with a narrower scope.

Either way: respond by writing a checkpoint finding within your next iteration. The operator is watching.

## 2026-05-20T01:00:00Z — STOP using hardcoded fake cookies

You've been issuing curl commands like:
```bash
curl -s -X POST 'http://127.0.0.1:8765/api/v1/task/done?wait=true' \
  -H 'Content-Type: application/json' \
  -b 'megalodon_session=fleet-dev-token-2026' \
  -d '{"lane":"C", ...}'
```

`megalodon_session=fleet-dev-token-2026` is a **placeholder, not a real cookie**. Every protected endpoint returns 401 with that, and you're looping endlessly (5+ iterations) issuing slightly-different but always-doomed curls.

**The launch template's "How to call the queue endpoints with curl" section** (in `launch-BACKEND.md`) shows the correct flow:

```bash
# ONCE at the start of any session that needs the queue:
TOKEN=$(cat .fleet/ui.token)
curl -s -c /tmp/cookies.txt -X POST -H "Content-Type: application/json" \
  -d "{\"token\":\"$TOKEN\"}" http://127.0.0.1:8765/api/v1/auth/exchange

# Then EVERY subsequent call uses -b /tmp/cookies.txt:
curl -s -b /tmp/cookies.txt -X POST -H "Content-Type: application/json" \
  -d '{"lane":"C","task_id":"P2-C","agent":"agent-d510"}' \
  'http://127.0.0.1:8765/api/v1/task/claim?wait=true'
```

**Two key points:**
1. Do `auth/exchange` ONCE per session (use Read tool on `.fleet/ui.token` to get the token), write the cookie to `/tmp/cookies-d510.txt`, reuse for the rest of the session
2. NEVER hardcode a session value — there's no fixed dev token; sessions are generated per-exchange and stored as opaque IDs

**Acknowledge by referencing this timestamp (2026-05-20T01:00:00Z) in your next finding.** Re-read `launch-BACKEND.md` lines 60-77 for the full curl flow.
