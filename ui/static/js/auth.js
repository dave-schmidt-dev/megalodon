// auth.js — Shared front-end auth gating + re-auth recovery.
//
// Why this module exists (P0 frontend audit, bugs #1 + #2):
//
//   1. FIRST-LOAD AUTH RACE. index.html runs `window.__auth_bootstrap__`, the
//      bearer-token → session-cookie exchange, as a top-level <script> promise.
//      Nothing awaited it, so the SPA's gated requests (narrative, lanes/stale,
//      narrative-stream EventSource, activity-wall, terminal pane-streams,
//      approval-rules, /state, /events) fired BEFORE the cookie existed → 401 →
//      permanently empty board. This module centralizes "do not touch a gated
//      endpoint until the cookie is in place".
//
//   2. NO RE-AUTH PATH. The token lived only in the URL hash and was wiped by
//      history.replaceState; a later 401 (server restart invalidated the
//      cookie) had no recovery. A modal helper existed but was registered ONLY
//      by the dead dashboard-v92.js IIFE — the live board/components hit
//      `undefined`. This module registers a GLOBAL re-auth modal shared by every
//      component and exchanges a freshly-pasted token, then lets callers retry.
//
// Public surface:
//   whenAuthReady()                 → Promise<boolean|null>  (idempotent; safe to await N times)
//   authedFetch(url, opts)          → Promise<Response>      (awaits bootstrap, credentials, 401→modal)
//   showReauthModal()               → void                   (idempotent global modal)
//   probeReauthOn401(url, opts)     → Promise<boolean>       (for SSE onerror: probe, modal on 401)
//
// The modal + helpers are also exposed on `window.__megalodon_auth__` so the
// non-module dashboard-v92.js IIFE (and any inline script) can share the SAME
// modal instance instead of registering its own.

const BOOTSTRAP_KEY = "__auth_bootstrap__";

/**
 * Await the token→cookie exchange the inline <script> in index.html kicked off.
 * Idempotent: the bootstrap is a single stored promise, so awaiting it multiple
 * times (router init + every defensive call site) resolves the same result and
 * never re-runs the exchange. Resolves to `true` on success, `null` when there
 * was no token (already-authed / cookie present), and never rejects (the inline
 * script swallows errors so a failed exchange surfaces later as a 401 we can
 * recover from, not an unhandled rejection that wedges the router).
 *
 * @returns {Promise<boolean|null>}
 */
export async function whenAuthReady() {
  try {
    const p = typeof window !== "undefined" ? window[BOOTSTRAP_KEY] : null;
    if (p && typeof p.then === "function") {
      return await p;
    }
  } catch (_) {
    /* never let auth-gating throw — fall through to "ready" */
  }
  return null;
}

/**
 * Run the token→cookie exchange for a pasted bearer token.
 * @param {string} token
 * @returns {Promise<boolean>} true iff the exchange returned ok
 */
async function exchangeToken(token) {
  const resp = await fetch("/api/v1/auth/exchange", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
    credentials: "same-origin",
  });
  return resp.ok;
}

// --- global re-auth modal ----------------------------------------------------
// Single shared <dialog>. Listeners that want to react to a successful re-auth
// (reconnect SSE, re-run a fetch) subscribe via onReauthSuccess().

/** @type {Set<() => void>} */
const _reauthListeners = new Set();

/**
 * Register a callback fired after a successful re-auth (token accepted, cookie
 * refreshed). Components use this to reconnect their streams / re-hydrate.
 * @param {() => void} fn
 * @returns {() => void} unsubscribe
 */
export function onReauthSuccess(fn) {
  _reauthListeners.add(fn);
  return () => _reauthListeners.delete(fn);
}

function _emitReauthSuccess() {
  for (const fn of [..._reauthListeners]) {
    try { fn(); } catch (err) { console.error("[auth] reauth listener error:", err); }
  }
}

/** @type {HTMLDialogElement|null} */
let _modalEl = null;

