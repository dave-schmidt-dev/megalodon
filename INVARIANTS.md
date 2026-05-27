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

### INV-4 — The governor Bash policy denies the red-team escape matrix
area: ["megalodon_ui/governor/policy.py", "megalodon_ui/governor/hook.py"]
gate_test: scripts/tests/test_governor_redteam.py
threshold: 2
rationale: the governor is the only sandbox constraining the autonomous fleet outside
  its target. ~33 live escapes (arbitrary code exec + out-of-scope write/delete) shipped
  with ZERO tests (found 2026-05-27): the Bash engine is allow-by-default + deny-matched,
  so denylist coverage regresses SILENTLY as new escape spellings appear. The matrix is
  the executable spec; DENY_CASES must deny, ALLOW_OK must allow, STILL_DENY/GATE_CMDS are
  regression controls. Known un-converging residuals (destructive git porcelain, arbitrary
  script-head exec) are tracked as xfail(strict=True) in the same file — the converging
  fix (Bash-head allowlist) is a flagged v10 operator decision.

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
