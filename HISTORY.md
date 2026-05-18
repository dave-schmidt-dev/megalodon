# Megalodon History — Run 2

Append-only log of mission events and finding completions.

**Run 2 mission ID:** `2026-05-16T17-30Z--megalodon-run2-make-it-work`

**Run 1 archived to:** `.archive/2026-05-16T17-06Z--megalodon-self-improvement-run1/`

Format for completions: `<UTC> | <agent-id> | <LANE> | <task-id> | <finding-filename> | <severity>`

(use ASCII single-letter LANE codes consistently: `A` | `B` | `C` | `D` | `E` | `F` per v8 Edit 18)

---

## Initialization

2026-05-16T17:30Z | orchestrator | — | INIT | (this file) | — — Run 2 mission begun. Pre-applied fixes: ui/server.py:1434 static-mount (was "/", now "/static"); run-1 archived; state reset (findings/, claims/, .phase-flip-locks/, .scratch/, .mission-events all clean). Protocol promoted to v8 (README.md is now v8 spec; docs/v8-changeset.md documents v7→v8 deltas including Edit 22 PHASE-OPERATOR-ACCEPTANCE added post-run-1).

---

## Completion log

(empty — workers append below as work completes)

2026-05-16T17:39Z | agent-dcbc | A | P1-A | agent-dcbc-A-P1-audit-plan-2026-05-16T17-34Z.md | DELTA
2026-05-16T17:39Z | agent-84f2 | C | P1-C | agent-84f2-C-P1-backend-plan-2026-05-16T17-35Z.md | DELTA
2026-05-16T17:40Z | agent-2e7a | D | P1-D | agent-2e7a-D-P1-frontend-plan-2026-05-16T17-38Z.md | DELTA
2026-05-16T17:41Z | agent-9bba | F | P1-F | agent-9bba-F-P1-meta-plan-2026-05-16T17-37Z.md | DELTA
2026-05-16T17:38Z | agent-fec0 | LANE-B | P1-B | findings/agent-fec0-B-P1-arch-plan-2026-05-16T17-37Z.md | DELTA
2026-05-16T17:43Z | agent-43d9 | LANE-E | P1-E | findings/agent-43d9-E-P1-test-plan-2026-05-16T17-36Z.md | NIT
2026-05-16T17:44Z | agent-fec0 | LANE-B | P2-B-to-A | findings/agent-fec0-B-P2-challenge-of-audit-2026-05-16T17-44Z.md | DELTA
2026-05-16T17:48Z | agent-dcbc | A | P2-A-to-F | agent-dcbc-A-P2-challenge-of-meta-2026-05-16T17-45Z.md | MAJOR
2026-05-16T17:50Z | agent-9bba | F | P2-F-to-E | agent-9bba-F-P2-challenge-of-test-2026-05-16T17-47Z.md | MAJOR
2026-05-16T17:55Z | agent-43d9 | LANE-E | P2-E-to-D | findings/agent-43d9-E-P2-challenge-of-frontend-2026-05-16T17-49Z.md | MAJOR
2026-05-16T17:56Z | agent-dcbc | A | P2.5-A | agent-dcbc-A-P2.5-audit-plan-v2-2026-05-16T17-54Z.md | DELTA
2026-05-16T17:57Z | agent-9bba | F | P2.5-F | agent-9bba-F-P2.5-meta-plan-v2-2026-05-16T17-55Z.md | DELTA
2026-05-16T17:55Z | agent-fec0 | LANE-B | P2.5-B | findings/agent-fec0-B-P2.5-arch-plan-v2-2026-05-16T17-55Z.md | DELTA
2026-05-16T18:03Z | agent-84f2 | C | P2-C-to-B | agent-84f2-C-P2-challenge-of-architect-2026-05-16T17-58Z.md | MAJOR
2026-05-16T18:16Z | agent-43d9 | LANE-E | P2.5-E | findings/agent-43d9-E-P2.5-test-plan-v2-2026-05-16T17-57Z.md | MAJOR
2026-05-16T18:17Z | agent-dcbc | A | S-2 | agent-dcbc-CROSS-S2-v8-coverage-2026-05-16T17-58Z.md | MAJOR
2026-05-16T18:16Z | agent-2e7a | D | P2-D-to-C | agent-2e7a-D-P2-challenge-of-backend-2026-05-16T18-16Z.md | MAJOR
2026-05-16T18:19Z | agent-fec0 | LANE-C | P2-C-to-B | findings/agent-84f2-C-P2-challenge-of-architect-2026-05-16T17-58Z.md | RECOVERY
2026-05-16T18:19Z | agent-2e7a | D | P2.5-D | agent-2e7a-D-P2.5-frontend-plan-v2-2026-05-16T18-19Z.md | DELTA
2026-05-16T18:28Z | agent-9bba | F | STATUS-recovery-BE | n/a | RECOVERY-2 (META applied RULE-6 step-4 extension per OBS-3 v8.1-candidate; recovered agent-84f2 STATUS row from working:P2-C-to-B @17:58Z to idle; P2-C-to-B substantive completion was at 18:03Z per HISTORY:31; STATUS step 4 missed = PARTIAL-RULE-10 case; 4-lane consensus authorized)
2026-05-16T18:31Z | agent-2e7a | D | S-6 | agent-2e7a-CROSS-S6-operator-friction-2026-05-16T18-31Z.md | DELTA
2026-05-16T18:53Z | agent-84f2 | C | P2.5-C | agent-84f2-C-P2.5-backend-plan-v2-2026-05-16T18-53Z.md | DELTA
2026-05-16T19:08Z | agent-fec0 | LANE-B | P3-B | ui/SPEC-v2.md+ui/adrs/ADR-006-make_app-factory.md | DELTA
2026-05-16T19:09Z | agent-9bba | F | P3-F | agent-9bba-F-P3-mid-mission-meta-2026-05-16T19-04Z.md | DELTA
2026-05-16T19:11Z | agent-dcbc | A | P3-A | docs/v8.1-candidate.md | MAJOR
2026-05-16T19:14Z | agent-9bba | F | S-8 | agent-9bba-CROSS-S8-queue-design-audit-2026-05-16T19-12Z.md | MAJOR
2026-05-16T19:19Z | agent-2e7a | D | P3-D | agent-2e7a-D-P3-frontend-build-2026-05-16T19-19Z.md | DELTA
2026-05-16T19:36Z | agent-84f2 | C | P3-C | agent-84f2-C-P3-C-delivery-2026-05-16T19-36Z.md | DELTA
2026-05-16T19:54Z | agent-43d9 | LANE-E | P3-E | findings/agent-43d9-E-P3-test-build-2026-05-16T19-54Z.md | MAJOR
2026-05-16T20:01Z | agent-dcbc | A | P4-A-to-B | agent-dcbc-A-P4-verify-of-architect-2026-05-16T19-59Z.md | DELTA
2026-05-16T20:00Z | agent-fec0 | LANE-B | P4-B-to-E | findings/agent-fec0-B-P4-verify-of-test-2026-05-16T19-59Z.md | MAJOR
2026-05-16T20:00Z | agent-2e7a | D | P4-D-to-A | agent-2e7a-D-P4-verify-of-audit-2026-05-16T20-00Z.md | DELTA
2026-05-16T20:03Z | agent-9bba | F | P4-F-to-ALL | agent-9bba-F-P4-interim-verify-2026-05-16T20-02Z.md | DELTA
2026-05-16T20:10Z | agent-84f2 | C | P4-C-to-D | agent-84f2-C-P4-verify-of-frontend-2026-05-16T20-10Z.md | DELTA
2026-05-16T20:06Z | agent-43d9 | LANE-E | P4-E-to-C | findings/agent-43d9-E-P4-verify-of-backend-2026-05-16T20-06Z.md | DELTA
2026-05-16T20:10Z | agent-43d9 | LANE-E | P5-RUN-PRIMITIVES | findings/agent-43d9-E-P5-RUN-primitives-2026-05-16T20-10Z.txt | EXEC-PASS
2026-05-16T20:11Z | agent-43d9 | LANE-E | P5-RUN-INTEGRATION | findings/agent-43d9-E-P5-RUN-integration-2026-05-16T20-11Z.txt | EXEC-PASS
2026-05-16T20:15Z | agent-2e7a | D | P5-RUN-UI-RENDER | agent-2e7a-D-P5-RUN-ui-render-2026-05-16T20-15Z.md | DELTA (EXEC-PASS)

