// @ts-check
// components/activity_wall.js — v9.4 Task 2.4: Activity Wall component.
//
// Renders a live feed of activity-wall events in the grid page's right column.
// Hydrates from GET /api/v1/activity-wall/snapshot on mount, then subscribes
// to SSE at GET /api/v1/activity-wall for live updates.
//
// Event shape (from T2.3 impl):
//   { type, lane, ts, summary, payload }
//   type: "finding" | "signal" | "history" | "queue" | "inject" | "restart-loop" | "governor"
//   lane: "A" | null
//   ts: ISO-8601Z
//   summary: string
//   payload: object
//
// Public export:
//   createActivityWall({ container }) → { element, cleanup }
//
// Constraints:
//   - No render framework. Plain DOM via el() helper.
//   - Do NOT poll snapshot after initial load.
//   - Cap DOM at 500 rows (drop oldest when exceeded).
//   - Filter chips: hide/show via display:none, no re-render.
//   - Pause: per-component state (not global store).

import { authedFetch, probeReauthOn401, onReauthSuccess } from '../js/auth.js';

// ---------------------------------------------------------------------------
// Minimal DOM helper (mirrors grid.js pattern)
// ---------------------------------------------------------------------------

/**
 * @param {string} tag
 * @param {Record<string, any>|null} attrs
 * @param {...(Node|string|null|false)} children
 * @returns {HTMLElement}
 */
function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === 'class') node.className = v;
      else if (k === 'style') node.setAttribute('style', v);
      else if (k === 'dataset') {
        for (const [dk, dv] of Object.entries(v)) node.dataset[dk] = String(dv);
      } else if (k.startsWith('on') && typeof v === 'function') {
        node.addEventListener(k.slice(2).toLowerCase(), v);
      } else if (v === true) {
        node.setAttribute(k, '');
      } else {
        node.setAttribute(k, String(v));
      }
    }
  }
  for (const c of children) {
    if (c == null || c === false) continue;
    node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return node;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MAX_DOM_ROWS = 500;

/** Filter chip labels → event type values (null = "All") */
const CHIP_DEFS = [
  { label: 'All',       type: null },
  { label: 'Findings',  type: 'finding' },
  { label: 'Signals',   type: 'signal' },
  { label: 'History',   type: 'history' },
  { label: 'Queue',     type: 'queue' },
  { label: 'Inject',    type: 'inject' },
  { label: 'Governor',  type: 'governor' },
];

// ---------------------------------------------------------------------------
// Time formatter
// ---------------------------------------------------------------------------

/**
 * Format an ISO-8601 timestamp as hh:mm:ss UTC.
 * @param {string} ts
 * @returns {string}
 */
function formatTime(ts) {
  try {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return ts;
    const hh = String(d.getUTCHours()).padStart(2, '0');
    const mm = String(d.getUTCMinutes()).padStart(2, '0');
    const ss = String(d.getUTCSeconds()).padStart(2, '0');
    return `${hh}:${mm}:${ss} UTC`;
  } catch (_) {
    return ts;
  }
}

// ---------------------------------------------------------------------------
// Row factory
// ---------------------------------------------------------------------------

/**
 * Build a single event row element.
 *
 * @param {{ type: string, lane: string|null, ts: string, summary: string, payload: object }} event
 * @param {(row: HTMLElement, event: object) => void} onRowClick
 * @returns {HTMLElement}
 */
function buildRow(event, onRowClick) {
  const { type, lane, ts, summary } = event;

  const timeSpan = el('span', {
    class: 'aw-row__time',
    style: [
      'font-family: var(--font-mono, ui-monospace, monospace);',
      'font-size: 11px;',
      'color: var(--text-muted, #9aa0a8);',
      'white-space: nowrap;',
      'flex-shrink: 0;',
    ].join(' '),
  }, formatTime(ts));

  const laneChip = lane
    ? el('span', {
        class: `aw-row__lane-chip`,
        style: [
          'font-family: var(--font-mono, ui-monospace, monospace);',
          'font-size: 11px;',
          'padding: 1px 5px;',
          'background: var(--surface-2, #1c1f24);',
          'border: 1px solid var(--border, #2a2e35);',
          'border-radius: 3px;',
          'color: var(--text-muted, #9aa0a8);',
          'white-space: nowrap;',
          'flex-shrink: 0;',
        ].join(' '),
      }, lane)
    : false;

  const typeChip = el('span', {
    class: `aw-row__type-chip aw-type--${type}`,
    style: [
      'font-family: var(--font-mono, ui-monospace, monospace);',
      'font-size: 11px;',
      'padding: 1px 5px;',
      'border-radius: 3px;',
      'white-space: nowrap;',
      'flex-shrink: 0;',
      _typeChipStyle(type),
    ].join(' '),
  }, type);

  const summarySpan = el('span', {
    class: 'aw-row__summary',
    style: [
      'font-size: 12px;',
      'color: var(--text, #e7e9ec);',
      'overflow: hidden;',
      'text-overflow: ellipsis;',
      'white-space: nowrap;',
      'flex: 1 1 0;',
      'min-width: 0;',
    ].join(' '),
    title: summary,
  }, summary);

  const sep = () => el('span', {
    style: 'color: var(--border, #2a2e35); flex-shrink: 0;',
    'aria-hidden': 'true',
  }, '·');

  const row = el('div', {
    class: 'aw-row',
    role: 'button',
    tabindex: '0',
    'data-event-type': type,
    'data-event-lane': lane || '',
    style: [
      'display: flex;',
      'align-items: center;',
      'gap: 6px;',
      'padding: 4px 8px;',
      'border-bottom: 1px solid var(--border, #2a2e35);',
      'cursor: pointer;',
      'user-select: none;',
      'transition: background-color 80ms ease;',
    ].join(' '),
  },
    timeSpan,
    sep(),
    ...(laneChip ? [laneChip, sep()] : []),
    typeChip,
    sep(),
    summarySpan,
  );

  row.addEventListener('mouseenter', () => {
    row.style.backgroundColor = 'var(--surface-2, #1c1f24)';
  });
  row.addEventListener('mouseleave', () => {
    row.style.backgroundColor = '';
  });
  row.addEventListener('click', () => onRowClick(row, event));
  row.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' || ev.key === ' ') {
      ev.preventDefault();
      onRowClick(row, event);
    }
  });

  return row;
}

