# Megalodon v9.2 — Tmux + Web UI (Headless Fleet) Design

**Status: SUPERSEDED 2026-05-17 — DO NOT IMPLEMENT.** External contrarian review (`verifications/2026-05-17-contrarian-v9-2-tmux.md`) returned verdict `spec-should-be-redone`. See `docs/superpowers/specs/2026-05-17-megalodon-v9-2-brief.md` for the handoff brief that captures the lessons and frames the next brainstorming pass.
**Date:** 2026-05-17
**Supersedes / extends:** `docs/v9/v9-2-ROADMAP.md` (the original sketch).
**Predecessor work assumed shipped:** v9.1 (in flight in a separate session at time of writing). Specifically: the `HarnessAdapter` Protocol in `megalodon_ui/harnesses/base.py` with `build_argv`, `parse_stream_line`, `session_log_path`, `auth_env_keys`, and concrete adapters for claude/codex/gemini/copilot/cursor/vibe.
**Review history:** In-session self-contrarian review applied (5 OW + 8 PW + 6 WR; 9 fixed inline). External contrarian review (GPT-5.5 xhigh) on revised draft surfaced 10 new OW findings invalidating core architecture assumptions — preserved here only as historical record; supersession brief is the canonical forward path.

## 1. Problem statement

v9.0/v9.1 spawns lanes via AppleScript into an iTerm 2×3 grid (`scripts/launch_fleet.sh` lines 159-286). This:

