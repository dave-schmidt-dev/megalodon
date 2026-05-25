# Megalodon Coordination Protocol

A blackboard multi-agent coordination protocol for parallel review, audit, synthesis, and similar deep-work missions across multiple Claude sessions.

**Version:** v9.1 (effective 2026-05-17; mission-config-driven fleet)
**Last updated:** 2026-05-17
**Default cadence:** 3 minutes (configurable in MISSION.md or `.mission-config.yaml`)

> **Current state (2026-05-25):** active work toward a *barely-workable autonomous
> run* is tracked in **[`docs/v10-readiness-plan.md`](docs/v10-readiness-plan.md)**.
> A 6-agent bug-hunt + fix wave landed (board, narrator, loop heartbeat, perms,
> queue, frontend); the fleet now restarts cleanly into PHASE-PLAN with seeded
> tasks and lanes that claim real work. **Top open risks:** lanes still stall on
> shell exploration (`find`) in the first minutes (§1b), and there is no operator
> visibility into the narrator (§1c). See the plan's §8 for tomorrow's sweep.

**v9.1 shipped 2026-05-17:** mission-config-driven lanes/phases/harnesses via `.mission-config.yaml`. Operators define lane names, phase sequences, and per-lane harness bindings in one YAML file; the fleet reads it on every tick. Three reference docs: `docs/v9/v9-1-MISSION-CONFIG.md` (schema + examples), `docs/v9/v9-1-HARNESS-ADAPTERS.md` (Claude/Codex/Gemini must-pass + Copilot/Cursor/Vibe experimental), `docs/v9/v9-1-PREFLIGHT.md` (pre-flight CLI interview REPL). Quick-start: `python -m megalodon_ui.mission_config init` writes the default software-engineering template; `python -m megalodon_ui.preflight "<goal>"` opens a Claude-assisted interview and proposes a config. v9.0 missions with no `.mission-config.yaml` keep working — a back-compatible shape is auto-synthesized. Known limitations: non-Claude lanes are manual-tick in v9.1 (CR-4); watchdog S3 JSONL staleness detector is Claude-only (WR-3). Both deferred to v9.2 (`docs/v9/v9-2-ROADMAP.md`).

## v9.2 — Tmux headless fleet (SHIPPED 2026-05-18)

- **Status:** SHIPPED. All seven phases closed (P0 pre-flight, P1 server-owned tmux spawn, P2 cookie auth, P3 stream tap, P4 SSE pane-stream, P5 xterm.js dashboard, P6 follow-up prompts + respawn, P7 destructive teardown + watchdog + docs). Two real-tmux tests run CI-Linux-only via `pytest -p forked -m isolated` (macOS hits the 104-byte tmux socket-path limit on `tmp_path`).
- **Doc set:** `docs/v9/v9-2-TMUX-FLEET.md` (architecture + operator runbook), `docs/v9/v9-2-AUTH.md` (bootstrap flow, cookie semantics, paste-token recovery), `docs/v9/v9-2-FOLLOWUP-PROMPTS.md` (adapter contract + respawn semantics + sentinel chunk). The earlier `docs/v9/v9-2-ROADMAP.md` is SUPERSEDED.
- **Entry point:** `python -m megalodon_ui --mission-dir <path> --host 127.0.0.1 --port 8000`. The bind-fd-first sequence (see `megalodon_ui/__main__.py`) binds the listener BEFORE handing the fd to uvicorn; this closes the v9.1 OW-2 probe-close-rebind race. Exits: 6 (tmux < 2.6), 7 (mission dir invalid), 8 (token write failed), 9 (EADDRINUSE), 10 (socket path too long), 11 (lifespan startup timeout), 12 (disk free < 50 MB).
- **Dashboard URL recovery:** the bootstrap URL is written to `<mission>/.fleet/dashboard.url` at startup. `cat <mission>/.fleet/dashboard.url` re-opens the dashboard from any shell (CV-11).
- **Destructive teardown:** `DELETE /api/v1/fleet` (cookie-gated) or standalone `python -m megalodon_ui.shutdown --mission-dir <path>` (CLI; idempotent). Both kill the tmux server + unlink `ui.token`, `tmux.sock`, `dashboard.url`.
- **`MEGALODON_FLEET_OWNED=1`** session-scoped env marker protects operator-created `lane-*` tmux sessions from orphan cleanup. Documented in `v9-2-TMUX-FLEET.md` §3.
- **Preview without spawning:** `python -m megalodon_ui.preview --mission-dir <path> [--include-tmux-argv]`.
- **Launcher:** `scripts/launch_fleet.sh` has three modes — `print` (default; delegates to preview), `--dry-run` (preview + tmux argv), `--spawn|--exec` (handoff via `exec uv run python -m megalodon_ui`). The `--no-launch` flag was removed in v9.2 (CV-4).
- **Runtime state directory:** `<mission>/.fleet/` holds `tmux.sock`, `ui.token` (mode 0600), `dashboard.url`, per-lane `<short>.stream.log`, and per-lane `<short>.session.txt`. Always gitignored; test fixtures under `scripts/tests/fixtures/**/.fleet/**` are re-included.
- **Watchdog (P7.3):** stream-log size warn at `STREAM_LOG_WARN_BYTES` (500 MB) — emits `STREAM-LOG-SIZE` SIGNAL finding. Never auto-rotates.
- **Test mode:** the lifespan honors `MEGALODON_LIFESPAN_TEST_MODE=1` to bypass fleet spawn for integration tests that exercise request handlers without a real tmux. Set automatically by the `async_client_with_lifespan` fixture and the `scripts/tests/conftest.py` autouse fixture.
- **Plan archive:** `~/Documents/Projects/.plans/megalodon/v9-2-tmux-headless-fleet-2026-05-17.md` (plan v1.4) + `…-tasks.md`. See `HISTORY.md` for the implementation log.

