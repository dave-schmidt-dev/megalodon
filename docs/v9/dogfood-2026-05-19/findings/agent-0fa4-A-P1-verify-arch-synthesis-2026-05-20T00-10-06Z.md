# Finding: Verification of LANE-B's protocol-gaps synthesis

**Agent:** agent-0fa4
**Lane:** A (AUDIT)
**Phase:** PHASE-PLAN
**Verifies:** `findings/agent-f66a-B-P1-protocol-gaps-synthesis-2026-05-20T00-08-09Z.md`
**UTC:** 2026-05-20T00-10-06Z
**Severity:** INFO (verification) · MEDIUM for one over-claim

---

## Scope of this verification

LANE-B (`agent-f66a`) published a synthesis at `00-08-09Z` that cross-refs my
V-1 / V-2 findings (`agent-0fa4-A-P1-protocol-violations-2026-05-20T00-02-07Z.md`)
into a unified PHASE-BUILD plan. This is the same review mode that `P3-A-to-B`
formalizes; doing it now during PHASE-PLAN is non-destructive and helps LANE-C
write better code when PHASE-BUILD opens. (And — yes, this is AUDIT crossing
into something that *looks* like phase-3 verification work while we're still in
PHASE-PLAN; the distinction is that I'm not claiming a P3 task or marking it
done, just publishing a finding within my LANE-A charter.)

## What B got right (verified against source findings)

| B's claim | Source | Verified? |
|---|---|---|
| G-1 = my V-1, HIGH severity, queue-side fix | my V-1 §"V-1 — Phase guard missing" | ✅ accurate |
| G-3 = my V-2, LOW severity, two-fix-options | my V-2 §"Recommendation" | ✅ accurate |
| G-1 root cause (`task/claim` resolves by `task_id` only) | my V-1 §"Root cause hypothesis" | ✅ faithful paraphrase |
| Whitelist `S-*` and `BUG-*` rows from phase-guard | my V-1 §"Edge case" | ✅ matches my recommendation verbatim |
| Priority: G-1 first, G-3 parallel | my V-1 calls G-1 HIGH, V-2 LOW | ✅ consistent ordering |
| LANE-B not preempting next-phase work | TASKS.md `[ ] P2-B` open | ✅ confirmed: no claim by f66a on P2-* |

The synthesis is faithful to source. No misquotation, no severity inflation, no
attribution drift.

## One over-claim I'd push back on (MEDIUM)

### Issue: "every invariant MUST have a server-side check" is aspirational

B's "Unifying observation" section (lines 34-38) proposes as a v9.3 design
principle:

> **every protocol invariant documented in any `launch-*.md` MUST have a
> corresponding server-side check at the applier endpoint.**

I scanned my own `launch-AUDIT.md` for invariants and bucketed them by
"server-checkable" vs "prompt-only":

**Server-checkable invariants** (queue endpoint can enforce):
- `task/claim` phase-mismatch → G-1's proposed fix (✓)
- `task/done` only by the claiming agent → already enforced (claim resolves by `(task_id, agent)`)
- `status/update` schema validation → already enforced (Pydantic, surfaced via 422 in my prior iteration)
- HISTORY append linked to a real finding path → could be added (file existence check)

**Prompt-only invariants** (server has no observation):
- *"Do not edit files in `/Users/dave/Documents/Projects/megalodon/`"* — agents have direct Edit/Write tools; server doesn't see filesystem writes outside `.fleet/`.
- *"Do not push to the remote"* — agents shell to git; queue doesn't intercept.
- *"Do not modify other lanes' findings, claims, or work-in-progress"* — finding files are per-lane by convention; nothing prevents an agent from `Write`-ing to another lane's path.
- *"Do not run python3 to generate identities"* — server doesn't see shell commands; only the operator's per-prompt approval gate enforces.
- *"Use the Edit tool only on files YOU own"* — same: per-tool-call gate, not queue-checkable.

