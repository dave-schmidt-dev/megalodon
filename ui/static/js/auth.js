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
  // R1 (regression fix): present as a NON-MODAL, pinned banner — never a
  // top-layer showModal() dialog. A modal <dialog> renders a ::backdrop in the
  // top layer that swallows ALL pointer events across the SPA, so a mid-session
  // 401 (transient blip / tightened auth gate) used to freeze navigation and
  // clicks board-wide. As a non-modal dialog the rest of the UI stays fully
  // interactive while the re-auth prompt is visible; the operator can keep
  // navigating and dismiss it via Escape or the close button.
  //
  // Inline styles (no dependency on base.css load order; this can appear during
  // an early auth race before page CSS matters). Pinned top-center so it does
  // not blanket the viewport and does not collide with the top-right alert
  // stack / activity panel.
  modal.style.cssText = [
    "position: fixed;",
    "top: 12px;",
    "left: 50%;",
    "transform: translateX(-50%);",
    "margin: 0;",
    "z-index: 2147483646;",
    "border: 1px solid var(--border, #2a2f37);",
    "border-radius: 6px;",
    "background: var(--surface, #15181d);",
    "color: var(--text, #e6e6e6);",
    "padding: 18px 20px;",
    "max-width: 420px;",
    "box-shadow: 0 8px 32px rgba(0,0,0,0.5);",
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

  // R1: an explicit dismiss affordance. A transient/false 401 must be
  // dismissible so it can't pin a stale prompt over a working board.
  const dismissBtn = document.createElement("button");
  dismissBtn.type = "button";
  dismissBtn.textContent = "Dismiss";
  dismissBtn.setAttribute("data-testid", "reauth-dismiss");
  dismissBtn.setAttribute("aria-label", "Dismiss re-auth prompt");
  dismissBtn.className = "button";
  dismissBtn.addEventListener("click", () => {
    try { modal.close(); } catch (_) { modal.removeAttribute("open"); }
  });

  const err = document.createElement("p");
  err.setAttribute("data-testid", "reauth-error");
  err.style.cssText = "color: #ff8b8b; margin: 4px 0 0; font-size: 12px;";
  err.hidden = true;

  row.appendChild(submit);
  row.appendChild(reloadBtn);
  row.appendChild(dismissBtn);
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

  // Escape-to-dismiss. A non-modal dialog (dialog.show()) does NOT get the
  // native Escape→cancel the top-layer modal form does, so wire it explicitly
  // while the prompt is open. Scoped to document so focus need not be inside it.
  function _onEscape(ev) {
    if (ev.key === "Escape" && modal.open) {
      ev.stopPropagation();
      try { modal.close(); } catch (_) { modal.removeAttribute("open"); }
    }
  }
  document.addEventListener("keydown", _onEscape);

  document.body.appendChild(modal);
  _modalEl = modal;
  return modal;
}

// R1: coalesce 401 bursts. A single stale cookie typically trips several gated
// calls at once (board + activity wall + signals + SSE probe). Without a guard
// each would re-invoke showReauthModal(); even though it's idempotent while
// open, debouncing avoids churn and keeps the affordance from re-opening the
// instant the operator dismisses it during a burst.
let _lastShownAt = 0;
const _SHOW_DEBOUNCE_MS = 1500;

/**
 * Show the shared re-auth modal. Idempotent — calling it while already open is a
 * no-op, so a burst of 401s (board + activity wall + terminal all at once)
 * surfaces exactly one modal.
 */
export function showReauthModal() {
  const modal = _ensureModal();
  if (modal.open) return;
  // Coalesce 401 bursts: ignore a re-open within the debounce window so a
  // single stale cookie tripping N gated calls doesn't thrash the prompt (and
  // can't pop back the instant the operator dismisses it mid-burst).
  const now = Date.now();
  if (now - _lastShownAt < _SHOW_DEBOUNCE_MS) return;
  _lastShownAt = now;
  // NON-MODAL on purpose (R1): show() keeps the prompt out of the top layer so
  // it has no ::backdrop and never intercepts pointer events for the rest of
  // the SPA — the board/nav stay usable while a re-auth prompt is up. We
  // deliberately do NOT call showModal().
  if (typeof modal.show === "function") {
    try { modal.show(); return; } catch (_) { /* fall through */ }
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
