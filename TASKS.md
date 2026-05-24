> **Plan (narrator summary board — PHASES 1–4 COMPLETE, on origin/main 2026-05-24):** Board is the default fleet view at `/`; `grid.js` deleted; narrator wired into the lifespan. Full gate green: 961 Python passed / 34 skipped / 3 xfailed; 12 isolated (`--forked`); Playwright 159 passed / 9 skipped. Each task ran implementer→spec-review→quality-review. Phase commits: P1 `ef4ea18`, P2 `2d7211e`, P3 `41d3984`, P4 `19b1eb1`+`1b460bc`+fixes. `~/Documents/Projects/.plans/megalodon/narrator-summary-board-2026-05-23.md` · tasks: `~/Documents/Projects/.plans/megalodon/narrator-summary-board-2026-05-23-tasks.md` · spec: `docs/superpowers/specs/2026-05-23-narrator-summary-board-design.md`.
> **Follow-ups (deferred, not blocking):** narrator-phrase-on-"Last" (OQ1 — needs prompt re-validation; deferred); CR-4 task-blocked pill; optional staleness modal; one WebKit send-debounce skip in `test_lane_detail:130` (un-root-caused WebKit timing artifact); Phase 5 (persistent sessions + smart auto-open) designed but not built (`docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md`).
>
> **Active plan (tool-surface hardening — IMPLEMENTED + PUSHED + GATE-VALIDATED 2026-05-24):** `docs/superpowers/plans/2026-05-22-agent-tool-surface-policy.md` · tasks: `~/Documents/Projects/.plans/megalodon/agent-tool-surface-policy-2026-05-22-tasks.md`. All 8 tasks done and on `origin/main` (`999088b` allowlist, `2748eab` launch.md, `a9a3e84` orientation-fix + auto-open). 56 policy tests green; full suite 858/0. **The "pending manual gate before push" was already moot — the work was pushed; this entry is corrected from the stale prior wording.**
> **Fresh-spawn acceptance gate — RUN 2026-05-24 (claude v2.1.142, Opus 4.7), hardening VALIDATED:** spawned single Opus AUDIT lanes (`tsgate`, `tsgate2`). Confirmed: (1) Read-tool orientation — Step 0 (`a9a3e84`) conclusively stops the v94h `ls`/`cd`/`tail` orientation prompts; (2) **bounded calls auto-approve** — `scripts/queue_submit.py … status` ran prompt-free; (3) **compounds/extra-shell correctly gate** (the desired CV-2 property). Three agent-habit findings surfaced + fixed/recorded below. Decision (operator, 2026-05-24): **ACCEPT — hardening works as designed**; remaining prompts are agents decorating bounded calls with shell, addressed best-effort in launch.md.
>   - **Finding A (HIGH — FIXED):** run-dir missions had no `scripts/`, so the allowlisted relative `scripts/<tool>` couldn't resolve from the spawn cwd (= run dir) → first bounded call would prompt. Fix: `new_run.sh` now symlinks `scripts/` into each run dir (`../../scripts`); `launch.md:5` corrected to "mission = your cwd = the run dir." Regression test `test_scaffold_links_scripts_for_run_dir_cwd`.
>   - **Finding B/C (MEDIUM — best-effort guidance):** agents wrap bounded calls in extra shell that gates — `cat .claude/settings.json | head` (B), `scripts/claim.sh … ; echo "exit=$?"` (C). Both are the hardening *correctly* gating compounds. `launch.md` Step 0 reinforced: don't inspect the allowlist; invoke bounded tools bare with nothing appended.
>   - **Finding (HIGH — FIXED 2026-05-24):** `new_run.sh` now validates the prospective `<run>/.fleet/tmux.sock` path against the 100-byte guard (`SOCKET_PATH_LIMIT_BYTES`) and refuses an over-budget slug up front with budget math (bytes-over + chars-to-trim), instead of letting `launch_fleet.sh --spawn` fail late at exit 10. Bypass via `MEGALODON_SKIP_SOCKET_BUDGET=1`. Tests: `test_rejects_slug_whose_socket_path_exceeds_budget`, `test_socket_budget_limit_matches_product_constant`. Origin finding: `.archive/2026-05-23T20-24Z--v94h/findings/operator-OPS-new_run-socket-path-no-validation-*.md`.
>
> **Dev gates (pre-commit hook — ADDED 2026-05-24):** `hooks/pre-commit` (activate per clone with `git config core.hooksPath hooks`) runs **ruff on staged `.py`** (pinned `ruff==0.15.14`) + a **vulture dead-code scan** across `megalodon_ui`/`scripts` (config in `pyproject.toml [tool.vulture]`; `signum/frame/exc_*` ignored as required-by-signature). **Tests intentionally NOT run on commit** (operator decision: CI owns the suite). Bypass: `git commit --no-verify`.
>   - **RESOLVED 2026-05-24 (commit 5033054) — lint debt cleared:** 17 whole-tree ruff errors fixed (E741 ambiguous `l`→`lane` ×6, E401 split imports ×2, F841 unused locals ×2, E402 ×5 — hoisted `applier.py` imports + `# noqa: E402` on deliberate section-local test imports). The hook can now be moved to whole-tree lint if desired.
>   - **TODO (gate parity, deferred per operator):** `FIXED (0064e60)` — CI `-p forked`→`--forked`. Still open: ruff remains unpinned in CI (`test.yml` uses `--with ruff`); CI has no dead-code (vulture) step. Tracked, not blocking.
>
> **Active plan (v9.4 — IMPLEMENTATION COMPLETE 2026-05-20; lifecycle + harness COMPLETE 2026-05-22):** `docs/superpowers/plans/2026-05-22-v94-dogfood-and-run-lifecycle.md`. Dashboard plan: `~/Documents/Projects/.plans/megalodon/v9-4-dashboard-rebuild-2026-05-19.md` (v2 — warp-complete).
> **Status**: T4.3 IN PROGRESS — lifecycle ready, dogfood is the next operator step.
> **Next action**: `bash scripts/preflight.sh --dry-run` → must print `PREFLIGHT: PASS`. Then `bash scripts/new_run.sh v94-ui-dogfood --title "v9.4 UI self-observation dogfood" --summary "..."`. Lifecycle convention: `docs/v9/v9-4-RUN-LIFECYCLE.md`.
> **Plan artifacts:** Implementation plan + tasks at `~/Documents/Projects/.plans/megalodon/v9-4-dashboard-rebuild-2026-05-19*.md`. Synthesis + reviews also archived in same directory.
>
> **Shipped (dashboard):** Full FE rewrite (grid.js, lane_detail.js, approval_rules.js + 6 page rewrites + new components) + 5 new BE endpoints + activity wall + approval rules + stale-lanes detection. See HISTORY.md "V9.4 SHIPPED" for full manifest.
> **Shipped (lifecycle 2026-05-22):** `scripts/new_run.sh`, `scripts/archive_run.sh`, `scripts/preflight.sh`, `scripts/_run_liveness.py`, `scripts/run_lib.sh`, `templates/run/` (7 templates), `runs_harness/stimulus.py` (stale-lane + signal-fidelity checks), `ui/tests/e2e/visibility.spec.ts` (snap-back, tab-highlight, activity-wall fidelity, empty-state).
>
> ---
>
> **Previous plan (v9.2 — SHIPPED 2026-05-18):** `~/Documents/Projects/.plans/megalodon/v9-2-tmux-headless-fleet-2026-05-17.md` (v1.4 — warp-complete).
> **Previous task file:** `~/Documents/Projects/.plans/megalodon/v9-2-tmux-headless-fleet-2026-05-17-tasks.md` — all P0-P7 tasks `done`.
>
> **v9.3 (interim dogfood iteration, 2026-05-19):** No formal plan — orchestrator's bug-fix sweep during a 6-hour dogfood run. Code shipped in commit `86f3ecc`; mission archive in commit `095882d`. See `docs/v9/dogfood-2026-05-19/README.md` for the run's 120 findings + 10 top failure modes (the foundation for v9.4 above).
> v9.2 — tmux + web UI headless fleet. Implementation complete.
>
> **P0 — Pre-flight:** done (9/9).
> **P1 — Server-owned tmux spawn + MissionConfig wiring:** done (7/8; Task 1.6 CV-9 deferred to v9.3).
> **P2 — Cookie auth:** done.
> **P3 — Stream tap (pipe-pane):** done.
> **P4 — SSE pane-stream:** done.
> **P5 — xterm.js dashboard:** done (Task 5.3 partial: 4 Playwright fixme stubs deferred to v9.3 fake-spawner mode).
> **P6 — Follow-up prompts + respawn:** done (Task 6.4 partial: `followup.spec.ts` fixme — same blocker).
> **P7 — Polish + destructive teardown + docs:** done (5 + 4 burn-residuals tasks). Surface: `DELETE /api/v1/fleet`, `python -m megalodon_ui.shutdown` CLI, watchdog `STREAM-LOG-SIZE` detector, v9-2-{TMUX-FLEET,AUTH,FOLLOWUP-PROMPTS}.md docs, audits, ruff cleanup (P7.6), real-tmux isolated tagging (P7.7), fake-spawner test mode + 4 fixme→active (P7.8), 9/13 v9.0 e2e fixes (P7.9 partial — 4 deferred to v9.3).
>
> **Final suite:** 637 passed, 34 skipped, 12 deselected, 3 xfailed, 0 failed. All 4 real-tmux files tagged `@pytest.mark.isolated` (CI Linux only). v9.2 Playwright: 11/11 chromium-v92-dashboard specs green.
>
> See `HISTORY.md` "V9.2 SHIPPED" for the full delivery record.

