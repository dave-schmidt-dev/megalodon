# v9.2 — Auth Flow

**Status:** SHIPPED 2026-05-18.
**Companion:** `v9-2-TMUX-FLEET.md` (architecture).

The v9.2 dashboard is bound to `127.0.0.1` by default but its endpoints proxy direct access to live shell processes. So we treat every request to the v9.2-new surface as untrusted until proven otherwise — even from `localhost`. The mechanism is a one-time bootstrap token exchanged for an HttpOnly cookie.

## 1 — Threat model in one paragraph

The server's job is to keep an attacker on the same machine (a curious sibling process, a stale browser tab from another tenant, a `curl` running under a different user) from posting follow-up prompts or reading lane bytes. We assume `<mission>/.fleet/` is mode 0700 and `ui.token` is mode 0600 — so anything that *can* read the bootstrap token already has filesystem-level mission access. The token is the boundary; everything inside it gets cookie-gated.

## 2 — Bootstrap flow

```
operator                browser                  server
   │                       │                       │
   │  open                 │                       │
   │  http://host:port/    │                       │
   │  #t=<token>           │                       │
   │ ────────────────────▶ │                       │
   │                       │  GET /                │
   │                       │ ────────────────────▶ │
   │                       │ ◀──────────  HTML +   │
   │                       │              /static  │
   │                       │                       │
   │                       │  JS reads #t          │
   │                       │  fragment             │
   │                       │                       │
   │                       │  POST /api/v1/auth/   │
   │                       │  exchange             │
   │                       │  {token: "<token>"}   │
   │                       │ ────────────────────▶ │
   │                       │                       │ validate ui.token
   │                       │ ◀──────  Set-Cookie:  │ (constant-time
   │                       │  mui_session=...      │  compare)
   │                       │  HttpOnly; Secure;    │
   │                       │  SameSite=Strict      │
   │                       │                       │
   │                       │  history.replaceState │
   │                       │  to strip #t          │
   │                       │                       │
   │                       │  open SSE channels    │
   │                       │  (cookie auto-sent)   │
   │                       │ ────────────────────▶ │
```

### 2.1 The token

- One-time, opaque, random. Written to `<mission>/.fleet/ui.token` at startup (mode 0600).
- Format: 32 bytes of `secrets.token_urlsafe(32)`.
- Compared in constant time via `hmac.compare_digest`.
- The same token value lives in the bootstrap URL (`#t=<token>`) and in `dashboard.url`.

### 2.2 The cookie

`POST /api/v1/auth/exchange` mints `mui_session=<sid>` with:

- `HttpOnly` — JS in the page cannot read the cookie value, so an XSS bug cannot exfiltrate it.
- `SameSite=Strict` — cross-site `fetch` / link clicks won't carry the cookie.
- `Secure` when the request is HTTPS (production); omitted on plain `http://127.0.0.1` for local dev.

The session id maps to an in-process `SessionStore` keyed by the cookie value. Lifetime: until the server exits or until `DELETE /api/v1/fleet` runs.

### 2.3 Failure paths produce identical 401s

The exchange endpoint returns the SAME 401 body for every failure:

- Token missing
- Token doesn't match
- `ui.token` file missing
- `ui.token` unreadable

This is intentional — an attacker cannot probe the token-file's existence by watching response shapes.

## 3 — Path gating

The middleware `v92_auth_gate` (in `megalodon_ui/server.py`) inspects every request:

```python
_V92_GATED_PATH_RE = re.compile(r"^/api/v1/lane/[^/]+(/|$)")
_V92_GATED_EXACT: frozenset[tuple[str, str]] = frozenset({
    ("DELETE", "/api/v1/fleet"),
})
```

- Anything matching `/api/v1/lane/<NAME>/...` requires a cookie (any method — GET pane-stream, POST followup, GET state).
- The exact pair `(DELETE, /api/v1/fleet)` requires a cookie.
- Everything else (v9.0 / v9.1 routes, `/healthz`, static assets, `POST /auth/exchange` itself) is NOT gated. This preserves backwards compatibility with v9.0 tooling that doesn't know about the cookie.

This is **CR-4 narrow** — the gating is whitelist by design. We could broaden later if a new surface needs auth; we cannot quietly de-auth a surface that was previously cookie-protected.

## 4 — Paste-token recovery modal

The dashboard JS hooks `EventSource.onerror` and the global `fetch` wrapper:

- If `EventSource` opens 401, the JS opens a modal with a single text input ("Paste your token from `<mission>/.fleet/ui.token`").
- If `fetch` returns 401 anywhere, same modal.
- On submit, the JS re-runs `POST /api/v1/auth/exchange` and, on 200, force-reconnects every open SSE channel.

This is the recovery path when:

- The operator manually deleted the cookie (browser dev tools).
- The cookie expired (not currently — the cookie lives as long as the server — but if we add an expiry later, the modal is the unwedge).
- The operator opened the dashboard URL from a different browser without `#t=...` in the fragment.

The token to paste is in `<mission>/.fleet/ui.token`:

```bash
cat <mission>/.fleet/ui.token
```

(or read `<mission>/.fleet/dashboard.url` for the whole URL.)

## 5 — Destructive teardown

`DELETE /api/v1/fleet` (cookie-gated):

1. Kills the per-mission tmux server (`tmux -S <socket> kill-server`).
2. Unlinks `<mission>/.fleet/ui.token`, `tmux.sock`, `dashboard.url` (idempotent — `missing_ok=True`).
3. Returns 200 `{"status": "shutdown"}`.
4. Sets `app.state.shutdown_requested = True` — the uvicorn lifespan tears down shortly after.

After this call, the bootstrap URL is permanently invalid. The next mission needs a fresh `python -m megalodon_ui` invocation.

## 6 — What v9.2 auth deliberately does NOT do

- **No user accounts.** Single-operator missions; the token is the user.
- **No password.** The token IS the credential.
- **No refresh tokens.** Cookie lives until server exit; recover via paste-modal.
- **No CSRF token on the v9.2-new surface.** `SameSite=Strict` + cookie-only auth covers it. The v9.0 surface (`/api/queue/*`) still uses its own CSRF token for back-compat.
- **No multi-tenant isolation.** One mission per server process.

## 7 — Test coverage

| Concern | Test |
| ------- | ---- |
| Token exchange happy path | `ui/tests/integration/test_auth_exchange.py` |
| Token-file missing returns 401 | same file |
| Wrong token returns 401 | same file |
| Cookie unlocks `/api/v1/lane/*` | `test_followup_endpoint.py`, `test_lane_state_endpoint.py`, `test_sse_pane_stream.py` |
| Missing cookie returns 401 | every endpoint test has a no-cookie variant |
| `DELETE /api/v1/fleet` requires cookie | `test_destructive_teardown.py::test_delete_fleet_without_cookie_returns_401` |