/**
 * Return inline style for a type chip by event type.
 * @param {string} type
 * @returns {string}
 */
function _typeChipStyle(type) {
  const styles = {
    finding:        'background: #3a2a1a; color: var(--sev-major, #e08a32); border: 1px solid #5a3f1a;',
    signal:         'background: #1a2a3a; color: var(--accent, #6db8ff); border: 1px solid #1a3a5a;',
    history:        'background: #1a2a1a; color: #4ec9b0; border: 1px solid #1a3a2a;',
    queue:          'background: #2a2a1a; color: var(--sev-minor, #d6c34c); border: 1px solid #3a3a1a;',
    inject:         'background: #2a1a2a; color: #c586c0; border: 1px solid #3a1a3a;',
    'restart-loop': 'background: #2a1a1a; color: var(--sev-blocking, #d04848); border: 1px solid #3a1a1a;',
    governor:       'background: #3a1a1a; color: var(--sev-blocking, #d04848); border: 1px solid #5a2020;',
  };
  return styles[type] || 'background: var(--surface-2, #1c1f24); color: var(--text-muted, #9aa0a8); border: 1px solid var(--border, #2a2e35);';
}

// ---------------------------------------------------------------------------
// Side drawer
// ---------------------------------------------------------------------------

/**
 * Build the side drawer overlay.
 * @returns {{ drawerEl: HTMLElement, openDrawer: (event: object) => void, closeDrawer: () => void }}
 */
