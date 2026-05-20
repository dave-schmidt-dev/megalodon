# v9.2 Roadmap — Tmux + Web UI (Headless Fleet)

**Status:** SUPERSEDED 2026-05-18 by `docs/v9/v9-2-TMUX-FLEET.md`. This file is the original design sketch; treat it as historical context only. For the as-shipped architecture, operator runbook, auth flow, and follow-up-prompt contract, read:

- `docs/v9/v9-2-TMUX-FLEET.md` — architecture + operator runbook (canonical).
- `docs/v9/v9-2-AUTH.md` — bootstrap flow, cookie semantics, paste-token recovery.
- `docs/v9/v9-2-FOLLOWUP-PROMPTS.md` — adapter contract, respawn semantics, sentinel chunk.

---

**Original status:** Design sketch, deferred from v9.1.
**Captured:** 2026-05-17 during v9.1 implementation session.
**Rationale:** v9.1 makes lanes/phases/harnesses configurable but keeps the v9.0 spawn model (AppleScript → iTerm grid). v9.2 decouples *spawn* from *operator-facing view* so the system runs headless, works on Linux/SSH/containers, and surfaces all lane terminals in one browser tab.

## Problem statement

v9.0 / v9.1 spawn model:
- `scripts/launch_fleet.sh` calls `osascript` to materialize a 2×3 iTerm grid (macOS-only, fragile).
- Operators must keep iTerm visible and tab between panes.
- No SSH / remote / Linux story.
- The harness CLIs already run *headless* — `claude --print` doesn't need a TTY — so the iTerm dependency is purely a *view* concern, not a *spawn* concern.

## Proposed architecture

### Spawn layer: tmux session per lane

```
tmux new-session -d -s lane-AUDIT     'claude --print --model claude-sonnet-4-6 ...'
tmux new-session -d -s lane-ARCHITECT 'codex exec -m gpt-5.5 ...'
tmux new-session -d -s lane-BACKEND   'gemini -p ... -m gemini-3.1-pro-preview ...'
```

- Detached (`-d`) — no TTY required.
- Session name encodes the lane for routing.
- Process lifecycle handled by tmux (auto-cleanup on exit, attachable on demand).
- `tmux kill-session -t lane-AUDIT` is the canonical teardown — no AppleScript needed.

### Data path: dual-tap on each session

For each lane:

1. **Structured stream (parsing path):**
   `tmux pipe-pane -t lane-AUDIT -O 'cat >> .fleet/audit.stream.log'`
   Continuous file append. `megalodon_ui` tails the log and parses each line via the harness adapter's `parse_stream_line` (already designed for this in v9.1 P1.6). STATUS / HISTORY mutations are derived here.

2. **Visual snapshot (view path):**
   Periodic `tmux capture-pane -p -t lane-AUDIT` (e.g. every 500 ms).
   Captures the rendered pane state — colors, cursor position, ANSI sequences intact. Streamed to the browser and rendered with `xterm.js`.

The two taps are independent: the structured stream is the source of truth for mission state; the visual is purely for human observability.

### View layer: web UI grid