## v9.4 — Dashboard rebuild + run lifecycle (SHIPPED 2026-05-20 / lifecycle 2026-05-22)

- **Status:** SHIPPED. Dashboard FE fully rewritten. 30 of 31 tasks complete; dogfood gate (T4.3) in progress (lifecycle + harness ready; dogfood is the next operator step).
- **Grid page** (`/lane/:short`) — replaces flat terminal layout with N-pane grid (config-driven; click a lane tile to open lane_detail modal with inject form, stale badge, restart-loop button).
- **Activity wall** — right-side panel merging 6 event sources (findings, signals, history, queue events, inject log, approval decisions). Filter chips by source type, pause button, expandable details drawer.
- **Stale-lanes detection** — mission header shows count of lanes exceeding 15-min staleness threshold. Restart-loop button triggers per-lane loop restart.
- **Approve & remember flow** — operator selects a finding → extracts pattern via regex modal → persists rule to `.fleet/approval-rules.json` → merged into `--allowedTools` at next spawn, **after the `_is_unbounded_tool` filter drops any interpreter/network/compound pattern** (2026-05-22 tool-surface policy — "approve & remember" can never re-admit `python`/`curl`). Complete audit trail in approval-rules page (CRUD UI).
- **Page rewrites** — 6 pages migrated to v9.4 patterns: findings (severity filter + search), signals (sortable columns), mission (orchestrator actions), tasks (kanban board), approval_rules (new page), grid (N-pane layout).
- **Backend endpoints** — 5 new: `POST /api/v1/lane/{short}/inject`, `POST /api/v1/lane/{short}/restart-loop`, `GET /api/v1/lanes/stale`, `GET /api/v1/activity-wall` + `POST /api/v1/activity-wall/snapshot`, `GET|POST|DELETE /api/v1/approval-rules` + `POST /api/v1/approval-rules/extract`.
- **PermissionWatcher.on_change callback** — (lane, info, action) signature where action is approve/approve_remember/deny. Activity wall surfaces these lifecycle events.
- **Migration note:** Fresh `.fleet/` required. Old `approval-rules.json` from prior runs is ignored (schema unversioned by design).
- **Test coverage:** 795 Python tests pass (+126 v9.4 tests). Playwright 23 chromium-grid tests green. Pre-existing v9.3-era failures on deprecated surface (v92-dashboard, default) preserved intentionally.
- **Plan archive:** `~/Documents/Projects/.plans/megalodon/v9-4-dashboard-rebuild-2026-05-19.md` (plan v2 warp-complete) + tasks + synthesis + reviews. See `HISTORY.md` "V9.4 SHIPPED" for full manifest.

## Narrator summary board (SHIPPED 2026-05-24)

- **Status:** SHIPPED. Phases 1–4 complete on `origin/main`, plus post-ship board enhancements (CR-4 blocked pill, staleness modal, narrator-on-Last). See also the "Persistent sessions + dashboard auto-open" section below (Phase 5). Current full gate: 1056 Python tests passed / 34 skipped / 3 xfailed; 12 isolated (`--forked`); Playwright 171 passed / 8 skipped, across 12 projects.
- **Board is the default fleet view at `/`** — `ROUTES[0]` now loads `board.js`; `grid.js` was deleted. One row per lane shows **Last / Now / Goal** + a state pill + token count + inline approve/deny + a click-to-open terminal drawer. The state pill shows BLOCKED when a lane has a blocked task (precedence: blocked > claimed > done > open). Clicking a STALE pill opens a staleness details modal.
- **Column sourcing:** Last (latest closed task id + description) and Goal (claimed task description, else lane role) are **deterministic** — they render from mission state with no model involved. **Now** and **Last** both carry advisory narrator phrases — each produced by its own independent single-phrase call. When narrator is unavailable, deterministic text is the fallback. Deterministic facts are load-bearing; the narrator supplies advisory prose only.
- **Narrative endpoints** (both session-cookie gated): `GET /api/v1/narrative` (cached map, initial paint) and `GET /api/v1/narrative-stream` (SSE, activity-wall pattern, watcher-gated).
- **Narrator runtime:** a supervised local `llama-server` subprocess serving the locked **gemma-e2b** model over an OpenAI-compatible API. Wired into the FastAPI lifespan (live branch only): `runtime.start()` is non-blocking, so **the dashboard serves immediately regardless of narrator readiness**. Lanes show a "narrator offline" dot until `/health` passes. The scheduler narrates only while ≥1 SSE subscriber is connected. Clean teardown in the lifespan `finally` block.
- **Degraded mode (CV-6/CR-8):** missing model file, missing/incompatible `llama-server` binary, or a held port all degrade to stable "not ready" (a single WARNING after a consecutive-failure ceiling; never fatal). The dashboard and all deterministic fields keep working.
- **External prerequisite (CR-8):** `llama-server` (current llama.cpp) must be on `PATH`, exposing `/health` and `/v1/chat/completions`. It is NOT a Python dependency and cannot be pinned in `pyproject.toml`. Full locked argv (from `megalodon_ui/narrator/runtime.py` `_build_argv`): `llama-server -m <model> --alias narrator --chat-template-kwargs '{"enable_thinking":false}' -ngl 99 -c 8192 --jinja --host 127.0.0.1 --port <port>`.
- **Plan archive:** `~/Documents/Projects/.plans/megalodon/narrator-summary-board-2026-05-23.md` + tasks. Spec: `docs/superpowers/specs/2026-05-23-narrator-summary-board-design.md`.