- Restricts megalodon to macOS.
- Forces operators to keep iTerm visible and tab between panes.
- Has no remote / SSH / container story.
- Couples *spawn* to *operator-facing view* even though the harness CLIs run headless (`claude --print` doesn't need a TTY).

v9.2 decouples the two: tmux owns spawn (cross-platform, detached, lifecycle-managed); a browser owns the view (one tab for all lanes).

## 2. Scope (decided 2026-05-17)

- **Single release**: tmux spawn swap + xterm.js browser grid + stdin proxy + auth ship together as v9.2.
- **Deployment topology**: server binds `127.0.0.1` only. Browser-on-same-host. Remote access via operator-managed `ssh -L`.
- **tmux availability**: hard prerequisite. No fallback. Server exits non-zero if `tmux` is missing.
- **Spawn lifecycle ownership**: the Python server (`megalodon_ui/server.py`) owns starting and stopping tmux sessions. `scripts/launch_fleet.sh` shrinks to pre-flight checks (heartbeat, launch-file verification, tmux presence) then `exec`s the server.

## 3. Architecture overview

Two runtime processes plus N child harness processes:

```
┌──────────────────────────────────────────────────────────────────────┐
│  megalodon_ui server (Python)                       127.0.0.1:PORT   │
│  • owns 6 tmux sessions: lane-AUDIT, lane-ARCHITECT, …               │
│  • runs per-lane log-tail tasks → feeds parse_stream_line            │
│  • runs per-lane capture-pane snapshot loops (500ms) → SSE           │
│  • serves /api/v1/… + static dashboard (xterm.js)                    │
│  • Session token (cookie-based) gates state-changing endpoints       │
└──────────────────────────────────────────────────────────────────────┘
              │ spawns                              │ ipc via files
              ▼                                     ▼
      ┌──────────────────┐                  ┌────────────────────────┐
      │ tmux server      │                  │ applier (existing)     │
      │  ├─ lane-AUDIT   │  pipe-pane ───▶  │ reads .fleet/*.stream  │
      │  ├─ lane-ARCHIT  │                  │ mutates STATUS/HISTORY │
      │  └─ …            │                  └────────────────────────┘
      │  each runs       │
      │  claude --print  │ capture-pane ───▶  /api/v1/lane/<N>/pane
      │  …               │                    (SSE to xterm.js)
      └──────────────────┘
                  ▲
                  │ send-keys (stdin proxy)
                  │
              browser (xterm.js dashboard)
```

**Files written at runtime:**

- `<mission>/.fleet/<lane>.stream.log` — append-only structured output (pipe-pane target). Source of truth for mission state.
- `<mission>/.fleet/ui.token` — server-generated session token (bearer), mode `0600`. Server prints `Dashboard: http://127.0.0.1:PORT/#t=<token>` on startup (hash fragment, not query — see §4 auth).
- (existing) `<mission>/queue/*` — unchanged.

**Component impact:**

| Component | Status in v9.2 |
|---|---|
| `scripts/launch_fleet.sh` AppleScript block (lines 159-286) | deleted |
| `scripts/launch_fleet.sh` pre-flight (heartbeat, launch files) | kept, simplified |
| `osascript`, `as_escape`, `sh_dquote`, `badge_prefix` helpers | deleted |
| `megalodon_ui/harnesses/*.py` adapter contract | unchanged |
| `megalodon_ui/queue/applier.py` | minor: change log source path |
| `megalodon_ui/server.py` | major: add spawn driver + SSE + xterm.js serving |
| `megalodon_ui/watchdog/*` | unchanged |

## 4. Component design

### `megalodon_ui/tmux.py` (new, stateless)

Thin wrapper. One function per tmux subcommand we use:

```python
def new_session(name: str, argv: list[str], env: dict[str, str], cwd: pathlib.Path) -> None: ...
def kill_session(name: str) -> None: ...   # no-op if missing
def has_session(name: str) -> bool: ...
def pipe_pane(session: str, dest_path: pathlib.Path) -> None: ...  # uses -O for non-destructive append
def capture_pane(session: str, *, with_escapes: bool = True) -> str: ...
def send_keys(session: str, keys: str, *, enter: bool) -> None: ...
def list_sessions(prefix: str = "lane-") -> list[str]: ...
```

Each shells out via `subprocess.run(["tmux", …])` with `check=True`. Errors raise `TmuxError(stderr)`.

**Tmux options set on every session:**

- `remain-on-exit on` — pane stays visible after child exits, so operators see exit output.
- `mouse on` — convenience.
- Default `tmux` socket (no custom socket path in v9.2).

**Pane size:** detached `tmux new-session -d` defaults to 80×24, but the browser xterm.js viewport is typically 150-250 cols × 40-60 rows. v9.2 sets a fixed initial size of **200×50** via `tmux new-session -d -x 200 -y 50`. Rationale:

- Wide enough that typical harness output (JSON streams, log lines, status tables) doesn't wrap unexpectedly.
- xterm.js with FitAddon adapts visually — narrower viewport scrolls horizontally, wider has dead space on the right.
- Browser-driven dynamic resize via `resize-window` is deferred: multiple SSE subscribers per lane would otherwise fight over size with last-write-wins, which is worse than a fixed sensible default.
- Operators needing wider output can edit the constant in v9.2; full dynamic resize is a v9.3 candidate.

### `megalodon_ui/spawn.py` (new, owns runtime state)

```python
@dataclass
class LaneSession:
    name: str                    # "AUDIT"
    session: str                 # "lane-AUDIT"
    adapter: HarnessAdapter
    model: str
    stream_log: pathlib.Path     # .fleet/audit.stream.log
    capture_task: asyncio.Task | None
    capture_queues: list[asyncio.Queue[str]]   # one per active SSE subscriber
    last_snapshot: str | None                  # cached for first-frame on connect

class FleetSpawner:
    def __init__(self, mission_dir, config): ...
    async def start_all(self) -> dict[str, LaneSession]: ...
    async def stop_all(self) -> None: ...
    async def restart(self, lane: str) -> None: ...
    def get(self, lane: str) -> LaneSession: ...
```

`start_all` walks `config.lanes[*]`, calls adapter's `build_argv`, invokes `tmux.new_session` → `tmux.pipe_pane` → starts per-lane capture task. `stop_all` cancels capture tasks before killing sessions. All tmux interaction goes through `tmux.py`; the spawner has no `subprocess` imports.

### `megalodon_ui/server.py` endpoint additions

All under `/api/v1/lane/<NAME>/`. Auth: `mui_session` cookie (see token transport above).

| Method | Path | Purpose |
|---|---|---|
| GET | `/pane-stream` | SSE: `capture-pane` frames @ 500 ms |
| POST | `/send-keys` | `{keys, enter}` → `tmux.send_keys` |
| DELETE | `` | `tmux.kill_session` |
| POST | `/restart` | kill + respawn with same adapter/model |
| GET | `/state` | lane status snapshot (running / exited / failed_to_spawn) |

Plus auth bootstrap (no cookie required): `POST /api/v1/auth/exchange` `{token}` → sets `mui_session` cookie.

**Session token** (bearer token, not CSRF — see §12 terminology note): generated with `secrets.token_urlsafe(32)` at server boot. Stored two ways:

- On disk: `.fleet/ui.token`, written atomically via `os.open(path, O_CREAT|O_EXCL|O_WRONLY, 0o600)` — never `write_text` then `chmod` (race window where mode is umask-default).
- In the printed URL: as a **URL hash fragment**, e.g. `http://127.0.0.1:PORT/#t=<token>`.

**Why hash fragment, not query param:** the hash is not sent to the server, not stored in the server's access logs, not included in `Referer` headers if the operator clicks an external link, and not indexed by search-history readers. It still appears in browser history and bookmarks (mitigation: rotate token on every server boot — bookmarks go stale, which is the intended behavior).

**Bootstrap → cookie exchange:**

1. Browser loads `http://127.0.0.1:PORT/#t=<token>`.
2. `index.html` inline bootstrap reads `location.hash`, parses token.
3. `POST /api/v1/auth/exchange` with `{token}` body.
4. Server validates against `.fleet/ui.token`, sets cookie `mui_session=<server-side-session-id>; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400`.
5. Bootstrap does `history.replaceState(null, '', '/')` — token removed from URL.
6. All subsequent requests (including `EventSource` for SSE) carry the cookie automatically; no token in URLs ever again.

**Why cookie, not header:** `EventSource` cannot set custom headers, but it *does* send cookies for same-origin requests. Cookies don't appear in URLs, history, bookmarks, or `Referer`. `HttpOnly` blocks JS access (defense in depth against XSS in the dashboard, even though dashboard is fully first-party).

**Endpoint auth:**

| Method | Path | Auth |
|---|---|---|
| GET | `/` (index.html) | none (bootstrap page) |
| POST | `/api/v1/auth/exchange` | body token must match `.fleet/ui.token` |
| GET | `/pane-stream`, POST `/send-keys`, DELETE, POST `/restart`, GET `/state` | `mui_session` cookie |

### `megalodon_ui/static/` layout

```
static/
  index.html          # dashboard entry, loads /static/dashboard.js
  dashboard.js        # ~200 lines: grid layout, SSE subscribers, focus model
  dashboard.css       # CSS grid, focus styling
  login.html          # token-paste fallback for 401s
  xterm/
    xterm.js          # vendored @xterm/xterm bundle
    xterm.css
```

Vendored, not CDN — self-containment per CLAUDE.md.

### `scripts/launch_fleet.sh` post-v9.2

```bash
#!/usr/bin/env bash
# Pre-flight: applier heartbeat, launch files, tmux presence.
# Then: exec python -m megalodon_ui --mission "$MISSION_DIR" "${SERVER_FLAGS[@]}"
```

Shrinks from 305 lines to ~50.

**Flag migration:** the existing script supports flags that need new homes on the Python server, not silent breakage:

| Old bash flag | New home |
|---|---|
| `--spawn` | Implicit (server always spawns). Removed. |
| `--dry-run` | Removed (was AppleScript-specific). |
| `--no-launch` | `--dry-run` on server: prints argv per lane, exits without spawning. |
| `--skip-applier-check` | Forwarded as-is to bash pre-flight. |
| `--cli-<lane>=<bin>` | Forwarded as `--lane-cli LANE=BIN`. Server applies as a per-lane adapter override. |
| `--prompt-override=<txt>` | Forwarded as `--prompt-override <txt>`. Server replaces the `read launch-<LANE>.md` prompt for every lane. |
| `-h`/`--help` | Unchanged (script-level help). Server has its own `--help`. |

Backward-compatibility: a deprecation banner from `launch_fleet.sh` if any operator passes `--spawn` or `--dry-run` ("v9.2 always spawns; this flag is a no-op"). Hard removal in v9.3.

## 5. Phase breakdown

Six phases, one PR each. Each ends in a green-test state where you could stop.

### P1 — Server-owned tmux spawn (replaces AppleScript)

- New: `megalodon_ui/tmux.py`, `megalodon_ui/spawn.py`.
- `server.py`: on startup, call `FleetSpawner.start_all`. On SIGTERM/SIGINT, call `stop_all`.
- `launch_fleet.sh`: delete AppleScript block; keep pre-flight; `exec python -m megalodon_ui ...`.
- Pre-flight in `launch_fleet.sh`: `command -v tmux >/dev/null || { echo "error: tmux not found; install with: brew install tmux (macOS) / apt install tmux (Debian)"; exit 6; }`. This is an early-fail courtesy; the Python server repeats the check on boot as the authoritative gate (Section 7).
- Tests: unit-mock tmux wrapper; integration test boots server with `--no-applier`, verifies 6 sessions via `tmux ls`.

### P2 — Structured-stream tap + applier rewire

- `spawn.py`: after `new_session`, immediately `pipe_pane(session, dest=<mission>/.fleet/<lane>.stream.log)`.
- `megalodon_ui/queue/applier.py`: change log source per lane to the new path. The applier's existing per-file byte-offset state (whatever it's called in v9.1) follows the renamed source.
- **No log rotation in v9.2.** The boot-time 10 MB rotation in the earlier draft was unsafe: it invalidates the applier's saved offset (offset points past the new fresh file's EOF, causing skip or replay). Spec moves rotation to v9.3+, where it requires a coordinated "stop applier → rotate → reset offset → restart applier" sequence, or a copytruncate strategy that keeps offsets valid.
- Operational mitigation in v9.2: append-only logs grow at ~1-10 MB/hour for typical missions, so multi-day missions before disk pressure becomes relevant. Watchdog logs a warning if any `.fleet/<lane>.stream.log` exceeds 500 MB (new check in P2). Operators can manually rotate during mission idle by stopping the server (sessions persist), `mv <lane>.stream.log <lane>.stream.log.archive`, removing the applier's offset file for that lane (re-derived from queue/STATUS on restart), and restarting the server.
- Tests: end-to-end with stub harness emitting known STATUS/HISTORY lines; assert applier mutates state. Plus a regression test for the operator manual-rotation sequence above.

