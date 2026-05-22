# Mission — v9.4 UI self-observation dogfood

- **Slug:** v94-ui-dogfood
- **Started:** 2026-05-22T19-50Z
- **Lanes:** AUDIT, ARCHITECT, BACKEND, FRONTEND, TEST, META

## Scope

Harden v9.4 dashboard + clear v9.x backlog + scope v10; each lane validates one dashboard surface against disk.

## Exit criteria

Stimulus harness PASS (run_stale_lane_check + run_signal_fidelity_check) AND visibility.spec.ts green AND each of 6 lanes logged >=2 STATUS heartbeats AND terminal .mission-events token written.

## Phase progression

INIT → PHASE-PLAN → PHASE-CHALLENGE → PHASE-BUILD → PHASE-VERIFY → PHASE-RUN →
PHASE-HEAL → PHASE-OPERATOR-ACCEPTANCE → DRAINING → COMPLETE
