# Mission

**Mission ID:** `2026-05-16T17-30Z--megalodon-run2-make-it-work`
**Started:** *(set on first PHASE-FLIP event)*
**Status:** **ACTIVE** — auto-managed via `.mission-events`
**Deliverable date:** ~1-3 hours after worker start
**Protocol version:** **v8** (this mission is the first to run under v8 governance — `README.md` is the live v8 spec; `docs/v8-changeset.md` documents the v7→v8 deltas including Edit 22 PHASE-OPERATOR-ACCEPTANCE added post-run-1)

---

## What this run must produce

**Run-1 (the v8-generating run) shipped 6500 LOC of code but it doesn't work.** Run-2's job is **make it work, prove it works, get operator sign-off.** No more documented-broken deliverables.

### Concrete exit criteria

A worker MUST NOT claim COMPLETE until ALL of the following hold:

1. **Tests execute and pass.** `uv run --with pytest --with fastapi --with 'uvicorn[standard]' --with sse-starlette --with pyyaml --with httpx --with pytest-asyncio pytest ui/tests/unit ui/tests/integration -v` reports **0 SKIPPED, 0 FAILED**. Every test in `ui/tests/unit/test_protocol_primitives.py` and `ui/tests/integration/test_api_endpoints.py` actually runs assertions.
2. **UI renders cleanly in a real browser.** `python ui/server.py --mission-dir ui/tests/fixtures/fix-medium --port 8765` launches; navigation to `http://127.0.0.1:8765` returns the dashboard with **zero console errors**, all CSS+JS loaded (200 not 404), and renders 6 lane rows from fix-medium STATUS.md. **Playwright headless screenshot artifact** is committed to `findings/<agent>-RUN-ui-render-<UTC>.png` as proof.
3. **All 6 POST mutation endpoints are wired in UI.** Currently 2 of 6 (signal, reclaim) have UI buttons. The other 4 (inject-challenge, phase-flip, mission-status, inject-task) must have working forms with success+error states. Verified by Playwright e2e.
4. **SSE stream verified end-to-end.** A connected client receives at least one `status-change` event when an STATUS.md heartbeat fires in the mission dir. E2E test must demonstrate.
5. **Operator (orchestrator-Claude, then human-operator David) explicitly ACKs.** No auto-COMPLETE. See `## PHASE-OPERATOR-ACCEPTANCE` below.

### Out of scope for run-2

- Mobile viewport tiers (deferred — operator may inject as run-3)
- axe-core a11y (deferred)
- Auth/Origin/CSRF live exercise tests (deferred — middleware exists, code-review verified)
- New v8.x protocol changes beyond what's already in `README.md`

---

## Known starting state (pre-applied fixes by orchestrator)

The orchestrator pre-applied the following to save run-2 cycles:

- **`ui/server.py:1434` static-mount fix:** `app.mount("/static", ...)` (was `"/"` — caused all 404s on CSS/JS in run-1)
- **Run-1 archived** to `.archive/2026-05-16T17-06Z--megalodon-self-improvement-run1/` (1.05 MB, full snapshot including findings/claims/STATUS/TASKS/HISTORY/docs/ui)
- **Run-2 mission state reset:** fresh `findings/`, `claims/`, `.phase-flip-locks/`, `.scratch/`; `.mission-events` cleared (worker writes the INIT entry on first claim)

Workers should **NOT redo** the static-mount fix. Verify it's already correct, then move on.

---

## Lanes (same 6 as run-1, but tasks differ)

| Code | Lane | Run-2 stance | Primary output |
|---|---|---|---|
| A | **AUDIT** | Verify the v8 changeset is correctly reflected in README.md; audit run-2's RUN+HEAL behavior; produce v8.1 candidate doc if recurring failures suggest spec gaps | `docs/v8.1-candidate.md` (if needed) + RUN-acceptance review |
| B | **ARCHITECT** | Define `megalodon_ui` package structure + `make_app(mission_dir=)` factory contract; spec the 4 missing POST endpoint UI wirings | `ui/SPEC-v2.md` (incremental, not full rewrite) + `ui/adrs/ADR-006-make_app-factory.md` |
| C | **BACKEND** | Build `megalodon_ui/` package with `primitives.py` + `server.py:make_app(mission_dir=)`; fix all SSE payload shape gaps from run-1 P4-C→D V2; wire all 6 mutation endpoints' server-side | `megalodon_ui/__init__.py` + `megalodon_ui/primitives.py` + `megalodon_ui/server.py` + server-side endpoint completeness |
| D | **FRONTEND** | Wire the 4 unwired POST endpoints in UI (inject-challenge, phase-flip, mission-status, inject-task); verify CSRF + SSE end-to-end work in browser via Playwright headless | `ui/static/pages/*.js` additions + Playwright-verified flows |
| E | **TEST** | Make ALL tests in `ui/tests/{unit,integration}` execute (no SKIPs) and pass; add e2e Playwright that actually launches headless browser; run+screenshot UI as part of test output; produce green test report | `ui/tests/` updates + `ui/tests/test-report-<UTC>.txt` |
| F | **META** | Observe run-2 against run-1 baseline; confirm PHASE-RUN+HEAL works as designed; confirm PHASE-OPERATOR-ACCEPTANCE prevents auto-COMPLETE; track HEAL cycles per task | `findings/<agent>-F-RUN2-CAPSTONE-<UTC>.md` |

