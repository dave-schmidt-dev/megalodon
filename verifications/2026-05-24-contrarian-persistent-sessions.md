# Contrarian review — 2026-05-24 persistent-sessions + smart-autoopen

**Reviewer:** GPT-5.5 (codex) xhigh, workspace-write
**Target:** docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md
**Started:** 2026-05-24T15:12:14Z

---

## 1. Obviously Wrong

### OW-1 — The 24h token-rotation story is fake

Spec: [docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md](/Users/dave/Documents/Projects/megalodon/docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md:67) says `.fleet/ui.token` is reused only while its mtime is younger than `COOKIE_MAX_AGE_SECONDS`, and claims this preserves the daily rotation cadence at lines 69-71 and the current "re-run to rotate" security property at lines 25-26.

Context: [/tmp/contrarian-2026-05-24/server_exchange_1382-1411.txt](/tmp/contrarian-2026-05-24/server_exchange_1382-1411.txt:15) shows `/api/v1/auth/exchange` blindly reads `.fleet/ui.token`, compares the supplied token, and mints a session at lines 15-18. There is no mtime check in the exchange path. [/tmp/contrarian-2026-05-24/constants_26-33.txt](/tmp/contrarian-2026-05-24/constants_26-33.txt:3) only defines the 86400-second constant; nothing in the shown exchange path enforces it.

That means a long-running server keeps accepting the original bearer token indefinitely. The "24h rotation" exists only at process launch. This is not an implementation detail; it contradicts the security claim the design itself elevates to a goal.

### OW-2 — The design admits it strands the operator, directly contradicting the goal

Spec: the goal promises "no stranding the operator with no tab" at [docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md](/Users/dave/Documents/Projects/megalodon/docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md:23). The proposed decision function at lines 96-104 is only `last_seen` age. The risks section then admits that a restart within `GRACE` after the only tab has already been closed will skip opening at lines 163-166.

Context: current startup unconditionally prints the URL and calls `_open_dashboard` after composing it in [/tmp/contrarian-2026-05-24/main_py_60-200.txt](/tmp/contrarian-2026-05-24/main_py_60-200.txt:113), with the actual browser open at line 122. The spec replaces that concrete open with a timestamp guess and then documents a false-negative case against its own stated goal.

This is not an "edge" relative to the stated requirement. It is the exact failure mode the feature claims to eliminate.

### OW-3 — `last-seen` is mission-scoped, but the live tab is host/port-scoped

Spec: [docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md](/Users/dave/Documents/Projects/megalodon/docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md:96) makes `should_auto_open()` depend only on `last_seen`, `now`, and `grace`. No host, port, browser origin, or dashboard URL identity is part of the decision.

Context: the server accepts `--port` and `--host` from CLI/env in [/tmp/contrarian-2026-05-24/main_py_60-200.txt](/tmp/contrarian-2026-05-24/main_py_60-200.txt:40) and line 45, and the actual dashboard URL is built from those values at line 114. A tab open on `127.0.0.1:8765` is not a live tab for a relaunch on `127.0.0.1:8766` or `localhost:8765`.

The design will suppress auto-open within `GRACE` even when the old tab is pointed at the wrong origin and cannot reconnect to the new server. That is another direct violation of "only when no live tab exists."

### OW-4 — Automatic token rotation does not revoke valid cookies

Spec: [docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md](/Users/dave/Documents/Projects/megalodon/docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md:67) presents mtime-based token regeneration as preserving a daily security cadence. Only the explicit `--rotate-token` path deletes `sessions.json` at lines 75-76.

Context: [/tmp/contrarian-2026-05-24/server_exchange_1382-1411.txt](/tmp/contrarian-2026-05-24/server_exchange_1382-1411.txt:18) shows a successful token exchange mints a `sid`, and [/tmp/contrarian-2026-05-24/auth_py_23-103.txt](/tmp/contrarian-2026-05-24/auth_py_23-103.txt:68) shows subsequent validation depends only on the cookie value and its session creation time, not on the bearer token that created it.

A session created late in a token's lifetime can remain valid after the token file is regenerated. The design rotates the URL credential while leaving the cookie credential alive. Calling that preserved rotation is wrong.

### OW-5 — `--rotate-token` is underspecified against the current startup order