### P3 — Visual stream backend (SSE)

- `spawn.py`: per lane, asyncio task runs `capture_pane -e -p -t lane-<N>` every 500 ms. The task updates `LaneSession.last_snapshot` and fan-outs to each active subscriber queue. Per-subscriber queues are `asyncio.Queue(maxsize=4)`; on `QueueFull`, the producer does `q.get_nowait()` (discard oldest) then `q.put_nowait()` (new frame) — this is the drop-oldest pattern, since `asyncio.Queue` has no native drop-oldest.
- `server.py`: `GET /api/v1/lane/<NAME>/pane-stream` SSE drains the queue.
- Capture cadence: fixed 500 ms. Snapshot encoding: full pane every tick (no delta in v9.2).
- Tests: curl the SSE endpoint, assert frames at ~500 ms cadence.

### P4 — xterm.js dashboard (read-only)

- New: `static/index.html`, `dashboard.js`, `dashboard.css`, vendored `xterm/`.
- CSS grid; columns derived from N lanes (`ceil(sqrt(N))` cols — reuses v9.1 P1.3 grid math).
- Each pane subscribes to its SSE endpoint, writes frames to an `xterm.Terminal` via `term.write(data)`.
- Token from `?t=` query param stored in `sessionStorage`, included on all `/api/v1/...` requests.
- Tests: Playwright (`@playwright/test`) — load page, assert 6 panes render, assert text appears within 5 s of spawn.

