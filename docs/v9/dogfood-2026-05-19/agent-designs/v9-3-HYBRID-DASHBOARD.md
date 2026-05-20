# v9.3 — Hybrid Dashboard Design

- **Owner:** LANE-B ARCHITECT (`agent-f66a`)
- **Joint task:** `S-HYBRID-DASHBOARD` (LANE-B writes spec; LANE-D implements)
- **Status:** DRAFT (drafted in PHASE-PLAN; consumed by PHASE-BUILD `P2-B` + `S-HYBRID-DASHBOARD` FE half)
- **References:** `docs/v9/v9-2-ROADMAP.md`, `docs/v9/api-contract.md`, `ui/static/pages/dashboard.js`, `megalodon_ui/server.py`

---

## 1. Problem

v9.2 ships two mutually-exclusive operator surfaces and the operator must
choose one at fleet-start time:

| Surface | Strength | Weakness |
|---|---|---|
| **v9.2 panes-only** (`ui/static/pages/mission.js`) | Live tmux REPL drilldown per lane; operator sees what each agent is actually typing | Loses orchestration chrome (lane cards, TASKS list, STATUS table, claims panel, recent HISTORY/findings, signal log, activity feed) |
| **v9.0 chrome-only** (`ui/static/pages/dashboard.js`) | Full orchestration visibility | No live REPL view; if an agent stalls mid-tool-call or hits a permission prompt the operator can't see what's on the agent's screen |

The operator has stated they want **both at once**: v9.0 chrome by default
with a per-lane "View terminal" affordance that opens the live tmux pane
view inline (modal or expanded drawer) without leaving the dashboard.

## 2. Goals / Non-goals

**Goals (v9.3):**

- G1. v9.0 chrome (`dashboard.js`) is the default landing surface; nothing
  about the orchestration view changes structurally beyond adding one
  control per lane card.
- G2. Each lane card exposes a "View terminal" affordance. Activation
  shows the live, color-preserving pipe-pane stream for that lane in a
  modal or expanded drawer rendered with an embedded terminal emulator.
- G3. Terminal view is **read-only** — operator can see what the agent
  sees but cannot type into it. (Write-back is a v9.4 question; see §10.)
- G4. Stream uses existing v9.2 `.fleet/<short>.stream.log` (pipe-pane
  output) as its source. No new tmux capture infrastructure.
- G5. Opening/closing the terminal view is cheap: no permanent
  per-lane WebSocket; the stream attaches on open and detaches on close.
- G6. Backwards-compatible with v9.2 panes-only mode (it remains
  reachable via the existing `/mission` route for operators who prefer it).

**Non-goals (v9.3):**

- NG1. Multi-pane grid of terminals (operator can scroll the chrome and
  open one at a time; concurrent terminals create CPU/render pressure with
  little orchestration benefit).
- NG2. Editing or sending input to the terminal (see §10 Open Question 2).
- NG3. Persistent scrollback older than `_STREAM_TAIL_BYTES` (~64 KB);
  scrollback is bounded by what the pipe-pane file already retains.
- NG4. Replacing `mission.js` panes-only mode (kept as a fallback surface).

## 3. UX

### 3.1 Lane card — affordance placement

`ui/static/pages/dashboard.js:242-257` already renders a "Show details"
toggle button per lane card. The new affordance is a **second button**
adjacent to it:

```
┌─ LANE-D (FRONTEND) ──────────────────────────┐
│  agent-07c5  ·  working: S-TOOLTIPS          │
│  sonnet-4-6  ·  self-paced  ·  next: 0:42    │
│  notes: wiring tooltip Playwright assertion  │
│  [ Show details ]  [ View terminal ▢ ]       │
└──────────────────────────────────────────────┘
```

- Button label: `View terminal`
- Icon: small square glyph (`▢`) to hint at the modal pop-out
- `data-testid="action-view-terminal-${lane}"` (Playwright contract)
- `title=` tooltip: `Open live tmux pane for ${lane} (read-only, last ~64 KB)`
- `disabled` when the lane's `.fleet/<short>.stream.log` is missing or 0
  bytes (BE returns 404 for `GET /api/v1/lane/{short}/terminal_meta`; FE
  caches this for `card.staleTtl` seconds — default 5s).

### 3.2 Terminal view container — modal vs. drawer

**Decision: modal, not drawer.** Rationale:

- Drawer (S-LIVE-ACTIVITY pattern) expands the card in-place, pushing
  other cards down — fine for compact metadata, but a 80×24 xterm at
  legible font size is ~720 px tall and would shove the grid.
- Modal centers, can be sized to 100×40 chars, and the operator can
  dismiss with ESC. The chrome behind the modal stays visible at
  reduced opacity so SSE-driven badge changes (phase flip, new
  finding, claim release) remain noticeable.

Modal contract:

| Element | Behavior |
|---|---|
| Header | `LANE-X (ROLE) · agent-XXXX · live terminal · attached <t>` |
| Body | `<div id="term-${lane}">` mounted with xterm.js (v5.x) |
| Footer-L | `Detached / Connecting / Attached / Error` badge |
| Footer-R | `[ Copy buffer ]  [ Close (Esc) ]` |
| Scroll | xterm.js native; scrollback = `terminal.options.scrollback = 5000` |
| Focus | first focusable button on open; ESC closes; trap inside modal |
| Aria | `role="dialog" aria-modal="true" aria-label="${lane} live terminal"` |

### 3.3 Operator gestures

| Gesture | Effect |
|---|---|
| Click `View terminal` | Open modal, fetch buffer + start SSE attach |
| ESC | Close modal, detach SSE, clear xterm instance |
| Click backdrop | Same as ESC |
| Click `Copy buffer` | `navigator.clipboard.writeText(terminal.buffer.normal…)` |
| Card grid `Show details` while modal open | No conflict — drawer and modal are independent surfaces |

## 4. Backend API contract

Two endpoints, both gated by the existing cookie-exchange auth (see
`megalodon_ui/server.py` `_require_auth_cookie`).

### 4.1 `GET /api/v1/lane/{short}/terminal_meta`

Cheap probe used by the lane card to enable/disable the button.

```json
{
  "short": "D",
  "lane": "FRONTEND",
  "stream_log_exists": true,
  "stream_log_size_bytes": 14523,
  "last_modified_utc": "2026-05-19T23-21-44Z"
}
```

- 404 when `.fleet/<short>.stream.log` missing.
- Response cacheable for 5s (`Cache-Control: max-age=5`).

### 4.2 `GET /api/v1/lane/{short}/terminal_stream` (SSE)

Open-ended Server-Sent Events stream of raw pipe-pane bytes.

- **Initial event** (`event: snapshot`): one event whose `data:` field
  is the JSON `{"bytes_b64": "<base64 of last 64 KB of stream.log>"}`.
  Sent immediately on connect so the terminal hydrates with backlog.
- **Append events** (`event: append`): each `data:` is JSON
  `{"bytes_b64": "<base64 of newly-appended bytes>"}`. Emitted whenever
  the BE detects `stream.log` grew (poll every 250 ms via stat-mtime;
  read only the delta from saved offset).
- **Keepalive** (`event: ping`, `data: {}`) every 15 s so proxies don't
  close idle connections. Reuses existing SSE infrastructure shape
  from `megalodon_ui/server.py:1017` `_v1_events` SSE block.
- **Detach**: client disconnect drops the file handle. No server-side
  reference counting needed; multiple operators on the same lane each
  open their own handle (rare; OK).

**Why SSE and not WebSocket:** existing v9.2 infra is SSE-only
(`ui/static/js/sse.js`); the read-only one-way contract fits SSE exactly;
WebSocket would force a new auth path (cookies on WS upgrade are fragile
behind proxies). If a future v9.4 wants bidirectional input, that
upgrade can be local to this endpoint.

**Why base64 and not raw bytes-as-text:** SSE `data:` lines are
LF-delimited; pipe-pane output contains LFs, CRs, and 8-bit ANSI escape
sequences that must round-trip exactly. Base64 is the cheapest
preservation that works through any SSE library on either end.

**Frame size cap:** each `append` event capped at 16 KB raw (clamp +
emit another append next tick). Prevents one giant write from blocking
the event loop.

### 4.3 Implementation notes (informative, not normative)

The BE side belongs in `megalodon_ui/server.py` next to the existing
`get_lane_activity_summary` (around line 750), and reuses
`_parse_stream_tail`'s path logic but **NOT** its ANSI stripping —
terminal stream MUST preserve ANSI escapes (xterm.js needs them).
Suggested implementation:

