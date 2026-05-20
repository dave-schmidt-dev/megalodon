# Finding: MEDIUM — LANE-D (agent-07c5) likely stuck on P2-D for 41+ minutes; reinforces V-1

**Agent:** agent-0fa4
**Lane:** A (AUDIT)
**Phase:** PHASE-PLAN
**UTC:** 2026-05-20T00-39-07Z
**Severity:** MEDIUM (operator-actionable; not blocking but accumulating cost)
**Related to:** V-1 (phase-guard); restart-trap HIGH (not the same issue)

---

## TL;DR

LANE-D's `agent-07c5` appears to have hung mid-P2-D. Multiple independent
signals converge:

| Signal | Value | Stale by |
|---|---|---|
| `STATUS.md` last_utc | `2026-05-19T23-58Z` | 41+ minutes |
| Last LANE-D finding | `agent-07c5-D-P1-history-unreadable-fix-2026-05-19T23-21Z.md` | 1h 18m |
| `.fleet/D.next_tick.txt` mtime | `2026-05-19T22:22:52Z` (UTC; 18:22 EDT) | 2h 17m |

Three independent stale-signals → high-confidence diagnosis: **D's `/loop`
driver has stopped firing**. The 4 other active lanes (A, B, C, F) all have
`next_tick.txt` mtimes within the last 20 minutes; D is the sole outlier.

## Why the cross-signals matter

Single staleness signal (e.g. just `last_utc`) could mean "D is in a long task
and hasn't updated STATUS yet." Multiple uncorrelated signals stale by very
different amounts (41min / 78min / 137min) cannot be explained by a single
healthy long-running iteration — the iteration would have been completed long
before any signal aged to 41min.

| Hypothesis | Predicted signal pattern | Matches? |
|---|---|---|
| D is in a long task | All signals stale equally; would-be ~30-60min | ✗ (signals diverge) |
| D's REPL crashed at 22:22 UTC | All signals frozen at 22:22+ | ✗ (STATUS updated at 23:58Z) |
| D was alive at 23:58Z, hung afterward | last_utc=23:58Z; nothing since | ✓ matches |
| D is blocked on a permission prompt | No iteration; pipe-pane log continues | ✓ also matches |

The most likely scenarios are **#3 or #4**: D claimed P2-D at 23:58:13Z (its
last meaningful action), then either crashed or got stuck waiting for an
operator approval on the next tool call.

## Why I'm calling this MEDIUM not HIGH

- Not blocking phase-flip directly — operator can still flip whenever R-2
  ships.
- Not blocking other lanes' work — they're proceeding around D.
- D's P2-D is wrong-phase work anyway; if D were re-spawned tomorrow, the
  P2-D claim would be covered by the Grandfather rollout in synthesis v2.

What makes it MEDIUM (not LOW):
- The dashboard misrepresents fleet health: D shows "working: P2-D" but isn't.
  Operator decisions made on the dashboard's apparent state could be wrong.
- D holds the `P2-D` task in `[claimed: ...]` state, which prevents another
  lane from picking it up if needed. This is a soft lock with no live owner.

## Connection to V-1 (insight worth threading back)

My earlier V-1 finding framed the phase-guard's value as race-prevention. This
incident exposes a **second value: observability**.

If V-1's proposed phase-guard had been in place at 23:58:13Z, D's preemptive
`P2-D` claim would have been rejected with 403 `PHASE_MISMATCH`. D would then
either:
- Find no claimable PHASE-PLAN task and enter the `idle` loop (5-min ticks
  with idle findings — staleness visible within ~10 minutes), OR
- Exit cleanly and not hide its hang behind a "looks-busy" STATUS row.

The current state — `working: P2-D` for 41 minutes with no activity — is the
worst-of-both-worlds. The dashboard reads "healthy lane doing work" when the
truth is "dead lane sitting on a wrong-phase claim." **Idle ticks would have
surfaced this within 10 minutes; the wrong-phase claim hid it.**

Updating V-1's recommendation section to include this observability angle is
on AUDIT for the PHASE-2 `P2-A` follow-up.

## Recommendations

### R-D1 — Operator-side investigation (now)

Operator should peek at D's tmux pane (`tmux -S .fleet/tmux.sock attach -t
megalodon:LANE-D` or whatever the dashboard's "View terminal" affordance
provides). Three diagnostic targets:

1. Is the Claude TUI rendering a permission prompt? If so, approve/deny.
2. Is the REPL showing an error or empty prompt? If so, agent crashed.
3. Is the REPL responsive but mid-thought-stream? If so, agent is genuinely
   in a long task and signal divergence is misleading (unlikely given the
   3-signal correlation).

### R-D2 — Reclaim or release (depending on R-D1)

- If R-D1 shows D crashed: reclaim via dashboard. Per BUG-PHASE-INDICATOR-STUCK
  history, the reclaim control exists. Releasing the `P2-D` claim restores it
  to claimable state.
- If R-D1 shows D blocked on permission: approve the prompt; D should resume.
- If R-D1 shows D alive: ignore this finding; will self-correct.

### R-D3 — Not for this iteration; capture for `P2-F` (META mid-mission report)

LANE-F should track this incident as input to `P2-F` (META writes mid-mission
report: per-lane tick counts, dominant failure modes, surprising behaviors).
"Stuck lane on wrong-phase claim hidden from dashboard staleness check" is a
surprising behavior worth documenting.

### R-D4 — Endorses S-ORCHESTRATOR-AUTO-LOOP

The S-ORCHESTRATOR-AUTO-LOOP secondary task's description explicitly includes:

> "detect stuck claims (held > 10 min with no finding) and write to
> feedback/<LANE>.md asking what's blocking"

D has been stuck for 41 minutes. The orchestrator would have detected this at
minute 11 and pinged D via feedback/FRONTEND.md. The fact that AUDIT is doing
this manually is the operator's signal that the orchestrator is worth
prioritizing for PHASE-BUILD. (LANE-B+C own that task per TASKS.md.)

## Out of scope here

- **Not** reaching into D's claim directory or status row myself. The
  protocol's release primitive (`rm -rf claims/<id>`) is allowed but is the
  CLAIMING agent's responsibility. AUDIT does not unclaim other lanes' work.
- **Not** writing to `feedback/FRONTEND.md` — F-lane peer feedback is fine but
  D being stuck means the message wouldn't be read. The operator-facing
  finding path is the right channel.

## Cross-refs

- V-1 source: `findings/agent-0fa4-A-P1-protocol-violations-2026-05-20T00-02-07Z.md`
- Synthesis v2 (Grandfather + phase-guard): `findings/agent-f66a-B-P1-protocol-gaps-synthesis-v2-2026-05-20T00-14-08Z.md`
- D's last actually-fired finding: `findings/agent-07c5-D-P1-history-unreadable-fix-2026-05-19T23-21Z.md`
- D's wrong-phase claim record: `TASKS.md` line for P2-D (`[claimed: agent-07c5 @ 2026-05-19T23:58:13Z]`)

## Next-tick

Wake in 300s at 2026-05-20T00-44Z. Will re-check whether D's signals refresh
(self-recovery) or whether the staleness deepens.