### P5 — stdin proxy (interactive control)

- New: `POST /api/v1/lane/<NAME>/send-keys` body `{"keys": "...", "enter": bool}`. `mui_session` cookie required (set by the auth bootstrap).
- Dashboard: click-to-focus model. Focused pane gets thin border + keyboard input routes to it. Esc unfocuses.
- xterm.js `onData` handler buffers keystrokes; flushes on 50 ms trailing-edge idle window (one POST per burst).
- No local echo — wait for round-trip through tmux and next capture frame (~50-550 ms perceived lag).
- Tests: Playwright — focus pane, type "ping", assert appears in `capture-pane` snapshot within 1 s.

### P6 — Lifecycle controls + polish

- Per-pane Restart and Kill buttons.
- `DELETE /api/v1/lane/<NAME>` → `kill_session`.
- `POST /api/v1/lane/<NAME>/restart` → kill + respawn with same adapter/model.
- Server graceful shutdown finalized: on SIGTERM, cancel capture tasks → kill all sessions → delete `.fleet/ui.token` → exit 0.
- Tests: kill via UI → assert session gone; restart → assert new session appears.

## 6. Data flow

### Flow 1 — Structured stream (mission state)

```
claude --print stdout
    │
    ▼
tmux pane buffer  ──── (also rendered for capture-pane) ────┐
    │                                                       │
    │ pipe-pane -O                                           │
    ▼                                                       │
.fleet/audit.stream.log  (append-only)                      │
    │                                                       │
    │ existing tail-and-parse loop in applier                │
    ▼                                                       │
adapter.parse_stream_line(line) -> Event                    │
    │                                                       │
    ▼                                                       │
queue/STATUS, queue/HISTORY mutations                       │
```

