# Design — Persistent Sessions + Observed Dashboard Auto-Open

**Project:** megalodon
**Date:** 2026-05-24
**Status:** design (revised after external contrarian review — see Review history)

## Problem

Every `python -m megalodon_ui` launch unconditionally opens the dashboard in a new
browser tab (`__main__.py:181` → `webbrowser.open(url, new=2)`). Across a dev
session of restarts this accumulates dead tabs. A naive "don't reopen" is unsafe:
sessions are in-memory (`auth.SessionStore`, `auth.py:73-103`) and the bearer token
regenerates each launch, so after a restart an already-open tab is *stale* — its
`mui_session` cookie no longer validates and the bearer was wiped from its URL
(`auth.py:7`). Skipping the reopen would then leave the operator with a stale tab
**and** no fresh one.

## Goal

1. An already-open dashboard tab **survives a server restart**: its cookie keeps
   validating and its `EventSource`s auto-reconnect with no manual re-auth.
2. A relaunch **does not pile up duplicate tabs** when a live tab is already
   connected, and **does open** a fresh tab when none reconnects.
3. Token rotation stays possible and is **explicit and correct** (actually revokes).

These are best-effort UX goals for a single-operator localhost tool, not security
guarantees. Limitations are stated explicitly under Limitations.

## Non-goals

- Stateless/signed-cookie auth refactor (keep `revoke()` + opaque session-id model).
- Reusing the *same* browser tab via OS focus (`webbrowser.open` is best-effort and
  typically opens a new tab; not pursued).
- Multi-host hardening or supporting two servers per mission concurrently.

## Design

### 1. Persistent session store, hashed at rest (`auth.py`)

- `SessionStore` gains optional `path: Path | None`. When set, `create()`/`revoke()`
  atomically (temp-file + `rename`, mode `0600`) write `{sha256(sid): created_epoch}`
  to it. **The raw session id is never written** — only its SHA-256 digest, so the
  on-disk file is not itself a usable credential (PW-4). `validate(cookie)` hashes
  the presented cookie and looks up the digest.
- On construction with a path, load + prune entries older than
  `COOKIE_MAX_AGE_SECONDS` (86400). `validate()` keeps expiry-eviction **and persists
  the eviction** so expired digests don't linger (PW-1-self).
- Expiry clock switches `time.monotonic` → wall-clock `time.time` (monotonic is
  process-local and resets each launch). **Acknowledged tradeoff (WR-2):** wall-clock
  means NTP jumps / manual clock changes / restored snapshots can shorten or extend a
  session. Acceptable for a 24h dev-session credential.