function buildDrawer() {
  const overlay = el('div', {
    class: 'aw-drawer-overlay',
    'data-testid': 'aw-drawer-overlay',
    style: [
      'display: none;',
      'position: fixed;',
      'inset: 0;',
      'background: rgba(0,0,0,0.4);',
      'z-index: 200;',
      'pointer-events: none;',
    ].join(' '),
  });

  const drawer = el('div', {
    class: 'aw-drawer',
    'data-testid': 'aw-drawer',
    style: [
      'position: fixed;',
      'top: 0;',
      'right: 0;',
      'bottom: 0;',
      'width: min(480px, 90vw);',
      'background: var(--surface, #15171b);',
      'border-left: 1px solid var(--border, #2a2e35);',
      'display: flex;',
      'flex-direction: column;',
      'z-index: 201;',
    ].join(' '),
  });

  const drawerHeader = el('div', {
    style: [
      'display: flex;',
      'align-items: center;',
      'justify-content: space-between;',
      'padding: 12px 16px;',
      'border-bottom: 1px solid var(--border, #2a2e35);',
      'flex-shrink: 0;',
    ].join(' '),
  });

  const drawerTitle = el('span', {
    style: 'font-size: 13px; font-weight: 600; color: var(--text, #e7e9ec);',
    'data-testid': 'aw-drawer-title',
  }, 'Event payload');

  const closeBtn = el('button', {
    type: 'button',
    class: 'button',
    'data-testid': 'aw-drawer-close',
    style: 'height: 28px; padding: 0 10px; font-size: 13px;',
    'aria-label': 'Close drawer',
  }, 'X');

  drawerHeader.appendChild(drawerTitle);
  drawerHeader.appendChild(closeBtn);

  const drawerBody = el('div', {
    style: [
      'flex: 1;',
      'overflow-y: auto;',
      'padding: 16px;',
    ].join(' '),
  });

  const payloadPre = el('pre', {
    'data-testid': 'aw-drawer-payload',
    style: [
      'font-family: var(--font-mono, ui-monospace, monospace);',
      'font-size: 12px;',
      'color: var(--text, #e7e9ec);',
      'white-space: pre-wrap;',
      'word-break: break-word;',
      'margin: 0;',
    ].join(' '),
  });

  drawerBody.appendChild(payloadPre);
  drawer.appendChild(drawerHeader);
  drawer.appendChild(drawerBody);
  overlay.appendChild(drawer);

  function openDrawer(/** @type {object} */ event) {
    payloadPre.textContent = JSON.stringify(event, null, 2);
    overlay.style.display = 'block';
    overlay.style.pointerEvents = 'auto';
  }

  function closeDrawer() {
    overlay.style.display = 'none';
    overlay.style.pointerEvents = 'none';
    payloadPre.textContent = '';
  }

  closeBtn.addEventListener('click', closeDrawer);
  overlay.addEventListener('click', (ev) => {
    if (ev.target === overlay) closeDrawer();
  });

  return { drawerEl: overlay, openDrawer, closeDrawer };
}

// ---------------------------------------------------------------------------
// Main export
// ---------------------------------------------------------------------------

/**
 * Create and mount the Activity Wall component.
 *
 * @param {{ container: HTMLElement }} opts
 * @returns {{ element: HTMLElement, cleanup: () => void }}
 */