- Latency target: sub-second. `pipe-pane` flushes line-buffered; applier tails with 100 ms poll.
- Durability: file persists across server restarts. Applier resumes from last-parsed offset (existing behavior).
- No coupling to visual path: if capture-pane stops, mission state still advances.

### Flow 2 — Visual stream (browser)

```
tmux pane buffer
    │
    │ capture-pane -e -p -t lane-AUDIT  (every 500 ms)
    ▼
update LaneSession.last_snapshot + fan-out to each subscriber queue
    │                                  (per-subscriber asyncio.Queue,
    │                                   maxsize=4, drop-oldest pattern)
    ▼
SSE consumer:  initial frame = last_snapshot; subsequent = queue.get()
    │
    ▼
GET /api/v1/lane/AUDIT/pane-stream?t=<token>  (text/event-stream)
    │
    ▼
browser EventSource → term.write(frame)
```

- Backpressure: per-subscriber queue maxsize=4. On `QueueFull`, producer discards oldest then enqueues new frame. Slow browser → frames drop, never blocks the capture loop. Operator sees jump-cut; acceptable for an observability layer.
- First-frame: on new SSE connection, immediately yield `LaneSession.last_snapshot` (if non-null) before awaiting the queue. Fresh tabs paint within milliseconds, not 500 ms.
- Reconnect: `EventSource` auto-reconnects with `Last-Event-ID`; we ignore the ID and send `last_snapshot`. Visual state is idempotent.

### Flow 3 — Stdin proxy

```
xterm.js term.onData(data)
    │
    │ 50 ms trailing-edge debounce
    │   (collect keystrokes until 50 ms idle, then POST as one batch)
    ▼
POST /api/v1/lane/AUDIT/send-keys
  headers: X-Megalodon-Token: <token>
  body:    {"keys": "hello", "enter": false}
    │
    ▼
server validates token
    │
    ▼
tmux.send_keys("lane-AUDIT", "hello", enter=False)
    │
    ▼
keystrokes appear in pane → output flows back through Flow 1 & 2
```

- No echo: browser does not locally echo — round-trip through tmux → next capture frame.
- Enter handling: `enter: true` adds `Enter` token to `send-keys` argv.
- Special keys: xterm.js passes raw bytes (Ctrl-C `\x03`, arrows, etc.); tmux `send-keys` accepts them literally. Pass-through.

### Server startup sequence

Startup runs entirely inside FastAPI's `lifespan` async context manager so there's exactly one event loop (uvicorn's). No `asyncio.run` before `uvicorn.run`.

Synchronous phase (before `uvicorn.run`):

1. argv parse → mission_dir, port.
2. tmux availability check; exit 6 if missing.
3. **Port pre-bind check.** Open a probe `socket.socket(AF_INET, SOCK_STREAM)`, `bind(("127.0.0.1", port))`, `close()`. If `EADDRINUSE`, exit 9. Done *before* touching `.fleet/ui.token` so we never unlink another live server's token. (Uvicorn re-binds in step 5; the brief gap between probe-close and uvicorn-bind is acceptable — race window is single-digit ms, blast radius is `EADDRINUSE` from uvicorn which we'd catch anyway.)
4. Atomic token write: `os.open(.fleet/ui.token, O_CREAT|O_EXCL|O_WRONLY, 0o600)`. If file already exists, unlink and retry once (the prior token is rotated out — safe now that the port-check confirmed no live server). Write `secrets.token_urlsafe(32)`.
5. Print `Dashboard: http://127.0.0.1:PORT/#t=<token>`.

Async phase (inside lifespan):

```python
@asynccontextmanager
async def lifespan(app):
    spawner = FleetSpawner(mission_dir, config)
    await spawner.start_all()   # spawn/reattach all lanes, pipe-pane, capture tasks
    app.state.spawner = spawner
    try:
        yield                   # uvicorn serves requests here
    finally:
        await spawner.stop_all(kill_sessions=False)  # see shutdown below
```

`uvicorn.run(app, host="127.0.0.1", port=port)` is the final call.

### Server shutdown sequence

**Default shutdown (SIGTERM/SIGINT) is non-destructive** — tmux sessions persist so the operator can restart the server without losing lane state (the headline restart-resilience property in §7).

```
SIGTERM → uvicorn → lifespan exit:
  spawner.stop_all(kill_sessions=False):
    - cancel all capture tasks
    - close all SSE subscriber queues
    - do NOT kill tmux sessions (they keep running detached)
    - do NOT delete .fleet/ui.token (so next boot can detect and rotate cleanly)
  exit 0.
```