2026-05-16T20:28Z | agent-fec0 | LANE-B | HEAL-1-SPEC-ADDENDUM | ui/SPEC-v2.md §3-bis SSE flush ≤500ms contract + OBS-RUN-6 ASGITransport mount-detect gap | MAJOR

2026-05-16T20:30Z | agent-84f2 | LANE-C | REPAIR-MUTATIONS-E2E-1-SSE | findings/agent-84f2-C-REPAIR-MUTATIONS-E2E-1-SSE-2026-05-16T20-30Z.md | MAJOR (HEAL-1 EXEC-PASS — primary StaticFiles mount + secondary SSE EventSourceResponse switch; 15/15 unit + 10/11 integration stable; e2e re-run owed to TEST)
2026-05-16T20:43Z | agent-2e7a | D | REPAIR-MUTATIONS-E2E-1-FE | agent-2e7a-D-REPAIR-MUTATIONS-E2E-1-FE-2026-05-16T20-41Z.md | DELTA (SCOPE-DONE; downstream-verify pending HEAL-2; Tier-1 + Tier-2 + 2 SIGNALs)

2026-05-16T20:42Z | agent-fec0 | LANE-B | HEAL-1-SPEC-ADDENDUM-2 | ui/SPEC-v2.md §3-ter SPA route enumeration (anchors incoming BE REPAIR-MUTATIONS-E2E-2-SPA-CATCHALL) | MAJOR

2026-05-16T20:44Z | agent-84f2 | LANE-C | REPAIR-MUTATIONS-E2E-2-SPA-CATCHALL | findings/agent-84f2-C-REPAIR-MUTATIONS-E2E-2-SPA-CATCHALL-2026-05-16T20-44Z.md | MAJOR (HEAL-1 EXEC-PASS — SPA catch-all per SIGNAL-FE-1; 25/25 + 1 XFAIL regression-free; 4-of-4 RULE-10 surfaces this time)

2026-05-16T20:56Z | agent-fec0 | LANE-B | HEAL-1-SPEC-ADDENDUM-3 | ui/SPEC-v2.md §3-quater /api/v1/tasks endpoint + staleness_seconds/is_stale fields (anchors incoming BE REPAIR-3) | MAJOR
2026-05-16T20:58Z | agent-43d9 | LANE-E | REPAIR-MUTATIONS-E2E-4-FIXTURE-OVERRIDE | findings/agent-43d9-E-REPAIR-MUTATIONS-E2E-4-FIXTURE-OVERRIDE-2026-05-16T20-58Z.md | MAJOR (HEAL-2 verification-deferred to collective re-run)