---

## Cadence

3 minutes (`/loop 3m`). BUILD lanes may self-extend to 10m for deep coding per protocol; declare in STATUS notes.

---

## Phase mechanics (v8 — load-bearing)

### Phase progression

```
INIT → PHASE-PLAN → PHASE-CHALLENGE → PHASE-BUILD → PHASE-VERIFY
                                                            ↓
                                              PHASE-RUN ↔ PHASE-HEAL
                                                            ↓
                                              PHASE-OPERATOR-ACCEPTANCE
                                                            ↓
                                                       DRAINING → COMPLETE
```

### Source of truth: `.mission-events`

Append-only log file at project root. Each line:
```
<UTC> <FROM>-><TO> by <agent-id> — <reason>
```
Last line's `<TO>` is current phase. Workers read `.mission-events` directly.

### RULE 11 — distributed atomic phase-flip (unchanged from v7-candidate)

At tick start, every worker:
1. Read current phase from `.mission-events` (last line)
2. If current phase has named tasks remaining, scan TASKS.md
3. **Completion test:** every named task `[done: ...]` AND every `claims/<task-id>/done` exists AND no lane has `working: <task>` with Last UTC <60s old
4. If completion test passes, try `mkdir .phase-flip-locks/<from>-to-<to>`:
   - **Exit 0:** append flip event to `.mission-events`, heartbeat with `PHASE-FLIP <from>→<to>`, continue
   - **Exit nonzero:** skip; next tick reads new phase naturally
5. **NEW Step 4a (v8) — stuck-flip recovery:** if lock exists but `.mission-events` last line is still the OLD phase >60s after lock-acquire, next worker scans, verifies lock-holder STATUS Last UTC >60s, `rm -rf` the lock, re-runs RULE 11. Handles the "lock-held-before-event-appended" race.

### PHASE-RUN (NEW in v8) — execution verification

PHASE-VERIFY catches defects by reading code; PHASE-RUN catches defects by executing it. Both required.

**Auto-flip from PHASE-VERIFY** when all `P4-*` tasks done.

PHASE-RUN tasks (auto-claimed by pairing matrix, NO self-verification):

- `P5-RUN-PRIMITIVES` — TEST runs `uv run pytest ui/tests/unit` against the new megalodon_ui.primitives. Failure → PHASE-HEAL with `REPAIR-PRIMITIVES-<n>`.
- `P5-RUN-INTEGRATION` — TEST runs `uv run pytest ui/tests/integration`. Failure → PHASE-HEAL.
- `P5-RUN-UI-RENDER` — FRONTEND launches server + headless browser via Playwright, screenshots, asserts 0 console errors and 6 lane rows visible. Failure → PHASE-HEAL.
- `P5-RUN-MUTATIONS-E2E` — TEST drives all 6 POST endpoints via Playwright, verifies success + error states. Failure → PHASE-HEAL.

### PHASE-HEAL (NEW in v8) — iterative repair

Auto-loops when any `P5-RUN-*` fails:
1. Failing run's owner injects `[REPAIR-<task-id>-<n>]` task in TASKS.md with failure transcript embedded
2. Relevant BUILD lane re-opens (state: `working: REPAIR-*`), fixes, re-claims `P5-RUN-*` for re-execution
3. **Budget per task: 3 HEAL cycles OR 30-min wall-clock.** Exceed → set state to `BLOCKED-DEGRADED` for operator triage. No infinite loops.

PHASE-HEAL → PHASE-RUN re-entry until all P5-RUN-* exit with status `EXEC-PASS`.

### PHASE-OPERATOR-ACCEPTANCE (NEW in v8, post-run-1) — mandatory human/orchestrator gate

When all `P5-RUN-*` are `EXEC-PASS`, mission flips to **PHASE-OPERATOR-ACCEPTANCE**. **NO AUTO-FLIP TO DRAINING.**

Phase-entry actions (any worker may execute, idempotent):
1. Inject task `[OPERATOR-ACCEPTANCE-REQUEST]` into TASKS.md `## OPERATOR-ACCEPTANCE` section with:
   - Deliverable summary (links to findings + ui/ artifacts)
   - Test-run transcripts (pytest + Playwright outputs)
   - UI screenshot artifacts (paths to `.png` files)
   - Outstanding issues / known gaps