**Destructive shutdown** is opt-in:

- Operator-driven, per-lane: `DELETE /api/v1/lane/<NAME>` → `tmux kill_session`.
- Operator-driven, fleet-wide: `python -m megalodon_ui --shutdown <mission>` (one-shot CLI mode that calls `tmux kill-session` for each `lane-*` then exits without starting the server).
- Server flag: `--kill-on-exit` makes SIGTERM also kill sessions. Off by default. Useful in CI where tests want a clean teardown.

The contradiction between "non-destructive restart" and "clean shutdown" is resolved by making the operator choose explicitly.

## 7. Error handling

### Startup-time failures (fatal, exit non-zero)

| Failure | Behavior |
|---|---|
| `tmux` not on PATH | Exit 6 with install hint. |
| Mission dir doesn't exist | Exit 1. |
| `.fleet/` not writable | Exit 7. |
| Token file write fails | Exit 8. |
| Port already in use | Exit 9. |

### Spawn-time failures (per-lane, isolated)

| Failure | Behavior |
|---|---|
| Stale `lane-X` session from prior crash (not a reattach candidate; see §7 Server-restart resilience) | Kill, then create fresh. Log `replacing stale session lane-X`. Note: this branch runs only when reattach is explicitly disabled (e.g., `--fresh` flag) or when `has_session` returns False but a residual artifact like an orphaned pipe-pane log exists. The normal restart path reattaches. |
| `tmux new-session` fails for one lane | Log + continue with other lanes. Lane appears in UI as `failed_to_spawn` with stderr in tooltip. Mission continues with degraded fleet. |
| Adapter `build_argv` raises | Same — lane marked failed, others spawn. |

### Runtime failures

| Failure | Behavior |
|---|---|
| Lane process exits | `remain-on-exit on` → pane stays visible with final output. UI shows `exited (rc=N)`. capture-pane keeps snapshotting frozen state. Restart button available. |
| `pipe-pane` stops appending | Existing watchdog detects log staleness, alerts operator. v9.2 adds no new watchdog logic. |
| capture-pane subprocess errors | Log warning, retry next tick. 3 consecutive failures → mark lane visual as `capture_unavailable`, stop the task, show static error placeholder. Structured stream unaffected. |
| Disk full writing `.fleet/*.stream.log` | Fatal — log + clean shutdown (kill sessions, exit). Better visible failure than corrupt mission state. |
| SSE client disconnect | Drop the per-connection queue listener; capture task keeps running. |
| Send-keys to nonexistent session | 404. UI surfaces "lane not running". |

### Server-restart resilience

On startup, before spawning, for each configured lane:

```
if tmux.has_session(f"lane-{lane}"):
    # Pre-existing session from previous server run.
    log("reattaching to existing session lane-X")
    # Idempotency caveat: `tmux pipe-pane` (no flags) *toggles* the pipe.
    # If the prior server already opened a pipe to the same path, calling
    # it again would close it. So the reattach path queries pipe state
    # with `tmux display-message -p -F '#{pane_pipe}' -t lane-X` first;
    # only opens a fresh pipe if `pane_pipe == 0`.
    # Re-start capture task.
    # Do NOT respawn the process — it's still running.
else:
    spawn fresh.
```

`kill -9` on the server, restart it, fleet picks up where it was. Zero extra design cost.

### Browser-side errors

| Failure | Behavior |
|---|---|
| SSE disconnect | EventSource auto-reconnects. UI shows per-pane "reconnecting…" badge during gap. |
| Token rejected (401) | Redirect to `/login.html` with paste-token textbox. |
| xterm.js throws on bad input | Caught per-pane; error displayed in that pane only; other panes keep working. |

### Out of scope for v9.2 error handling

- Auto-restart of crashed lanes (manual button only).
- Quota / rate-limit handling for harness APIs (adapter concern).
- Mid-run log rotation (only at boot in v9.2).

## 8. Testing strategy

### Test pyramid

```
                  ┌─────────────┐
                  │ Playwright  │   ~5 tests, ~30 s total
                  │     E2E     │   real server + tmux + stub harness + browser
                  └─────────────┘
                ┌─────────────────┐
                │  pytest server  │   ~15 tests, ~10 s total
                │  integration    │   FastAPI test client + real tmux
                └─────────────────┘
              ┌─────────────────────┐
              │   pytest unit       │   ~30 tests, <2 s total
              │  tmux.py, spawn.py  │   subprocess + tmux fully mocked
              └─────────────────────┘
```