2026-05-16T21:18Z | agent-84f2 | LANE-C | REPAIR-MUTATIONS-E2E-5-STATUS-VIEW | findings/agent-84f2-C-REPAIR-MUTATIONS-E2E-5-STATUS-VIEW-2026-05-16T21-18Z.md | MAJOR (HEAL-2 EXEC-PASS — staleness_seconds+is_stale in parse_status + /api/v1/tasks endpoint via parse_tasks helper; retroactive-recovery claim from RULE-6 silent; 25/25+1XFAIL regression-free; closes 2 of 13 residuals; #10/#11 reclassified fixture-class for LANE-E)
2026-05-16T21:19Z | agent-2e7a | D | REPAIR-MUTATIONS-E2E-3-ACTION-PANEL | agent-2e7a-D-REPAIR-MUTATIONS-E2E-3-ACTION-PANEL-2026-05-16T21-15Z.md | DELTA (SCOPE-DONE; CONTROL_MODE_KEY mismatch + 5 testid wiring fixes across store.js/dashboard.js/mission.js; downstream-verify pending HEAL-3 + BE REPAIR-5)
2026-05-16T21:29Z | agent-43d9 | LANE-E | REPAIR-MUTATIONS-E2E-9-FIXTURE-DATA | findings/agent-43d9-E-REPAIR-MUTATIONS-E2E-9-FIXTURE-DATA-2026-05-16T21-29Z.md | MAJOR (added .scratch.md; MAJOR-severity already present)
2026-05-16T21:29Z | agent-43d9 | LANE-E | REPAIR-MUTATIONS-E2E-10-FAILURE-MODES-FIXTURE-CONTENT | findings/agent-43d9-E-REPAIR-MUTATIONS-E2E-10-FAILURE-MODES-FIXTURE-CONTENT-2026-05-16T21-29Z.md | MAJOR (VERIFIED-NO-CHANGE — fixture content complete; downstream blocks on BE state endpoint)

2026-05-16T21:30Z | agent-fec0 | LANE-B | HEAL-3-SPEC-ADDENDUM-4 | ui/SPEC-v2.md §3-quater AMEND (/api/v1/state aggregate bootstrap endpoint; ARCH-verified via sse.js:67 + store.js:193-217 grep; anchors incoming BE REPAIR-11-STATE; SIGNAL TEST to align HEAL-3 scope) | MAJOR
2026-05-16T21:32Z | agent-2e7a | D | REPAIR-MUTATIONS-E2E-7-ACTION-FORM-WIRING | agent-2e7a-D-REPAIR-MUTATIONS-E2E-7-ACTION-FORM-WIRING-2026-05-16T21-32Z.md | DELTA (SCOPE-DONE; challenge-finding-picker size=6 + signal-from field + optimistic store.set for phase/missionStatus)
2026-05-16T21:32Z | agent-2e7a | D | REPAIR-MUTATIONS-E2E-8-STATUS-STALE-WIRING | agent-2e7a-D-REPAIR-MUTATIONS-E2E-8-STATUS-STALE-WIRING-2026-05-16T21-32Z.md | DELTA (VERIFIED-CORRECT-NO-CHANGE; FE wiring already correct, upstream-blocked on REPAIR-11 /api/v1/state)

2026-05-16T21:34Z | agent-fec0 | LANE-B | HEAL-3-SPEC-RETRACT | ui/SPEC-v2.md §3-quater AMENDMENT RECONSIDERED-RETRACTED (BE retracted /api/v1/state hypothesis per mea-culpa #3; store has SSE+lazy fallbacks; my grep verification missed transitive completeness) | MAJOR
2026-05-16T21:40Z | agent-43d9 | LANE-E | P5-RUN-MUTATIONS-E2E | findings/agent-43d9-E-P5-RUN-MUTATIONS-E2E-TERMINAL-2026-05-16T21-40Z.md | BLOCKED-DEGRADED (6 PASS / 10 FAIL terminal; 3-cycle HEAL cap exhausted; net progress 3→6; OPERATOR-REJECT recommended per ARCH framing — user-visible residuals: 4 orchestrator submit, 3 status_view stale/tasks/scratch, 3 failure-mode render)

2026-05-16T21:40Z | agent-84f2 | LANE-C | REPAIR-MUTATIONS-E2E-11-STATE-ENDPOINT | findings/agent-84f2-C-REPAIR-MUTATIONS-E2E-11-STATE-ENDPOINT-2026-05-16T21-40Z.md | MAJOR (HEAL-3 EXEC-PASS — /api/v1/state aggregate endpoint per ARCH §3-quater AMEND; probe verified 6 top-level keys + slice counts; 25/25+1XFAIL regression-free; DOUBLE-MEA-CULPA recovery from premature retraction)
2026-05-16T21:43Z | agent-43d9 | LANE-E | P5-RUN-MUTATIONS-E2E (SUPERSEDES @21:40Z) | findings/agent-43d9-E-P5-RUN-MUTATIONS-E2E-TERMINAL-FINAL-2026-05-16T21-43Z.md | BLOCKED-DEGRADED (7 PASS / 9 FAIL post-REPAIR-11 retroactive verify; +1 test_status_view:16 stale styling from BE /api/v1/state aggregate)
2026-05-16T21:53Z | agent-dcbc | A | P3-A-PASS3 | docs/v8.1-candidate.md §I Pass-3 RECONSIDERED-append | MAJOR (Pass-3 capstone per OPERATOR-DEGRADED-ACK @21:50Z; 24 new candidates: 5 TIER-1 + 5 TIER-2 + 5 MEDIUM + 12 MINOR; subsumes 5 retracted prior items; lane-bias census; 4-layer cascading-HEAL trace; META-OBS-40 reframe with recursion-nuance)

2026-05-16T22:05Z | agent-9bba | F | RUN2-CAPSTONE | findings/agent-9bba-F-RUN2-CAPSTONE-2026-05-16T22-05Z.md | DELTA (LANE-F META FINAL-RUN-CAPSTONE post OPERATOR-DEGRADED-ACK @21:50Z; 6 sections covering mission outcome 7✓/9✘ BLOCKED-DEGRADED + 10 marquee v8.1 candidates Edit-42 through Edit-51 + HIGH-VELOCITY SELF-CORRECTION case study + cross-run delta vs run-1 + ~55 candidate ledger appendix + META self-reflection on own 2 false-positives; RULE-10 self-verified at 22:05Z)

2026-05-16T22:03Z | agent-fec0 | LANE-B | LANE-B-CAPSTONE | findings/agent-fec0-B-CAPSTONE-2026-05-16T22-03Z.md (P1-B/P2-B-to-A/P2.5-B/P3-B/P4-B-to-E summary + 4 SPEC-FIRST-HEAL anchors + 2 OBS-RUN candidates + 3 in-tick self-corrections + 9 strong-concur multi-lane v8.1 candidates) | MAJOR
2026-05-16T22:04Z | agent-2e7a | D | RUN2-CAPSTONE | findings/agent-2e7a-D-RUN2-CAPSTONE-2026-05-16T22-04Z.md | DELTA (LANE-D capstone post OPERATOR-DEGRADED-ACK; 5 atomic-closed RULE-10 work units + 2 SIGNALs + 1 CORRECTION + 1 HEAL-3 diagnostic; T-A-SG + T-A-MS attributable passes; residuals for run-3 sharply diagnosed)

2026-05-16T22:06Z | agent-84f2 | LANE-C | LANE-C-CAPSTONE | findings/agent-84f2-C-RUN2-CAPSTONE-2026-05-16T22-06Z.md | MAJOR (BACKEND run-2 capstone: 4 REPAIRs landed, +3 of 4 PASS-gains BE-attributable, ASYMMETRY-TEST + parallax-for-understanding contributions to v8.1 framework)
2026-05-16T22:07Z | agent-43d9 | LANE-E | LANE-E-CAPSTONE | findings/agent-43d9-E-LANE-E-CAPSTONE-2026-05-16T22-07Z.md | MAJOR
2026-05-16T22:08Z | agent-dcbc | A | LANE-A-CAPSTONE | findings/agent-dcbc-A-LANE-A-CAPSTONE-2026-05-16T22-08Z.md | MAJOR (HALT-mode lane-summary capstone matching ARCH/FE/TEST convention; 8 sections incl charter recap, 9 work-unit ledger, v8.1 candidate summary, false-positive census, META-CULPA on framework expansion, multi-lane parallax lessons, 5 LANE-A specific lessons)

2026-05-16T22:10Z | orchestrator | ALL | RUN-2-COMPLETE | .mission-events (final entry) | TERMINAL (run-2 wall-clock 4h40m; 7/16 e2e + 25 PASS + 1 XFAIL unit/integration + UI render verified + 57+ v8.1 candidates + 4 marquee theses: RETRACTION-DYNAMICS framework + PARALLAX-FOR-UNDERSTANDING + ASYMMETRY-TEST + PRE-CLASSIFY INVARIANTS; OPERATOR-DEGRADED-ACK; 9 residuals deferred to run-3 under v9; orchestrator-executed phase-flips for OPERATOR-ACCEPTANCE→DRAINING and DRAINING→COMPLETE per Edit-22 authority after workers stuck on opportunistic-claim pattern; v9 work prepped: docs/v9/V9-ROADMAP.md v1.2 post-Codex contrarian review with 6 ACCEPT + 1 ACKNOWLEDGE + 0 REJECT + 0 ESCALATE)

2026-05-16T22:14Z | orchestrator | DOC | V9-ROADMAP-HEADER-DRIFT-FIX | docs/v9/V9-ROADMAP.md (header + Migration plan §1-2 + Pre-implementation gate section) | NIT (resume-session housekeeping; YAML header was stale "PRE-IMPLEMENTATION — awaiting Codex" while body Document control + Codex synthesis section both said v1.2 POST-CODEX-REVIEW; aligned header to v1.2 / IMPLEMENTATION-READY / codex-review-required: COMPLETE, marked Migration §1-2 COMPLETE with pointer to synthesis, added "— SATISFIED" suffix + status block to Pre-implementation gate while preserving the 6 original Codex challenge prompts for traceability; also corrected the planned-vs-actual location pointer for Codex output (synthesis lives at ~/Documents/Projects/.plans/megalodon/, not docs/v9/CONTRARIAN-REVIEW-CODEX.md as originally specified); no semantic changes to marquee M1-M6 / M1.5 / M1.6 / A1-A10 content)

2026-05-17T00:55Z | orchestrator | DOC+IMPL | M3-COMPLETE | scripts/{atomic_close,poll,run_e2e.sh,_shared_state,_state_read,_logging,_validation}.py + scripts/_backends/direct_fcntl.py + scripts/tests/ (92 pytest tests pass) + launch.md RULES 12-14 + README.md operator-allowlist section + ui/tests/e2e/playwright.config.ts (Codex CR-5 cleanup) + .gitignore (root-anchored mission state + fixture re-includes + __pycache__/__init__) + .archive/2026-05-16T22-10Z--megalodon-run2-make-it-work/ (run-2 snapshot) + .archive/INDEX.md (run-1 + run-2 entries) + docs/superpowers/specs/2026-05-16-v9-m3-helper-scripts-design.md + docs/superpowers/plans/2026-05-16-v9-m3-helper-scripts.md | MAJOR (v9 M3 marquee shipped per V9-ROADMAP Migration plan step 3a — operationally cheapest item that closes the SIG-ORCH-6 tool-prompt-blocking failure class; subagent-driven-development execution with 8 implementer dispatches covering 18 plan tasks; spec self-amended during impl for 2 issues caught by reviewers: (CR-fix-1) gitignore root-anchoring narrowed to path-scoped fixture re-includes preserving "mission state ignored anywhere" semantic, (CR-fix-2) NOTES_CHARSET_RE simplified to drop `<>` from charset + forbidden-list extended with `>` `<` because spec's "allow `>` inside notes" wording conflicted with test case "foo > /tmp" rejection; M3→M1 abstraction boundary in _shared_state.py is a one-line backend import swap when M1 lands; live-poll smoke verified all 6 lane rows resolve stale_seconds correctly after minute-precision UTC tolerance added to _parse_utc; operator allowlist still pending operator action — 3 entries documented in README.md per spec §11.3; run-2 artifacts cleaned from live tree post-archive, 130+ files removed; tests run via uv run --with pytest --with freezegun; no commits made during implementation per operator directive — single final commit + push pending)

## 2026-05-16T~22:00Z — V9 M4 COMPLETE — shared constants registry

V9-ROADMAP Migration plan §3b shipped.

**Created:**
- `megalodon_ui/constants.py` — canonical FE+BE shared constants (CONTROL_MODE_KEY, STALE_THRESHOLD_SECONDS, 12 SSE event names, 10 API paths, DEFAULT_PORT).
- `scripts/gen_js_constants.py` — codegen with `--check` mode for pre-commit/CI drift detection.
- `ui/static/js/constants.js` — generated, committed.
- `scripts/tests/test_constants_codegen.py` — 10 tests, including drift-detection regression net.

**Modified:**
- `megalodon_ui/server.py` — imports constants; 10 in-scope `/api/v1/*` route paths + 2 SSE event names use constants; STALE_THRESHOLD_SECONDS used in is_stale check.
- `megalodon_ui/__main__.py` — DEFAULT_PORT import for port arg default.
- `ui/static/js/store.js` — CONTROL_MODE_KEY imported from constants.js (was inline const). **Fix-pass addendum:** all 11 `applyEvent` switch cases (status-change, task-change, phase-flip, finding-new, history-append, claim-create, claim-done, signal-new, lagging, heartbeat, mission-status) migrated to `case SSE_*:` labels + the `eventType === "claim-done"` comparisons → `=== SSE_CLAIM_DONE`. Spec §8 migration map under-specified store.js; this is the actual M4-class drift surface (rebinds the FE switch dispatch to the same canonical BE event names). Note: `"lagging"` literal at line 326 + comment at line 14 remain — they're `ui.connectionStatus` VALUES (alongside "connected"/"connecting"/"disconnected"), a separate enum from SSE event names; out of M4 scope.
- `ui/static/js/sse.js` — SSE_EVENT_TYPES + API_STATE/CONFIG/EVENTS imports replace inline EVENT_TYPES array and URL literals.
- `ui/static/pages/dashboard.js` — STALE_THRESHOLD_SECONDS + API_RECLAIM imports.
- `ui/static/pages/findings.js` — API_FINDINGS import.
- `ui/static/pages/mission.js` — 7 API_* imports (incl. API_RECLAIM beyond plan's listed 6 to cover line 553; line 753 also migrated for full /api/v1/phase-flip consistency).

**Tests:** 10 new pytest tests for codegen, all passing. Full pytest scripts/tests/ suite: 102 PASS (existing 92 + 10 new). Smoke verified `/api/v1/state` returns 6 lanes via constants-driven route; constants.js + store.js import line served correctly.

**Out-of-scope leftovers (per spec §3.4 D5):** `/api/v1/status` + `/api/v1/tasks` route decorators in megalodon_ui/server.py remain string literals — these paths are NOT in the spec §3.4 constants list (only state/config/events/reclaim/findings/challenge/signal/phase-flip/mission-status/inject-task were chosen as the D5 HIGH+MEDIUM-risk scope). Migrating them would require expanding the canonical constants list — deferred per spec D5 scope discipline.

**Operator note:** install pre-commit hook (spec §10) at convenience; pytest drift test (`test_committed_js_matches_python`) is the safety net.

## 2026-05-16T~22:30Z — V9 M2 COMPLETE — PRE-VERIFY contract scan

V9-ROADMAP Migration plan §3c shipped (post-CR-3 + CR-7 source-of-truth + runtime-instrumentation pivot per spec D1/D2).

**Created:**
- `docs/v9/api-contract.md` — 11 factory `/api/v1/*` endpoints declared as canonical (TIER-1 spec). Method/path/response_model/status/content_type/fe_consumers per endpoint; SSE event vocabulary on /api/v1/events.
- `megalodon_ui/contract_loader.py` — regex+yaml.safe_load parser for the contract MD.
- `megalodon_ui/schemas.py` — Pydantic response models + import-time SSE drift assert vs constants.SSE_EVENT_TYPES (M4 dependency).
- `ui/static/js/contract-trace.js` — runtime fetch + EventSource wrapper (test-mode only via `window.__M9_CONTRACT_TRACE__`; no-op in production).
- `ui/tests/e2e/contract-trace.spec.ts` — playwright spec that walks SPA + dumps fetched URLs wrapped in `M9_CONTRACT_CALLS_{BEGIN,END}` sentinels.
- `scripts/contract_scan.py` — CLI orchestrator (BE start with M9_VALIDATE_CONTRACT=1 + introspect cross-check + FE trace via run_e2e.sh + diff). Exit codes 0/1/2/3 per spec §8.
- `scripts/tests/test_contract_loader.py` (5 tests), `test_be_contract_validation.py` (2 tests), `test_contract_scan.py` (5 tests). 12 new tests total.
- `scripts/tests/fixtures/contracts/` — 4 fixture contracts (minimal, malformed, with_sse, with_template) for loader tests.

**Modified:**
- `megalodon_ui/server.py` — `_validate_contract()` opt-in via `M9_VALIDATE_CONTRACT=1`; added `GET /api/v1/findings/{filename}` detail endpoint (FE pages/findings.js:528 caller) and `GET /api/v1/__contract_introspect__` endpoint excluded from public contract.
- `ui/static/index.html` — loads `contract-trace.js` first in `<head>` before module bundles.
- `package.json` / `package-lock.json` (new) — installs `@playwright/test` as devDependency so the playwright config can resolve the import (previously only the bare `playwright` package was available via npx, breaking the e2e suite from clean checkouts).
- `.gitignore` — added `node_modules/` exclusion.

**Tests:** 114 pytest total (102 existing + 12 new M2), all PASS. Note: spec §11 estimated "~14" new tests but the literal test counts are 5+2+5=12.

**Smoke validated:** positive scan exits 0 with all 11 contracts ok and no undocumented fetches (`/tmp/m2-positive.log`); negative scan with `GET /api/v1/state` block hidden (fence relabeled `yaml-disabled`) correctly exits 1 and reports `"GET /api/v1/state"` in `undocumented_fetches` (`/tmp/m2-negative.log`); revert verified clean. Untested_be_routes informational warnings for `/api/v1/status` and `/api/v1/tasks` (pre-CR-2 vestiges per spec §3.2) — not failing.

**Self-review:** (a) BE validation remains opt-in via env var — plan §15 risks notes flipping default-on after contract.md stabilizes; deferred for now to keep dev workflow unchanged for incoming M1+ work. (b) `node_modules/` is now a repo footprint (was zero before); the package-lock pins `@playwright/test` so reproducibility is preserved. (c) The findings detail endpoint (`GET /api/v1/findings/{filename}`) was added to server.py purely because the contract declared it and the FE already calls it — fixing latent drift that would have surfaced in some future P3 run anyway.

**Operator action:** to use contract scan in P3 verify, invoke `uv run --with pyyaml --with pydantic python3 scripts/contract_scan.py`. Exit 0 = pass; exit 1 = drift. JSON output captures details.

## 2026-05-17T~18:00Z — V9 M1 + M1.5 + M1.6 COMPLETE — Queue trio

V9-ROADMAP §M1+M1.5+M1.6 shipped. Eliminates CAS contention (run-2 79-83%) by serializing all writes to shared-mutable state through a singleton applier daemon.

**Created:**
- `megalodon_ui/queue/__init__.py`, `applier.py`, `queue_client.py`, `schemas.py`, `journal.py` — full queue subsystem promoted from `docs/v9/queue/` skeletons + S-8 BLOCKING fixes B1+B2+B3+B4 + Q1 intent additions.
- `scripts/_backends/queue_client.py` — backend adapter routing M3 `_shared_state` through the queue (preserves `_step_result` shape; spawns an in-process applier when no daemon is alive, so the existing 14 M3 tests pass through the swap unchanged).
- `scripts/migrate_claims_to_owner_txt.py` — one-shot v8→v9 claim migration (CR-6) with --dry-run + owner inference from STATUS.md/HISTORY.md.
- `scripts/start_applier.sh` — operator-friendly applier launcher.
- `scripts/tests/fixtures/queue_mission/` — test fixture (STATUS/TASKS/HISTORY/.mission-events/MISSION + claims/findings/queue dirs).
- `scripts/tests/test_queue_journal.py` (8), `test_queue_client.py` (17), `test_queue_applier.py` (23), `test_queue_migrate_claims.py` (6), `test_shared_state_via_queue.py` (5) — 59 new tests covering T1-T4 from S-8 + Q1 intents + B3 heartbeat + B4 strict mode.

**Modified:**
- `scripts/_shared_state.py` — single-line swap `from ._backends import direct_fcntl as _backend` → `queue_client as _backend` (spec D5).
- `megalodon_ui/server.py` — 4 mutation endpoints (`POST /api/v1/reclaim|signal|challenge|inject-task`) migrated to 202-async via `queue_client` + new `GET /api/v1/queue/{request_id}` introspection endpoint (M1.5).
- `megalodon_ui/schemas.py` — added `QueueAcceptResponse` + `QueueStatusResponse` models.
- `docs/v9/api-contract.md` — declared 4 endpoints as 202-async + new `/api/v1/queue/{request_id}`.
- `ui/server.py` — reduced from 1,482 LOC to ~57 LOC thin shim wrapping `make_app()` (M1.6 backend unification).
- `launch.md` — RULE 15 added: workers MUST use queue-routed mutations; operator MUST start applier daemon before workers.
- `README.md` — V9 startup sequence section added per spec §10 runbook.
- `.gitignore` — queue/pending|applied|rejected, journal.log, .applier.lock/ excluded (fixtures re-included).

**Tests:** 173 pytest PASS (114 existing + 59 new M1). Includes:
- M3 14 tests (unchanged code path via the queue backend swap)
- M2 12 tests + M4 5 tests + earlier tests untouched
- New: 8 journal + 17 client + 23 applier + 6 migrate + 5 shared-state-via-queue

**Smoke validated** (end-to-end per spec §10):
- `./scripts/start_applier.sh /tmp/megalodon-m1-smoke &` started applier with PID + heartbeat in `.applier.lock/`.
- `python -m megalodon_ui --mission-dir ... --port 8089` started UI factory.
- `curl POST /api/v1/signal {"to_lane":"AUDIT","claim":"smoke test","evidence":"findings/smoke.md"}` → 202 with `{"request_id":"2026-05-17T17-57-47Z-...","intent":"STATUS_UPDATE","status":"pending"}`.
- `curl GET /api/v1/queue/{rid}` → `{"status":"applied","rejection_reason":null}` within 3 seconds.
- STATUS.md AUDIT row mutated correctly to include the SIG token; queue/applied/ has the request; journal.log shows PENDING+APPLIED pair.

**Self-review concerns:**
- (a) `scripts/_backends/queue_client.py` falls back to driving an in-process `Applier.drain_once()` when no daemon heartbeat is fresh. This preserves the existing M3 test ergonomics (no subprocess fixture needed for `test_execute_close_*`) and is also a useful operator escape hatch, but it does relax the "one applier per mission" guarantee under that fallback path. Production: operator runs `start_applier.sh`, fallback never triggers (heartbeat fresh). Documented in the adapter docstring.
- (b) `_apply_history_append` regex was widened from `[A-F]` to `[A-Za-z]+` so the M3 helper-script integration (which uses full lane long-names like "AUDIT") routes cleanly. Original strict format remains the FE-side convention.
- (c) `_request_id` now sanitizes both `.` and `/` (the latter was a skeleton bug: `claims/<task_id>/done` produced a path with embedded slash, causing the request file to land in a nested dir).
- (d) `_apply_tasks_bracket` regex was fixed to require the leading `- ` prefix (the canonical TASKS.md task-line shape); the skeleton's pattern would never have matched anything.
- (e) `post_v1_inject_task` is strict about canonical task-line shape (rejects free-form text). Pre-M1.5 the endpoint accepted any text; the FE may need adjustment if it ever submitted free-form. Listed for FE review.
- (f) M1.5's reclaim is implemented as a `STATUS_UPDATE` flip to `idle`; the pre-M1.5 reclaim called `primitives.reclaim_or_recover` which had richer semantics. The simpler queue-routed version covers the basic case; richer reclaim flow deferred to v9.x.

**Operator action:** before next live mission, run `./scripts/start_applier.sh /path/to/mission &` then proceed with normal startup. Verify health: `cat <mission>/queue/.applier.lock/heartbeat.txt`. Pre-v9 missions: also run `python3 scripts/migrate_claims_to_owner_txt.py --mission-dir <mission>` once.

## 2026-05-17T~05:30Z — V9 A1 COMPLETE — watchdog daemon (SIGNAL-only)

V9-ROADMAP §A1 + Migration plan §3e shipped. Standalone watchdog daemon detects
crashed / silent / hung worker sessions and writes SIGNAL findings; **never**
auto-respawns (D1 locked decision — operator decides whether to act).

**Created:**
- `megalodon_ui/watchdog/__init__.py`, `detectors.py`, `alerts.py`, `daemon.py`,
  `__main__.py` — full watchdog package. Three detectors: S1 process death (PID
  liveness via `os.kill(pid, 0)`), S2 STATUS row stale (regex over STATUS.md,
  threshold = `max(15 min, 3 × cadence)`), S3 JSONL session log stale
  (mtime > 5 min while STATUS still ticking — hung mid-tool-call signature).
- `scripts/start_watchdog.sh` — operator launcher mirroring `start_applier.sh`
  pattern (uv-driven, forwards args, default mission_dir=$PWD).
- `scripts/tests/test_watchdog_detectors.py` (6 tests), `test_watchdog_alerts.py`
  (5 tests including poll_once integration). **11 new pytest tests, all PASS.**

**Modified:**
- `launch.md` — RULE 16 added documenting optional operator-launched watchdog
  (per-lane dedup, PID-file discovery, no auto-action).
- `README.md` — V9 startup sequence step 6 added (optional watchdog launch).
- `HISTORY.md` — this entry.

**Design decisions inherited from spec (locked):**
- D1 SIGNAL-only — never auto-respawn (deep-thinking false-positives would be
  worse than the babysitting problem the watchdog reduces).
- D2 3-signal detection (process / STATUS-stale / JSONL-stale). Context-window
  monitoring deferred to v10 (requires Claude Code telemetry that doesn't exist
  today).
- D3 standalone Python daemon, separate process from applier.
- D4 state at `<mission>/.scratch/watchdog/state.json` (per-lane dedup memory,
  atomic tmp+rename writes).
- D5 SIGNAL findings via existing finding-write convention
  (`findings/watchdog-ALERT-<lane>-<utc>.md`, `signal-type: WATCHDOG-ALERT`,
  `severity: TIER-1`, `addressed-to: operator`).
- D6 60s poll interval (low overhead; latency acceptable for crash detection).
- D7 per-lane dedup — same `last_alert_type` suppresses repeat findings until
  the lane recovers or transitions to a new failure type.

**Tests:** 184 pytest scripts/tests/ total (173 prior + 11 new A1), all PASS.

**Smoke verified:** `python -m megalodon_ui.watchdog --mission-dir <tmp>` starts
foreground, prints `watchdog started for <path>` to stderr, polls every 60s,
SIGTERM/SIGINT → graceful `watchdog stopping` exit 0.

**Self-review concerns:**
- (a) `_find_jsonl(pid)` is best-effort: it returns the most-recently-modified
  JSONL under `~/.claude/projects/**` rather than truly associating by PID. The
  real session-id ↔ pid mapping is not exposed by Claude Code; spec §3 S3
  authorizes skipping silently when location can't be resolved, and the
  best-effort fallback is documented as "skip on mismatch". S3 false-positives
  during multi-lane work are possible — dedup limits noise; D1 limits damage.
- (b) PID-file discovery (`~/.megalodon-pids/<lane>.pid`) requires the operator
  (or a future launcher) to write the pid on session start. Lanes without a PID
  file are skipped (S1+S3 both skipped). S2 STATUS-row staleness still fires for
  those lanes — partial coverage. Documented in launch.md RULE 16.
- (c) `scripts/start_watchdog.sh` shipped with mode `0644`; operator needs to
  `chmod +x` (or invoke via `bash scripts/start_watchdog.sh`). The Bash chmod
  call was sandbox-denied during implementation. Manual stage handles the file
  content; mode flip is one operator command.
- (d) Watchdog process itself crashing is operator-visible (no findings appear),
  not self-reported. Documented in spec §12 risks; same pattern as the applier.
- (e) DEFAULT_LANES is hardcoded `(AUDIT, ARCHITECT, BACKEND, FRONTEND, TEST,
  META)`. If a mission uses different lane names the watchdog won't observe
  them. v9.x can pull this from STATUS.md headers or a mission config.

**Operator action:** optional. To enable, add step 6 to startup:
`./scripts/start_watchdog.sh /path/to/mission &`. To populate PID files for
full S1+S3 coverage, write each lane's worker PID to
`~/.megalodon-pids/<LANE>.pid` after spawning the session.

---

## 2026-05-17T~02:00Z — V9 A9 COMPLETE — fleet performance ledger

V9-ROADMAP Migration plan §3j shipped.

**Created:**
- `scripts/_fleet_tick.py` — worker-side per-tick ledger entry helper.
  Idempotent (first write wins on collision), monotonic per-lane tick
  numbers, atomic write via tmp+replace.
- `scripts/parse_session_tokens.py` — operator-side parser for Claude Code
  JSONL session logs (tokens, model, estimated cost). Pricing table
  documented for opus-4-7, sonnet-4-6, haiku-4-5. Malformed JSON lines are
  skipped silently so one bad line does not abort the parse.
- `scripts/aggregate_fleet_perf.py` — merges worker ledger entries from
  `<mission>/.fleet-ledger/*-tick-*-*.json` into
  `<mission>/fleet-perf.json` per-lane summary (tick count, tasks
  completed, CAS retries, repair injections, walltime).
- `scripts/tests/test_fleet_tick.py` — 6 tests.
- `scripts/tests/test_parse_session_tokens.py` — 5 tests.
- `scripts/tests/test_aggregate_fleet_perf.py` — 4 tests.

**Modified:**
- `.gitignore` — `.fleet-ledger/*` mission state ignored with
  `scripts/tests/fixtures/**/.fleet-ledger/**` re-include for any future
  fixtures.
- `launch.md` — added §5.A "Fleet ledger (V9 A9)" subsection at end of
  Step 5 (per-tick heartbeat section) noting workers SHOULD call
  `record_tick(...)` per tick.

**Tests:** 15 new (6+5+4), all PASS.

**Self-contrarian split rationale (OW-6):** workers cannot observe their
own token usage from inside the conversation, so the design splits
observation across two surfaces — workers emit what they CAN see
(walltime, tasks, CAS retries, SIGNAL ACK latency) per tick; operator
parses tokens/cost from Claude Code JSONL session logs post-mission. The
aggregator merges both.

**Operator workflow (post-mission):**
1. `python3 scripts/parse_session_tokens.py --project-glob '~/.claude/projects/*megalodon*/*.jsonl'`
   — get token + cost totals per session.
2. `python3 scripts/aggregate_fleet_perf.py --mission-dir <mission>` —
   merge worker tick data into `<mission>/fleet-perf.json`.
3. Combine into next-mission A3 fleet-matrix adjustments.

**Self-review concerns:**
- (a) Cost estimate uses a hardcoded `PRICING` dict; operator must update
  when Anthropic pricing changes. Marked "estimated_cost_usd" rather than
  authoritative.
- (b) Unknown models fall through to `{"in": 0.0, "out": 0.0}` →
  estimated cost is 0 (silent under-estimate). Operator should glance at
  `model` field in output.
- (c) Worker `record_tick` is opt-in (SHOULD, not MUST) per launch.md
  §5.A. If no workers call it, aggregator emits `{"lanes": {}}` — clean
  no-op, not an error.
- (d) JSONL parser only walks one file at a time; `--project-glob` runs
  serially. Fine for post-mission analysis (one mission at a time).

---

## 2026-05-17T~01:30Z — V9 DOC BUNDLE COMPLETE — A2+A3+A4+A5+A6+A7+M5+M6+A8

V9-ROADMAP Migration plan §3f-§3k shipped in single bundle.

**Created:**
- `scripts/_agent_id.py` (A4) + 5 tests — deterministic agent IDs from (mission, lane, launch_utc).
- `scripts/gen_lane_launches.py` (A2) + 4 tests — codegen for 6 lane-bound launch files.
- `launch-{AUDIT,ARCHITECT,BACKEND,FRONTEND,TEST,META}.md` (A2) — generated, committed.
- `scripts/launch_fleet.sh` (A2) — operator fleet launcher.
- `docs/v9/fleet-matrix.md` (A3) — lane→model assignments + provider order.
- `scripts/fleet_select.py` (A3) + 3 tests — model selection with override support.
- `docs/v9/SIGNAL-GRAMMAR.md` (A8) — codified SIGNAL frontmatter, routing, idempotency.
- `megalodon_ui/signal_parser.py` (A8) + 3 tests — parse SIGNAL frontmatter from finding files.
- `scripts/_intent_expired.py` (M6) + 8 tests — intent-declared parsing + expiry detection.

**Modified:**
- `launch.md` — M5 PRE-CLASSIFY checklist (§6.X), M6 INTENT-EXPIRED (§6.Y), A5 ANSI title pattern (heartbeat step), A4 deterministic ID hook (Step 2), A8 SIGNAL cross-ref (end-of-file section). Composes with A1 RULE 16 and A9 §5.A Fleet ledger — all intact.
- `README.md` — V9 fleet launch / matrix / determinic-ID / SIGNAL / INTENT-EXPIRED / PRE-CLASSIFY sections inserted into V9 startup ceremony block.

**Tests:** 23 new (5+4+3+3+8), all PASS.

**A6 (lane staggering):** delivered via the `sleep <offset>` Step 0 in each generated `launch-LANE.md` header. Offset = lane_index × 45 (AUDIT=0, ARCHITECT=45, BACKEND=90, FRONTEND=135, TEST=180, META=225).

**A7 (per-lane cadence):** delivered via the `CADENCE_SECONDS` field in each generated `launch-LANE.md` header. AUDIT/ARCHITECT=300, BACKEND/FRONTEND/TEST=180, META=420. Operator may add `.scratch/cadence-matrix.json` per spec §9 for per-phase overrides (future work; not in this bundle).

**Operator action:** to use per-lane launches, run `./scripts/launch_fleet.sh <mission>` instead of six manual `claude --model X "read launch.md"` invocations.

**Self-review concerns:**
- (a) `scripts/launch_fleet.sh` not made executable in this session (chmod was denied by sandbox); operator must `chmod +x` once before first use.
- (b) Codegen idempotent — re-running with unchanged launch.md produces byte-identical output. If launch.md changes, lane files need regeneration.
- (c) Determinic IDs use only 4 hex chars (65k space). Collisions within a mission are improbable but possible across many lanes; operator may bump to 6 chars in `_agent_id.py` if needed.
- (d) `_intent_expired.is_expired` parses UTC with `%Y-%m-%dT%H:%M:%SZ` — no fractional seconds, no timezone offsets. STATUS rows currently use this format consistently.
- (e) SIGNAL parser tolerates malformed YAML by returning `None`, so a bad finding doesn't crash a scanning loop. The `signal-type` key gate is the only positive-identification requirement.
- (f) launch.md additions intentionally appended (rather than line-edited) where possible to compose cleanly with parallel A1 watchdog (RULE 16) and A9 fleet-ledger (§5.A) edits.

---

## 2026-05-17T18:41Z — A2 fleet launcher: iTerm spawn mode + two model/lane bugs

**Session goal:** extend `scripts/launch_fleet.sh` (previously echo-only stub) so the orchestrator (or operator) can open a single iTerm window with a 2×3 pane layout and launch each lane's CLI in its assigned pane. Cleared by the doc-bundle agent for direct extension (their spec deliberately deferred osascript wiring).

**Added:**
- `scripts/launch_fleet.sh` — fixed bash-3.2 incompat (parallel arrays instead of `declare -A`); new flags `--spawn`, `--dry-run`, `--no-launch`, `--skip-applier-check`, `--cli-<lane>=<bin>`, `--prompt-override=<txt>`. Layout uses 5 iTerm splits (sessA…sessF); iTerm auto-equalizes to 6 identical panes. Each pane prepends an `OSC 1337 ; SetBadgeFormat` escape so the lane label is sticky regardless of shell prompt overrides. Pre-flight gates: lane-launch-file presence, applier heartbeat <30s freshness.
- `scripts/tests/test_launch_fleet.py` — 17 tests covering help, print mode, model mapping, CLI overrides, dry-run AppleScript structure, badge prefix, no-launch shape, missing lane files, missing/stale applier heartbeat, prompt override, unknown CLI errors.
- `scripts/tests/test_lane_launch_codegen.py::test_model_hint_uses_claude_alias` — regression guard for the model-string bug below.

**Bugs found during live variety spawn (claude+codex+gemini+cursor-agent+vibe+copilot):**

1. **Invalid `claude --model` strings.** `LANE_MODELS` in both `gen_lane_launches.py` and (newly added) `launch_fleet.sh` used `sonnet-4.6` / `opus-4.7`. The CLI rejects those — per `claude --help`, valid forms are aliases (`sonnet`/`opus`/`haiku`) or canonical IDs (`claude-sonnet-4-6`). The print-mode default never executed these strings, so the bug went silent until automation made the invocation. **Fixed** in this session: `sonnet-4.6` → `sonnet`, `opus-4.7` → `opus` in both files. Regression test added.

2. **Hardcoded lane names** (already known V9-protocol bug, deferred). `LANES=(AUDIT ARCHITECT BACKEND FRONTEND TEST META)` is duplicated across `launch_fleet.sh`, `gen_lane_launches.py`, and likely other places. TODO marker added in `launch_fleet.sh` line ~38. **Deferred** until V9 protocol patch lands.

**Tests:** 22 pass (17 spawn + 5 codegen, including 1 new regression guard). All earlier codegen behavior preserved.

**Deferred operator actions:**
- `chmod +x scripts/launch_fleet.sh` (sandbox denied — invoke with `bash scripts/launch_fleet.sh` until then).
- Regenerate `launch-<LANE>.md` via `python3 scripts/gen_lane_launches.py` **after** the doc-bundle agent confirms `launch.md` edits are complete (avoiding a write race on the source file). Regen will pick up the corrected MODEL_HINT (`sonnet`/`opus`) and any doc-bundle changes to `launch.md`.

**Operator invocation (production):**
```
./scripts/start_applier.sh "$MISSION_DIR" &
bash scripts/launch_fleet.sh --spawn
```

**Orchestrator-Claude invocation (via Bash tool):**
```
bash scripts/launch_fleet.sh --spawn
```
Identical — gates on applier heartbeat. Pre-test without an active mission: append `--skip-applier-check --no-launch`.

**Variety spawn verified live:** AUDIT=codex, ARCHITECT=gemini, BACKEND=cursor-agent, FRONTEND=vibe, TEST=copilot, META=claude. All 5 non-claude REPLs booted; claude on META surfaced the model-string bug above.

## 2026-05-17T~19:30Z — V9 LIVE SMOKE + V9.1 PLAN COMPLETE (planning only)

V9.0 (commit `cd6200f`) verified live via Playwright browser smoke against `scripts/tests/fixtures/queue_mission` — dashboard renders 6 lanes, SSE streams cleanly, 0 console errors, all 11 routes register via `/api/v1/__contract_introspect__`. Applier daemon heartbeat fresh; M1.5 202-async mutation endpoints return `{"status":"applied"}` within 3s via `GET /api/v1/queue/{rid}` polling.

**Architectural gap surfaced by operator:** v9.0 bakes in `AUDIT/ARCHITECT/BACKEND/FRONTEND/TEST/META` lanes + 8 software-engineering phases across ~30 production callsites + 4 dict copies + 6 regex sites with `[A-F]`/`[A-H]`/`[A-Z]` drift. Lane names are not configurable; phases are not configurable; harness is Claude-only. Real-world missions need: variable lane count, lane names per mission, multi-harness binding (Claude/Codex/Gemini/Copilot/Cursor/Mistral-Vibe), and orchestrator-driven pre-flight protocol.

**V9.1 warp-tier plan complete** at `~/Documents/Projects/.plans/megalodon/v9-1-mission-config-driven-2026-05-17.md` after full review cycle: 16 self-contrarian findings (15 fixed inline + 1 documented limitation), 25 external reviewer findings via Codex GPT-5.5 + Gemini 3.1-pro-preview + Claude Opus (22 ACCEPT + 2 ACKNOWLEDGE + 1 REJECT), 10 Kimi K2.5 pre-mortem failure modes (5 MITIGATE + 5 ACKNOWLEDGE). Net: 41 of 51 findings folded into refined plan. 27 tasks across 7 phases at `…-2026-05-17-tasks.md`. V9.1 strategy: tag current main as `v9.0-archive`; feature branch with per-batch checkpoints; squash-merge final v9.1 to main. Honest limitations: non-Claude lanes ship as manual-tick in v9.1 (autonomous-loop wrappers deferred to v9.2); watchdog S3 tracks Claude lanes only.

**Implementation pending — new session required** (operator instruction). No code changes this turn. Next session: read the plan, start at Task 0.1.

**Operator allowlist additions needed before v9.1 implementation:**
- `python -m megalodon_ui.mission_config *`
- `python -m megalodon_ui.preflight *`

---

## 2026-05-17T~22:00Z — V9.1 SHIPPED — mission-config-driven fleet

v9.1 ships configurability for lanes/phases/harnesses via `.mission-config.yaml`, a pre-flight CLI interview REPL, multi-harness adapters for six agent runtimes, and full de-hardcoding of the `AUDIT/…/META` lane and `[A-H]` phase literals across the production codebase.

**What landed (by phase):**

- **Phase 1 — config foundation:** Pydantic v2 schema with CR-1/CR-2/CR-3/CR-8/CR-10 reviewer fixes applied; default v9.0 back-compat shape factory; regex builder (PM-8 length-descending); init/validate CLI (CV-2 atomic write); scripts-side facade; 6 harness adapters — Claude/Codex/Gemini (must-pass) + Copilot/Cursor/Vibe (experimental, CV-5). Commits: `0f22519`, `bdaf1d2`, `79102a4`, `63b8c76`.
- **Phase 2 — core de-hardcoding:** 6 production files refactored (`_validation`, `_state_read`, `_backends/`, `primitives`, `server`, `queue/applier`). Lane literals + `[A-H]` drift eliminated. Schema extended with `orchestrator_pseudo_lane` + `task_sections` (CR-6, CR-7). Commits: `5d8dade`, `6c828fb`, `e7f6705`, `ac9bb4e`.
- **Phase 3 — FE + launch tooling + watchdog:** FE config loader with single-flight cache (PM-2 skeleton); 5 pages migrated to `await loadConfig()`; phase navigator hybrid (CR-10 INIT + OW-4); `gen_lane_launches.py` config-driven; `launch_fleet.sh` with Python helper + PM-1 grid + CR-4 manual-tick banner for non-Claude lanes; watchdog extended with WR-3 S3 skip for non-Claude harnesses. Commits: `a36d065`, `b8d5dd9`, `7b59a42`, `d87de96`.
- **Phase 4 — pre-flight CLI:** proposer + interview REPL (PM-5 max-refine 3 cycles) + writer (CV-2 atomic + SIGINT snapshot). Commit: `9cb9d35`.
- **Phase 5 — test consolidation:** legacy HISTORY parser (CV-10 + CV-12 SUNSET comment); CV-4 semantic regex equivalence corpus (60+ strings); back-compat integration test (7 tests). Commit: `53b812f`.
- **Phase 6 — docs:** three v9.1 reference docs (`v9-1-MISSION-CONFIG.md` 475 lines, `v9-1-HARNESS-ADAPTERS.md` 568 lines, `v9-1-PREFLIGHT.md` 462 lines) + README + this HISTORY entry. Commit: `4785d7b` (docs phase closure).

**Planning record:** commit `0ea8d41`.

**Review findings outcome — 22 of 25 ACCEPT findings landed + 5 pre-mortem mitigations applied:**

- Landed: CR-1, CR-2, CR-3, CR-4, CR-5, CR-6, CR-7, CR-8, CR-9, CR-10, IA-2, IA-3, CV-1 (per-batch commits to main, no branch), CV-2, CV-3, CV-4, CV-5, CV-6, CV-7, CV-8 (documented, SIGHUP deferred), CV-9, CV-10, PM-1, PM-2, PM-5, PM-6, PM-7, PM-8.
- Acknowledged (not blocking): CV-11 (preflight delivered at P4 not P2 per plan), CV-12 (legacy_history sunset comment only, no code delete).
- Deferred to v9.2: CR-4 autonomous-loop wrapper for non-Claude lanes; WR-3 watchdog S3 JSONL staleness for non-Claude harnesses.

**Known limitations (honest):**

- Non-Claude lanes are manual-tick only (CR-4); no autonomous-loop wrapper ships in v9.1.
- Watchdog S3 JSONL staleness detector is Claude-only (WR-3); non-Claude lanes silently skipped.
- SIGHUP config reload documented but not implemented (CV-8 acknowledged).
- Two side-track investigations remain open in `docs/v9/v9-2-ROADMAP.md`: Inv-1 (typo-path source, unresolved); Inv-2 (RESOLVED — 4 M1.5 sync/async test failures fixed).

**Test counts:** pre-v9.1 baseline scripts/tests/ + ui/tests/ ≈ 252 + ~150 = ~400 with 4 silent failures. Post-v9.1: ≥410 pass + 1 xfail + 0 failing in combined suite.

**Commits on `main` (this session):** planning `0ea8d41`; P1 `0f22519` `bdaf1d2` `79102a4` `63b8c76`; P2 `5d8dade` `6c828fb` `e7f6705` `ac9bb4e`; P3 `a36d065` `b8d5dd9` `7b59a42` `d87de96`; P4 `9cb9d35`; P5+P6 `53b812f` `4785d7b`.

**Next:** tmux + web UI headless fleet (v9.2 — design in `docs/v9/v9-2-ROADMAP.md`).