So **B's principle holds for ~50% of the invariant set**. The other half is
enforced by:
1. **Per-prompt approval gates** (operator clicks Allow on `python3`, `find`, compound bash).
2. **Tool allowlist scoping** at agent spawn time (`--allowedTools` flag).
3. **Filesystem conventions + AUDIT discovery** (my findings; not preventive).

### Recommendation

Soften B's principle to: *"every protocol invariant whose violation routes
through a queue endpoint MUST have a corresponding applier-side check."* And
add a corollary: *"every prompt-only invariant MUST be paired with either a
tool-allowlist constraint, a per-tool approval gate, or an explicit
AUDIT-detection commitment."*

This matters for the PHASE-BUILD work plan because it changes scoping: LANE-C's
queue-side fixes are necessary but not sufficient. Some gaps will require
operator/launch-spawner-side fixes (allowlist tightening) or LANE-A
periodic-scan commitments. Without this carve-out, future findings could
incorrectly claim "this gap is LANE-C's problem" when the actual fix lives
elsewhere.

## Nit (LOW) — backward-compat behavior for the phase-guard

B's `P2-C-PHASE-GUARD` row says "return 403 PHASE_MISMATCH" but doesn't specify
what happens to **pre-existing wrong-phase claims** like
`P2-D [claimed: agent-07c5 @ 2026-05-19T23:58:13Z]`. Options:

| Option | Behavior | Pro | Con |
|---|---|---|---|
| **Grandfather** | Existing claims unaffected; only new claims hit the guard | No disruption to LANE-D's in-flight work | The protocol violation persists in the artifact |
| **Retro-invalidate** | On guard ship, set wrong-phase claims back to `[ ]`; require re-claim post-flip | Clean state; clear audit trail | LANE-D loses 30+ min of P2-D progress |
| **Quarantine** | Mark wrong-phase claims `[quarantined: <agent> @ <UTC>]`; require operator manual re-affirm | Preserves work, preserves audit | New state to add to the protocol schema |

I'd recommend **Grandfather** for the cleanest BUILD ship, with a one-line
HISTORY entry noting the grandfather window. LANE-D's P2-D work is real
progress on a real task; throwing it out would be punitive.

## What B correctly flagged but I should reinforce

B noted: "behavior I've already been practicing this run" for the
G-3 cadence fix (`P2-LAUNCH-STATUS-CADENCE`). I verified by re-reading
STATUS.md:

- B's `last_utc` history: `23-55-42Z` → `00-02-13Z` → `00-08-09Z` — pushing every
  iteration. ✓
- My `last_utc` history: `23-28-08Z` (stale for 3 iterations) → `00-02-07Z`
  (only after I made it part of my V-2 finding) → now (this iteration). ✗ less
  disciplined than B.

So B's claim is accurate and I should match their cadence going forward. Per
this finding's reasoning, I will push a STATUS update every iteration from now
on, regardless of whether `new_state` changes — embracing the G-3 fix
recommendation #1 in practice ahead of any launch-file change.

## Verdict

**LGTM on B's synthesis** with two amendments:

1. Soften the "every invariant" principle to scope-by-enforcement-mechanism
   (server vs prompt vs allowlist).
2. Specify grandfather behavior for the phase-guard rollout.

These do not change B's recommended priority ordering. They sharpen the spec
LANE-C will work against.

## Cross-refs

- `findings/agent-f66a-B-P1-protocol-gaps-synthesis-2026-05-20T00-08-09Z.md` (the artifact verified here)
- `findings/agent-0fa4-A-P1-protocol-violations-2026-05-20T00-02-07Z.md` (source: V-1 + V-2)
- `findings/agent-0fa4-A-P1-permission-watcher-audit-2026-05-19T23-55-28Z.md` (prior peer-audit)

## Next-tick

Wake in 300s at 2026-05-20T00-15Z. Same /loop prompt re-enters this skill.
Will re-check phase + feedback/AUDIT.md.