### Stub harness fixture + stub adapter

Two fixtures are needed because the spawn path goes `adapter.build_argv(...) → tmux.new_session(argv, ...)`. CI can't depend on real `claude`/`codex`/`gemini` binaries, so we need both a fake harness script AND a fake adapter whose `build_argv` returns that script's path.

**`tests/fixtures/stub_adapter.py`**:

```python
from megalodon_ui.harnesses.base import Capabilities, Event, ModelSpec

class StubAdapter:
    name = "stub"
    default_model = "stub-happy"
    available_models = (
        ModelSpec(id="stub-happy", is_default=True),
        ModelSpec(id="stub-error"),
        ModelSpec(id="stub-long-running"),
    )
    supports_autonomous_loop = False

    def build_argv(self, prompt_or_launch_path, *, model, cwd, **_):
        script = pathlib.Path(__file__).parent / "stub_harness.sh"
        return [str(script), model.removeprefix("stub-")], {}

    def parse_stream_line(self, line):
        line = line.rstrip("\n")
        if not line.strip(): return None
        if line.startswith("{"):
            try: return Event(kind="text", text=json.loads(line).get("text",""), raw=json.loads(line))
            except json.JSONDecodeError: return Event(kind="text", text=line)
        return Event(kind="text", text=line)

    def session_log_path(self, cwd, session_id): return None
    def auth_env_keys(self): return []
    def supports(self): return Capabilities(False, False, True, False)
```

**`tests/fixtures/stub_harness.sh`**:

```bash
#!/usr/bin/env bash
case "$1" in
  audit-happy)
    echo '{"type":"text","text":"STATUS: starting"}'
    sleep 0.1
    echo '{"type":"text","text":"HISTORY: step 1 complete"}'
    sleep 0.1
    echo '{"type":"text","text":"STATUS: done"}'
    ;;
  audit-error)
    echo '{"type":"text","text":"STATUS: starting"}'
    sleep 0.1
    echo 'STATUS: failed (boom)' >&2
    exit 17
    ;;
  long-running)
    i=0; while true; do echo "{\"type\":\"text\",\"text\":\"tick $i\"}"; i=$((i+1)); sleep 1; done
    ;;
esac
```

Used wherever the spec needs "a process that produces output." No real harness binary required in CI.

### Unit tests (per phase)

| Phase | File | What's tested |
|---|---|---|
| P1 | `tests/test_tmux.py` | argv construction; error mapping for nonzero exit; quoting of names with special chars. |
| P1 | `tests/test_spawn.py` | `start_all` calls `tmux.new_session` once per lane with adapter argv; `stop_all` cancels capture tasks before killing; reattach branch on `has_session=True`. |
| P2 | `tests/test_applier_v92.py` | applier reads from `.fleet/<lane>.stream.log`; pre-existing applier tests still pass. |
| P5 | `tests/test_send_keys.py` | Endpoint: 401 on missing token; 404 on nonexistent lane; correct keys+enter passthrough. |

### Integration tests (`tests/integration/`)

- `test_real_tmux_spawn.py`: real `tmux` against scratch session prefix `megalodon-test-<pid>-`. Spawn stub harness; assert `pipe-pane` file fills with expected lines; assert `capture-pane` returns non-empty; kill cleanly. Fixture autocleans on teardown.
- `test_server_startup.py`: FastAPI `TestClient` + real tmux + stub harnesses for 6 lanes. Hit `/api/v1/lane/AUDIT/state`; assert running. Hit `/pane-stream` for 1 s; assert ≥1 frame.
- `test_reattach.py`: spawn → drop FleetSpawner → new one with same config → assert reattach (no respawn).
- `test_shutdown_cleanup.py`: spawn → SIGTERM → assert `tmux ls` shows no `lane-*` sessions and `.fleet/ui.token` is gone.

### E2E (`tests/e2e/` with `@playwright/test`)

- `playwright.config.ts`: `webServer` boots `python -m megalodon_ui --mission tests/fixtures/mission_v92 --port 8765`; `workers: 1` (tmux name collisions); `retries: 2`; HTML reporter.
- `dashboard-loads.spec.ts` — page loads, 6 `.lane-pane` elements present, each has `xterm-screen`.
- `streams-render.spec.ts` — within 5 s, each pane contains expected stub output.
- `send-keys-roundtrip.spec.ts` — focus AUDIT, type "ping", assert appears in pane within 1 s.
- `restart-lane.spec.ts` — click Restart on AUDIT, assert pane briefly empties then re-fills.
- `token-rejection.spec.ts` — no `?t=`, assert redirect to `/login.html`.