# Tasks — Run 2 (make-it-work)

Format: `[ ] [LANE-X] <task-id> — <description>`

States: `[ ]` open · `[claimed: <agent-id> @ <UTC>]` · `[done: <agent-id> @ <UTC>]`

Claim via `mkdir claims/<task-id>` per RULE 2. Use ASCII task IDs only (per v8 Edit 3): `P2-A-to-F` not `P2-A→F`.

Task IDs encode phase and lane per MISSION.md task-assignment matrix.

---

## PHASE 1 — PLAN (Pass-1 fresh eyes; do NOT read other lanes' plans)

- [done: agent-dcbc @ 2026-05-16T17:39Z] [LANE-A] `P1-A` — AUDIT plan: scan run-1 archive for what AUDIT got right/wrong; design run-2 audit methodology; plan v8.1-candidate doc structure (if recurring failures suggest spec gaps). Output: `findings/<agent>-A-P1-audit-plan-<UTC>.md`
- [done: agent-fec0 @ 2026-05-16T17:38Z] [LANE-B] `P1-B` — ARCHITECT plan: design `megalodon_ui/` package structure; spec `make_app(mission_dir=)` factory contract; design the 4 missing POST endpoint UI wiring patterns. Output: `findings/<agent>-B-P1-arch-plan-<UTC>.md`
- [done: agent-84f2 @ 2026-05-16T17:39Z] [LANE-C] `P1-C` — BACKEND plan: design `megalodon_ui/primitives.py` API (enumerate from `ui/tests/unit/test_protocol_primitives.py` imports — `is_stale`, `try_claim`, `validate_signal`, and any others); design `make_app(mission_dir=)` factory wrapping current `ui/server.py`'s app; plan SSE payload shape fixes. Output: `findings/<agent>-C-P1-backend-plan-<UTC>.md`
- [done: agent-2e7a @ 2026-05-16T17:40Z] [LANE-D] `P1-D` — FRONTEND plan: scan run-1's `ui/static/pages/*.js` to inventory what's wired vs what's not; design the 4 missing POST endpoint UI flows; plan Playwright integration. Output: `findings/<agent>-D-P1-frontend-plan-<UTC>.md`
- [done: agent-43d9 @ 2026-05-16T17:43Z] [LANE-E] `P1-E` — TEST plan: enumerate ALL tests in `ui/tests/{unit,integration,e2e}` and what each needs to actually execute (not SKIP); plan Playwright headless + UI-screenshot artifact production; design test report format. Output: `findings/<agent>-E-P1-test-plan-<UTC>.md`
- [done: agent-9bba @ 2026-05-16T17:41Z] [LANE-F] `P1-F` — META plan: observation framework targeting run-2's specific risk (PHASE-RUN+HEAL behavior + PHASE-OPERATOR-ACCEPTANCE gating). Output: `findings/<agent>-F-P1-meta-plan-<UTC>.md`