- Tolerant load: missing/corrupt file → empty store + one WARNING; never fatal.
- **Persistence is live-mode-only (invariant, WR-3):** `make_app` passes
  `path=mission_dir/.fleet/sessions.json` **only** in the live lifespan branch. The
  `MEGALODON_LIFESPAN_TEST_MODE` and `MEGALODON_FAKE_SPAWNER` branches construct
  `SessionStore(path=None)` (pure in-memory, today's behavior). Tests therefore never
  write session state, so the git-tracked fixture `.fleet/` dirs (`.gitignore:28-29`)
  cannot be polluted. A guard test asserts no `sessions.json` appears under
  `scripts/tests/fixtures/`.

Effect: after a restart, an old tab's `mui_session` cookie still validates → its
gated `EventSource`s auto-reconnect with no user action.

### 2. Stable bearer token; explicit rotation only (`__main__.py`)

- Reuse `.fleet/ui.token` if present and non-empty; otherwise generate + write. No
  mtime/age logic — the earlier "24h auto-rotation" was illusory (the exchange path
  has no age check, `server.py:1382-1410`), so it is dropped (OW-1, PW-6). The URL is
  thus stable across restarts.
- Stop unlinking the token on normal exit. Error-path cleanup unlinks **only if this
  run generated it** (track `token_was_generated`); never deletes a reused token.
- `--rotate-token` flag: rotation that actually revokes. **Ordering (OW-5):** in
  `main()`, when the flag is set, delete `.fleet/ui.token` and `sessions.json`
  **before** `make_app()` runs (so the new `SessionStore` loads nothing), then a
  fresh token is generated. (The revised observed-auto-open design uses no marker
  file, so there is nothing else to clear.) Clearing `sessions.json` invalidates all
  prior cookies → rotation revokes existing sessions (OW-4). Documented in README as
  the replacement for the old per-launch rotation.

### 3. Observed auto-open (`server.py` lifespan + `__main__.py`)

The open-decision **observes reality** instead of reading a timestamp. A timestamp
heuristic cannot distinguish "tab open" from "tab closed" from "tab reconnecting
after a long outage" (the OW-2 / OW-6 contradictions), and client `setInterval`
heartbeats are throttled on hidden tabs (PW-1). Observing live reconnection avoids
all three.

- A lifespan background task `_auto_open_watch` (live branch only), started after
  fleet startup: poll the **authenticated SSE subscriber count** (union of
  `NarrativeHub.subscriber_count` + activity-wall + pane-stream subscribers — all
  gated endpoints, so any subscriber is authenticated) every 0.5s for up to
  `OPEN_GRACE_S` (default 8s, env `MEGALODON_DASHBOARD_OPEN_GRACE_S`).
  - If a subscriber appears within the window → a live tab reconnected to **this**
    server (host/port-correct by construction, OW-3) → **do not open**.
  - If the window elapses with zero subscribers → no tab → **open** the dashboard.
- `--no-browser` forces off; `--rotate-token` forces open. `app.state` carries the
  composed URL + the enabled flag set by `__main__`; the watch task performs the
  `webbrowser.open` so it can observe post-startup state.
- The 8s window only delays the open on a genuinely fresh launch (URL is printed to
  stdout immediately regardless). It is not a security timer; no precise tuning
  needed — it must merely exceed typical EventSource reconnect latency (~3s default).

### 4. Security hardening of existing exposures (PW-2)

- Stabilizing the token worsens existing token-in-the-clear exposures, so: write
  `.fleet/dashboard.url` mode `0600` (currently `0644`, `__main__.py:27-57` slice),
  and log a **redacted** dashboard URL at INFO (`…/#t=<redacted>`); the full
  token-bearing URL still prints to stdout for the operator to copy.
- If `--host` resolves to a non-loopback address, log a WARNING that persisted
  credentials + non-local bind is unsupported (PW-5). Binding policy itself is
  unchanged (pre-existing).

## Units & interfaces

- `SessionStore(path=None)` — file-backed (hashed) session map; tolerant load; atomic
  persist; injected `now` for tests.
- Token lifecycle in `main()` — reuse-vs-generate + `--rotate-token` clear-before-build.
- `_auto_open_watch(app_state, grace)` — observe-then-open task; pure decision core
  (`subscriber_count`, `elapsed`, `grace`) unit-testable.

## Error handling

- Browser-open failure stays non-fatal (`__main__.py:78-84`).
- Corrupt/missing `sessions.json` → empty store + WARNING; never blocks startup.
- The auto-open watch is best-effort: any exception logs and is swallowed (never
  crashes the lifespan).

## Testing

- `auth.py`: hashed-persistence round-trip (create → reload in a new store → validate
  the *raw* cookie succeeds; file contains only digests); wall-clock expiry; prune on
  load; validate-eviction rewrites file; revoke removes; `0600`; corrupt/missing
  tolerant; `path=None` writes nothing.
- `__main__`: token reused when present, generated when absent, never unlinked on
  normal exit, error-unlink only when generated; `--rotate-token` clears
  token+sessions+marker before make_app.
- Decision core: zero-subscriber-through-window → open; subscriber-appears → skip;
  grace env parse/clamp.
- Live-mode invariant: test/fake branches construct `path=None`; guard test asserts
  nothing is written under `scripts/tests/fixtures/`.
- **Restart-reconnect (Playwright, the real linchpin, PW-3):** load the dashboard tab
  (authenticated), restart the server process against the same `.fleet`, and assert
  the tab's SSE reconnects and renders without surfacing the paste-token modal
  (`__v92_showPasteTokenModal`) — exercising the actual client `EventSource.onerror`
  / reconnect paths (`terminal_pane.js:66-97`, `board.js`, `sse.js`), not a bare
  server-side cookie check.

## Limitations (explicit)

- **Parked-page observation gap:** observation sees a tab only while it holds ≥1
  gated SSE. The default board page always holds `narrative-stream`; a tab parked on
  a page with no gated stream (e.g. `/tasks`) won't be observed and may get a
  duplicate tab. Acceptable — the common left-open case is the board.
- **Multi-process (PW-7):** two servers on the same mission (different ports) share
  one `sessions.json` and can clobber each other's writes. Unsupported;
  `_bind_listener` already blocks same-port double-launch (`__main__.py:27-57`).
- **Wall-clock skew (WR-2):** see §1.
- `webbrowser.open` is best-effort (WR-1); we do not assume a specific tab outcome.

## Review history

- External contrarian (GPT-5.5, xhigh): `verifications/2026-05-24-contrarian-persistent-sessions.md` — verdict `spec-should-be-redone`. This revision drops the timestamp/heartbeat open-heuristic for observed reconnection, drops the illusory token auto-rotation for explicit `--rotate-token`, hashes sessions at rest, makes persistence live-mode-only, and hardens token-URL exposure. Findings PW-5/PW-7 accepted as documented limitations.
