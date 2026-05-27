# Invariants — megalodon

> System contract. The harvest tool reads `area:` globs to map HISTORY bug entries
> to invariants. Per-project convention (commit prefix, invariant refs) is declared
> in this project's README, not globally.

### INV-1 — All mutation endpoints server-enforce auth → CSRF → control-mode
area: ["megalodon_ui/server.py", "ui/tests/e2e/test_orchestrator_actions.spec.ts"]
gate_test: ui/tests/integration/test_csrf_canonical_routes.py
threshold: 2
rationale: control-mode shipped client-only ("REAL" was false); CSRF parity gap recurred R1→R3.

### INV-2 — Single source of truth per state field (read == write file)
area: ["megalodon_ui/server.py", "megalodon_ui/narrator/board_state.py"]
gate_test: ui/tests/integration/test_state_source_of_truth.py
threshold: 2
rationale: mission-status writes README.md but UI reads MISSION.md (found this session); STATUS/TASKS staleness.

## Retired

### INV-3 — The blocking CI gate covers every project (no chromium-board-only)
retired: 2026-05-27
reason: CI removed entirely (operator decision — the value sought is push-time
  visibility on the local machine, which a remote runner cannot provide; cost was
  moot on a public repo). Megalodon's CI never gated the fleet's autonomous output
  (that lands in the target repo, validated by `target.gates` + the target's own
  CI); it only gated megalodon's own source. The coverage concern INV-3 encoded
  (no project-gap hiding regressions) now belongs to the local gate — to be
  re-expressed as a local-gate invariant once that gate is built.
former_rationale: chromium-board-only gating hid CSRF routes + mission-status split twice.
