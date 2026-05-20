# LANE-A AUDIT finding — StatusUpdatePayload schema drift vs launch docs

- **Agent:** agent-0fa4 (LANE-A AUDIT)
- **Phase:** PHASE-PLAN
- **Discovered:** 2026-05-19T23-15-43Z
- **Severity:** MEDIUM (operational friction; visible failure mode; no data loss)

## Summary

The `launch-AUDIT.md` (and presumably the other lane launch files) document the `/api/v1/status/update` payload as:

```json
{"lane": "A", "agent": "agent-0fa4",
 "new_state": "working: <task-id>" | "idle" | "BLOCKED",
 "new_utc": "<UTC>"}
```

But the **server-side `StatusUpdatePayload` schema rejects this exact payload** with:

```
payload-schema:1 validation error for StatusUpdatePayload
new_notes
  Input should be a valid string [type=string_type, input_value=None, input_type=NoneType]
```

`new_notes` is a required string, not Optional[str], so every agent following the launch file verbatim will get a `rejected` response from the queue on their first STATUS_UPDATE call.

## Reproduction

1. Read the auth token and exchange for a session cookie (per launch-AUDIT.md "How to call the queue endpoints with curl").
2. POST exactly the documented payload to `http://127.0.0.1:8765/api/v1/status/update?wait=true`:
   ```bash
   curl -s -b /tmp/cookies.txt -X POST -H "Content-Type: application/json" \
     -d '{"lane":"A","agent":"agent-0fa4","new_state":"idle","new_utc":"2026-05-19T23-15-43Z"}' \
     "http://127.0.0.1:8765/api/v1/status/update?wait=true"
   ```
3. Observe response:
   ```json
   {"request_id":"...","intent":"STATUS_UPDATE","status":"rejected",
    "rejection_reason":"... StatusUpdatePayload\nnew_notes\n  Input should be a valid string ..."}
   ```
4. Add `"new_notes":"<any string>"` to the payload and the same request returns `"status":"applied"`.

## Evidence — observed in this run

This very iteration's first status-update request (request_id `2026-05-19T23-16-08Z-agent-0fa4-STATUS_md-STATUS_UPDATE-47f6`) was rejected for this reason. STATUS.md's LANE-A row had been showing `unclaimed | — | —` for hours despite `P1-A` being marked done since 2026-05-19T19-20-52Z — strongly suggesting earlier iterations also hit this rejection and silently treated it as "nothing to do" rather than as a contract violation.

The mirror evidence: STATUS.md observed at the start of this iteration shows lanes C/D/E with rich `Current task` / notes strings (`implementing next_tick BE+FE`, `running dashboard audit Playwright suite`), confirming the *schema* requires notes — only the *launch docs* omit them. The drift is in documentation, not in the applier.

## Impact

1. Every new agent loop has at least one wasted iteration where its STATUS update is silently rejected, leaving its lane card dark on the dashboard.
2. `BUG-STATUS-NOT-WRITTEN` (LANE-C, currently open in TASKS.md) blames "agents claim via mkdir but never write to STATUS.md". The *root cause is partially the launch-template doc drift*, not solely missing instruction. Fixing the doc + retrofitting all six `launch-<LANE>.md` files closes that bug for new spawns without any server code change.
3. The rejection message is informative (pydantic emits the field name + expected type), so a self-correcting agent CAN recover — but the v9.2 idle agents in this run did not, because the launch file says "use this exact payload" without showing the `new_notes` field.

## Recommended fixes (ranked)

1. **Two-line doc fix (smallest blast radius)** — update step 9 in every `launch-<LANE>.md` to:
   ```
   body: {"lane": "A", "agent": "agent-0fa4",
          "new_state": "working: <task-id>" | "idle" | "BLOCKED",
          "new_utc": "<UTC>",
          "new_notes": "<short human-readable note, can match Current task column>"}
   ```
   File the patch under `S-LAUNCH-DOC-FIX` or fold into `BUG-STATUS-NOT-WRITTEN`.
2. **Schema fix (alternative)** — if the design intent was that `new_notes` is optional and the dashboard should fall back to `Current task` when absent, change `StatusUpdatePayload.new_notes` to `Optional[str] = None` and have the applier coerce `None` to an empty string before rendering. This keeps the documented contract honest.
3. **Belt-and-suspenders** — add a smoke test in `scripts/tests/` that POSTs the exact payload shown in each `launch-<LANE>.md` to a stub applier and asserts `"status":"applied"`. This catches doc/schema drift before it ships. Suggested file: `scripts/tests/test_launch_doc_payloads.py`.

## Next-step recommendation for orchestrator

This finding is in scope for either LANE-A's `P2-A` (AUDIT build phase top-3 findings) or LANE-C's `BUG-STATUS-NOT-WRITTEN`. Suggest folding it into BUG-STATUS-NOT-WRITTEN since the proximate symptom (dashboard cards going dark) is identical and the fix is one-shot across all six launch templates.

## Cross-reference

- Earlier finding from this lane: `findings/agent-0fa4-A-P1-status-applier-seed-mismatch-2026-05-19T22-35-08Z.md` (related but different — that was about the applier's seed/state mismatch, this is the payload schema vs doc mismatch).
- Related open task in TASKS.md: `BUG-STATUS-NOT-WRITTEN` (LANE-C).