## PHASE 2 — CHALLENGE (directed cycle A→F→E→D→C→B→A)

- [done: agent-dcbc @ 2026-05-16T17:48Z] [LANE-A] `P2-A-to-F` — AUDIT challenges META plan. Output: `findings/<agent>-A-P2-challenge-of-meta-<UTC>.md`
- [done: agent-9bba @ 2026-05-16T17:50Z] [LANE-F] `P2-F-to-E` — META challenges TEST plan. Output: `findings/<agent>-F-P2-challenge-of-test-<UTC>.md`
- [done: agent-43d9 @ 2026-05-16T17:55Z] [LANE-E] `P2-E-to-D` — TEST challenges FRONTEND plan. Output: `findings/<agent>-E-P2-challenge-of-frontend-<UTC>.md`
- [done: agent-2e7a @ 2026-05-16T18:16Z] [LANE-D] `P2-D-to-C` — FRONTEND challenges BACKEND plan. Output: `findings/<agent>-D-P2-challenge-of-backend-<UTC>.md`
- [done: agent-84f2 @ 2026-05-16T17:58Z (RULE-6 retroactive recovery by agent-fec0 @ 2026-05-16T18:19Z — split-tick RULE-10; finding existed, bracket missed)] [LANE-C] `P2-C-to-B` — BACKEND challenges ARCHITECT plan. Output: `findings/<agent>-C-P2-challenge-of-architect-<UTC>.md`
- [done: agent-fec0 @ 2026-05-16T17:44Z] [LANE-B] `P2-B-to-A` — ARCHITECT challenges AUDIT plan. Output: `findings/<agent>-B-P2-challenge-of-audit-<UTC>.md`

### PHASE 2.5 — Plan-v2 reconciliation

- [done: agent-dcbc @ 2026-05-16T17:56Z] [LANE-A] `P2.5-A` — AUDIT plan-v2 incorporating ARCHITECT challenge.
- [done: agent-fec0 @ 2026-05-16T17:55Z] [LANE-B] `P2.5-B` — ARCHITECT plan-v2 incorporating BACKEND challenge.
- [done: agent-84f2 @ 2026-05-16T18:53Z] [LANE-C] `P2.5-C` — BACKEND plan-v2 incorporating FRONTEND challenge.
- [done: agent-2e7a @ 2026-05-16T18:19Z] [LANE-D] `P2.5-D` — FRONTEND plan-v2 incorporating TEST challenge.
- [done: agent-43d9 @ 2026-05-16T18:16Z] [LANE-E] `P2.5-E` — TEST plan-v2 incorporating META challenge.
- [done: agent-9bba @ 2026-05-16T17:57Z] [LANE-F] `P2.5-F` — META plan-v2 incorporating AUDIT challenge.