2. All lanes set state to `idle | awaiting OPERATOR-ACK`
3. **Halt and wait.** Workers continue heartbeating but claim NO new tasks.

Phase-exit conditions (only one of):
- **`[OPERATOR-ACK]`** by orchestrator or operator → flip to DRAINING → COMPLETE
- **`[OPERATOR-REJECT]` + `[REPAIR-<n>]`** task injected → re-enter PHASE-HEAL with operator-specified fix list
- **`[OPERATOR-DEGRADED-ACK]`** — operator explicitly accepts degraded delivery with documented caveats → flip to DRAINING with `severity-degraded: true` flag

**The orchestrator (orchestrator-Claude session, distinct from worker sessions) is the gate.** Workers do NOT self-flip past PHASE-OPERATOR-ACCEPTANCE.

### DRAINING + COMPLETE

Unchanged from v7. Workers write LANE-CAPSTONEs, META writes RUN2-CAPSTONE. COMPLETE flips when: all lanes idle + META CAPSTONE on disk + HISTORY quiet >10min.

### BLOCKED vetoes auto-flip (unchanged)

Any lane setting state `BLOCKED` freezes auto-flip.

---

## Useful pointers

- **Protocol doc:** `README.md` (now v8 — read once on first tick)
- **v7→v8 changeset:** `docs/v8-changeset.md` (21 edits + Edit 22 post-run-1)
- **Run-1 archive:** `.archive/2026-05-16T17-06Z--megalodon-self-improvement-run1/` (READ-ONLY; reference for what was built)
- **Run-1 capstone:** `.archive/2026-05-16T17-06Z--megalodon-self-improvement-run1/findings/agent-5f87-F-FINAL-RUN-CAPSTONE-2026-05-16T16-31Z.md` (META's v8 evidence)
- **Run-1 RR-1 finding:** `.archive/2026-05-16T17-06Z--megalodon-self-improvement-run1/findings/agent-8318-CROSS-RR1-runtest-2026-05-16T16-54Z.md` (BACKEND's runtest transcript — the only execution-verified work from run-1)

---

## Hard constraints

- Workers **cannot `git`, `curl`, `wget`, `npm`, `pip`, `brew`, `sudo`, `chmod`, `chown`, `ssh`, `scp`**. The operator commits manually after the run.
- `.archive/` is READ-ONLY (existing deny). Workers reference run-1 but don't write there.
- BUILD lanes write to `megalodon_ui/`, `ui/`, `docs/`. TEST writes to `ui/tests/`.
- No `git push`, no merge-to-main, no PRs. Operator decides keep-worthiness post-run.
- **No self-verification.** PHASE-VERIFY and PHASE-RUN pairings are explicit.
- **Tests SKIP-due-to-ImportError is a FAILURE, not a pass.** If `from megalodon_ui import primitives` raises ImportError, the run is incomplete.

---

## Deliverable

On operator-ACK, you (the operator) will have:

1. **`megalodon_ui/`** — proper Python package with `primitives.py` + `server.py:make_app(mission_dir=)`
2. **`ui/server.py`** — patched (already done by orchestrator: static-mount + run-1 RR-1 patches)
3. **`ui/static/`** — all 6 POST endpoints wired in UI
4. **`ui/tests/`** — passing pytest suite (0 SKIPPED, 0 FAILED) + working Playwright e2e
5. **`findings/<agent>-RUN-ui-render-<UTC>.png`** — Playwright screenshot artifact
6. **`findings/<agent>-F-RUN2-CAPSTONE-<UTC>.md`** — META's run-2 retrospective
7. **All artifacts archived** under `.archive/<UTC>--megalodon-run2-make-it-work/` post-COMPLETE

---

## Pre-deployment checklist

- [x] Run-1 archived (`.archive/2026-05-16T17-06Z--megalodon-self-improvement-run1/`)
- [x] Run-1 known fixes pre-applied (static-mount in ui/server.py:1434)
- [x] README.md promoted to v8 (this mission is v8.0)
- [x] MISSION.md (this file) defines run-2 scope + new phases + OPERATOR-ACCEPTANCE gate
- [x] TASKS.md seeded with run-2 P1-P4 + P5-RUN-* + secondary CROSS tasks
- [x] STATUS.md reset to 6 unclaimed rows
- [x] HISTORY.md reset (new run log)
- [x] `.mission-events` empty (worker writes first INIT entry on first claim)
- [x] `.phase-flip-locks/`, `findings/`, `claims/`, `.scratch/` reset
- [ ] Start 6 Claude sessions with the launch one-liner (see operator-handed prompt)
