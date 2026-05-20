# LANE-B ARCHITECT — Hybrid dashboard design (PHASE-PLAN proactive)

**Agent:** agent-f66a · **Lane:** B · **Phase:** PHASE-PLAN
**UTC:** 2026-05-19T23-34-22Z · **Severity:** INFO
**Refs:** task `S-HYBRID-DASHBOARD` (cross-lane LANE-B+D);
prior plan `findings/agent-f66a-B-P1-arch-plan-2026-05-19T20-06-30Z.md`
(§Next steps explicitly committed to drafting this if BUILD hadn't opened).

## Operator feedback acknowledgement

Acknowledging `feedback/ARCHITECT.md` op-msg `2026-05-19T22:37:00Z`
(compound-bash ban) — already ack'd in prior idle-heartbeat findings;
this iteration used zero compound bash. Self-snapshot was Read tool +
single `curl` + single `date` per the corrected primitives.

## Summary

Wrote `docs/v9/v9-3-HYBRID-DASHBOARD.md` (LANE-B half of joint task
`S-HYBRID-DASHBOARD`). The design specifies:

1. **UX** — v9.0 chrome stays default; per-lane "View terminal" button
   opens a modal embedding xterm.js for the live tmux pane stream.
2. **BE contract** — two new endpoints
   (`GET /api/v1/lane/{short}/terminal_meta` + SSE
   `GET /api/v1/lane/{short}/terminal_stream`), both reusing the existing
   `.fleet/<short>.stream.log` pipe-pane source. SSE frames carry
   base64-encoded raw bytes (preserves ANSI escapes that xterm.js needs).
3. **FE component** — new `ui/static/pages/terminal_modal.js` with
   xterm.js v5.5.0 vendored at `ui/static/vendor/xterm/` (offline-safe).
4. **Security** — per-cookie concurrent-stream cap (4), CSRF posture
   matches `/api/v1/events`, xterm.js pinned + SRI-hashed.
5. **Test plan** — 5 BE unit tests + 4 Playwright tests (chromium +
   webkit), all under MISSION exit-criterion test command.
6. **Open questions** — xterm.js vs. server-rendered HTML (recommend
   xterm.js); write-back deferred to v9.4; modal-only in v9.3;
   non-Claude harness rendering deferred.

Design unblocks LANE-D to start implementing the moment PHASE-BUILD opens
and matches the operator's stated requirement (v9.0 chrome + per-lane
terminal drilldown, no surface choice forced).

## Evidence

- New file: `docs/v9/v9-3-HYBRID-DASHBOARD.md` (~260 lines, §1–§11).
- Cross-checked existing FE surface — `ui/static/pages/dashboard.js`
  already has a "Show details" toggle + `lane-drawer-${lane}` region
  pattern (`dashboard.js:242-315`); the new "View terminal" button slots
  in beside it without touching the drawer.
- Cross-checked BE surface — `_parse_stream_tail`
  (`megalodon_ui/server.py:72-100`) already reads
  `.fleet/<short>.stream.log` and strips ANSI for `lane_activity`. The
  terminal stream endpoint reuses the **same file**, **same path
  validation** (`^[A-Z]{1,4}$`), but **must NOT** strip ANSI (xterm.js
  needs the escapes).
- Cross-checked auth — existing cookie-exchange (`_require_auth_cookie`)
  already covers SSE; no new auth path needed.
- Cross-checked deps — xterm.js vendored, not CDN, per the fleet's
  offline-capable posture (no inbound network reqs from operator browser).

## Protocol gap surfaced (operator decision needed)

**Cross-lane task IDs cannot be claimed through the queue.** My first
move this iteration was the spec-compliant claim:

```
POST /api/v1/task/claim?wait=true
body: {"lane":"B","task_id":"S-HYBRID-DASHBOARD","agent":"agent-f66a"}
→ {"status":"rejected",
   "rejection_reason":"apply-failed: task-not-unique:id=S-HYBRID-DASHBOARD:matches=0"}
```

The applier returns `matches=0` because the TASKS.md line reads
`[LANE-B+D]`, not `[LANE-B]`. The regex in the applier evidently expects
a single-lane bracket token. **Two other open tasks have the same
shape**: `S-LIVE-ACTIVITY` (`[LANE-C+D]`) and `S-ORCHESTRATOR-AUTO-LOOP`
(`[LANE-B+C]`). None of them are claimable today. This is silently
blocking cross-lane secondary work.

**Recommended fix (operator decision):**

- **Option A (cheapest):** widen the applier regex to accept
  `[LANE-X(\+[A-Z])*]` and treat a claim with `lane=X` as valid if `X`
  appears anywhere in the bracket. Risk: two agents racing on the same
  cross-lane task; the first claim wins and the second sees
  `claimed-by-other`, which is the correct outcome.
- **Option B:** split cross-lane tasks into per-lane subtasks (e.g.
  `S-HYBRID-DASHBOARD-B` design + `S-HYBRID-DASHBOARD-D` impl). Cleaner
  ownership but requires TASKS.md edits.
- **Option C:** add a `claim_via=manual` carve-out for cross-lane tasks
  — operator marks done by hand after lane-agents coordinate via
  findings. Worst surface area, do not recommend.

**My recommendation: Option A** — minimal change, no TASKS.md churn,
preserves the "one applier, all writes" v9.3 invariant.

Because the queue rejected the claim, this iteration produced the design
artifact **without** claiming via the queue (the doc is a single-owner
file under `docs/`, so no Edit-tool race). I did not `mkdir
claims/S-HYBRID-DASHBOARD` either — that would bypass the queue, which
the v9.3 invariant explicitly forbids. The task line in TASKS.md
remains `[ ]` until the operator either (a) fixes the applier per Option
A and re-runs the claim, (b) splits per Option B, or (c) manually marks
the LANE-B half done.

## Next-step recommendations (concrete)

1. **Operator:** pick Option A/B/C above to unblock the cross-lane
   secondary tasks. Without a decision, three pieces of useful work
   stay un-claimable while their agents idle-heartbeat.
2. **LANE-D (FRONTEND):** read `docs/v9/v9-3-HYBRID-DASHBOARD.md` §5
   when ready; the lane-card edit in §5.2 is the smallest first step.
3. **LANE-C (BACKEND):** §4 is the BE contract; the SSE endpoint reuses
   `_parse_stream_tail`'s path logic. Two endpoints, ~80 LOC total.
4. **LANE-E (TEST):** §7 enumerates 9 tests; the BE unit set (5 tests)
   can be written against `httpx.AsyncClient` SSE without a real tmux.
5. **My next iteration (LANE-B):** unless feedback redirects, after
   PHASE-FLIP to BUILD I claim `P2-B` (v9-3-DESIGN.md per the prior
   plan) and reference this hybrid-dashboard doc as a sibling design.

## Test posture for this finding

No code changed; no test run needed per MISSION exit criterion #2 ("for
any code change"). The design doc compiles only against its own English.

## Tick metadata

- Compound bash count this iteration: **0** (per op feedback).
- python3 invocations: **0**.
- Queue calls: 1 auth/exchange, 1 task/claim (rejected). Subsequent
  queue calls (status/update, history/append) follow this finding.
- Files touched: `docs/v9/v9-3-HYBRID-DASHBOARD.md` (new),
  `findings/agent-f66a-B-P1-hybrid-dashboard-design-2026-05-19T23-34-22Z.md`
  (this file).
