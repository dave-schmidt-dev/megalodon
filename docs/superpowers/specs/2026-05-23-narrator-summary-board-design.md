# Narrator-Driven Summary Board — Design

**Date:** 2026-05-23
**Status:** design approved (operator, 2026-05-23) — ready for implementation plan.
**Origin:** v94-ui-dogfood pain — six tiled tmux terminals are too cramped to
monitor; operator chose a "summary-first, terminal-on-demand" board, with a
local small-model narrator for the prose. Narrator model selection is settled in
a prior cycle: **gemma-e2b locked** (see
`~/Documents/Projects/LLM/benchmarks/megalodon-narrator-summarization.md` and
`benchmarks/narrator/`). This spec covers wiring that locked narrator into a
production board.

## Problem

The dashboard's 6-tile grid (`ui/static/pages/grid.js`) renders six live tmux
panes simultaneously; the text is too small and runs together, so the operator
cannot actually monitor the fleet — which defeats the product's whole purpose
(fleet observability). Two further gaps surfaced in the dogfood:

1. **No at-a-glance state.** Reading what each agent is doing requires squinting
   at raw terminal scrollback across six panes.
2. **No way to approve from the UI.** Agents block on permission prompts and the
   operator had no in-dashboard approve/deny control (the backend send path
   exists; the render side was never built).

## Principle

**Deterministic facts are load-bearing and exact; the local model is advisory
prose only.** Every load-bearing value on the board (lane state, tokens, task
IDs, goal) comes from mission state and renders even if the model is down. The
narrator (gemma-e2b) contributes only a short human-readable *phrase* that can
be dropped without losing any fact. The board must never block on the model.

## Resolved decisions (operator, 2026-05-23)

- **Rows:** three lines per lane — **Last / Now / Goal** — plus a state pill,
  tokens, inline approve/deny when blocked, and a click-to-open terminal drawer.
- **Line sourcing — hybrid:** Last + Now task IDs and Goal are **deterministic**
  (claims/, queue history, lane role); the narrator appends a **short color
  phrase** to Last and Now. Goal carries no narrator text. The model cannot
  fabricate which task is which.
- **Narrator runtime — megalodon-supervised:** the dashboard launches and
  supervises the `llama-server` child; `MEGALODON_NARRATOR_URL` overrides to an
  already-running endpoint (skips supervision).
- **Refresh — server timer + SSE push, watcher-gated:** one server-side per-lane
  loop on a single configurable interval (default **30 s**, tunable to **15 s**),
  active only while ≥1 board subscriber is connected. REST endpoint serves the
  cached value for initial paint.
- **Degradation — graceful by construction:** narrator down/slow → deterministic
  half renders fully, phrases omit, "narrator offline" dot in the topbar.
- **Rollout — board replaces the grid entirely:** `grid.js` and its route are
  deleted; the summary board is the only fleet view. Full per-lane terminals
  remain reachable via the drawer. (Operator: "the grid is unusable.")

## Architecture / change set

### New modules

1. **`megalodon_ui/narrator/runtime.py`** — narrator subprocess supervisor.
   - On `--spawn` (unless `MEGALODON_NARRATOR_URL` is set), launch
     `llama-server -m gemma-4-E2B-it-Q4_K_M.gguf --alias narrator
     --chat-template-kwargs '{"enable_thinking":false}' -ngl 99 -c 8192 --jinja`
     on a chosen port (default discovered/free; configurable).
   - Health-gate on `/health` before marking ready; restart on crash with
     backoff; terminate + reap on dashboard exit.
   - Model path resolves from config (default
     `~/models/narrator-bench/gemma-e2b/gemma-4-E2B-it-Q4_K_M.gguf`); if missing,
     log WARNING and run in degraded mode (no narrator), never fatal.

2. **`megalodon_ui/narrator/client.py`** — async narrator client.
   - `async narrate(lane, digest_text) -> NarrativePhrases | None` — POSTs the
     OpenAI-compatible chat request built from `narrator/prompt.py`
     (`build_messages`) to the endpoint; bounded timeout (~3 s).
   - Returns `None` on any failure (timeout, connection refused, 5xx, empty
     content) — the single graceful-degradation choke point.
   - Produces the two short phrases (Last, Now). Prompt extended to request
     exactly those two phrases for a given lane's recent activity (small,
     few-shot; reuses the existing faithfulness guardrails).

3. **`megalodon_ui/narrator/board_state.py`** (or extend an existing state
   helper) — **deterministic** per-lane assembly:
   - `last_task` (most-recently-closed task id + outcome, from queue history /
     `claims/`), `now_task` (currently-claimed task id), `goal` (claimed task
     title from TASKS.md, else lane role from mission config), plus `state`,
     `tokens`, `tok_s`, `pending_approval`. Reuses the existing task/claims
     parsing in `server.py` (lines ~372–465, ~736).