### Narrator environment variables

Read at lifespan start from `megalodon_ui/narrator/runtime.py` (`from_env()`) and `megalodon_ui/server.py`. All optional.

| Variable | Default | Effect |
|---|---|---|
| `MEGALODON_NARRATOR_URL` | (unset) | Use this already-running OpenAI-compatible base URL; skip spawning a subprocess (best-effort health-gated). |
| `MEGALODON_NARRATOR_MODEL` | `~/models/narrator-bench/gemma-e2b/gemma-4-E2B-it-Q4_K_M.gguf` | GGUF model path passed to `llama-server -m`. Missing file → degraded, non-fatal. |
| `MEGALODON_NARRATOR_PORT` | `8085` | Local port `llama-server` binds (also forms the client base URL). |
| `MEGALODON_NARRATOR_TIMEOUT_S` | `6.0` | Per-narrate request timeout in seconds. |
| `MEGALODON_NARRATOR_INTERVAL_S` | `30` | Scheduler tick interval in seconds, clamped to `[15, 120]`. |

## Persistent sessions + dashboard auto-open (SHIPPED 2026-05-24)

- **Status:** SHIPPED (Phase 5, D1–D6). Best-effort UX for a single-operator localhost tool — explicitly **not** a security boundary (see caveats below).
- **An open dashboard tab survives a server restart.** Sessions are persisted to `.fleet/sessions.json` (hashed at rest — only the SHA-256 digest of each session id is stored, never the raw cookie) and the bearer token in `.fleet/ui.token` is **stable** across restarts (reused, not regenerated). So after you `Ctrl-C` and relaunch, an already-open tab's `mui_session` cookie still validates and its SSE `EventSource`s auto-reconnect with **no manual re-auth** — no paste-token modal.
- **Observed auto-open (no tab-spam):** a relaunch does **not** unconditionally open a new tab. The live-branch lifespan watches the authenticated SSE subscriber count for `MEGALODON_DASHBOARD_OPEN_GRACE_S` seconds (default **8**); if a live tab reconnects within that window it opens **nothing**, and only opens a fresh tab if no tab reconnects. The full token-bearing URL is still printed to stdout immediately on every launch regardless.
- **`--rotate-token`:** the explicit rotation path. It deletes `.fleet/ui.token` + `sessions.json` **before** the app builds, so all prior cookies are revoked, a fresh token is minted, and a new authenticated tab is force-opened. This replaces the old (illusory) per-launch auto-rotation.
- **`--no-browser`** (or `MEGALODON_NO_BROWSER=1`): forces auto-open OFF. Always used by the e2e/test webServers so a test run never spawns browser tabs.
- **Caveats:**
  - Persisted credentials assume a **localhost, single-operator** bind. If `--host` resolves to a non-loopback address the server logs a WARNING — persisted creds + non-local bind is unsupported.
  - Two servers on the same mission (different ports) share one `sessions.json` and can clobber each other's writes; unsupported.
  - Expiry uses wall-clock (`time.time`, 24h), so clock jumps can shorten/extend a session.
- **Verification:** `ui/tests/e2e/test_restart_reconnect.spec.ts` (PW-3) spawns a real server, authenticates a tab, kills + respawns the server against the same `.fleet`, and asserts the cookie + SSE reconnect without the paste-token modal. Spec: `docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md`.

## Run lifecycle (v9.4 convention)

Every mission run is scaffolded into a self-contained `runs/<UTC>--<slug>/` subdir, then archived to `.archive/<UTC>--<slug>/` with an `INDEX.md` entry when complete. No run leaves ephemera in the repo root.

- **Scaffold:** `scripts/new_run.sh <slug> [--title T] [--summary S] [--exit-criteria X] [--force]`
- **Pre-flight gate:** `scripts/preflight.sh [--dry-run]` — four automated checks (pytest scope, test deps, friction allowlist, lifecycle smoke round-trip) plus a manual loops-armed gate.
- **Archive:** `scripts/archive_run.sh <run-dir> [--force]` — transactional `git mv` to `.archive/`, registers one INDEX row.
- **Liveness:** determined by `.mission-events` — terminal tokens are `COMPLETE | ABORTED | DEGRADED-CLOSE`.
- **Dashboard visibility gate:** `runs_harness/stimulus.py` (stale-lane + signal-fidelity assertions) + `ui/tests/e2e/visibility.spec.ts` (snap-back, tab-highlight, activity-wall fidelity, empty-state).

Full convention: `docs/v9/v9-4-RUN-LIFECYCLE.md`. API contract: `docs/v9/api-contract.md`.

## v9.1 startup sequence

New to v9.1? Start here before the v8 section.

- **0. Read** `docs/v9/v9-1-MISSION-CONFIG.md` for schema reference and worked examples.
- **1. Initialize config** (pick one):
  - `python -m megalodon_ui.mission_config init` — write the default software-engineering template.
  - `python -m megalodon_ui.preflight "<goal>"` — Claude interviews you and proposes a config.
  - Copy a YAML directly from the examples in `v9-1-MISSION-CONFIG.md`.
- **2. Validate:** `python -m megalodon_ui.mission_config validate .mission-config.yaml`
- **3. Launch fleet:** `./scripts/launch_fleet.sh --mission-dir .` (run `--dry-run` first to preview).
- **4. Operator dashboard:** browse `http://localhost:8089` (or the port you configured).

