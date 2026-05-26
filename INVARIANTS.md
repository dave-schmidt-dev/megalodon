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

### INV-3 — The blocking CI gate covers every project (no chromium-board-only)
area: [".github/workflows/test.yml", "ui/tests/e2e/playwright.config.ts"]
gate_test: ui/tests/e2e/playwright.config.ts
threshold: 2
rationale: chromium-board-only gating hid CSRF routes + mission-status split twice.