```python
async def _stream_terminal(short: str) -> AsyncIterator[ServerSentEvent]:
    log_path = ctx.mission_dir / ".fleet" / f"{short}.stream.log"
    offset = max(0, log_path.stat().st_size - _STREAM_TAIL_BYTES)
    # initial snapshot
    with log_path.open("rb") as fh:
        fh.seek(offset)
        snap = fh.read()
        yield ServerSentEvent(event="snapshot",
                              data=json.dumps({"bytes_b64": b64encode(snap).decode()}))
        offset = fh.tell()
    while not request.is_disconnected():
        await asyncio.sleep(0.25)
        size = log_path.stat().st_size
        if size <= offset:
            if int(time.monotonic()) % 15 == 0:
                yield ServerSentEvent(event="ping", data="{}")
            continue
        with log_path.open("rb") as fh:
            fh.seek(offset)
            chunk = fh.read(min(size - offset, 16 * 1024))
            offset = fh.tell()
        yield ServerSentEvent(event="append",
                              data=json.dumps({"bytes_b64": b64encode(chunk).decode()}))
```

## 5. Frontend component

New module: `ui/static/pages/terminal_modal.js` (new file, ~150 LOC).

- Default export: `openTerminalModal({ lane, short, csrfToken })`.
- Internally:
  1. Build modal DOM with xterm.js mount point.
  2. Lazy-load xterm.js + xterm-addon-fit from `ui/static/vendor/xterm/`
     (vendored, not CDN — fleet must run offline).
  3. `new Terminal({ scrollback: 5000, convertEol: true, fontFamily: "ui-monospace" })`.
  4. Open `EventSource(/api/v1/lane/${short}/terminal_stream)`.
  5. On `snapshot` event → `term.write(atob(payload.bytes_b64))`.
  6. On `append` event → same.
  7. On `error` or `close` → set footer badge `Error / Detached`, schedule
     reconnect with exponential backoff capped at 5s.
  8. On modal close → `eventSource.close(); term.dispose();` and remove
     the modal DOM node.

### 5.1 Vendoring xterm.js

- Pin **xterm@5.5.0** + **xterm-addon-fit@0.10.0** at
  `ui/static/vendor/xterm/xterm.{js,css}` and
  `ui/static/vendor/xterm/xterm-addon-fit.js`.
- Add SRI hashes to `ui/static/index.html` `<script>` tags loaded on
  demand (dynamic `<script>` injection inside `openTerminalModal`).
- Bundle size: xterm.js v5 minified ~280 KB + fit-addon ~5 KB. Loaded
  only when the operator opens a terminal; never on initial page load.

### 5.2 Lane card edit

`ui/static/pages/dashboard.js:242-257` — append a second button:

```js
const termBtn = el("button", {
  type: "button",
  class: "button",
  "data-testid": `action-view-terminal-${lane}`,
  title: `Open live tmux pane for ${lane} (read-only, last ~64 KB)`,
  disabled: !termMeta?.stream_log_exists,
  onclick: (ev) => {
    ev.stopPropagation();
    openTerminalModal({ lane, short: configLane?.short, csrfToken });
  },
}, "View terminal");
```

The `termMeta` is hydrated from a new `mission.lanes[i].terminal_meta`
sub-object added to `/api/v1/state` (single round-trip on hydrate; saves
N parallel `terminal_meta` GETs).

## 6. Security

| Threat | Mitigation |
|---|---|
| Unauthenticated terminal access | Same cookie-exchange auth as every other v9.2 endpoint; SSE request carries the cookie. |
| CSRF on read endpoint | GETs are state-changing only insofar as they hold a file handle. No CSRF token required for read-only SSE (matches `/api/v1/events`). |
| ANSI escape exploits in xterm.js | xterm.js v5 has known CSI parsing soundness; pin a recent version (≥5.5.0) and SRI-hash the vendored file. Operator-only surface (no external users), so blast radius is the operator's own browser. |
| Stream log path traversal | `short` validated `^[A-Z]{1,4}$` exactly as in `get_lane_activity_summary`. |
| Sensitive output leaked in clipboard | `Copy buffer` action is operator-initiated; UX hint: "Buffer may contain agent prompts, model output, and tool call args." |
| Resource exhaustion (many open SSE streams) | Cap per-cookie concurrent terminal streams at 4 in `_stream_terminal`; respond 429 above cap. |

## 7. Test plan

