// @ts-check
// dashboard-v92.js — v9.2 xterm.js dashboard (one Terminal per lane).
//
// Spec: ~/Documents/Projects/.plans/megalodon/v9-2-tmux-headless-fleet-2026-05-17.md §6.5
// Tests: ui/tests/e2e/dashboard-loads.spec.ts, auth-redirect.spec.ts (P5.3)
//
// Loaded by index.html (xterm.js + addon-fit.js are also loaded there; this
// IIFE is a NO-OP unless /api/v1/config returns `v92_dashboard: true`).
// This keeps v9.0 / v9.1 dashboards untouched while letting a v9.2-mode server
// take over the `/` page.
//
// v9.4 refactor: xterm + pane-stream SSE logic has been lifted into
// components/terminal_pane.js. This file uses createTerminalPane() instead
// of carrying the decode + write loop inline.
//
// Prerequisite: window.createTerminalPane must be defined.
// index.html loads components/terminal_pane.js as a module — this script
// waits for DOMContentLoaded so the module is ready.

(async () => {
  // Wait for the inline auth bootstrap in index.html so any `#t=<token>` URL
  // has been exchanged for a cookie before we issue gated requests.
  try {
    await (window.__auth_bootstrap__ || Promise.resolve());
  } catch (e) {
    // bootstrap failures are non-fatal here; downstream fetches will surface 401.
  }

  let config;
  try {
    const resp = await authFetch('/api/v1/config');
    if (!resp || !resp.ok) {
      if (resp && resp.status === 401) showPasteTokenModal();
      return;
    }
    config = await resp.json();
  } catch (e) {
    console.error('dashboard-v92: /api/v1/config fetch failed', e);
    return;
  }

  if (!config || !config.v92_dashboard) return;

  document.body.classList.add('v92-mode');
  injectV92Styles(config.lanes.length);
  const grid = ensureGridRoot();
  ensurePasteTokenModal();

  for (const lane of config.lanes) {
    mountLane(grid, lane);
  }

  // Test-only hook: close every open SSE connection. Some Playwright specs
  // need to free up Chrome's 6-connection-per-host HTTP/1.1 budget so other
  // fetches don't queue behind streaming pane-streams. No-op in production
  // (nobody calls it).
  window.__v92_closeAllStreams = () => {
    for (const [, entry] of _laneEntries) {
      try { entry.cleanup && entry.cleanup(); } catch {}
      entry.cleanup = null;
    }
  };

  // Expose the paste-token modal show function globally so terminal_pane.js can
  // surface it on 401 without importing the whole dashboard.
  window.__v92_showPasteTokenModal = showPasteTokenModal;
})();

// --- helpers ---------------------------------------------------------------

// Maps lane.name → { lane, termHost, cleanup, statusEl, sendBtn, sawFirstByte,
//                     _sendTimeoutId }
// `cleanup` is what createTerminalPane() returned; null after __v92_closeAllStreams.
const _laneEntries = new Map();

function injectV92Styles(laneCount) {
  if (document.getElementById('v92-dashboard-style')) return;
  const cols = Math.max(1, Math.ceil(Math.sqrt(laneCount || 1)));
  const style = document.createElement('style');
  style.id = 'v92-dashboard-style';
  style.textContent = `
    body.v92-mode .app-header,
    body.v92-mode .app-nav,
    body.v92-mode #app-root,
    body.v92-mode #toast-region { display: none !important; }
    body.v92-mode { margin: 0; padding: 0; background: #0b0d10; color: #e6e6e6; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    .v92-lane-grid {
      display: grid;
      grid-template-columns: repeat(${cols}, 1fr);
      gap: 6px;
      padding: 6px;
      min-height: 100vh;
      box-sizing: border-box;
    }
    .v92-lane-pane {
      background: #15181d;
      border: 1px solid #2a2f37;
      display: flex;
      flex-direction: column;
      min-height: 200px;
      overflow: hidden;
    }
    .v92-lane-header {
      padding: 4px 8px;
      font-size: 12px;
      background: #1f242c;
      border-bottom: 1px solid #2a2f37;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
    }
    .v92-lane-term { flex: 1; min-height: 0; }
    .v92-paste-token-modal {
      border: 1px solid #2a2f37;
      background: #15181d;
      color: #e6e6e6;
      padding: 16px 20px;
      max-width: 460px;
    }
    .v92-paste-token-modal::backdrop { background: rgba(0,0,0,0.6); }
    .v92-paste-token-form { display: flex; flex-direction: column; gap: 8px; }
    .v92-paste-token-form input[type=text] {
      background: #0b0d10; color: #e6e6e6; border: 1px solid #2a2f37;
      padding: 6px 8px; font-family: inherit; font-size: 13px;
    }
    .v92-paste-token-form button {
      background: #2a2f37; color: #e6e6e6; border: 1px solid #3a414b;
      padding: 6px 12px; cursor: pointer;
    }
    .v92-paste-token-form button:hover { background: #353c46; }
    .v92-paste-token-error { color: #ff8b8b; margin: 4px 0 0; font-size: 12px; }
    .v92-paste-token-hint { font-size: 11px; opacity: 0.7; margin: -4px 0 4px; }
    .v92-followup { display: flex; gap: 6px; padding: 6px; border-top: 1px solid #2a2f37; }
    .v92-followup textarea {
      flex: 1; resize: vertical; min-height: 36px;
      background: #0b0d10; color: #e6e6e6; border: 1px solid #2a2f37; padding: 4px 6px;
      font-family: inherit; font-size: 12px;
    }
    .v92-followup button {
      background: #2a2f37; color: #e6e6e6; border: 1px solid #3a414b;
      padding: 4px 12px; cursor: pointer;
    }
    .v92-followup button:disabled { opacity: 0.5; cursor: not-allowed; }
  `;
  document.head.appendChild(style);
}