Spec: [docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md](/Users/dave/Documents/Projects/megalodon/docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md:75) says `--rotate-token` deletes `sessions.json` and `last-seen`. The persistent store wiring at lines 57-58 says `make_app()` constructs `SessionStore(path=...)`, which loads and prunes an existing file at lines 42-45.

Context: [/tmp/contrarian-2026-05-24/main_py_60-200.txt](/tmp/contrarian-2026-05-24/main_py_60-200.txt:93) shows current `main()` imports and calls `make_app()` at lines 93-96 before defining token paths at lines 98-99 and before the token write/open lifecycle at lines 101-122.

If this design is applied to the current structure, the app can load old sessions into memory before `--rotate-token` deletes `sessions.json`. Deleting the file after construction does not revoke the in-memory `SessionStore`. The design claims explicit rotation but does not specify the ordering needed to make that claim true.

### OW-6 — After `GRACE`, the design intentionally duplicates still-open tabs

Spec: [docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md](/Users/dave/Documents/Projects/megalodon/docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md:21) says an already-open dashboard tab survives restart and line 23 says a relaunch opens a new tab only when no live tab exists. The risks section then says a launch after `GRACE` opens a fresh tab after the operator stopped the server and walked away at lines 163-164.

Context: [/tmp/contrarian-2026-05-24/terminal_pane_onerror_66-97.txt](/tmp/contrarian-2026-05-24/terminal_pane_onerror_66-97.txt:22) shows network errors are intentionally silent in the terminal pane. Targeted broadening shows the main board and activity-wall streams do not close the tab either: [ui/static/pages/board.js](/Users/dave/Documents/Projects/megalodon/ui/static/pages/board.js:737), [ui/static/components/activity_wall.js](/Users/dave/Documents/Projects/megalodon/ui/static/components/activity_wall.js:615). External verification: MDN says EventSource connections restart by default when the connection closes (https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events, lines 310-312).

An open tab can still be waiting to reconnect after a server outage longer than 90s. The design treats "no heartbeat during downtime" as "no live tab exists" and deliberately opens a duplicate. That is the tab-spam failure the feature is supposed to stop.

## 2. Probably Wrong

### PW-1 — The heartbeat depends on background timers the browser is allowed to throttle

Spec: [docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md](/Users/dave/Documents/Projects/megalodon/docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md:85) requires a global `setInterval` heartbeat every ~10s and says it runs regardless of `document.visibilityState` at lines 85-90. The 90s grace is justified at lines 99-101 by hand-waving against that 10s interval.

Context: [/tmp/contrarian-2026-05-24/app_js_bootstrap_205-222.txt](/tmp/contrarian-2026-05-24/app_js_bootstrap_205-222.txt:4) shows `app.js` is just a bootstrap hook today; the proposed liveness mechanism will be a browser timer added there, not a server-observed connection property.