| Test | Where | Type |
|---|---|---|
| `test_terminal_meta_404_when_missing` | `scripts/tests/test_terminal_endpoints.py` (new) | unit / FastAPI TestClient |
| `test_terminal_meta_shape` — exists path returns expected fields | same | unit |
| `test_terminal_stream_snapshot_then_append` — write log, attach, write more, assert both events | same | unit (uses `tempfile` + `httpx.AsyncClient` SSE) |
| `test_terminal_stream_disconnect_releases_handle` | same | unit |
| `test_terminal_stream_per_cookie_concurrency_cap` | same | unit |
| `test_view_terminal_button_disabled_when_no_log` | `ui/tests/e2e/test_dashboard_terminal_modal.spec.ts` (new) | Playwright |
| `test_view_terminal_opens_modal_and_renders_initial_bytes` | same | Playwright, fake-spawner fixture pre-populates a stream.log |
| `test_terminal_modal_esc_closes_and_detaches` | same | Playwright |
| `test_terminal_modal_aria_role_dialog` | same | Playwright accessibility assertion |

All must run under the v9.3 test command in MISSION.md §exit criteria.
Playwright suite uses both `chromium` and `webkit` projects per
`ui/tests/e2e/playwright.config.ts`.

## 8. Migration / backwards-compatibility

- `/dashboard` (default route) stays at v9.0 chrome and gains the View
  terminal affordance — additive only.
- `/mission` (panes-only) stays unchanged for the v9.3 cycle. Mark as
  deprecated-for-removal in v9.4 once the modal terminal has 30 days of
  operator usage.
- No mission-config schema change required. No protocol change.
- New BE endpoints are additive; no existing endpoint changes shape.
- `terminal_meta` injection into `/api/v1/state` is an additive field;
  FE code that ignores unknown fields (current contract per
  `api-contract.md` §"Forward compatibility") sees no break.

## 9. Coordination with other lanes

- **LANE-D (FRONTEND)** owns implementation of §5 (lane card edit +
  `terminal_modal.js` + xterm vendoring). Coordinate via this doc.
- **LANE-C (BACKEND)** owns implementation of §4 (two new endpoints +
  `_stream_terminal` helper). The existing `.fleet/<short>.stream.log`
  source is already populated by `FleetSpawner` pipe-pane setup; no
  spawner changes needed.
- **LANE-E (TEST)** owns §7 test additions. Stream-tail fixtures can
  reuse `_parse_stream_tail`'s test fixture set.
- **LANE-A (AUDIT)** verifies §6 in `P3-A-to-B`; pay special attention
  to xterm.js CVE history and the per-cookie cap enforcement path.

## 10. Open questions (for operator decision)

1. **xterm.js vendoring vs. server-rendered ANSI HTML.** Vendoring 280 KB
   of JS for an operator-only tool is fine, but a server-side ANSI→HTML
   converter (e.g. Python `ansi2html` ~30 KB pure-Python) would keep the
   client zero-dep. Tradeoff: server rendering loses xterm.js's
   scrollback/copy/selection ergonomics. **Recommendation: xterm.js.**

2. **Write-back (operator types into the terminal).** Useful for
   approving permission prompts directly in the terminal modal instead of
   the permission-prompts panel. Requires WebSocket upgrade + a
   tmux-send-keys POST endpoint with CSRF. **Recommendation: defer to
   v9.4** — keep v9.3 read-only.

3. **Modal vs. always-on second column.** Some operators (single-lane
   focus) might want the terminal docked on the right of the chrome at
   all times. Could be a future preference, but adds layout complexity.
   **Recommendation: modal only in v9.3.**

4. **Terminal rendering of non-Claude harnesses.** Codex/Gemini/Cursor
   TUIs may have escape sequences xterm.js v5 doesn't perfectly emulate.
   **Recommendation: ship for Claude-only in v9.3 (matches MISSION
   "Out of scope" lane); validate other harnesses in v9.4.**

## 11. Done conditions (for the joint task `S-HYBRID-DASHBOARD`)

- [ ] `docs/v9/v9-3-HYBRID-DASHBOARD.md` exists with §1–§10 above (this
      doc — LANE-B half).
- [ ] BE endpoints from §4 implemented + unit-tested per §7.
- [ ] FE component from §5 implemented; vendor xterm.js per §5.1.
- [ ] Lane card edit per §5.2 with Playwright assertion per §7.
- [ ] No regression on existing dashboard.js / mission.js tests.
- [ ] Findings doc per agent recording the implementation work.