- Add `@xterm/xterm` to `ui/static/`.
- Dashboard renders one xterm pane per `config.lanes[*]`, sized by `cols × rows` derived from N (re-uses P1.3's grid math).
- Each pane subscribes to `/api/v1/lane/<NAME>/pane-stream` (SSE) which the server feeds from the capture-pane tap.
- Operator click → side panel attaches: stdin proxied via `tmux send-keys -t lane-AUDIT <input>`; stdout already streaming.
- Manual-tick for non-Claude lanes (CR-4) becomes a textbox in the browser instead of an iTerm tab.

### What gets eliminated

| v9.0/v9.1 | v9.2 |
|---|---|
| AppleScript / `osascript` | gone — `tmux` cross-platform |
| iTerm dependency | gone — browser is the UI |
| macOS-only spawn | works on Linux, SSH, containers |
| 6 iTerm tabs to monitor | one browser tab |
| `scripts/launch_fleet.sh` AppleScript loop | `scripts/launch_fleet.sh` tmux loop (much simpler) |
| Per-lane terminal lifecycle in the operator's window manager | tmux server owns lifecycle |

### What stays the same

- Harness adapters (`megalodon_ui/harnesses/*.py`) — unchanged contract; the spawn driver invokes `adapter.build_argv(...)` and passes the result to tmux instead of AppleScript.
- Queue applier — unchanged; still consumes the structured stream.
- Mission config schema — unchanged.
- Watchdog — unchanged (still watches log freshness; tmux just changes where the logs come from).

## Open design questions for v9.2

1. **Capture cadence.** 500 ms is a reasonable default but burns CPU on idle lanes. Adaptive cadence (slow down when output is quiet) is a v9.2-or-later concern.
2. **History scrollback.** `capture-pane -S -<N>` can grab N lines of scrollback; UI may want a "show last 1000 lines" affordance per pane. Disk cost vs. ergonomics trade.
3. **Authentication for the web UI when serving remotely.** v9.0 binds 127.0.0.1 only; v9.2 likely needs proper auth (token, OIDC, or behind a reverse proxy). Tied to the SSH/container deploy story.
4. **xterm.js bundle size.** ~200KB minified — non-trivial for a self-contained UI. Tree-shaking + lazy load by lane count.
5. **tmux availability.** Most Linux servers ship it; macOS needs `brew install tmux`. Document as a hard prerequisite, or fall back to plain `nohup` + log tail for the bare-minimum path.

## Why not v9.1

v9.1's scope is *configurability* of an existing fleet model. Bolting on a new spawn mechanism *and* a new UI rendering path *and* a stdin proxy mid-v9.1 would:

- Triple the surface area.
- Require xterm.js integration, SSE multiplexing, tmux lifecycle code, and a fresh layer of integration tests.
- Conflict with PM-7's sequential refactor discipline.

v9.1 ships configurability. v9.2 picks up the headless story from there. The harness adapter contract built in v9.1 P1.6 is the bridge — adapters already separate "what command to spawn" from "how to interpret the output", which is exactly what tmux-based spawn needs.

## Predecessor work that unlocks v9.2

Already shipped in v9.1:

- Harness adapter Protocol with `build_argv` — produces an argv that any spawner (AppleScript, tmux, `subprocess.Popen`, ...) can consume.
- `parse_stream_line` per adapter — the structured-stream interpretation is already factored out of the spawn driver.
- `auth_env_keys` — credential injection is a one-line `extra_env` in any spawn model.
- `session_log_path` — gives the watchdog a stable file to monitor regardless of spawn model.

The v9.2 work is, in essence: replace `scripts/launch_fleet.sh`'s AppleScript loop with a tmux loop, plumb the capture-pane stream through to the FE, and add xterm.js rendering. The Phase 1 work in v9.1 was deliberately structured to make this swap clean.

---

## Side-track investigations (not part of the headless story)

These items surfaced during v9.1 implementation and were intentionally deferred so v9.1 could ship. They are independent of the tmux + web UI work above and could land in any order.

### Inv-1. Typo-path artifact accumulation (`/Users/dave/Documents/Projects/megaladon/`)

**Observed 2026-05-17:** A sibling directory with a one-letter typo (`megaladon`, missing the second `o`) had accumulated `.claude/`, `.playwright-mcp/`, and screenshot PNGs. Cause: Claude Code was launched at least once with the typo path as CWD (this session's startup banner showed it as the primary working directory). Anything spawned from that session — Claude Code's `.claude/` settings, Playwright MCP screenshots — wrote relative to CWD and pooled there.

**Cleanup status:** the typo dir was manually deleted 2026-05-17.

**Search done:** no shell aliases / espanso shortcuts / startup files reference the typo path. So nothing actively recreates it — the source was muscle-memory typing once.

**v9.2 (or v9.1.x) action to consider:**

- Belt-and-suspenders symlink: `ln -s megalodon ~/Documents/Projects/megaladon`. Any future typo'd `cd megaladon` then transparently lands in the real dir. Trade: the typo becomes invisible / unreported, so genuine "wrong project" errors get masked.
- Or do nothing: the next typo will fail loudly with `cd: no such directory`, which is the desired feedback.
- Investigation item: confirm Playwright MCP and Claude Code both write artifacts strictly relative to CWD — if they ever follow a configured "project root" that diverges from CWD, we want to know.

Low priority, no functional impact on Megalodon itself.

### Inv-2. Four M1.5 sync/async test mismatches in `ui/tests/integration/`

**Observed 2026-05-17 during P2.5 verification:**

```
FAILED ui/tests/integration/test_api_endpoints.py::test_A_CH_inject_appends_task_and_creates_claim
FAILED ui/tests/integration/test_api_endpoints.py::test_A_RC_reclaim_stale_row_retroactive
FAILED ui/tests/integration/test_api_endpoints.py::test_A_RC_reclaim_stale_row_no_finding
FAILED ui/tests/integration/test_api_endpoints.py::test_A_SG_post_signal_appends_to_notes
```

All fail with `AssertionError: got 202: ... "status":"pending"` while asserting `status_code in (200, 201)`. The endpoint started returning 202-Accepted with an async request_id (M1.5 mutation-endpoint async conversion), but these four integration tests still expect the v9.0-pre-M1.5 sync 200/201 shape.

**Confirmed pre-existing:** verified via git stash in P2.5 subagent; tests fail on a clean working tree before any v9.1 refactor.

**Severity:** low — the rest of the v9.0/v9.1 fleet behaves correctly via the documented poll path (`GET /api/v1/queue/{rid}`). These four tests are stale assertions that haven't been updated to the M1.5 async contract.

**Fix is straightforward and small (v9.1.x or v9.2 housekeeping):**

- Adjust the four tests to:
  - Accept 202 as the immediate response.
  - Extract `request_id` from the JSON body.
  - Poll `GET /api/v1/queue/{request_id}` until `status == "applied"`.
  - Then assert the same downstream side effects (TASKS.md mutation, STATUS.md row insert, etc.) the original tests checked.
- Pattern is already used elsewhere in the suite; copy the polling helper rather than re-inventing it.

Tracking here so v9.1 ships with the failure honestly documented rather than silently tolerated.

**Resolved 2026-05-17:** All four tests updated to accept 202, extract `request_id`, and drive the `Applier.drain_once()` directly in a `wait_for_queue_applied` helper (added to `ui/tests/integration/conftest.py`). The helper instantiates `Applier` against the test's `tmp_path` mission dir and calls `drain_once()` before each poll of `GET /api/v1/queue/{request_id}`. Full suite: 354 passed, 1 xfailed, 0 failures.
