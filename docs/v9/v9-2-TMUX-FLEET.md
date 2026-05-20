# v9.2 — Tmux Headless Fleet

**Status:** SHIPPED 2026-05-18.
**Supersedes:** v9.0 / v9.1 iTerm-grid spawn model.
**Plan:** `~/Documents/Projects/.plans/megalodon/v9-2-tmux-headless-fleet-2026-05-17.md` + `…-tasks.md`.

The v9.2 release decouples *spawn* (tmux owns it) from *operator-facing view* (browser owns it). The orchestrator becomes a single FastAPI process; lane harnesses run as detached tmux panes; the dashboard renders each pane in a browser-side xterm.js terminal over Server-Sent Events.

This document is the architecture overview + operator runbook. Companion docs:

- `v9-2-AUTH.md` — bootstrap flow, cookie semantics, paste-token recovery.
- `v9-2-FOLLOWUP-PROMPTS.md` — adapter contract, respawn semantics, sentinel chunk.
- `v9-2-ROADMAP.md` — earlier design sketch (SUPERSEDED).

## 1 — High-level architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  python -m megalodon_ui  (single uvicorn process)                   │
│                                                                     │
│   ┌──────────────┐    ┌────────────────────────────────────────┐    │
│   │ FleetSpawner │ ── │  per-mission tmux server                │    │
│   │              │    │   socket: <mission>/.fleet/tmux.sock    │    │
│   │              │    │   sessions: lane-A, lane-B, …           │    │
│   └──────┬───────┘    │   pipe-pane → <short>.stream.log         │    │
│          │            └────────────────────────────────────────┘    │
│          │ subscribers (asyncio.Queue per session)                  │
│          ▼                                                          │
│   ┌──────────────┐                                                  │
│   │ FastAPI app  │ ── /api/v1/lane/{lane}/pane-stream   (SSE)       │
│   │              │ ── /api/v1/lane/{lane}/followup      (POST)      │
│   │              │ ── /api/v1/lane/{lane}/state         (GET)       │
│   │              │ ── DELETE /api/v1/fleet              (teardown)  │
│   └──────────────┘                                                  │
│          │                                                          │
└──────────┼──────────────────────────────────────────────────────────┘
           ▼
    Browser dashboard:
      ui/static/pages/dashboard-v92.js  →  xterm.js grid (one term per lane)
```

### 1.1 Spawn ownership

- The orchestrator process owns the tmux server lifecycle on a per-mission socket at `<mission>/.fleet/tmux.sock`.
- Each lane is one tmux session named `lane-<NAME>` (e.g., `lane-AUDIT`).
- Sessions are detached (`-d`) — no TTY required. The harness CLIs already run headless.
- `tmux pipe-pane` taps each pane's stdout into `<mission>/.fleet/<short>.stream.log` for replay-on-connect.

### 1.2 View ownership

- Each lane's pane gets one SSE channel: `GET /api/v1/lane/{lane}/pane-stream`.
- The first SSE event is `\x1bc` (terminal-clear) so a reconnect resets the xterm state.
- The second event (if any) is the last `TAIL_ON_CONNECT_BYTES` of the stream log — replay so a late connect catches up.
- Subsequent events are base64-encoded byte chunks delivered through a per-subscriber `asyncio.Queue`.
- All SSE traffic is HttpOnly+SameSite=Strict cookie-gated (see `v9-2-AUTH.md`).

## 2 — Operator runbook

### 2.1 Start a mission

```bash
python -m megalodon_ui \
    --mission-dir ~/Documents/Projects/<mission> \
    --host 127.0.0.1 \
    --port 8000
```

The first line of stdout is the dashboard URL with a one-time bootstrap token:

```
http://127.0.0.1:8000/#t=<token>
```

Open that URL in a browser. The fragment `#t=<token>` is exchanged once for an HttpOnly session cookie via `POST /api/v1/auth/exchange`, then the dashboard renders.

### 2.2 Recover the dashboard URL after closing the terminal

The bootstrap URL is also written to `<mission>/.fleet/dashboard.url` at startup. To re-open the dashboard from another shell:

```bash
cat <mission>/.fleet/dashboard.url
# http://127.0.0.1:8000/#t=<token>
```

This file is the canonical recovery path for **CV-11**. If you lose your terminal, the URL (with token) lives on disk for the lifetime of the mission.

If `<mission>/.fleet/dashboard.url` is gone but the server is still running, your session cookie may already be valid — open `http://127.0.0.1:8000/` and the paste-token modal will prompt for the token. The token is in `<mission>/.fleet/ui.token` (mode 0600).

### 2.3 Stop a mission

Two paths:

1. **Live server, browser open:** the dashboard exposes a destructive `DELETE /api/v1/fleet` button. Cookie-gated. Kills the tmux server, unlinks `.fleet/ui.token`, `.fleet/tmux.sock`, `.fleet/dashboard.url`, then uvicorn shuts down.
2. **Server dead or unreachable:** run the standalone CLI

   ```bash
   python -m megalodon_ui.shutdown --mission-dir <path>
   ```

   Idempotent. Same effect (kill tmux server + unlink the three artifact files). Exit 0 always when `--mission-dir` is a valid directory.

### 2.4 Inspect a single pane manually

You can attach to any lane's pane directly from your terminal — useful for debugging without the dashboard.

```bash
tmux -S <mission>/.fleet/tmux.sock attach -t lane-AUDIT
```

Detach with `Ctrl-b d`. The pane keeps running; attach again any time.

## 3 — `MEGALODON_FLEET_OWNED` and the orphan-cleanup contract