Steps 1–4 above replace the v9.0 manual applier/server sequence for new missions. The v9.0 applier sequence below is still required (start applier before workers).

## What's new in v8

v8 promotes v7 with 22 edits documented in `docs/v8-changeset.md` (run-1 produced edits 1-21; orchestrator added Edit 22 post-run-1). The protocol-level changes you must internalize on first read:

- **ASCII task IDs only** (Edit 3): claim with `mkdir claims/P2-A-to-F`, never `claims/P2-A→F`. The Unicode arrow form is deprecated and causes dup-claim races. Filenames + TASKS.md identifiers use `-to-` exclusively.
- **YAML frontmatter on every finding** (Edit 3): see `## Verifier-report format` below — `lineage: v8`, `finding-type:` required.
- **CAS + lock-order on shared mutable files** (Edit 4-bis, TIER-2 strong default): before write to `STATUS.md` / `TASKS.md`, hash the file, hold the hash, re-read just before commit; retry if changed (max 3). Multi-file ops use `flock` in `sorted(absolute_paths)` order. `HISTORY.md` MAY be appended without CAS (append-only).
- **RULE 5 sub-clause** (Edit 2): DEFER citing non-existence MUST first `ls findings/ | grep <pattern>` AND `ls claims/<task-id>/done`. NO-RESPONSE (Edit 13) is a trace state, not a fourth choice.
- **RULE 10 self-check** (Edit 20): before exiting any tick that touches `claims/<task-id>/done`, verify all four steps completed in same tick. Document with "RULE-10 self-verified at <utc>" in STATUS Notes.
- **Subagent walltime declaration** (Edit 19): if expected walltime >10 min, write scratch BEFORE dispatch + declare expected walltime in STATUS Notes + RULE-6 reclaimers must check fs mtimes as secondary liveness.
- **PHASE-RUN + PHASE-HEAL** (Edit 21, MISSION.md governs): execution-verification phase between VERIFY and DRAINING. P5-RUN-* tasks executed by paired (non-self) lane. Failures inject `[REPAIR-<task>-<n>]` and re-enter HEAL. Budget: 3 cycles or 30-min wall-clock.
- **PHASE-OPERATOR-ACCEPTANCE** (Edit 22, NEW post-run-1, MISSION.md governs): mandatory gate between RUN/HEAL and DRAINING. Workers post `OPERATOR-ACCEPTANCE-REQUEST` task and HALT. Only the orchestrator (or human operator) can flip past this phase via `OPERATOR-ACK` / `OPERATOR-REJECT` / `OPERATOR-DEGRADED-ACK`. **No auto-COMPLETE.**

Workers operate under v8 governance from first tick. MISSION.md defines the per-mission overlay (lanes, tasks, cadence, phase progression).

---

## How it works

5+ Claude sessions run `/loop` in this directory. Each reads `README.md` (the protocol) and `MISSION.md` (the current mission) on every tick. They self-organize via shared markdown files:

- `STATUS.md` — heartbeat board (one row per lane)
- `TASKS.md` — work queue with mkdir-based atomic claims
- `claims/<task-id>/` — atomic claim directories (POSIX-atomic; source of truth)
- `findings/` — workers' outputs (one file per finding)
- `HISTORY.md` — append-only completion log
- `MISSION.md` — mission-specific scope (edit per deployment)

The orchestrator (you, or a dedicated Claude session) sets Mission status, pushes new tasks, watches progress. Workers self-organize within the protocol's rules.

---

## How to deploy