## PHASE 3 — BUILD

- [done: agent-dcbc @ 2026-05-16T19:11Z] [LANE-A] `P3-A` — AUDIT writes `docs/v8.1-candidate.md` IF the run surfaces protocol spec gaps. Otherwise produces a "v8-stable-after-run-2" attestation. Output: `docs/v8.1-candidate.md` OR `findings/<agent>-A-P3-v8-attestation-<UTC>.md`
- [done: agent-fec0 @ 2026-05-16T19:08Z] [LANE-B] `P3-B` — ARCHITECT writes `ui/SPEC-v2.md` (incremental delta) + `ui/adrs/ADR-006-make_app-factory.md`
- [done: agent-84f2 @ 2026-05-16T19:36Z] [LANE-C] `P3-C` — BACKEND builds `megalodon_ui/` package. **Publish stub in tick 1-2 so TEST can integrate against it.** Output: `megalodon_ui/__init__.py` + `megalodon_ui/primitives.py` + `megalodon_ui/server.py` (with `make_app(mission_dir=Path)` factory) + fixes to `ui/server.py` for SSE payload shapes (run-1 P4-C→D V2).
- [done: agent-2e7a @ 2026-05-16T19:19Z] [LANE-D] `P3-D` — FRONTEND wires the 4 unwired POST endpoints in `ui/static/pages/*.js` (inject-challenge, phase-flip, mission-status, inject-task). Each must have form + success/error toast + Playwright-testable `data-testid` hooks.
- [done: agent-43d9 @ 2026-05-16T19:54Z] [LANE-E] `P3-E` — TEST updates test code so imports succeed against new `megalodon_ui` package; adds Playwright headless smoke tests; ensures all tests REACH ASSERTION (no SKIPs).
- [done: agent-9bba @ 2026-05-16T19:09Z] [LANE-F] `P3-F` — META mid-mission report on PHASE-RUN behavior. Output: `findings/<agent>-F-P3-mid-mission-meta-<UTC>.md`

## PHASE 4 — VERIFY (rotated pairings; no self-verification)

- [done: agent-dcbc @ 2026-05-16T20:01Z] [LANE-A] `P4-A-to-B` — AUDIT verifies ARCHITECT SPEC-v2 honors v8 semantics.
- [done: agent-fec0 @ 2026-05-16T20:00Z] [LANE-B] `P4-B-to-E` — ARCHITECT verifies TEST coverage maps to SPEC-v2.
- [done: agent-43d9 @ 2026-05-16T20:06Z] [LANE-E] `P4-E-to-C` — TEST verifies BACKEND code (megalodon_ui package + ui/server.py fixes).
- [done: agent-84f2 @ 2026-05-16T20:10Z] [LANE-C] `P4-C-to-D` — BACKEND verifies FRONTEND consumes the 4 new endpoints correctly.
- [done: agent-2e7a @ 2026-05-16T20:00Z] [LANE-D] `P4-D-to-A` — FRONTEND verifies AUDIT's v8.1-candidate (or attestation).
- [done: agent-9bba @ 2026-05-16T20:03Z] [LANE-F] `P4-F-to-ALL` — META interim verify; FINAL-RUN-CAPSTONE happens post-OPERATOR-ACCEPTANCE.

## PHASE 5 — RUN (execution verification — NEW in v8)

Auto-claim by pairing matrix (no self-verification). Failure injects PHASE-HEAL repair task. **Budget per RUN task: 3 HEAL cycles OR 30-min wall-clock.** Exceed → `BLOCKED-DEGRADED`.

- [done: agent-43d9 @ 2026-05-16T20:10Z] [LANE-E] `P5-RUN-PRIMITIVES` — TEST runs `uv run --with pytest --with fastapi --with 'uvicorn[standard]' --with sse-starlette --with pyyaml --with httpx --with pytest-asyncio pytest ui/tests/unit -v`. **MUST exit 0 with 0 SKIPPED, 0 FAILED.** Output transcript to `findings/<agent>-E-P5-RUN-primitives-<UTC>.txt`. On failure: inject `[REPAIR-PRIMITIVES-<n>]` task with transcript embedded.
- [done: agent-43d9 @ 2026-05-16T20:11Z] [LANE-E] `P5-RUN-INTEGRATION` — TEST runs `uv run --with ... pytest ui/tests/integration -v`. Same exit criteria. Output transcript.
- [done: agent-2e7a @ 2026-05-16T20:15Z] [LANE-D] `P5-RUN-UI-RENDER` — FRONTEND launches `uv run --with fastapi --with 'uvicorn[standard]' --with sse-starlette --with pyyaml python ui/server.py --mission-dir ui/tests/fixtures/fix-medium --port 8765 &`, waits 3s, runs Playwright headless `goto('http://127.0.0.1:8765')`, asserts page title + 6 lane rows visible + **0 console errors**. Takes screenshot to `findings/<agent>-D-P5-RUN-ui-render-<UTC>.png`.
- [done: agent-43d9 @ 2026-05-16T21:43Z — BLOCKED-DEGRADED — 7 PASSED / 9 FAILED final after 3 HEAL cycles + retroactive verify post-REPAIR-11; supersedes @21:40Z close which was based on pre-REPAIR-11 transcript; see `findings/agent-43d9-E-P5-RUN-MUTATIONS-E2E-TERMINAL-FINAL-2026-05-16T21-43Z.md`] [LANE-E] `P5-RUN-MUTATIONS-E2E` — TEST runs Playwright e2e that exercises all 6 POST endpoints (signal, reclaim, inject-challenge, phase-flip, mission-status, inject-task). All must return success codes. Output transcript + screenshots.

