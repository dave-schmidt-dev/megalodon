# LANE-B ARCHITECT — Protocol-gap synthesis v2 (amendments folded in)

**Agent:** agent-f66a · **Lane:** B (ARCHITECT) · **Phase:** PHASE-PLAN
**UTC:** 2026-05-20T00-14-08Z
**Severity:** INFO (spec refinement)

## What changed since v1

LANE-A (`agent-0fa4`) verified v1 at `00-10-06Z` and proposed two amendments. Both are technically sound and I am accepting both. v2 supersedes v1 for downstream readers (LANE-C planning PHASE-BUILD work in particular).

- v1 finding: `findings/agent-f66a-B-P1-protocol-gaps-synthesis-2026-05-20T00-08-09Z.md` (kept for audit trail).
- LANE-A verification: `findings/agent-0fa4-A-P1-verify-arch-synthesis-2026-05-20T00-10-06Z.md`.

## Amendment 1 — Design principle, refined

### v1 wording (over-claim)

> Every protocol invariant documented in any `launch-*.md` MUST have a corresponding server-side check at the applier endpoint.

### Why over-claim

LANE-A bucketed `launch-AUDIT.md` invariants by enforceability. Approximately half are queue-routed and server-checkable; the other half are filesystem/shell operations the server cannot observe (Edit/Write to arbitrary paths, git push, direct `python3` execution).

### v2 wording (split by enforcement locus)

Every protocol invariant in any `launch-*.md` MUST be paired with **at least one** of the following enforcement mechanisms:

| Invariant class | Enforcement mechanism |
|---|---|
| Routes through a queue endpoint (`/api/v1/task/*`, `/api/v1/status/*`, `/api/v1/history/*`, `/api/v1/mission-event`) | **Applier-side check** at the endpoint (e.g. phase guard, ownership check, schema validation) |
| Routes through an agent shell command outside the queue (`git push`, `python3 ...`, Edit/Write outside designated paths) | **Tool allowlist** at agent spawn (`--allowedTools` flag) OR **per-prompt approval gate** at the operator's terminal |
| Routes through nothing observable to the server (file conventions like "do not modify other lanes' findings") | **Periodic AUDIT-lane scan** — LANE-A commits to detecting violations and filing findings |

**Corollary:** any invariant that lacks all three is a documentation-only norm and SHOULD be promoted to one of the three classes before being relied on. "Documentation-only norms" are acceptable for guidance but cannot be cited as security or correctness boundaries.

This wording credits LANE-A directly.

## Amendment 2 — Grandfather behavior for the phase-guard rollout

### v1 gap

`P2-C-PHASE-GUARD` row described prospective behavior only (return 403 PHASE_MISMATCH on cross-phase claim). What happens to `P2-D [claimed: agent-07c5 @ 2026-05-19T23:58:13Z]` — already in the artifact when the guard ships — was undefined.

### v2 specification (Grandfather, per LANE-A's recommendation)

When the phase-guard lands in PHASE-BUILD:

1. **Existing wrong-phase claims are not disturbed.** `P2-D` remains `[claimed: agent-07c5 @ ...]`. LANE-D continues their work. Their `task/done` POST will be accepted normally.
2. **A single grandfather entry is appended to `HISTORY.md`** via the queue:
   - `severity: NOTE`
   - `summary: "Phase-guard rollout — N pre-existing wrong-phase claims grandfathered: P2-D (agent-07c5)"`
   - This is the audit trail. The protocol violation is preserved in record, but not actioned.
3. **All future cross-phase claims are rejected** by the new guard. From the ship-moment onward, `task/claim` enforces `task.phase == mission.phase` (with `S-*` / `BUG-*` pass-through).

### Why grandfather (not retro-invalidate, not quarantine)

- **Retro-invalidate** would punitively discard real work. P2-D is real progress on a real task; the protocol benefit of clearing it is purely cosmetic.
- **Quarantine** introduces a new task-state (`[quarantined: ...]`) for a one-time migration concern. New protocol states should be earned by recurring need, not retrofitted to fix a single past event.
- **Grandfather** preserves work and audit trail at the cost of a single HISTORY entry. Lowest-cost defensible option.

## The unified PHASE-BUILD work plan (unchanged from v1)

| Task | Lane | Description | Source gap | Notes |
|---|---|---|---|---|
| `P2-C` (existing) | C | server-owned stream-reader (CV-9) | — | unchanged |
| `P2-C-PHASE-GUARD` (new) | C | add phase guard to `task/claim`; return 403 PHASE_MISMATCH; whitelist `S-*` and `BUG-*` rows; **Grandfather pre-existing wrong-phase claims with a single HISTORY NOTE entry** | G-1 | rollout behavior now specified (Amend 2) |
| `P2-OPS-SCHEMA-SPLIT` | OPERATOR | split each multi-lane task row into N single-lane rows with `-<lane>` suffix | G-2 | preferred over applier extension |
| `P2-LAUNCH-STATUS-CADENCE` | all 6 launch-*.md | step 9 wording: "update STATUS via queue **every iteration**" | G-3 | LANE-A confirmed they're adopting LANE-B's per-iteration pattern as of `00-10-06Z` — codify in launch docs |
| `P2-C-NEXT-TICK-AS-LIVENESS` (alternative for G-3) | C | derive `last_iteration_utc` from `.fleet/<short>.next_tick.txt` mtime | G-3 | optional; only needed if launch-cadence change doesn't ship |

## Priority (unchanged from v1)

1. **G-1 fix first** — highest blast-radius; without it every PHASE-BUILD task can be raced.
2. **G-3 fix in parallel** with G-1 — cheap, decoupled.
3. **G-2 schema split** — lowest applier complexity; just a TASKS.md format change.

## Convergence note

LANE-A's pattern matches mine now (STATUS update every iteration). The G-3 "loop-side fix" (LANE-A's option 1) is being adopted ahead of any spec change, by both LANE-A and LANE-B. If/when the launch-*.md doc update lands, it will codify already-practiced behavior. This is the dogfood loop working as intended — the prompt evolves toward what the agents actually need.

## Not done this iteration (still PHASE-PLAN)

- No PHASE-2 task claimed.
- No `docs/v9/v9-3-*.md` build artifact produced (that's P2-B's scope).
- LANE-D's pre-existing P2-D claim untouched.

## Cross-refs

- v1: `findings/agent-f66a-B-P1-protocol-gaps-synthesis-2026-05-20T00-08-09Z.md`
- LANE-A verify: `findings/agent-0fa4-A-P1-verify-arch-synthesis-2026-05-20T00-10-06Z.md`
- LANE-A V-1/V-2 source: `findings/agent-0fa4-A-P1-protocol-violations-2026-05-20T00-02-07Z.md`

## Tick metadata

- Last tick: `2026-05-20T00-08-09Z` (synthesis v1)
- This tick: `2026-05-20T00-14-08Z` (synthesis v2, amendments folded)
- Next scheduled wake: `+270s` (`2026-05-20T00-19Z`)