### Server wiring (`megalodon_ui/server.py`)

4. **Per-lane narrative scheduler** — an asyncio background task per lane:
   parse live session digest (`narrator/digest.py`) → build deterministic
   board_state → call `client.narrate` for phrases → assemble a `LaneNarrative`
   → cache → emit an SSE event. Loop interval = `MEGALODON_NARRATOR_INTERVAL_S`
   (default 30, clamp [15, 120]). **Subscriber-gated:** runs only while the board
   SSE channel has ≥1 connection; pauses otherwise.

5. **`GET /api/v1/lane/{lane}/narrative`** — returns the cached `LaneNarrative`
   (deterministic fields always present; phrases nullable). For initial paint and
   non-SSE clients.

6. **SSE event** — narrative updates pushed over the dashboard event channel
   (reuse the existing stream; new event type, e.g. `lane-narrative`).

7. **Approval wiring** — the inline approve/deny calls the **existing**
   approve/deny send endpoint (`server.py` ~2371). No new backend; this closes
   the render gap only.

### Frontend (`ui/static/`)

8. **`ui/static/pages/board.js`** (new) — the summary board. Renders rows from
   `/api/v1/state` (deterministic) merged with `lane-narrative` SSE (advisory).
   Inline approve/deny → existing endpoint. **terminal ▸** opens a drawer that
   mounts the existing `components/terminal_pane.js` against
   `/api/v1/lane/{lane}/pane-stream`. "narrator offline" dot in the topbar when
   the runtime reports unhealthy.

9. **Remove `ui/static/pages/grid.js`** and its route/registration; make the
   board the default fleet view. Update `index.html` / router accordingly. Audit
   `dashboard.js` / `dashboard-v92.js` references to the grid and repoint them.

### Config / lifecycle

10. **`megalodon_ui/__main__.py`** — start the narrator runtime alongside the
    server (after the existing dashboard bring-up), pass interval + model-path +
    URL-override config through; ensure teardown on exit.

## Testing

- **`client.py`** — returns `None` on timeout / connection-refused / 5xx / empty
  content; success path returns both phrases. (httpx mock / fake server.)
- **`runtime.py`** — builds the locked argv (asserts `enable_thinking:false`,
  `-ngl 99`, alias); health-gates before ready; `MEGALODON_NARRATOR_URL` skips
  supervision; missing model → degraded, not fatal. (subprocess mocked.)
- **`board_state.py`** — deterministic Last/Now/Goal from a fixture mission
  (claimed + closed tasks, role fallback for Goal, no claim → role).
- **Scheduler** — honors interval (clamped to [15,120]); pauses with zero
  subscribers, resumes on subscribe; one narrate call per lane per tick.
- **`GET …/narrative`** — returns cached value; deterministic fields present when
  phrases are `null`.
- **Board E2E (Playwright, `@playwright/test`)** — rows render Last/Now/Goal +
  state + tokens; approve/deny POSTs to the approval endpoint and clears the
  banner; terminal drawer opens/closes and streams; narrator-offline dot shows
  when narrative phrases are absent. (Per repo rule: `playwright.config.ts` with
  `webServer`, retries, HTML reporter.)
- **Grid removal guard** — a test asserts no live route/import references
  `grid.js`.

## Non-goals

- Changing the spawn mechanism, mission-config schema, or the queue applier.
- The v10 refactor (separate track).
- Per-lane differentiated intervals (single global interval knob).
- Narrator fine-tuning or model re-selection (gemma-e2b is locked).
- A new approval *backend* (the send path already exists; only the UI render is
  added).

## Done when

- [x] Narrator runtime supervises `llama-server` (locked config), health-gated,
  restarts on crash, torn down on exit; `MEGALODON_NARRATOR_URL` override works.
- [x] Per-lane scheduler emits narratives on the configurable interval
  (default 30 s, tunable to 15 s), gated on board subscribers.
- [x] Board renders 3-line rows (hybrid sourcing), inline approve/deny wired to
  the existing endpoint, terminal drawer via `terminal_pane.js`.
- [x] `grid.js` deleted; board is the default fleet view; removal guard test
  green.
- [x] Narrator-down path leaves the deterministic board fully functional.
- [x] Unit + integration + Playwright suites green; full suite green.
- [ ] Then (operator follow-up): exercise on the next v9.4 dogfood.

## Build order (one spec, phased)

1. **Runtime + client** — narrator subprocess supervision + the degrading client.
2. **State + scheduler + endpoint** — deterministic board_state, the
   watcher-gated scheduler, REST endpoint, SSE event.
3. **Board UI + approval + grid removal** — `board.js`, drawer, inline approve/
   deny, delete `grid.js`, make board default.

Each phase is independently testable and lands behind its own tests.