## PHASE-HEAL REPAIRS (per Edit 21 — injected when P5-RUN-* fails)

- [done: agent-84f2 @ 2026-05-16T20:30Z] [LANE-C] `REPAIR-MUTATIONS-E2E-1-SSE` — BACKEND: fix SSE `status-change` event delivery so UI receives event within ~3s of file mutation. Affects 5-8 e2e tests that timeout @30s waiting for UI to reflect POST'd mutation. Per BE STATUS:11 self-diagnosis: switch `/api/v1/events` `StreamingResponse` → `EventSourceResponse` (sse-starlette per-event flush) OR add periodic flush via keepalive comments. Re-verify by running existing `test_sse_stream_emits_status_change_on_file_touch` (currently XFAIL strict; should XPASS after fix). Transcript: `findings/agent-43d9-E-P5-RUN-mutations-e2e-2026-05-16T20-12Z.txt`. Inject by agent-43d9 @ 2026-05-16T20:24Z. HEAL cycle 1 of 3. **SCOPE-BROADENED at claim**: BE re-diagnosis on detailed transcript read — *primary* root cause is **missing `StaticFiles` mount in `megalodon_ui/server.py`** (`ui/static/index.html:8,60-62` ref `/static/css/base.css`, `/static/js/{store,sse,app}.js` → all 404 → no JS loads → no `data-testid` elements ever render → ALL 16 tests fail). Legacy `ui/server.py:1434` has the mount; my factory does not. SSE flush is *secondary* (only matters after JS loads). Will ship both fixes in this claim. NOTE TO LANE-D: many of your REPAIR-MUTATIONS-E2E-1-FE failures may auto-resolve after static mount lands — recommend waiting until I close before re-classifying.
- [done: agent-2e7a @ 2026-05-16T20:43Z] [LANE-D] `REPAIR-MUTATIONS-E2E-1-FE` — FRONTEND: fix selector/data-testid + fixture-render gaps for fast-fail tests (test_status_view × 4: lane-row, stale-styling, last-utc live-update, task-card; test_failure_modes × 4: stuck-flip warning, recovery-action, claim-collision panel, history-drift glyph). Tests fail at ~5s suggesting either rendered DOM missing the expected `data-testid` attributes OR fixture data shape mismatch. Cross-ref fixtures at `ui/tests/fixtures/fix-medium*` per `_gen.py`. Transcript: same as above. Inject by agent-43d9 @ 2026-05-16T20:24Z. HEAL cycle 1 of 3.
- [done: agent-84f2 @ 2026-05-16T20:44Z] [LANE-C] `REPAIR-MUTATIONS-E2E-2-SPA-CATCHALL` — BACKEND: add SPA route catch-all so `/tasks`, `/findings`, `/mission`, `/signals` serve the same `index.html` shell (client-side router takes over). Currently `megalodon_ui/server.py` only defines `@app.get("/")`; 7 e2e tests `page.goto()` SPA paths directly → 404. Affects: `test_failure_modes.spec.ts:22,31,39,48` (`/mission` ×3, `/tasks` ×1) + `test_status_view.spec.ts:37,46,55` (`/tasks` ×1, `/findings` ×2). Triggered by SIGNAL-FE-1 (`findings/agent-2e7a-D-SIGNAL-FE-1-spa-routes-2026-05-16T20-36Z.md`, MAJOR) from agent-2e7a @ 2026-05-16T20:36Z. **Atomic-claim ceremony fixed**: `mkdir claims/REPAIR-MUTATIONS-E2E-2-SPA-CATCHALL/ + owner.txt` AND TASKS bracket (defensive both-and per mea-culpa from REPAIR-1-SSE drift). HEAL cycle 1 of 3 (still within HEAL-1 budget — same trigger event).

### HEAL CYCLE 2 (triggered @ 2026-05-16T20:53Z by re-run residuals — 3 PASSED / 13 FAILED)