### Pre-commit / CI parity

- Pre-commit: `ruff` + `pytest tests/unit -x`.
- Pre-push: full pytest + playwright.
- CI runs identical commands. `tmux` installed in CI image (`apt install tmux` on `ubuntu-latest`).

### Out of scope for v9.2 testing

- Load testing (concurrent SSE clients).
- Cross-browser (Chromium only).
- Visual regression of xterm.js rendering.

## 9. Out of scope / explicitly deferred

| Item | Rationale |
|---|---|
| Adaptive capture cadence | Fixed 500 ms. Optimize when measurable. |
| Scrollback UI affordance (`capture-pane -S -<N>`) | Operators can `tmux a -t lane-X` for full history. |
| OIDC / reverse-proxy auth | Replaced by process-local bearer token + same-origin cookie. Real auth deferred until remote serving is in scope. |
| xterm.js bundle optimization | Accept ~200 KB vendored. Tree-shake/lazy-load deferred. |
| tmux fallback (nohup, AppleScript) | Hard prereq; explicitly rejected. |
| Auto-restart of crashed lanes | Cascade-failure debugging hell. Manual button only. |
| Delta encoding for visual stream | Full snapshots at ~600 KB/s on localhost are fine. |
| Log rotation (boot-time or mid-run) | Earlier draft had 10 MB boot prune; removed because it invalidates applier's saved offset. Operators manually rotate during idle (procedure documented in §5 P2). Coordinated rotation in v9.3+. |
| Multi-mission per server | One mission per server process. |
| Cross-browser support | Chromium only in v9.2 E2E. |
| Mobile / responsive UI | Desktop-only operator workflow. |
| HTTPS / TLS | Localhost only — TLS is the SSH tunnel's job. |
| Light mode / a11y polish | Dark mode default (per CLAUDE.md). |
| Lane reordering / custom grid layouts | Fixed grid per `config.lanes[*]` order. |
| SIGHUP config reload | Read once at boot. |
| Mission-history dashboard / playback | Real-time only; logs on disk if needed. |

### Explicit non-goals (will not happen without strong reason, even in v9.3+)

- Bespoke tmux-replacement in Python. tmux is the dependency.
- WebSocket bidirectional protocol. SSE + POST is sufficient.
- General-purpose process supervisor abstraction. `FleetSpawner` knows about megalodon lanes, not arbitrary processes.

## 10. Open questions resolved (from roadmap)

| Roadmap Q | Disposition |
|---|---|
| Capture cadence (adaptive?) | Fixed 500 ms. Adaptive deferred. |
| Scrollback UI | Deferred. |
| Web UI auth | Process-local bearer token via hash-fragment → cookie exchange, 127.0.0.1 only. Remote auth deferred. |
| xterm.js bundle size | Accept ~200 KB. Deferred optimization. |
| tmux availability fallback | Rejected. Hard prereq. |

## 11. Predecessor work assumed shipped from v9.1

- Harness adapter `Protocol` with `build_argv` returning `(argv, env_overlay)` — produces argv usable by any spawner.
- `parse_stream_line` per adapter — structured-stream interpretation already factored out of spawn driver.
- `auth_env_keys` — credential injection via `extra_env`.
- `session_log_path` — stable file path conventions for watchdog.

If v9.1 reshapes any of these contracts before merging, the affected v9.2 sections require revision before implementation.

## 12. Terminology notes

- **Session token / bearer token** — the random 32-byte secret in `.fleet/ui.token`. Functions as bearer credentials when sent to the server (initially in the auth-exchange POST body; thereafter as the underlying secret of the `mui_session` cookie). Not a CSRF token (no cookie-bound double-submit pattern); calling it "CSRF" in earlier drafts was incorrect.
- **Session (tmux sense)** — a tmux session, named `lane-<NAME>`, owning one detached pane running one harness CLI. Disambiguate from "mui_session cookie" (browser-side) when both appear in the same sentence.
- **Lane** — a logical role (AUDIT, ARCHITECT, BACKEND, FRONTEND, TEST, META in v9.1's default config). A lane maps to exactly one tmux session at a time.
- **Frame** — a single `capture-pane -e -p` snapshot of a pane, ANSI-escapes intact, suitable for `term.write()` on the browser side.
