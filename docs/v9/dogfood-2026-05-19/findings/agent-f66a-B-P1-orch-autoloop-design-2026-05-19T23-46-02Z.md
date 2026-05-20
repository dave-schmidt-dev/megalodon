# LANE-B ARCHITECT — Orchestrator auto-loop design (PHASE-PLAN proactive)

**Agent:** agent-f66a · **Lane:** B · **Phase:** PHASE-PLAN
**UTC:** 2026-05-19T23-46-02Z · **Severity:** INFO
**Refs:** task `S-ORCHESTRATOR-AUTO-LOOP` (cross-lane LANE-B+C);
prior plan `findings/agent-f66a-B-P1-arch-plan-2026-05-19T20-06-30Z.md`;
sibling finding `findings/agent-f66a-B-P1-hybrid-dashboard-design-2026-05-19T23-34-22Z.md`
(same cross-lane-claim pattern).

## Operator feedback acknowledgement

Acknowledging `feedback/ARCHITECT.md` op-msg `2026-05-19T22:37:00Z`
(compound-bash ban). This iteration:

- **Compound bash invocations: 0.**
- **python3 invocations: 0.**
- Self-snapshot used the corrected primitives only: `Read` tool +
  single `date -u +...` + (planned) single localhost `curl` per
  queue call.

## Summary

Wrote `docs/v9/v9-3-ORCHESTRATOR-AUTO-LOOP.md` (LANE-B half of the
cross-lane task `S-ORCHESTRATOR-AUTO-LOOP`). The doc specifies the
protocol contract for a 7th `/loop`-driven Claude Code session
("LANE-G — Guardian") that watches the fleet on the operator's behalf:

1. **Scope (§1)** — narrow surface: auto-approve permission prompts
   against a safelist; nudge stuck lanes via `feedback/<LANE>.md`;
   PushNotification the operator on significant events. Explicitly
   forbidden: task/phase/mission mutations, source-code edits, signals,
   non-localhost network ops.
2. **Tick structure (§2)** — 30s cadence (beats the 5-min cache window
   and is well under operator eyeball latency). ≤ 8 BE calls per tick.
3. **Safelist policy (§3)** — allowlist of ~25 regex patterns
   (read-only file ops, read-only git, v9 protocol primitives, `date`),
   denylist that always wins, four-factor approval condition
   (parsed-command non-null AND safelist-match AND no denylist match
   AND lane-state in `working|initialized`). Rate limit: 12/lane/min,
   30 fleet-wide/min.
4. **Stuck-claim detection (§4)** — 10-min threshold, de-dup at half the
   threshold, escalation to PushNotification after 3 nudges.
5. **PushNotification triggers (§5)** — phase boundaries, task
   completions, stuck escalations, unrecognized prompts, mission status
   changes. No detected-text in notification bodies (potential
   secrets-adjacency).
6. **Security model (§6)** — guardian session has a different
   `--allowedTools` set than worker lanes; singleton lock; operator
   kill switch via `touch .fleet/GUARDIAN.disabled`.
7. **BE contract additions (§8)** — two new endpoints for LANE-C to
   implement, with exact YAML matching `api-contract.md` style:
   `GET /api/v1/permission_prompts` and
   `POST /api/v1/permission_prompts/{lane}/respond`.
8. **Test plan (§9)** — 5 unit tests; all fit under the mission's
   existing exit-criterion `uv run pytest ...` command (no new test
   infra).
9. **Rollout (§11)** — phased: BE endpoints ship in v9.3; guardian in
   `--dry-run` in v9.3.1; live auto-approval in v9.3.2;
   operator-tunable safelist in v9.4.

The design unblocks LANE-C to start the BE endpoints (§8) the moment
PHASE-BUILD opens, and it explicitly converges with the
hybrid-dashboard design (the two share the `/api/v1/permission_prompts`
endpoint — guardian = policy engine, dashboard = operator UI).

## Evidence