- [done: agent-2e7a @ 2026-05-16T21:19Z] [LANE-D] `REPAIR-MUTATIONS-E2E-3-ACTION-PANEL` — FRONTEND: fix orchestrator action panel render — 6 mutation tests in `test_orchestrator_actions.spec.ts:{16,29,41,61,75,89}` ALL time out 30s on `page.locator('[data-testid="action-X"]').click()`. Test setup navigates to `/mission`, sets `localStorage.controlMode='true'`, reloads. Testids exist in `ui/static/pages/mission.js:514+` (`action-inject-challenge`, `action-reclaim-lane`, `action-post-signal`, etc) but never become clickable. Hypothesis: controlMode flip-on-load doesn't trigger panel rebuild, OR initial paint hides under controlMode=false then localStorage+reload sequence races. Check `store.set("ui.controlMode")` subscription + render gating in `mission.js`. Affects tests T-A-CH-e2e, T-A-RC-e2e, T-A-SG-e2e, T-R11-a-e2e, T-A-IT-e2e, T-A-MS-e2e. Transcript: `findings/agent-43d9-E-P5-RUN-mutations-e2e-2026-05-16T20-50Z.txt` lines 159-326. Inject by agent-43d9 @ 2026-05-16T20:53Z. HEAL cycle 2 of 3.
- [done: agent-43d9 @ 2026-05-16T20:58Z] [LANE-E] `REPAIR-MUTATIONS-E2E-4-FIXTURE-OVERRIDE` — TEST: switch failure-mode tests to use `fix-medium-failure-modes` fixture. Per SIGNAL-FE-2 (`findings/agent-2e7a-D-SIGNAL-FE-2-fixture-override-2026-05-16T20-36Z.md`, MAJOR), 3 tests fail because playwright config defaults all to `fix-medium` but `test_failure_modes:21/38/47` need fixture-specific lock dirs / claim collisions / drift entries. Fix: convert `playwright.config.ts` `webServer:` single → array of 2 webServers (ports 8765+8766) + 2 projects with testMatch split (chromium-default vs chromium-failure-modes). Affects T-FX-FAILMODE-a (stuck-flip), T-FX-FAILMODE-b (non-canonical-panel), T-FX-FAILMODE-c (HISTORY-drift). Inject by agent-43d9 @ 2026-05-16T20:53Z. HEAL cycle 2 of 3.
- [done: agent-84f2 @ 2026-05-16T21:18Z (retroactive-recovery: claimed @21:15Z after 18min silence, shipped + closed inside HEAL-2 budget)] [LANE-C] `REPAIR-MUTATIONS-E2E-5-STATUS-VIEW` — BACKEND (RE-OWNED per BE STATUS:11 pre-diagnosis + ARCH SPEC-v2 §3-quater anchor): ship 2 api-contract gaps. (a) `parse_status` must add `staleness_seconds: float` + `is_stale: bool` (RULE-1 15-min threshold, computed from `now_utc - parse(last_utc)`) — fixes `test_status_view:16` stale row styling via FE `dashboard.js:115,187`. (b) Add `@app.get("/api/v1/tasks")` returning `{phases: [{name, tasks:[{id, lane, state, ...}]}]}` parsed from TASKS.md bracket grammar — fixes `test_status_view:36` `task-card-*` count by populating `tasks.js:417,452` store reads. **Residual (c)(d) reclassified as fixture-class per BE STATUS:11**: `test_status_view:45` severity filter likely needs MAJOR-severity findings in `fix-medium/findings/` frontmatter (TEST/fixture scope); `test_status_view:53` scratch chip needs `.scratch.md` files present in fixture (zero today per `ls`). Will inject `REPAIR-MUTATIONS-E2E-6-FIXTURE-DATA` for LANE-E (mine) if residuals after BE close. BE pre-drafted ~30 LOC. Inject by agent-43d9 @ 2026-05-16T20:53Z; re-owned LANE-D → LANE-C by agent-43d9 @ 2026-05-16T21:03Z. HEAL cycle 2 of 3.

### HEAL CYCLE 3 (triggered @ 2026-05-16T21:25Z — HEAL-2 re-run still 3/16 PASS but failure types progressed; FINAL cycle in 3-cycle cap)

Reclassify finding: `findings/agent-43d9-E-P5-RUN-mutations-e2e-HEAL2-RECLASSIFY-2026-05-16T21-25Z.md`. Transcript: `findings/agent-43d9-E-P5-RUN-mutations-e2e-2026-05-16T21-22Z.txt`. Progress: FE controlMode fix unblocked all 6 orchestrator initial-clicks; BE staleness/tasks fix changed test #8 from "0 cards" to "wrong count"; fixture-override routing works. Remaining failures are mostly downstream (form mechanics, fixture content, FE wiring).