1. **Edit `MISSION.md`** to define your mission (scope, lanes, source project, deliverable, optional cadence override).
2. **Seed `TASKS.md`** with lane-tagged tasks (`[ ] [LANE-X] <id> — <description>`).
3. **Set Mission status to ACTIVE** in this README's `## Mission status` section.
4. **Start 5+ Claude sessions** in this directory with `/loop 3m` (or your chosen cadence).
5. **Watch STATUS.md / HISTORY.md / findings/** — workers self-organize from here.
6. **At end:** orchestrator sets Mission status to DRAINING → wait one cycle → COMPLETE → run archive process.

---

## Operator allowlist for v9 helper scripts

Workers invoke three scripts that must be wildcard-allowlisted once to prevent
mid-mission permission prompts (SIG-ORCH-6 cause). Add to your Claude Code
permissions (`settings.json` `allow` list or equivalent):

    python3 scripts/atomic_close.py *
    python3 scripts/poll.py *
    ./scripts/run_e2e.sh *

The scripts internally validate ALL args against strict whitelist regexes
(see `docs/superpowers/specs/2026-05-16-v9-m3-helper-scripts-design.md` §6.1).
Any non-conforming arg is rejected with exit code 2 and a stderr explanation.
The wildcard is safe because the scripts — not the allowlist — enforce input safety.

See RULES 12, 13, 14 in `launch.md` for the worker-side discipline these scripts enable.

> **Two distinct surfaces.** The above is the *operator's own* interactive session
> allowlist (`.claude/settings.json`). The **spawned-fleet** allowlist is separate —
> a bounded set built in `megalodon_ui/harnesses/claude.py` (`build_argv`, `live_repl`):
> native tools + path-scoped scripts (`poll.py`, `atomic_close.py`, `claim.sh`,
> `queue_submit.py`, `run_e2e.sh`, `run_tests.sh`) + `sleep`/`date`/`printf`, and
> nothing else — no `python`/`uv run`/`curl`/compound (2026-05-22 tool-surface policy).

---

## V9 startup sequence (M1 queue applier)

V9 serializes all shared-state writes through a singleton applier daemon
to eliminate CAS contention. The operator MUST start the applier BEFORE
workers.

1. **Start the applier** (background, one per mission):

       ./scripts/start_applier.sh /path/to/mission &

2. **Start the UI server** (factory canonical, ui/server.py is a thin shim):

       uv run --with fastapi --with "uvicorn[standard]" --with sse-starlette \
           --with pyyaml --with pydantic \
           python -m megalodon_ui --mission-dir /path/to/mission --port 8080

   (Or equivalently `python ui/server.py --mission-dir ... --port 8080`
   per the V9 M1.6 shim.)

3. **Verify applier health**:

       cat /path/to/mission/queue/.applier.lock/heartbeat.txt
       # Should be a UTC stamp within the last 5 seconds.

4. **(One-time, v8→v9 cutover only)** migrate legacy claims to add
   owner.txt files (without this step, the applier's strict-mode B4
   check rejects pre-v9 claim dirs):

       python3 scripts/migrate_claims_to_owner_txt.py --mission-dir /path/to/mission

5. **Workers** (per launch.md RULE 15): all shared-state writes go
   through `scripts/atomic_close.py` (queue-routed via M1 backend swap)
   or `scripts/queue_submit.py` (the bounded path-scoped wrapper over
   `queue_client.main`; `python -m megalodon_ui.queue.queue_client` is no
   longer an agent path under the 2026-05-22 tool-surface policy). Direct
   Edit-tool writes to shared state are NO LONGER permitted.

6. **(Optional, V9 A1) Start the watchdog daemon** for crash/silent/hung
   worker detection:

       ./scripts/start_watchdog.sh /path/to/mission &

   Polls every 60s and writes SIGNAL findings
   (`findings/watchdog-ALERT-<lane>-<utc>.md`,
   `signal-type: WATCHDOG-ALERT`) when a lane appears dead, has a stale
   STATUS row (>15 min), or has a stale Claude Code session JSONL while
   STATUS is fresh (hung mid-tool-call). **Never auto-respawns** — the
   operator decides whether to restart, signal the lane, or dismiss.
   Per-lane PID discovery reads `~/.megalodon-pids/<lane>.pid`; lanes
   without a PID file are skipped silently. See launch.md RULE 16.

### Per-lane launch (V9 A2)

Instead of running `claude --model X "read launch.md"` six times manually:

    bash scripts/launch_fleet.sh --spawn

Requires `.claude/CLAUDE.md` allow rules for `bash scripts/launch_fleet.sh *`.
Flags:
- `--spawn` — open iTerm with 2×3 pane layout; launch each lane's CLI in its assigned pane (macOS only).
- `--dry-run` — print the AppleScript that would be run (no iTerm open).
- `--no-launch` — verify structure + print commands, but don't execute.
- `--skip-applier-check` — skip the applier heartbeat gate (useful for testing without an active mission).
- `--cli-<lane>=<bin>` — override the CLI for a lane (lowercase: `--cli-audit=codex`).
- `--prompt-override=<txt>` — replace each lane's `"read launch-<LANE>.md"` prompt
  (useful for variety/smoke tests that must not join a live mission).

Each lane gets a pre-bound launch file (`launch-AUDIT.md`, `launch-ARCHITECT.md`,
`launch-BACKEND.md`, `launch-FRONTEND.md`, `launch-TEST.md`, `launch-META.md`)
with model, cadence, and stagger offset baked in. Regenerate after `launch.md`
changes:

    python3 scripts/gen_lane_launches.py

### Fleet matrix (V9 A3)

Lane→model assignments documented in `docs/v9/fleet-matrix.md`. Override per
mission via `<mission>/.scratch/fleet-matrix-override.json`:

    {"lanes": {"AUDIT": {"model": "haiku-4.5"}}}

The selector `scripts/fleet_select.py:select(lane, mission_dir)` returns the
override value if present, else the baked-in default, else `opus-4.7` for
unknown lanes.

### Deterministic agent IDs (V9 A4)

`scripts/_agent_id.py:deterministic_agent_id(mission_id, lane, launch_utc)`
replaces `secrets.token_hex(2)`. Same (mission, lane, launch_utc) → same ID.
Useful for crash recovery: re-launch with the same triple reproduces the
agent's identity.

### SIGNAL grammar (V9 A8)

Cross-agent + operator-facing directives codified at
`docs/v9/SIGNAL-GRAMMAR.md`. Use this for any new SIGNAL-class finding.
Parser at `megalodon_ui/signal_parser.py:parse_signal(path)` returns the
frontmatter dict iff `signal-type` is present.

### INTENT-EXPIRED + cross-lane reclaim (V9 M6)

Workers may stamp their STATUS Notes with
`intent-declared: <task-id> @ <utc> walltime: <Nm>` to claim a task on the
next tick. Helper `scripts/_intent_expired.py:is_expired(intent, now)` flags
declarations that have passed `max(12, walltime+5)` minutes after the
declared UTC. See launch.md §6.Y.

### PRE-CLASSIFY discipline (V9 M5)

launch.md §6.X codifies the 5-step pre-classification checklist (liveness
check → completion signal → invariants/uniformity/lane-bias → cause-class
taxonomy → convergence-can-be-wrong). Applies before any artifact
classification.

---

## Mission status

**Current: RUN-2 COMPLETE (BLOCKED-DEGRADED, 2026-05-16T22:10Z)**

Run-2 "make-it-work" terminal outcome: 7/16 e2e PASS + 25 PASS + 1 XFAIL unit/integration + UI render verified (41KB screenshot artifact) + 57+ v8.1 candidates harvested + 3 SPEC-FIRST HEAL addenda shipped. Wall-clock 4h40m (17:30Z → 22:10Z). OPERATOR-DEGRADED-ACK injected by orchestrator @21:50Z. 9 residuals diagnosed, deferred to run-3 under v9 protocol.

**Next**: archive run-2 to `.archive/2026-05-16T22-10Z--megalodon-run2-make-it-work/`, then implement v9 per `docs/v9/V9-ROADMAP.md` (post-Codex contrarian review, ready to ship).

For phase-gated missions (see MISSION.md §"Phase mechanics"), the source of truth is the append-only `.mission-events` log. This section is a best-effort visual rendering of the latest event — workers read `.mission-events` directly per RULE 11.

Possible status values:
- **IDLE** — no active mission (template state)
- **ACTIVE** — claim and work on tasks normally (single-phase missions)
- **PHASE-PLAN / PHASE-CHALLENGE / PHASE-BUILD / PHASE-VERIFY** — phase-gated mission; stance per phase defined in MISSION.md
- **DRAINING** — finish current task, write CAPSTONE if your lane drained, then idle. Do NOT claim new tasks.
- **COMPLETE** — write final HISTORY entry with session totals; heartbeat last time; halt loop

Workers re-read this section AND `.mission-events` on every tick BEFORE claiming.

---

# TIER 1 — Load-bearing rules (mandatory, non-negotiable)

These cannot be skipped. They are the protocol's atomic-correctness guarantees.

## RULE 0 — Keep the loop alive
Re-arm your next wakeup before any work each tick.
- **Dynamic mode:** `ScheduleWakeup({delaySeconds: 180, prompt: "<<autonomous-loop-dynamic>>", reason: "megalodon next tick"})`
- **Cron mode** (`/loop 3m`): runtime re-arms automatically. Calling ScheduleWakeup anyway is harmless.

## RULE 1 — Heartbeat every tick
Update your STATUS row's `Last UTC` every tick, even mid-task. A worker stale >15 min is presumed dead and reclaimed.

## RULE 2 — Atomic claim via claim.sh
`scripts/claim.sh <task-id> <agent-id>` is the lock (a bounded wrapper over the
`mkdir claims/<task-id>` mutex + `owner.txt`). Exit 0 = you own it (or idempotent
re-claim); exit 3 = another agent holds it, pick another. The bare `mkdir claims/`
chain is no longer an agent path under the 2026-05-22 tool-surface policy. TASKS.md
is informational; `claims/` is authoritative.

## RULE 3 — Hybrid review stance (Pass-1 / Pass-2)
- **Pass 1 (FRESH EYES):** form your view from the artifact alone. Do NOT read prior verifications or peer findings. This is the load-bearing safeguard against anchoring.
- **Pass 2 (RECONCILE):** read prior verifications + peer findings. Add `## Reconciliation` section: Concordance, Missed by me, Novel to me, Disagreements. Use RECONSIDERED notes — never rewrite originals, append.

## RULE 4 — SIGNALs must cite evidence
When signaling another worker via STATUS notes, you MUST cite evidence (`path:line` or `path:section`). Unsourced claims are invalid; recipients ignore them.

## RULE 5 — ACK-VERIFIED / DISSENT / DEFER (never bare ACK)
When responding to a peer signal, choose one explicitly. Each requires independently reading the cited evidence first.

```
ACK-VERIFIED <sender>: I read <file:line> at <UTC> and confirm <claim>. Updating via RECONSIDERED.
DISSENT <sender>: I read <file:line> at <UTC> and disagree because <reason>. My finding stands.
DEFER <sender>: will address in tick N when I work on <task>. Recording in scratch.
```

## RULE 6 — Stale-row reclamation
Each tick, scan STATUS for rows with State ≠ idle/PEER-REVIEWER AND Last UTC >15 min old.
- **Retroactive recovery:** if a finding file exists matching their working task-id, recover (touch done, mark TASKS done, append RECOVERY HISTORY entry).
- **Otherwise reclaim:** set State to STALE-RECLAIMED, release the lock (`rm -rf claims/<id>`; reset TASKS bracket to `[ ]`).

## RULE 7 — Source project is read-only
Workers may read anything under the source project (see MISSION.md). Writes are forbidden anywhere outside `<PROJECT_ROOT>/` (this Megalodon directory). No `git`, no build scripts, no DuckDB writes, no package installs. If a tool would modify the source project, do not run it.

## RULE 8 — No hallucination
Every assertion in a finding cites `path:line` or `path:section`. If you cannot verify, write `UNVERIFIED — reason: ...` and continue. Do not guess.

## RULE 9 — Synthesis stays with you
You may dispatch subagents (Explore, general-purpose, code-reviewer) for sub-questions. Never delegate the synthesis. The finding is yours.

## RULE 10 — Atomic completion block
When marking a task done, do all four in one tick:
1. `touch claims/<task-id>/done`
2. Mark TASKS bracket: `[done: <agent-id> @ <UTC>] [LANE-X]`
3. Append HISTORY: `<UTC> | <agent-id> | <LANE> | <task-id> | <finding-filename> | <severity>`
4. Update STATUS row to `idle | <UTC> | <task-id> done — <summary>`

Splitting these across ticks creates stale-detection false-positives.

---

# TIER 2 — Strong defaults (opt-out with rationale in STATUS notes)

These are the recommended defaults. Override only with explicit rationale.

## Cadence: 3 minutes
Default `*/3 * * * *` cron or `delaySeconds: 180` dynamic. Stays inside the 5-min prompt cache TTL. Drop to 10-15m for DRAINING phases; 20-30m for idle/monitoring. Avoid 5m (worst cache economics — sits exactly on the cache boundary). MISSION.md may override.

## Lane CAPSTONE on primary drain
When your lane's task queue empties, produce a CAPSTONE rollup: `findings/<your-id>-LANE-X-CAPSTONE-<UTC>.md`. Rolls up your lane's findings + cross-lane convergence + delivery-team recommendations.

**Equivalent:** If you took the GLOBAL-PEER-REVIEWER role, your `PEER-REVIEW-LOG.md` counts as your CAPSTONE — don't double-write.

## Mandatory scratch for multi-tick work
Before exiting any tick on unfinished work, write in-progress state to `findings/<your-id>-<task-id>.scratch.md`. Resumption reads scratch. Recommended even for context-persistent sessions to survive compaction.

## GLOBAL-PEER-REVIEWER (one slot, first-claim)
First lane to drain may set State to `PEER-REVIEWER`. Mandate:
- Read all new findings each tick
- Verify peer signals (post nudges if signals go unresponded >2 ticks)
- Track cross-lane convergence in `findings/PEER-REVIEW-LOG.md` (append-only)
- Surface quorum-escalation candidates
- Self-assign CHALLENGE on highest-converged findings
- RECONSIDERED on own prior findings as new info warrants

## CHALLENGE role (devil's advocate)
For any finding with 3+ lane convergence, any worker may self-assign a CHALLENGE task: `[ ] [CHALLENGE-<finding-id>] Construct the strongest argument that the consensus is wrong.` Claim via mkdir. Output: `findings/<your-id>-CHALLENGE-<finding-id>-<UTC>.md`. CHALLENGEs typically produce DELTA-class refinements, not overturns — that's the design goal.

## Severity escalation
- **MINOR → MAJOR:** 1 peer's Pass-1 independent finding on same artifact.
- **MAJOR → BLOCKING:** 2+ INDEPENDENT lanes' Pass-1 findings. ACK-VERIFIED responses do NOT count toward quorum.
- **Single-source BLOCKING allowed but flag as `SINGLE-LANE-BLOCKING — awaits independent confirmation`.** Don't gate on quorum; let the operational-class BLOCKINGs (build artifacts, placeholders) settle without waiting for corroboration.

## BLOCKING is final unless CHALLENGED within 2 ticks
BLOCKING claims settle automatically. CHALLENGE is opt-in. If filed within 2 ticks, BLOCKING is provisional until resolved.

## Scan findings/+claims/ at tick start
Proactive new-CHALLENGE and new-finding detection. Adds ~1 sec per tick; catches signals up to 2 ticks faster.

## Triage signals; don't always preempt
On tick start, check for SIGNALs addressed to you. Respond within 2 ticks (use DEFER if mid-task). Don't context-switch immediately — finish what you're doing first unless the signal explicitly affects your current task.

## Subagent budget ≤3 parallel per finding
For decomposable tasks, dispatch up to 3 subagents in parallel. Brief them with absolute paths and the read-only/write-scoped constraints from RULE 7. Cap response length (`"report in under 300 words"`).

## Worker self-introduction
First tick: generate ID via `python -c "import secrets; print('agent-'+secrets.token_hex(2))"`. Cache it. Reuse every tick.

## YAML frontmatter on findings
Use YAML frontmatter for finding metadata to enable Obsidian Dataview / Bases queries:
```yaml
---
lane: LOGIC
agent: agent-3ff8
task: L1
severity: MAJOR
utc: 2026-05-16T00:56Z
---
```

## RECONSIDERED preserves audit trail
Never rewrite a finding. Append RECONSIDERED notes with new evidence and reasoning. The trail is the deliverable.

## Quorum survives RECONSIDER unless explicitly revoked
If a RECONSIDER refines a finding but the core claim holds, quorum stands. If a RECONSIDER explicitly revokes the original, quorum point is voided.

---

# TIER 3 — Observed patterns (informational, from past runs)

These behaviors emerged in past runs. Documented for awareness — not encoded as rules. Workers may use these as priors but should derive from current evidence.

## Asymmetric collaboration styles
Different lane stances produce different styles. Forensic-evidence lanes (LEGAL, LOGIC) heavily cross-reference. Synthesis lanes (PROSE-as-PEER-REVIEWER) catch what task-driven workers miss. Independent verification lanes (SQL, MATH) tend to work solo until publishing.

## Cross-lane meta-patterns
Findings can converge into meta-patterns (e.g., "wrong-direction bridge across multiple findings", "scope-disclaim pattern across multiple sections"). Pattern-level findings can't be patched piecemeal — they signal systemic issues.

## Emergent role inventions
Workers may invent new roles. In the okx_case run: LOGIC invented LANE-PEER-REVIEWER (lane-local capstone synthesis) which spread to 3+ lanes within 3 ticks. Don't suppress — observe; codify in v8+ if useful.

## Forward pointers in STATUS notes
Workers may signal "tick N: <task> next" to let others align. Cheap coordination without explicit RPC.

## "Mea culpa" audit-trail discipline
When a worker misses a signal, the corrected behavior is to add an explicit acknowledgment in STATUS notes (e.g., "ACK LOGIC tick-N correction"). Don't hide errors — log them for the trail.

## Reproduction-as-concession defect class
When a witness/author reproduces opposing-party data exactly, this STRENGTHENS the opposing case — the rebuttal must acknowledge the empirical concession.

## Tool diversity emerges
Workers may invent tools beyond protocol defaults (e.g., SQL agent wrote its own Python diff harness rather than using DuckDB CLI alone). Don't constrain unless it conflicts with hard rules.

## Defect taxonomy by layer
Across past runs, defects partition into: arithmetic / methodology-in-scope / citation-chain / provenance / pipeline-determinism / inferential-direction / inferential-class / definitional-drift / header-content / operational-artifact. Useful for cross-finding aggregation.

## CHALLENGEs tend to produce refinement, not overturn
4-of-4 CHALLENGEs in the okx_case run ended as DELTA-class modified-consensus. Plan for this — CHALLENGE work is high-leverage even when it doesn't change the headline severity.

---

## Verifier-report format

```markdown
---
lane: <LOGIC | PROSE | SQL | MATH | LEGAL | ...>
agent: <agent-id>
task: <task-id>
severity: <BLOCKING | MAJOR | MINOR | NIT | DELTA>
utc: <timestamp>
artifact: <absolute path(s)>
---