function ensureGridRoot() {
  let grid = document.querySelector('[data-testid="lane-grid"]');
  if (grid) return grid;
  grid = document.createElement('div');
  grid.setAttribute('data-testid', 'lane-grid');
  grid.className = 'v92-lane-grid';
  document.body.appendChild(grid);
  return grid;
}

function mountLane(grid, lane) {
  const pane = document.createElement('div');
  pane.setAttribute('data-testid', `lane-pane-${lane.name}`);
  pane.setAttribute('data-lane', lane.name);
  pane.className = 'v92-lane-pane';

  const header = document.createElement('header');
  header.className = 'v92-lane-header';
  header.setAttribute('data-testid', `lane-header-${lane.name}`);
  const label = document.createElement('span');
  label.textContent = `${lane.name} (${lane.short || ''})`;
  const status = document.createElement('span');
  status.className = 'v92-lane-status';
  status.setAttribute('data-testid', `lane-status-${lane.name}`);
  status.textContent = 'running';
  header.appendChild(label);
  header.appendChild(status);

  // Terminal host: wraps the component element, carries the data-testid used
  // by Playwright specs to find the pane's content.
  const termHost = document.createElement('div');
  termHost.className = 'v92-lane-term';
  termHost.setAttribute('data-testid', `lane-term-${lane.name}`);

  const form = document.createElement('form');
  form.className = 'v92-followup';
  form.setAttribute('data-testid', `lane-followup-${lane.name}`);
  const ta = document.createElement('textarea');
  ta.placeholder = `Follow-up prompt for ${lane.name}…`;
  ta.setAttribute('data-testid', `followup-input-${lane.name}`);
  const sendBtn = document.createElement('button');
  sendBtn.type = 'submit';
  sendBtn.textContent = 'Send';
  sendBtn.setAttribute('data-testid', `followup-send-${lane.name}`);
  form.appendChild(ta);
  form.appendChild(sendBtn);

  pane.appendChild(header);
  pane.appendChild(termHost);
  pane.appendChild(form);
  grid.appendChild(pane);

  // Mount the terminal component.  createTerminalPane() lives in
  // components/terminal_pane.js (loaded as a module — window.createTerminalPane
  // is set by that module for use here).
  const termComponent = window.createTerminalPane({
    lane: lane.short || lane.name,
    scrollback: 5000,
  });
  termHost.appendChild(termComponent.element);

  const entry = {
    lane,
    termHost,
    cleanup: termComponent.cleanup,
    statusEl: status,
    sendBtn,
  };
  _laneEntries.set(lane.name, entry);
  startLaneStatePoll(entry);

  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const prompt = (ta.value || '').trim();
    if (!prompt) return;
    sendBtn.disabled = true;
    // Release the send button after 3 s (timeout-only debounce; the v9.2
    // sentinel early-release is handled by the component internals).
    entry._sendTimeoutId = window.setTimeout(() => {
      sendBtn.disabled = false;
      entry._sendTimeoutId = null;
    }, 3_000);
    try {
      const r = await authFetch(`/api/v1/lane/${encodeURIComponent(lane.short || lane.name)}/followup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt }),
      });
      if (r && r.ok) {
        ta.value = '';
      }
    } catch (e) {
      // ignore — debounce still releases on timeout
    }
  });
}

function startLaneStatePoll(entry) {
  const { lane } = entry;
  const key = lane.short || lane.name;
  const tick = async () => {
    try {
      const r = await authFetch(`/api/v1/lane/${encodeURIComponent(key)}/state`, { cache: 'no-store' });
      if (r && r.ok) {
        const body = await r.json();
        if (entry.statusEl) {
          if (body.running === false && body.exited_rc !== null) {
            entry.statusEl.textContent = `exited (rc=${body.exited_rc})`;
            entry.statusEl.setAttribute('data-running', 'false');
          } else {
            entry.statusEl.textContent = 'running';
            entry.statusEl.setAttribute('data-running', 'true');
          }
        }
      }
    } catch {}
    window.setTimeout(tick, 2_000);
  };
  tick();
}

function ensurePasteTokenModal() {
  if (document.querySelector('[data-testid="paste-token-modal"]')) return;
  const modal = document.createElement('dialog');
  modal.setAttribute('data-testid', 'paste-token-modal');
  modal.className = 'v92-paste-token-modal';

  const form = document.createElement('form');
  form.className = 'v92-paste-token-form';
  const heading = document.createElement('p');
  heading.textContent = 'Session expired or invalid. Paste a fresh bearer token:';
  const hint = document.createElement('p');
  hint.className = 'v92-paste-token-hint';
  hint.textContent = 'Recover via `cat <mission>/.fleet/dashboard.url` or server stdout.';
  const input = document.createElement('input');
  input.type = 'text';
  input.placeholder = 'bearer token';
  input.required = true;
  input.autocomplete = 'off';
  input.setAttribute('data-testid', 'paste-token-input');
  const submit = document.createElement('button');
  submit.type = 'submit';
  submit.textContent = 'Submit';
  submit.setAttribute('data-testid', 'paste-token-submit');
  const err = document.createElement('p');
  err.className = 'v92-paste-token-error';
  err.setAttribute('data-testid', 'paste-token-error');
  err.hidden = true;

  form.appendChild(heading);
  form.appendChild(hint);
  form.appendChild(input);
  form.appendChild(submit);
  form.appendChild(err);
  modal.appendChild(form);

  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const token = (input.value || '').trim();
    if (!token) return;
    err.hidden = true;
    err.textContent = '';
    try {
      const r = await fetch('/api/v1/auth/exchange', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token }),
        credentials: 'same-origin',
      });
      if (r.ok) {
        modal.close();
        reconnectAllPaneStreams();
      } else {
        err.textContent = `Token rejected (HTTP ${r.status}).`;
        err.hidden = false;
      }
    } catch (e) {
      err.textContent = `Network error: ${e && e.message ? e.message : e}`;
      err.hidden = false;
    }
  });

  // Non-modal <dialog> doesn't get native Escape-to-close; wire it explicitly
  // so the recovery prompt is dismissible and never traps the operator.
  modal.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') { ev.preventDefault(); try { modal.close(); } catch {} }
  });

  document.body.appendChild(modal);
}

function showPasteTokenModal() {
  ensurePasteTokenModal();
  const modal = document.querySelector('[data-testid="paste-token-modal"]');
  // Use NON-modal show() (not showModal()): the v92 dashboard script's IIFE
  // loads on every page via index.html and probes /api/v1/config, which now
  // 401s under the deny-by-default auth gate. A modal <dialog> renders a
  // top-layer ::backdrop that makes the whole SPA non-interactive — so on a
  // generic page that brick navigation. show() surfaces the recovery prompt
  // without stealing pointer events from the rest of the UI (mirrors the
  // global reauth modal in auth.js). Escape dismisses it.
  if (modal && typeof modal.show === 'function' && !modal.open) {
    try { modal.show(); } catch {}
  } else if (modal) {
    modal.setAttribute('open', '');
  }
}

function reconnectAllPaneStreams() {
  for (const [, entry] of _laneEntries) {
    // Teardown old component.
    try { entry.cleanup && entry.cleanup(); } catch {}
    // Clear the terminal host and mount a fresh component.
    while (entry.termHost.firstChild) entry.termHost.removeChild(entry.termHost.firstChild);
    const newComponent = window.createTerminalPane({
      lane: entry.lane.short || entry.lane.name,
      scrollback: 5000,
    });
    entry.termHost.appendChild(newComponent.element);
    entry.cleanup = newComponent.cleanup;
  }
}

async function authFetch(url, init) {
  const opts = Object.assign({ credentials: 'same-origin' }, init || {});
  const resp = await fetch(url, opts);
  if (resp.status === 401) showPasteTokenModal();
  return resp;
}