- [done: agent-2e7a @ 2026-05-16T21:32Z] [LANE-D] `REPAIR-MUTATIONS-E2E-7-ACTION-FORM-WIRING` — FRONTEND: fix form mechanics for orchestrator tests. **Test #1 T-A-CH-e2e** now fails on `challenge-finding-picker > option:nth(1)` — element "is not visible" because tests use `option.click()` but `<option>` inside `<select>` requires `selectOption()` instead. Spec tests need update OR FE picker must be `<div>` + roving-tabindex pattern (not native `<select>`). **Tests #3 T-A-SG-e2e** also hangs on selectOption — verify dropdown is populated. **Tests #4/#5/#6** progress past actions; fail at downstream assertions (toContainText/toBeVisible) — verify form-submission result-state matches what spec asserts (success toast text, task-card appearance, etc.). 6 tests affected. HEAL cycle 3 of 3. Inject by agent-43d9 @ 2026-05-16T21:25Z.
- [done: agent-2e7a @ 2026-05-16T21:32Z] [LANE-D] `REPAIR-MUTATIONS-E2E-8-STATUS-STALE-WIRING` — FRONTEND: investigate why test_status_view:16 stale styling still fails after BE shipped `staleness_seconds`/`is_stale`. Either (a) FE `dashboard.js` doesn't translate `is_stale: true` → `data-stale="true"` attribute on `lane-row-{LANE}`, OR (b) `fix-medium` fixture has no lanes that exceed 15min staleness threshold. Cross-check: `curl http://127.0.0.1:8765/api/v1/status | jq` against running server to see if `is_stale` true for any row. Test #8 `not.toHaveCount(0)` may also need FE wiring check (task-cards may exist but wrong shape). HEAL cycle 3 of 3. Inject by agent-43d9 @ 2026-05-16T21:25Z.
- [done: agent-43d9 @ 2026-05-16T21:29Z] [LANE-E] `REPAIR-MUTATIONS-E2E-9-FIXTURE-DATA` — TEST: add fixture content for severity-filter + scratch tests. (a) Test #9 `filter-severity-MAJOR` needs at least one MAJOR-severity finding in `fix-medium/findings/`; check current files via `grep -l 'severity: MAJOR' ui/tests/fixtures/fix-medium/findings/`. Add one if absent. (b) Test #10 scratch chip needs at least one `.scratch.md` file in `fix-medium/findings/`; add via `_gen.py` or manual touch. HEAL cycle 3 of 3. Inject by agent-43d9 @ 2026-05-16T21:25Z.
- [done: agent-43d9 @ 2026-05-16T21:29Z (VERIFIED-NO-CHANGE — fixture content complete)] [LANE-E] `REPAIR-MUTATIONS-E2E-10-FAILURE-MODES-FIXTURE-CONTENT` — TEST: verify `fix-medium-failure-modes` fixture has expected state for 3 failure-mode tests. Inspect: (a) stuck-flip lock dir presence (`.mission-events/phase-flip-lock`?); (b) `claims/P2-C→B/` directory existence (multi-form claim collision); (c) 3 drift-shaped HISTORY entries. If missing, augment fixture via `_gen.py` OR manual files. HEAL cycle 3 of 3. Inject by agent-43d9 @ 2026-05-16T21:25Z.
- [done: agent-84f2 @ 2026-05-16T21:40Z (DOUBLE-MEA-CULPA recovery: retracted @21:30Z, un-retracted @21:36Z after empirical verification, shipped+closed @21:40Z = 4min SLA)] [LANE-C] `REPAIR-MUTATIONS-E2E-11-STATE-ENDPOINT` — BACKEND: add `@app.get("/api/v1/state")` returning aggregate `{status, tasks, findings, signals, mission, config}` per `store.js:193-217 hydrate()`. **Empirical ground truth**: `grep -n '/api/v1/state' ui/server.py` returns 2 hits (legacy server line 916 defines the route); same grep on `megalodon_ui/server.py` returns 0. UI-RENDER passed against legacy because of this endpoint; MUTATIONS-E2E uses factory. Originally injected by agent-43d9 @21:29Z, retracted @21:33Z per my mea-culpa, **re-claimed by agent-84f2 @21:36Z with empirical verification**. HEAL cycle 3 of 3.

## PHASE-OPERATOR-ACCEPTANCE (NEW in v8 post-run-1)

Auto-flips when all `P5-RUN-*` are EXEC-PASS. Workers HALT and wait.

### OPERATOR-ACCEPTANCE TASKS

- [ ] [ANY-LANE] `OPERATOR-ACCEPTANCE-REQUEST` — first worker to enter the phase writes a summary task here with: deliverable summary, test transcripts (paths), screenshot paths, outstanding issues. Then ALL lanes set state to `idle | awaiting OPERATOR-ACK`. **No new claims.**

- [OPERATOR-DEGRADED-ACK] by orchestrator-Claude (representing operator David @ Zero Delta LLC) @ 2026-05-16T21:50Z — Run-2 mission ACCEPTED WITH DEGRADED status. Rationale: final tally 7/16 e2e PASS (43.75%) + 25 PASS + 1 XFAIL unit/integration + UI renders cleanly with 0 console errors + 41KB screenshot artifact. Net progress 3→7 across 3 HEAL cycles + bonus REPAIR-11 (Edit-21 validated). 9 residuals are user-visible and well-diagnosed (4 FE form-submit gate, 2 status_view, 3 failure-mode-fixture); these become run-3 work under v9 protocol. Run-2's primary deliverable was protocol validation: 53+ v8.1 candidates harvested from execution evidence (vs run-1's 0 from doc review), 3 SPEC-FIRST HEAL addenda shipped (§3-bis SSE / §3-ter SPA / §3-quater /tasks+status), 4-cascading-HEAL pattern documented, Edit-22 retract-reversibility gap (META-OBS-41) and REPAIR-RE-UNDERTAKE pattern (META-OBS-42) surfaced. Workers: flip PHASE-OPERATOR-ACCEPTANCE → PHASE-DRAINING with degraded flag. META: write FINAL-RUN-CAPSTONE referencing this ACK. AUDIT: write Pass-3 RECONSIDERED-append to v8.1-candidate ledger. All other lanes: heartbeat through DRAINING → COMPLETE → halt /loop per launch.md §8.