# Finding: <short title>

## Summary
<2–4 sentences>

## Pass 1 — Fresh-eyes findings
<numbered: claim + evidence (path:line) + impact + recommended action>

## Pass 2 — Reconciliation with prior verifications and peer findings
- Concordance: ...
- Missed by me: ...
- Novel to me: ...
- Disagreements: ...

## Inter-agent signals received and responses
<list: signal source | claim | evidence | my response | UTC>

## Out-of-lane observations
<brief; do not investigate>

## Subagents dispatched
<list: subagent_type | purpose | one-line outcome>

## Confidence
<HIGH | MEDIUM | LOW>, one-sentence justification.
```

## Severity tags

- **BLOCKING** — ship-stopper. Tribunal/audit-credibility damage if shipped as-is.
- **MAJOR** — substantive defect; fix before delivery.
- **MINOR** — improvement; fix if cheap.
- **NIT** — cosmetic.
- **DELTA** — difference of opinion; documented for the record.

Be conservative with BLOCKING. Reserve for issues a competent adversary would weaponize.

---

## Communication

- **STATUS.md row** — live state (heartbeat, working/idle, lane)
- **STATUS.md Notes column** — short signals to orchestrator OR peers (with evidence per RULE 4)
- **A finding with severity DELTA** — protocol/design questions, scope concerns, proposals
- **BLOCKED state** — orchestrator intervention required

The orchestrator reads STATUS.md every cadence interval.

---

## Permission management

Workers run autonomously when `.claude/settings.json` defines a Bash allowlist + deny list. Auto-accept edits (Shift+Tab in Claude Code) covers file ops; the allowlist covers Bash. Both are needed for fully prompt-free operation.

**First-time setup (after cloning):**
```bash
cp .claude/settings.example.json .claude/settings.json
# Replace every <PROJECT_ROOT> in .claude/settings.json with the absolute path to this directory.
# Claude Code permission rules don't support variables — absolute paths only.
```

`.claude/settings.json` is gitignored (per-user / per-machine config); commit changes to `.claude/settings.example.json` if you want the template updated.

---

## End-of-run process

When mission deliverables are complete:

1. Orchestrator sets Mission status to **DRAINING** in this README
2. Wait one cron cycle for workers to acknowledge and complete in-flight work
3. Orchestrator sets Mission status to **COMPLETE**
4. Workers write final HISTORY entries with session totals
5. Orchestrator runs archive:
   - `cp -R findings claims STATUS.md TASKS.md HISTORY.md README.md` → `.archive/<UTC>--<mission-slug>/`
   - Replace STATUS / TASKS / HISTORY / findings / claims with empty templates
   - Update `.archive/INDEX.md`
6. User deletes crons (`CronDelete <id>` for each) or closes worker sessions

---

## Protocol changelog

- **v8 (2026-05-16T17:30Z):** Promoted from v7 via the megalodon-self-improvement run (`.archive/2026-05-16T17-06Z--megalodon-self-improvement-run1/`). 22 edits in `docs/v8-changeset.md`. Highlights: ASCII task IDs (Edit 3); File-collision CAS+lock-order (Edit 4-bis); RULE 5 DEFER verification + NO-RESPONSE trace (Edits 2, 13); RULE 11 stuck-flip recovery step 4a (Edit 14); Subagent walltime declaration (Edit 19); RULE 10 self-check (Edit 20); PHASE-RUN+HEAL execution-verification (Edit 21); PHASE-OPERATOR-ACCEPTANCE mandatory operator gate (Edit 22, post-run-1).
- **v7 (2026-05-16):** Tiered structure (load-bearing rules / strong defaults / observed patterns); MISSION.md split; per-mission lanes/cadence; 3m default; emergent role recognition (LANE-CAPSTONE, GLOBAL-PEER-REVIEWER); CHALLENGE refinement protocol; YAML frontmatter; lessons from 42-observation review of okx_case run.
- **v6 (2026-05-15 21:24 EDT):** PEER-REVIEWER role generalized.
- **v5 (2026-05-15 21:17 EDT):** Inter-agent communication (SIGNAL / ACK-VERIFIED / DISSENT / DEFER); severity quorum; CHALLENGE role.
- **v4 (2026-05-15 20:42 EDT):** Mandatory heartbeat (RULE 1); atomic completion ordering; retroactive completion recovery.
- **v3 (2026-05-15 20:37 EDT):** Atomic mkdir claim; stale-row reclamation; Mission status; pre-claim duplicate check; mandatory scratch; early-proceed in race resolution.
- **v2 (2026-05-15 20:22 EDT):** Auto lane assignment; Rule 0 loop-keepalive.
- **v1 (2026-05-15 20:17 EDT):** Initial protocol; manual lane assignment.