When `FleetSpawner.start_all()` creates a tmux session, it sets the session-scoped environment variable `MEGALODON_FLEET_OWNED=1`. The orphan-cleanup helper (`scripts/launch_fleet.sh` "first-run orphan sweep" + the in-process startup check) treats this marker as authoritative: any tmux session matching `lane-*` whose env does NOT carry `MEGALODON_FLEET_OWNED=1` is left alone.

Why this matters: an operator who runs

```bash
tmux new -d -s lane-DEBUG bash
```

to manually triage a flaky harness will NOT have their session killed by the next fleet launch. Only sessions the orchestrator itself created get reaped.

To verify the marker on a running fleet session:

```bash
tmux -S <mission>/.fleet/tmux.sock show-environment -t lane-AUDIT | grep MEGALODON_FLEET_OWNED
# MEGALODON_FLEET_OWNED=1
```

## 4 — Follow-up prompts (CV-12)

`POST /api/v1/lane/{lane}/followup` with body `{prompt, model?}` does the following:

1. Resolves the lane's harness adapter (claude / codex / gemini / copilot / cursor / vibe).
2. Calls `adapter.build_followup_argv(prompt, prior_session_id=..., model=..., cwd=...)`. Claude appends `--resume <sid>`; Codex emits `codex exec resume <sid> <prompt>`; others fall back to a fresh `build_argv` invocation.
3. Calls `FleetSpawner.respawn(lane, argv, env)`, which executes:
   - `tmux respawn-pane -k` to replace the running child in-place.
   - **Re-pipes the new pane to the stream log** — `respawn-pane -k` drops the pipe-pane association, so we must call `pipe_pane` again and verify it took via `display-message -p '#{pane_pipe}'` (PM-3 fix).
   - **Drains then pushes a "restarting" sentinel** under `subscribers_lock`. The sentinel is the exact byte sequence

     ```
     b"\x1bc\xe2\x9f\xb3 restarting\xe2\x80\xa6\r\n"
     # → ESC c (terminal clear) + UTF-8 "⟳ restarting…\r\n"
     ```

     Drain-then-push under lock guarantees the sentinel is the first post-respawn chunk every subscriber sees, even under slow-consumer backpressure that would otherwise drop-oldest (CV-12 + PM-7).

See `v9-2-FOLLOWUP-PROMPTS.md` for the adapter-by-adapter contract and the rationale for the byte-pinned sentinel.

## 5 — Lane state (CV-8)

`GET /api/v1/lane/{lane}/state` returns

```json
{
  "running": true,
  "exited_rc": null,
  "started_utc": "2026-05-18T20:00:00Z",
  "last_bytes_offset": 12345
}
```

When a harness exits (e.g., `claude --print` finishes, or the process crashes), the next `GET /state` reports `running: false` with `exited_rc` set to the integer rc captured by tmux. The detection uses `tmux display-message -p '#{pane_dead}|#{pane_dead_status}'` lazily — no background polling. A 1 s TTL cache on `LaneSession.pane_dead_checked_at` bounds the tmux query cost to one call per lane per second regardless of dashboard polling rate.

The dashboard polls `/state` after observing an SSE disconnect, so a fast-failing lane shows `exited_rc=17` within 5 s of spawn — exercised by the CV-8 integration test (`scripts/tests/test_lane_exit_detected_within_5s.py`).

## 6 — Stream log size warn (P7.3)

The watchdog polls `<mission>/.fleet/<short>.stream.log` on every cycle. When a stream log reaches `STREAM_LOG_WARN_BYTES` (500 MB; defined in `megalodon_ui/_v92_constants.py`) the watchdog emits a `STREAM-LOG-SIZE` SIGNAL finding under `findings/`. Operator action: rotate or truncate the file.

This is a *warn*, not an *auto-rotate* — the watchdog never deletes operator data. Manual truncation while the fleet runs is safe (`pipe-pane` re-opens the file on next write); rotation is also safe as long as the new file lands at the same path.

## 7 — `@pytest.mark.isolated` and CI

Two real-tmux integration tests cannot run under normal `pytest` invocation:

- `scripts/tests/test_followup_pipe_pane_preserved.py` (PM-3 fix verification)
- `scripts/tests/test_lane_exit_detected_within_5s.py` (CV-8)

Reason: real tmux sockets sit under `tmp_path`, which on macOS exceeds the 104-byte `sun_path` limit. Both tests carry the marker

```python
pytestmark = [pytest.mark.isolated]
```

and the project's pytest config declares `isolated` in `pytest.ini`. CI runs them via

```bash
pytest -p forked -m isolated
```

on Linux only, where the socket-path limit is 108 bytes and `tmp_path` fits. Local macOS dev runs the rest of the suite via

```bash
pytest -m "not isolated"
```

(the default for `pytest -m "not isolated"` is documented in `pytest.ini`).

## 8 — Exit codes

The `megalodon_ui` entrypoint emits structured exit codes:

| Code | Meaning |
| ---- | ------- |
| 0 | Clean shutdown |
| 6 | tmux version < 2.6 (server-attach handshake required) |
| 7 | `--mission-dir` invalid (missing or not a directory) |
| 8 | `ui.token` atomic write failed (likely .fleet permissions) |
| 9 | EADDRINUSE on `--port` |
| 10 | tmux socket path > 104 bytes (macOS `sun_path` limit) |
| 11 | Lifespan startup timeout (`MEGALODON_LIFESPAN_TIMEOUT_S`) |
| 12 | Free disk < 50 MB under mission dir |

## 9 — Where to look next

- Auth flow + cookie lifecycle: `v9-2-AUTH.md`.
- Adapter contract + respawn semantics: `v9-2-FOLLOWUP-PROMPTS.md`.
- API surface declarations: `api-contract.md`.
- Implementation log: `HISTORY.md` (search "v9.2").