Phase exits when one of:
- `[OPERATOR-ACK]` task appears here → flip to DRAINING
- `[OPERATOR-REJECT]` + `[REPAIR-<n>]` tasks appear → flip back to PHASE-HEAL
- `[OPERATOR-DEGRADED-ACK]` → flip to DRAINING with degraded flag

The orchestrator-Claude (or human operator) injects one of these tasks.

---

## CHALLENGE TASKS

(workers may self-assign CHALLENGEs on 3+ lane converged findings per TIER 2; orchestrator may inject)

---

## CROSS-LANE / SECONDARY TASK POOL

(claimable by any drained lane; tag `[CROSS]`; only after primary lane work is done for the current phase)

- [ ] [CROSS] `S-1` — Compare run-1 RR-1 patch vs what run-2 produces; quantify reduction in defects. Output: `findings/<agent>-CROSS-S1-run-comparison-<UTC>.md`
- [ ] [CROSS] `S-2` — Audit `.archive/2026-05-16T17-06Z--megalodon-self-improvement-run1/findings/` for failure modes v8 didn't address. Output: `findings/<agent>-CROSS-S2-v8-coverage-<UTC>.md`
- [ ] [CROSS] `S-3` — Add tests for §9.10 Origin/CSRF/localhost-bind exercise (run-1 ARCH P4-B→E gap). Output: `ui/tests/integration/test_auth.py` + run-evidence.
- [ ] [CROSS] `S-4` — Add SSE-timing test exercising 300ms file-watch vs 2.5s poll backstop. Output: `ui/tests/integration/test_sse_timing.py` + run-evidence.
- [ ] [CROSS] `S-5` — Add concurrent-write CAS test for ADR-001 (run-1 §9.4 gap). Output: `ui/tests/integration/test_cas_concurrent.py` + run-evidence.
- [done: agent-2e7a @ 2026-05-16T18:31Z] [CROSS] `S-6` — Track operator-friction events in run-2 (any moment where operator-Claude had to intervene). Output: `findings/<agent>-CROSS-S6-operator-friction-<UTC>.md`
- [ ] [CROSS] `S-7` — Devil's-advocate CHALLENGE on the entire run-2 deliverable set (claimable in PHASE-VERIFY+1 by any lane). Output: `findings/<agent>-CROSS-S7-meta-challenge-<UTC>.md`

---

## V9.1 — SHIPPED 2026-05-17 (mission-config-driven fleet)

**Current plan:** `~/Documents/Projects/.plans/megalodon/v9-1-mission-config-driven-2026-05-17.md`

All v9.1 tasks are **done**. See HISTORY.md §"V9.1 SHIPPED" for the full delivery record.

### v9.1 completed tasks (summary)

- [done @ 2026-05-17] P1 — config foundation: Pydantic v2 schema, default_v9_0_shape, regex builder, init/validate CLI, 6 harness adapters (Claude/Codex/Gemini must-pass + Copilot/Cursor/Vibe experimental)
- [done @ 2026-05-17] P2 — core de-hardcoding: 6 production files refactored; lane literals + [A-H] drift eliminated; schema extended with orchestrator_pseudo_lane + task_sections
- [done @ 2026-05-17] P3 — FE + launch tooling + watchdog: FE config loader, 5 pages migrated, phase navigator hybrid, gen_lane_launches.py config-driven, launch_fleet.sh, watchdog WR-3 non-Claude skip
- [done @ 2026-05-17] P4 — pre-flight CLI: proposer + interview REPL (max-refine 3 cycles) + writer (CV-2 atomic + SIGINT snapshot)
- [done @ 2026-05-17] P5 — test consolidation: legacy HISTORY parser (CV-10 + CV-12), CV-4 semantic regex corpus (60+ strings), back-compat integration test (7 tests)
- [done @ 2026-05-17] P6 — docs: v9-1-MISSION-CONFIG.md, v9-1-HARNESS-ADAPTERS.md, v9-1-PREFLIGHT.md, README + HISTORY updates, P6.5 final documentation pass

**Test suite post-v9.1:** 410 passed + 1 xfailed + 0 failing (combined scripts/tests/ + ui/tests/unit/ + ui/tests/integration/).

### v9.2 follow-up items (deferred)

- CR-4: autonomous-loop wrapper for non-Claude lanes — see `docs/v9/v9-2-ROADMAP.md`
- WR-3: watchdog S3 JSONL staleness for non-Claude harnesses — see `docs/v9/v9-2-ROADMAP.md`
- CV-8: SIGHUP config reload (signal stub exists; reload logic not implemented) — see `docs/v9/v9-2-ROADMAP.md`
- Inv-1: typo-path symlink decision (`megaladon` vs loud-fail) — see `docs/v9/v9-2-ROADMAP.md §Inv-1`
- Inv-2: RESOLVED 2026-05-17 in commit `b8d5dd9` — four M1.5 sync/async test mismatches fixed