- New file: `docs/v9/v9-3-ORCHESTRATOR-AUTO-LOOP.md` (~280 lines,
  §1–§12 + frontmatter).
- Cross-checked existing BE surface — confirmed
  `/api/v1/permission_prompts` is referenced by FE
  (`ui/static/pages/dashboard.js:649,656,669`) but has **no
  corresponding `@app.get` in `megalodon_ui/server.py`** (verified via
  single `grep -n "@app.get|@app.post" megalodon_ui/server.py`).
  The endpoint is a known v9.3 BE gap that LANE-C will close as part
  of `BUG-PROMPT-FLICKER` or a sibling task. The design's §8 spec is
  the LANE-B contract that LANE-C's implementation must satisfy.
- Cross-checked permission-detection — `permission_watcher.py:30-83`
  already exposes `scan_all(lane_shorts)` + `clear_lane(lane)` +
  5-second suppression window. The §8 endpoints wire these to the
  HTTP surface with no new detection logic required.
- Cross-checked auth surface — the guardian reuses the existing
  `POST /api/v1/auth/exchange` cookie pattern (no new auth path).
- Lane letter `G` chosen to preserve the single-letter regex
  invariant (`^[A-Z]{1,4}$`) used in path validation, e.g.
  `_parse_stream_tail` and the v9.3 queue applier.

## Protocol gap status (carried forward)

The cross-lane claim bug surfaced in my prior 23:34 finding
(applier returns `matches=0` for `[LANE-B+D]` and `[LANE-B+C]`) **is
unchanged**. This iteration produced the design artifact without
attempting a queue claim (the v9.3 invariant forbids
`mkdir claims/<id>` bypass, and the doc is a single-owner file under
`docs/v9/` so no Edit-tool race). The applier fix recommendation
(Option A: widen the regex to accept `[LANE-X(\+[A-Z])*]` and treat a
claim with `lane=X` as valid if `X` appears anywhere in the bracket)
still stands.

## Next-step recommendations (concrete)

1. **Operator:** decision on the cross-lane-claim Option A/B/C from
   the 23:34 finding still pending. Without it, three secondary tasks
   remain un-claimable through the queue: `S-HYBRID-DASHBOARD`,
   `S-LIVE-ACTIVITY`, `S-ORCHESTRATOR-AUTO-LOOP`.
2. **LANE-C:** when PHASE-BUILD opens, the two `/api/v1/permission_prompts`
   endpoints in §8 are the smallest first slice for
   `S-ORCHESTRATOR-AUTO-LOOP`. Estimated ~120 LOC; no new dependencies;
   reuses `PermissionWatcher.scan_all()` and `clear_lane()`.
3. **LANE-E:** the 5 unit tests in §9 are writable today against a
   fixture `PermissionWatcher` — they don't depend on a running tmux.
4. **My next iteration (LANE-B):** continue idle-heartbeat or, if
   PHASE-FLIP to BUILD happens, claim `P2-B` (v9-3-DESIGN.md). That
   doc will reference both `v9-3-HYBRID-DASHBOARD.md` and
   `v9-3-ORCHESTRATOR-AUTO-LOOP.md` as the two concrete v9.3
   feature designs.

## Test posture for this finding

No code changed; no test run needed per MISSION exit criterion #2
("for any code change"). Design doc compiles only against its own
English. The §9 test plan is for LANE-E to execute later, not for me
to execute now.

## Tick metadata

- Compound bash count this iteration: **0** (per op feedback).
- python3 invocations: **0**.
- Queue calls planned this iteration: 1 auth/exchange,
  1 status/update, 1 history/append. No task/claim (cross-lane bug
  blocks it; documented in 23:34 finding).
- Files touched: `docs/v9/v9-3-ORCHESTRATOR-AUTO-LOOP.md` (new),
  `findings/agent-f66a-B-P1-orch-autoloop-design-2026-05-19T23-46-02Z.md`
  (this file), `.fleet/B.next_tick.txt` (S-NEXT-TICK-VISIBILITY).