External verification: MDN documents inactive-tab timeout throttling and browser-dependent behavior, including Chrome intensive throttling where timers are checked once per minute after hidden/silent conditions (https://developer.mozilla.org/en-US/docs/Web/API/Window/setTimeout, lines 460-487). Chrome's own documentation says `setInterval` is a chained timer and hidden pages can be checked once per minute under intensive throttling (https://developer.chrome.com/blog/timer-throttling-in-chrome-88/, lines 137-183).

The design treats "we scheduled a 10s interval" as "the browser will produce a heartbeat within the grace window." That assumption is false in current browsers. The 10s heartbeat and 90s grace are vibes, not design constraints backed by the platform.

### PW-2 — The security section ignores the token-bearing dashboard URL

Spec: [docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md](/Users/dave/Documents/Projects/megalodon/docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md:128) discusses `ui.token` and `sessions.json` as the protected files, and line 106 says the skipped-open path logs `open manually: <url>`.

Context: [/tmp/contrarian-2026-05-24/main_py_60-200.txt](/tmp/contrarian-2026-05-24/main_py_60-200.txt:114) shows `<url>` includes `#t={token}`. The same URL is printed and logged at lines 115-116. [/tmp/contrarian-2026-05-24/main_py_27-57_bind.txt](/tmp/contrarian-2026-05-24/main_py_27-57_bind.txt:23) shows `dashboard.url` is explicitly mode `0644`, and it is written from the token-bearing URL at lines 27-29 in that slice plus line 117 in `main_py_60-200.txt`.

The design talks as if the bearer secret only lives in `ui.token` with mode `0600`. It also lives in logs/stdout and in `dashboard.url`. Making the token stable across restarts makes those existing exposures materially worse, and the design does not account for them.

### PW-3 — The restart-reconnect "linchpin" test proves almost nothing about a real tab

Spec: [docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md](/Users/dave/Documents/Projects/megalodon/docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md:146) calls the restart-reconnect integration the linchpin and claims a server-side cookie validation plus SSE `200` proves "a real tab self-heals without the paste-token modal" at lines 146-150.

Context: [/tmp/contrarian-2026-05-24/terminal_pane_onerror_66-97.txt](/tmp/contrarian-2026-05-24/terminal_pane_onerror_66-97.txt:3) shows the actual tab behavior is client-side `EventSource.onerror` logic, a probe fetch, and a modal path at lines 14-18. Targeted broadening found other SSE handlers too: [ui/static/pages/board.js](/Users/dave/Documents/Projects/megalodon/ui/static/pages/board.js:737) only marks narrator offline on `EventSource.CLOSED`, [ui/static/components/activity_wall.js](/Users/dave/Documents/Projects/megalodon/ui/static/components/activity_wall.js:615) only logs on closed state, and [ui/static/js/sse.js](/Users/dave/Documents/Projects/megalodon/ui/static/js/sse.js:159) closes and schedules its own reconnect.

The proposed test exercises neither browser `EventSource` state transitions nor the modal branch nor the custom reconnect path. A raw authenticated SSE request returning `200` does not prove a loaded dashboard tab self-heals.

### PW-4 — Raw persistent session IDs expand the secret-at-rest surface

Spec: [docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md](/Users/dave/Documents/Projects/megalodon/docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md:42) says `SessionStore` will persist `{sid: created_at_epoch}` to disk, and line 128 treats `sessions.json` as protected only by `.fleet/` and mode `0600`.

Context: [/tmp/contrarian-2026-05-24/auth_py_23-103.txt](/tmp/contrarian-2026-05-24/auth_py_23-103.txt:63) shows `sid` is a bearer credential generated by `secrets.token_urlsafe`, and validation at lines 68-77 accepts possession of that exact cookie value. [/tmp/contrarian-2026-05-24/server_exchange_1382-1411.txt](/tmp/contrarian-2026-05-24/server_exchange_1382-1411.txt:20) shows it is installed directly as the `mui_session` cookie.

This takes a credential that currently exists only in memory and the browser cookie jar and writes it raw to a project file. "Mode 0600" is not a complete security argument; it is a permission bit.

### PW-5 — The localhost security assumption is not enforced by the entry point

Spec: [docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md](/Users/dave/Documents/Projects/megalodon/docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md:34) dismisses multi-host hardening because this is supposedly a single-operator localhost tool. The security section at lines 126-132 relies on local files and a gated heartbeat.

Context: [/tmp/contrarian-2026-05-24/main_py_60-200.txt](/tmp/contrarian-2026-05-24/main_py_60-200.txt:45) shows `--host` is configurable and defaults from `MEGALODON_HOST`. [/tmp/contrarian-2026-05-24/server_exchange_1382-1411.txt](/tmp/contrarian-2026-05-24/server_exchange_1382-1411.txt:27) sets the cookie with `secure=False`.

The design's threat model says localhost, but the code path allows non-local binding. Persisting bearer material and live cookie credentials while relying on an unenforced deployment assumption is not a security design; it is wishful thinking.

### PW-6 — File mtime is a bad credential birth record

Spec: [docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md](/Users/dave/Documents/Projects/megalodon/docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md:67) makes token freshness depend on `.fleet/ui.token` mtime.

Context: [/tmp/contrarian-2026-05-24/auth_py_23-103.txt](/tmp/contrarian-2026-05-24/auth_py_23-103.txt:36) shows the token file stores only the token string; `read_token()` returns text and no creation metadata. [/tmp/contrarian-2026-05-24/server_exchange_1382-1411.txt](/tmp/contrarian-2026-05-24/server_exchange_1382-1411.txt:15) likewise reads only the token value for exchange.

mtime is mutable filesystem metadata. Copying, restoring, touching, or editor behavior can change the apparent credential age without changing the credential. The design uses a weak proxy as if it were authoritative security state.

### PW-7 — "Atomic write" does not solve multi-process clobbering

Spec: [docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md](/Users/dave/Documents/Projects/megalodon/docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md:42) says `create()` and `revoke()` atomically write the whole `{sid: created_at_epoch}` map to `sessions.json`.

Context: [/tmp/contrarian-2026-05-24/main_py_27-57_bind.txt](/tmp/contrarian-2026-05-24/main_py_27-57_bind.txt:1) shows the process guard is only a host/port bind. [/tmp/contrarian-2026-05-24/main_py_60-200.txt](/tmp/contrarian-2026-05-24/main_py_60-200.txt:40) shows `--port` is configurable, so two processes can serve the same mission on different ports. Both would target the same `.fleet/sessions.json` under the spec's line 58 path.

A temp-file rename makes one write indivisible. It does not prevent two independent processes with stale in-memory maps from overwriting each other's sessions. The design uses "atomic" as if it meant "coherent."

## 3. Worth Reconsidering

### WR-1 — The `webbrowser.open` non-goal is overstated

Spec: [docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md](/Users/dave/Documents/Projects/megalodon/docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md:32) says browsers open a new tab for `webbrowser.open` "regardless."

Context: [/tmp/contrarian-2026-05-24/main_py_60-200.txt](/tmp/contrarian-2026-05-24/main_py_60-200.txt:18) shows the current call uses `webbrowser.open(url, new=2)`.

External verification: Python documents `new=2` as opening a new browser page/tab "if possible," and `open_new_tab()` likewise says "if possible" (https://docs.python.org/3.14/library/webbrowser.html, lines 75-90). The design turns a best-effort API contract into an absolute browser behavior claim.

### WR-2 — Wall-clock expiry imports clock-skew behavior without acknowledging it

Spec: [docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md](/Users/dave/Documents/Projects/megalodon/docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md:46) switches session expiry from `time.monotonic` to `time.time`.

Context: [/tmp/contrarian-2026-05-24/auth_py_23-103.txt](/tmp/contrarian-2026-05-24/auth_py_23-103.txt:59) shows the current store injects `time.monotonic`, and expiry is computed from that clock at lines 74-76.

Cross-restart persistence requires a wall-clock representation, but the design never names the cost: manual clock changes, NTP jumps, VM time shifts, and restored filesystem snapshots can extend or shorten sessions. The doc says semantics are unchanged at line 48; they are not.

### WR-3 — The fixture-write fix is still a process promise, not a design invariant

Spec: [docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md](/Users/dave/Documents/Projects/megalodon/docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md:57) says the `SessionStore` path is `mission_dir / ".fleet" / "sessions.json"` and "always the runtime mission dir" at lines 57-59. The risk section adds a guard test at lines 154-158.

Context: [/tmp/contrarian-2026-05-24/server_sessionstore_wiring_1063-1072.txt](/tmp/contrarian-2026-05-24/server_sessionstore_wiring_1063-1072.txt:7) shows `make_app()` currently just constructs `auth.SessionStore()` from the supplied `mission_dir` context. [/tmp/contrarian-2026-05-24/gitignore_10-30.txt](/tmp/contrarian-2026-05-24/gitignore_10-30.txt:19) shows fixture `.fleet/` directories are explicitly un-ignored.

Nothing in the proposed interface can distinguish a "runtime" mission dir from a fixture mission dir. The self-pass fix relies on tests remembering not to do the dangerous thing while adding a persistence feature whose default path is exactly the dangerous path shape.

### WR-4 — The doc has visible LLM-agent process fingerprints

Spec: [docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md](/Users/dave/Documents/Projects/megalodon/docs/superpowers/specs/2026-05-24-persistent-sessions-smart-autoopen-design.md:5) labels authorship as "orchestrated by Claude Opus 4.7." Lines 168-178 preserve an internal "self-contrarian pass" taxonomy and review diary.

That metadata is not design. It is process exhaust. In a project design doc, it dilutes the engineering argument and advertises agent ceremony instead of requirements, constraints, and decisions.

---

## Verdict
spec-should-be-redone

The persistent-session direction is not the fatal part. The fatal part is that the design sells a timestamp heuristic as "only open when no live tab exists," while documenting cases where it strands the operator and cases where it duplicates a still-open tab. The security story is also materially false: token rotation is launch-only, exchange ignores token age, rotated tokens do not revoke cookies, and `--rotate-token` is not specified against the current app-construction order.