export function createActivityWall({ container }) {
  // ---- per-component state ------------------------------------------------
  /** @type {Set<string>} - set of active type filters; empty means "All" */
  const activeFilters = new Set();
  let paused = false;
  /** @type {EventSource|null} */
  let es = null;
  // Bug #5: reconnect machinery. The original onerror only console.warn'd, so a
  // dropped SSE froze the feed silently with no recovery and no visible state.
  let disposed = false;
  let reconnectDelay = 500;
  const RECONNECT_MAX_MS = 30_000;
  /** @type {ReturnType<typeof setTimeout>|null} */
  let reconnectTimer = null;
  /** @type {() => void} */
  let offReauth = () => {};
  // Dedupe key set so a reconnect snapshot-backfill (bug #5) doesn't re-insert
  // events already in the DOM. Keyed on type|lane|ts|summary (stable per event).
  /** @type {Set<string>} */
  const seenKeys = new Set();

  /** @param {{type?: string, lane?: string|null, ts?: string, summary?: string}} ev */
  function _eventKey(ev) {
    return `${ev.type || ''}|${ev.lane || ''}|${ev.ts || ''}|${ev.summary || ''}`;
  }

  // ---- root element -------------------------------------------------------
  const root = el('div', {
    class: 'activity-wall',
    'data-testid': 'activity-wall-root',
    style: [
      'display: flex;',
      'flex-direction: column;',
      'height: 100%;',
      'min-height: 0;',
      'background: #15181d;',
      'border: 1px solid #2a2f37;',
      'border-radius: 4px;',
      'overflow: hidden;',
      'font-family: ui-monospace, SFMono-Regular, Menlo, monospace;',
    ].join(' '),
  });

  // ---- header: title + pause button ---------------------------------------
  const headerBar = el('div', {
    style: [
      'display: flex;',
      'align-items: center;',
      'justify-content: space-between;',
      'padding: 6px 10px;',
      'border-bottom: 1px solid #2a2f37;',
      'flex-shrink: 0;',
      'background: #1f242c;',
    ].join(' '),
  });

  const title = el('span', {
    style: 'font-size: 12px; font-weight: 600; color: #e6e6e6; letter-spacing: 0.3px;',
  }, 'Activity Wall');

  const pauseBtn = el('button', {
    type: 'button',
    class: 'button',
    'data-testid': 'aw-pause-btn',
    style: 'height: 22px; padding: 0 8px; font-size: 11px;',
  }, 'Pause');

  headerBar.appendChild(title);
  headerBar.appendChild(pauseBtn);

  // ---- connection status bar (bug #5) -------------------------------------
  // Visible "disconnected / reconnecting" state so a frozen feed is never
  // mistaken for "quiet". Hidden while connected.
  const statusBar = el('div', {
    'data-testid': 'aw-status',
    'data-state': 'connecting',
    style: [
      'display: none;',
      'padding: 4px 10px;',
      'font-size: 11px;',
      'border-bottom: 1px solid #2a2f37;',
      'flex-shrink: 0;',
      'background: #2a1f15;',
      'color: var(--sev-major, #e08a32);',
    ].join(' '),
  }, '');

  /** @param {"connected"|"connecting"|"disconnected"} state */
  function setConnState(state) {
    statusBar.dataset.state = state;
    if (state === 'connected') {
      statusBar.style.display = 'none';
      statusBar.textContent = '';
    } else if (state === 'connecting') {
      statusBar.style.display = '';
      statusBar.textContent = 'Reconnecting…';
    } else {
      statusBar.style.display = '';
      statusBar.textContent = 'Disconnected — retrying';
    }
  }

  // ---- filter chips bar ---------------------------------------------------
  const chipsBar = el('div', {
    class: 'aw-chips',
    'data-testid': 'aw-chips',
    style: [
      'display: flex;',
      'flex-wrap: wrap;',
      'gap: 4px;',
      'padding: 6px 8px;',
      'border-bottom: 1px solid #2a2f37;',
      'flex-shrink: 0;',
      'background: #1a1e25;',
    ].join(' '),
  });

  /** @type {Map<string|null, HTMLElement>} */
  const chipEls = new Map();

  for (const { label, type } of CHIP_DEFS) {
    const chip = el('button', {
      type: 'button',
      'data-chip-type': type === null ? 'all' : type,
      'data-testid': `aw-chip-${type === null ? 'all' : type}`,
      style: [
        'font-size: 11px;',
        'padding: 2px 8px;',
        'border-radius: 999px;',
        'border: 1px solid #2a2f37;',
        'background: #1c1f24;',
        'color: #9aa0a8;',
        'cursor: pointer;',
        'transition: background-color 80ms ease, color 80ms ease;',
      ].join(' '),
    }, label);
    chipEls.set(type, chip);
    chipsBar.appendChild(chip);
  }

  // ---- event list ---------------------------------------------------------
  const listEl = el('div', {
    class: 'aw-list',
    'data-testid': 'aw-list',
    style: [
      'flex: 1;',
      'min-height: 0;',
      'overflow-y: auto;',
      // Disable Chrome scroll-anchor so insertBefore(row, firstChild) does not
      // silently adjust scrollTop when the list is paused. We manage scroll
      // position ourselves (scrollTop = 0 in prependRow when !paused).
      'overflow-anchor: none;',
    ].join(' '),
  });

  // ---- empty state (bug #5) -----------------------------------------------
  // "No activity yet" so a blank list reads as "nothing happened", not "broken".
  const emptyEl = el('div', {
    'data-testid': 'aw-empty',
    style: [
      'padding: 16px 12px;',
      'font-size: 12px;',
      'color: var(--text-muted, #9aa0a8);',
      'text-align: center;',
    ].join(' '),
  }, 'No activity yet.');
  listEl.appendChild(emptyEl);

  /** Show the empty placeholder iff the list has no event rows. */
  function _refreshEmptyState() {
    const hasRows = listEl.querySelector('.aw-row') !== null;
    emptyEl.style.display = hasRows ? 'none' : '';
  }

  // ---- drawer -------------------------------------------------------------
  const { drawerEl, openDrawer, closeDrawer } = buildDrawer();

  // ---- assemble root -------------------------------------------------------
  root.appendChild(headerBar);
  root.appendChild(statusBar);
  root.appendChild(chipsBar);
  root.appendChild(listEl);
  document.body.appendChild(drawerEl);

  // ---- chip state helpers -------------------------------------------------

  function _updateChipVisuals() {
    const allActive = activeFilters.size === 0;
    for (const [type, chip] of chipEls.entries()) {
      const isActive = type === null ? allActive : activeFilters.has(type);
      if (isActive) {
        chip.style.background = 'var(--accent, #6db8ff)';
        chip.style.color = 'var(--bg, #0e0f12)';
        chip.style.borderColor = 'var(--accent, #6db8ff)';
      } else {
        chip.style.background = '#1c1f24';
        chip.style.color = '#9aa0a8';
        chip.style.borderColor = '#2a2f37';
      }
    }
  }

  function _applyFilterToAllRows() {
    const allActive = activeFilters.size === 0;
    for (const row of /** @type {NodeListOf<HTMLElement>} */ (listEl.querySelectorAll('.aw-row'))) {
      const rowType = row.dataset['eventType'] || '';
      row.style.display = (allActive || activeFilters.has(rowType)) ? '' : 'none';
    }
  }

  function _isRowVisible(/** @type {HTMLElement} */ row) {
    const rowType = row.dataset['eventType'] || '';
    return activeFilters.size === 0 || activeFilters.has(rowType);
  }

  // Initialize "All" chip as active
  _updateChipVisuals();

  // ---- chip click handlers ------------------------------------------------

  for (const [type, chip] of chipEls.entries()) {
    chip.addEventListener('click', () => {
      if (type === null) {
        // "All" chip — clear selection
        activeFilters.clear();
      } else {
        // Toggle this type
        if (activeFilters.has(type)) {
          activeFilters.delete(type);
        } else {
          activeFilters.add(type);
        }
        // If nothing selected, revert to "All" state
        // (no-op: size === 0 is "all")
      }
      _updateChipVisuals();
      _applyFilterToAllRows();
    });
  }

  // ---- pause/resume -------------------------------------------------------

  function _updatePauseBtn() {
    pauseBtn.textContent = paused ? 'Resume' : 'Pause';
    pauseBtn.setAttribute('aria-pressed', String(paused));
  }

  pauseBtn.addEventListener('click', () => {
    paused = !paused;
    _updatePauseBtn();
  });

  // ---- row click (drawer) -------------------------------------------------

  function onRowClick(/** @type {HTMLElement} */ _row, /** @type {object} */ event) {
    openDrawer(event);
  }

  // ---- row insertion ------------------------------------------------------

  /**
   * Prepend a new event row to the list.
   * Enforces MAX_DOM_ROWS by dropping oldest children.
   * Respects pause flag for auto-scroll.
   *
   * @param {object} event
   */
  function prependRow(event) {
    const key = _eventKey(/** @type {any} */ (event));
    if (seenKeys.has(key)) return; // dedupe (bug #5: reconnect backfill overlap)
    seenKeys.add(key);
    const row = buildRow(/** @type {any} */ (event), onRowClick);
    if (!_isRowVisible(row)) {
      row.style.display = 'none';
    }

    const scrollAtTop = listEl.scrollTop === 0;

    listEl.insertBefore(row, listEl.firstChild);

    // Enforce cap
    while (listEl.children.length > MAX_DOM_ROWS) {
      listEl.removeChild(listEl.lastChild);
    }

    // Auto-scroll to top only if not paused and user was already at top
    if (!paused) {
      listEl.scrollTop = 0;
    }
    _refreshEmptyState();
  }

  // ---- snapshot hydration -------------------------------------------------

  async function fetchSnapshot() {
    try {
      // authedFetch awaits the first-load auth exchange (bug #1) and surfaces
      // the re-auth modal on 401 (bug #2).
      const resp = await authedFetch('/api/v1/activity-wall/snapshot?limit=100');
      if (!resp.ok) return;
      const json = await resp.json();
      const events = Array.isArray(json.events) ? json.events : [];
      // Snapshot is newest-first. We want to render newest at top.
      // Append in reverse (oldest first) so the final order is newest at top.
      // Dedupe against rows already present (a reconnect backfill re-fetches the
      // snapshot to recover missed events — bug #5 — and must not duplicate).
      const fragment = document.createDocumentFragment();
      let appended = 0;
      for (let i = events.length - 1; i >= 0; i--) {
        const event = events[i];
        const key = _eventKey(event);
        if (seenKeys.has(key)) continue;
        seenKeys.add(key);
        const row = buildRow(event, onRowClick);
        if (!_isRowVisible(row)) {
          row.style.display = 'none';
        }
        fragment.appendChild(row);
        appended++;
      }
      if (appended > 0) {
        // Insert the whole batch at the top in one operation.
        listEl.insertBefore(fragment, listEl.firstChild);
        // After hydration: scroll to top so newest is visible.
        listEl.scrollTop = 0;
      }
      _refreshEmptyState();
    } catch (err) {
      console.warn('[activity-wall] snapshot fetch failed:', err);
    }
  }

  // ---- SSE subscription ---------------------------------------------------

  function _clearReconnectTimer() {
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  }

  /**
   * Schedule a capped-backoff reconnect (bug #5). On the next attempt we
   * re-run fetchSnapshot() to backfill events missed during the outage, then
   * re-open the SSE. seenKeys dedupes the overlap.
   */
  function _scheduleReconnect() {
    if (disposed) return;
    _clearReconnectTimer();
    setConnState('disconnected');
    const delay = Math.min(reconnectDelay, RECONNECT_MAX_MS);
    reconnectTimer = setTimeout(async () => {
      reconnectTimer = null;
      if (disposed) return;
      setConnState('connecting');
      // Backfill missed events before re-subscribing so the gap is filled.
      await fetchSnapshot();
      if (disposed) return;
      startSSE();
    }, delay);
    reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
  }

  function startSSE() {
    if (disposed) return;
    es = new EventSource('/api/v1/activity-wall', { withCredentials: true });
    es.onopen = () => {
      reconnectDelay = 500;
      setConnState('connected');
    };
    es.onmessage = (ev) => {
      try {
        const event = JSON.parse(ev.data);
        prependRow(event);
      } catch (err) {
        console.warn('[activity-wall] SSE parse error:', err);
      }
    };
    es.onerror = () => {
      // Bug #5: a dropped/closed stream must reconnect with backoff + backfill,
      // and probe for a 401 (session expired) to surface the re-auth modal.
      // Previously this only console.warn'd, freezing the feed forever.
      if (!es || es.readyState !== EventSource.CLOSED) return;
      try { es.close(); } catch (_) { /* ignore */ }
      es = null;
      probeReauthOn401('/api/v1/activity-wall');
      _scheduleReconnect();
    };
  }

  // ---- ESC key handler ----------------------------------------------------

  function onKeyDown(/** @type {KeyboardEvent} */ ev) {
    if (ev.key === 'Escape') {
      closeDrawer();
    }
  }
  document.addEventListener('keydown', onKeyDown);

  // ---- bootstrap (async, fire-and-forget) ---------------------------------
  setConnState('connecting');
  fetchSnapshot().then(() => startSSE());

  // After a successful re-auth (operator pasted a fresh token), force an
  // immediate reconnect + backfill rather than waiting out the backoff (bug #2/#5).
  offReauth = onReauthSuccess(() => {
    if (disposed) return;
    _clearReconnectTimer();
    reconnectDelay = 500;
    if (es) { try { es.close(); } catch (_) { /* ignore */ } es = null; }
    setConnState('connecting');
    fetchSnapshot().then(() => { if (!disposed) startSSE(); });
  });

  // ---- cleanup ------------------------------------------------------------

  function cleanup() {
    disposed = true;
    _clearReconnectTimer();
    try { offReauth(); } catch (_) {}
    if (es) {
      try { es.close(); } catch (_) {}
      es = null;
    }
    document.removeEventListener('keydown', onKeyDown);
    // Remove the drawer overlay from the body
    try { document.body.removeChild(drawerEl); } catch (_) {}
  }

  return { element: root, cleanup };
}

export default createActivityWall;