function _ensureModal() {
  if (_modalEl && document.body.contains(_modalEl)) return _modalEl;
  const modal = /** @type {HTMLDialogElement} */ (document.createElement("dialog"));
  modal.setAttribute("data-testid", "reauth-modal");
  modal.className = "reauth-modal";
  // Inline styles (no dependency on base.css load order; this can appear during
  // an early auth race before page CSS matters).
  modal.style.cssText = [
    "border: 1px solid var(--border, #2a2f37);",
    "border-radius: 6px;",
    "background: var(--surface, #15181d);",
    "color: var(--text, #e6e6e6);",
    "padding: 18px 20px;",
    "max-width: 420px;",
    "font-family: ui-monospace, SFMono-Regular, Menlo, monospace;",
  ].join(" ");

  const form = document.createElement("form");
  form.style.cssText = "display: flex; flex-direction: column; gap: 8px;";

  const heading = document.createElement("p");
  heading.setAttribute("data-testid", "reauth-heading");
  heading.textContent = "Session expired. Paste a fresh token or reload.";
  heading.style.cssText = "margin: 0 0 4px; font-weight: 600;";

  const hint = document.createElement("p");
  hint.style.cssText = "font-size: 11px; opacity: 0.7; margin: 0 0 6px;";
  hint.textContent = "Recover via `cat <mission>/.fleet/dashboard.url` or server stdout.";

  const input = document.createElement("input");
  input.type = "text";
  input.placeholder = "bearer token";
  input.autocomplete = "off";
  input.setAttribute("data-testid", "reauth-token-input");
  input.style.cssText = [
    "background: var(--bg, #0e0f12);",
    "color: var(--text, #e6e6e6);",
    "border: 1px solid var(--border, #2a2f37);",
    "border-radius: 4px;",
    "padding: 7px 10px;",
    "font-size: 13px;",
    "font-family: inherit;",
  ].join(" ");

  const row = document.createElement("div");
  row.style.cssText = "display: flex; gap: 8px;";

  const submit = document.createElement("button");
  submit.type = "submit";
  submit.textContent = "Re-authenticate";
  submit.setAttribute("data-testid", "reauth-submit");
  submit.className = "button button--primary";

  const reloadBtn = document.createElement("button");
  reloadBtn.type = "button";
  reloadBtn.textContent = "Reload";
  reloadBtn.setAttribute("data-testid", "reauth-reload");
  reloadBtn.className = "button";
  reloadBtn.addEventListener("click", () => {
    try { location.reload(); } catch (_) { /* ignore */ }
  });

  const err = document.createElement("p");
  err.setAttribute("data-testid", "reauth-error");
  err.style.cssText = "color: #ff8b8b; margin: 4px 0 0; font-size: 12px;";
  err.hidden = true;

  row.appendChild(submit);
  row.appendChild(reloadBtn);
  form.appendChild(heading);
  form.appendChild(hint);
  form.appendChild(input);
  form.appendChild(row);
  form.appendChild(err);
  modal.appendChild(form);

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const token = (input.value || "").trim();
    if (!token) return;
    err.hidden = true;
    err.textContent = "";
    submit.disabled = true;
    try {
      const ok = await exchangeToken(token);
      if (ok) {
        try { modal.close(); } catch (_) { /* ignore */ }
        input.value = "";
        _emitReauthSuccess();
      } else {
        err.textContent = "Token rejected.";
        err.hidden = false;
      }
    } catch (e) {
      err.textContent = `Network error: ${e && e.message ? e.message : e}`;
      err.hidden = false;
    } finally {
      submit.disabled = false;
    }
  });

  document.body.appendChild(modal);
  _modalEl = modal;
  return modal;
}

/**
 * Show the shared re-auth modal. Idempotent — calling it while already open is a
 * no-op, so a burst of 401s (board + activity wall + terminal all at once)
 * surfaces exactly one modal.
 */
export function showReauthModal() {
  const modal = _ensureModal();
  if (modal.open) return;
  if (typeof modal.showModal === "function") {
    try { modal.showModal(); return; } catch (_) { /* fall through */ }
  }
  modal.setAttribute("open", "");
}

/**
 * Credentials-bearing fetch that (a) waits for the first-load auth exchange
 * before issuing the request and (b) surfaces the re-auth modal on a 401 so a
 * stale/invalidated cookie doesn't silently brick the caller. The 401 Response
 * is still returned so callers can render their own error/retry state.
 *
 * @param {string} url
 * @param {RequestInit} [opts]
 * @returns {Promise<Response>}
 */
export async function authedFetch(url, opts = {}) {
  await whenAuthReady();
  const resp = await fetch(url, { credentials: "same-origin", ...opts });
  if (resp.status === 401) {
    showReauthModal();
  }
  return resp;
}

/**
 * For SSE onerror handlers: an EventSource gives no status code, so probe the
 * same URL with a plain GET to learn whether the disruption is a 401 (session
 * expired → show the re-auth modal) vs a transient outage (caller should just
 * back off and reconnect).
 *
 * @param {string} url
 * @param {RequestInit} [opts]
 * @returns {Promise<boolean>} true iff the probe came back 401 (modal shown)
 */
export async function probeReauthOn401(url, opts = {}) {
  try {
    const probe = await fetch(url, {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "text/event-stream" },
      ...opts,
    });
    // Free the streaming body so the probe connection is released.
    try { probe.body && probe.body.cancel(); } catch (_) { /* ignore */ }
    if (probe.status === 401) {
      showReauthModal();
      return true;
    }
  } catch (_) {
    // network error — caller treats as transient outage
  }
  return false;
}

// --- global handle for the non-module dashboard-v92.js IIFE ------------------
// Share ONE modal across module + non-module consumers. We also alias the
// legacy name the terminal pane historically probed for, so any not-yet-migrated
// caller still reaches the live modal instead of `undefined`.
if (typeof window !== "undefined") {
  window.__megalodon_auth__ = {
    whenAuthReady,
    authedFetch,
    showReauthModal,
    probeReauthOn401,
    onReauthSuccess,
  };
  // Back-compat: terminal_pane.js + dashboard-v92.js used this name.
  if (typeof window.__v92_showPasteTokenModal !== "function") {
    window.__v92_showPasteTokenModal = showReauthModal;
  }
}
