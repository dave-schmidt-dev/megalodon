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
import { store } from '../js/store.js';

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

// Bug R5: liveness watchdog. The activity-wall SSE emits keepalive comments
// every ~heartbeatIntervalSeconds (~15s). Treat silence longer than
// HEARTBEAT_GRACE_MULTIPLIER × that interval as a silently-dead connection and
// force a reconnect+backfill (mirrors ui/static/js/sse.js).
const HEARTBEAT_GRACE_MULTIPLIER = 2.5;
const DEFAULT_HEARTBEAT_SECONDS = 15;

// I3: how close to the top counts as "at the top" for auto-scroll. A small
// slack absorbs sub-pixel rounding and a reader sitting on the newest row.
const NEAR_TOP_PX = 8;

// I1: persist the operator's open/closed choice. The open/close CONTROL lives
// in board.js (the "activity ▸" toggle), which mounts/unmounts this component.
// This component can't auto-mount itself, but it CAN record whether it was last
// open and default to open when no choice is stored, so board.js can honour the
// preference. See activityWallShouldDefaultOpen() below (exported for board.js).
const AW_OPEN_KEY = 'megalodon.activityWall.open';

/**
 * Whether the activity wall should be open by default. For a "see what agents
 * are doing" tool we default to OPEN; once the operator toggles it we persist
 * and honour their last choice. board.js can call this on board mount to decide
 * whether to auto-open the panel (I1).
 *
 * @returns {boolean}
 */
export function activityWallShouldDefaultOpen() {
  try {
    const v = localStorage.getItem(AW_OPEN_KEY);
    if (v === '0') return false; // operator explicitly closed it last
    return true; // default-open (no stored choice) or explicitly opened ('1')
  } catch (_) {
    return true;
  }
}

function _persistOpenState(open) {
  try { localStorage.setItem(AW_OPEN_KEY, open ? '1' : '0'); } catch (_) { /* ignore */ }
}

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
 * Parse a signal filename into from/to/topic when the server payload didn't
 * enrich it. Canonical grammar (UTC anchored at the end so topic may contain
 * dashes): `LANE-<FROM>-to-LANE-<TO>-<topic>-<UTC>.md`; legacy: no trailing UTC.
 *
 * @param {string} filename
 * @returns {{ from_lane: string, to_lane: string, topic: string }}
 */
function _parseSignalFilename(filename) {
  const base = String(filename || '').replace(/\.md$/i, '');
  const m = base.match(
    /^(LANE-[A-Z0-9]+)-to-(LANE-[A-Z0-9]+)-(.+)-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}(?:-\d{2})?Z)$/,
  );
  if (m) return { from_lane: m[1], to_lane: m[2], topic: m[3] };
  const legacy = base.match(/^(LANE-[A-Z0-9]+)-to-(LANE-[A-Z0-9]+)-(.+)$/);
  if (legacy) return { from_lane: legacy[1], to_lane: legacy[2], topic: legacy[3] };
  return { from_lane: '', to_lane: '', topic: base };
}

/**
 * Build the who→whom·topic content for a signal row. Uses payload.from_lane/
 * to_lane/topic, falling back to parsing payload.filename.
 *
 * @param {{ summary?: string, payload?: object }} event
 * @returns {HTMLElement}
 */
function _buildSignalSummary(event) {
  const payload = event.payload || {};
  let from = payload.from_lane || '';
  let to = payload.to_lane || '';
  let topic = payload.topic || '';
  if ((!from || !to) && payload.filename) {
    const parsed = _parseSignalFilename(payload.filename);
    from = from || parsed.from_lane;
    to = to || parsed.to_lane;
    topic = topic || parsed.topic;
  }

  const wrap = el('span', {
    class: 'aw-row__summary aw-row__signal',
    'data-testid': 'aw-signal-summary',
    style: [
      'display: flex;',
      'align-items: center;',
      'gap: 4px;',
      'font-size: 12px;',
      'color: var(--text, #e7e9ec);',
      'overflow: hidden;',
      'white-space: nowrap;',
      'flex: 1 1 0;',
      'min-width: 0;',
    ].join(' '),
    title: from && to ? `${from} → ${to} · ${topic}` : (event.summary || ''),
  });

  const chipStyle = [
    'font-family: var(--font-mono, ui-monospace, monospace);',
    'font-size: 11px;',
    'padding: 0 4px;',
    'border-radius: 3px;',
    'background: var(--surface-2, #1c1f24);',
    'border: 1px solid var(--border, #2a2e35);',
    'color: var(--accent, #6db8ff);',
    'flex-shrink: 0;',
  ].join(' ');

  if (from && to) {
    wrap.appendChild(el('span', { class: 'aw-signal__from', style: chipStyle }, from));
    wrap.appendChild(el('span', { style: 'flex-shrink: 0; color: var(--text-muted, #9aa0a8);', 'aria-hidden': 'true' }, '→'));
    wrap.appendChild(el('span', { class: 'aw-signal__to', style: chipStyle }, to));
    wrap.appendChild(el('span', {
      class: 'aw-signal__topic',
      style: 'overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; color: var(--text-muted, #9aa0a8);',
    }, topic ? `· ${topic}` : ''));
  } else {
    // Non-canonical: show whatever summary the server gave us.
    wrap.appendChild(el('span', {
      style: 'overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0;',
    }, event.summary || payload.filename || 'signal'));
  }
  return wrap;
}

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

  // For signal rows render sender→receiver · topic (who→whom→subject) instead
  // of the raw filename/summary. Falls back to parsing payload.filename when the
  // server didn't enrich the payload (legacy / non-canonical signal files).
  const summarySpan = (type === 'signal')
    ? _buildSignalSummary(event)
    : el('span', {
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

  // Signal excerpt/body block — surfaced above the raw JSON so a signal's
  // human-readable handoff text is the first thing the operator sees.
  const excerptPre = el('pre', {
    'data-testid': 'aw-drawer-excerpt',
    style: [
      'font-family: var(--font-mono, ui-monospace, monospace);',
      'font-size: 12px;',
      'color: var(--text, #e7e9ec);',
      'white-space: pre-wrap;',
      'word-break: break-word;',
      'margin: 0 0 12px;',
      'padding: 8px 10px;',
      'background: var(--surface-2, #1c1f24);',
      'border: 1px solid var(--border, #2a2e35);',
      'border-radius: 4px;',
      'display: none;',
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

  drawerBody.appendChild(excerptPre);
  drawerBody.appendChild(payloadPre);
  drawer.appendChild(drawerHeader);
  drawer.appendChild(drawerBody);
  overlay.appendChild(drawer);

  function openDrawer(/** @type {object} */ event) {
    // Signal events: surface the excerpt/body text prominently above the JSON.
    const payload = (event && event.payload) || {};
    const excerpt = event && event.type === 'signal'
      ? String(payload.excerpt || payload.body || '')
      : '';
    if (excerpt.trim()) {
      excerptPre.textContent = excerpt;
      excerptPre.style.display = '';
    } else {
      excerptPre.textContent = '';
      excerptPre.style.display = 'none';
    }
    payloadPre.textContent = JSON.stringify(event, null, 2);
    overlay.style.display = 'block';
    overlay.style.pointerEvents = 'auto';
  }

  function closeDrawer() {
    overlay.style.display = 'none';
    overlay.style.pointerEvents = 'none';
    payloadPre.textContent = '';
    excerptPre.textContent = '';
    excerptPre.style.display = 'none';
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
  // Bug R5: liveness watchdog timer + a flag marking that the current SSE was
  // opened AFTER a prior failure/silence. When set, the next es.onopen runs a
  // snapshot backfill to fill the gap the browser's silent auto-reconnect left.
  /** @type {ReturnType<typeof setTimeout>|null} */
  let heartbeatTimer = null;
  let needsBackfillOnOpen = false;
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
    // I3: capture scroll position BEFORE inserting. We only auto-scroll the
    // reader back to the top when they were already at/near the top; a reader
    // who scrolled back into history must NOT be yanked up on every new event.
    const wasAtTop = listEl.scrollTop <= NEAR_TOP_PX;
    const row = buildRow(/** @type {any} */ (event), onRowClick);
    // Tag the row with its dedupe key so cap-eviction can drop it from seenKeys
    // in lockstep (bug: seenKeys grew unbounded — a long-lived wall leaked one
    // key per event forever even though the DOM was capped at MAX_DOM_ROWS).
    row.dataset.eventKey = key;
    if (!_isRowVisible(row)) {
      row.style.display = 'none';
    }

    listEl.insertBefore(row, listEl.firstChild);

    _enforceCap();

    // I3: auto-scroll to top ONLY when not paused AND the reader was already at
    // (or within NEAR_TOP_PX of) the top. Previously this checked only !paused,
    // so a reader scrolled back into history was yanked to the top on every new
    // event. (insertBefore at firstChild pushes content down, so a reader not at
    // the top would otherwise drift; overflow-anchor:none keeps their position.)
    if (!paused && wasAtTop) {
      listEl.scrollTop = 0;
    }
    _refreshEmptyState();
  }

  /**
   * Enforce MAX_DOM_ROWS, counting/evicting ONLY `.aw-row` elements so the
   * `emptyEl` placeholder (a non-row child of listEl) does not consume a slot.
   *
   * Two bugs fixed here:
   *  - emptyEl off-by-one: the old loop used `listEl.children.length`, which
   *    counts emptyEl, so the cap was effectively MAX_DOM_ROWS-1 real rows and
   *    `removeChild(lastChild)` could even target emptyEl at the boundary.
   *  - seenKeys leak: each evicted row's key is removed from `seenKeys` so the
   *    set stays bounded by the number of rows actually in the DOM.
   */
  function _enforceCap() {
    let rows = listEl.querySelectorAll('.aw-row');
    while (rows.length > MAX_DOM_ROWS) {
      const oldest = rows[rows.length - 1];
      const k = oldest.dataset && oldest.dataset.eventKey;
      if (k) seenKeys.delete(k);
      listEl.removeChild(oldest);
      rows = listEl.querySelectorAll('.aw-row');
    }
    // Observability hook (also a regression guard): reflect the dedupe-set size
    // onto the root so a test can assert seenKeys stays bounded by the row cap
    // rather than leaking one entry per event forever.
    root.dataset.seenKeys = String(seenKeys.size);
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
      // Snapshot is newest-first (server sorts by ts desc). A DocumentFragment
      // inserts its children IN ORDER, so to land newest at the top of the list
      // the fragment must itself be newest-first — i.e. iterate the events
      // forward. (The old reverse-iteration put the OLDEST event at the top,
      // which only looked correct while every snapshot event shared the same
      // arrival order as its ts; once history events — old ts — entered the
      // snapshot they wrongly floated to the top.)
      // Dedupe against rows already present (a reconnect backfill re-fetches the
      // snapshot to recover missed events — bug #5 — and must not duplicate).
      const fragment = document.createDocumentFragment();
      let appended = 0;
      for (let i = 0; i < events.length; i++) {
        const event = events[i];
        const key = _eventKey(event);
        if (seenKeys.has(key)) continue;
        seenKeys.add(key);
        const row = buildRow(event, onRowClick);
        row.dataset.eventKey = key;
        if (!_isRowVisible(row)) {
          row.style.display = 'none';
        }
        fragment.appendChild(row);
        appended++;
      }
      if (appended > 0) {
        // Insert the whole batch at the top in one operation.
        listEl.insertBefore(fragment, listEl.firstChild);
        _enforceCap();
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

  function _clearHeartbeatTimer() {
    if (heartbeatTimer !== null) {
      clearTimeout(heartbeatTimer);
      heartbeatTimer = null;
    }
  }

  /**
   * (Re)arm the liveness watchdog (bug R5). Called on open and on every SSE
   * message — any received byte resets the clock. If the grace window elapses
   * with no traffic the connection is silently dead (the browser may still be
   * stuck in CONNECTING and auto-retrying), so force a visible reconnect that
   * backfills the gap via fetchSnapshot().
   */
  function _armHeartbeatWatchdog() {
    _clearHeartbeatTimer();
    if (disposed) return;
    const cfg = store.get('config') || {};
    const interval = cfg.heartbeatIntervalSeconds ?? DEFAULT_HEARTBEAT_SECONDS;
    const graceMs = Math.round(interval * HEARTBEAT_GRACE_MULTIPLIER * 1000);
    heartbeatTimer = setTimeout(() => {
      if (disposed) return;
      console.warn('[activity-wall] heartbeat watchdog tripped; forcing reconnect+backfill');
      // Tear down the (possibly CONNECTING-but-silent) EventSource ourselves so
      // the browser's invisible auto-reconnect can't keep masking the gap.
      if (es) { try { es.close(); } catch (_) { /* ignore */ } es = null; }
      probeReauthOn401('/api/v1/activity-wall');
      _scheduleReconnect();
    }, graceMs);
  }

  /**
   * Schedule a capped-backoff reconnect (bug #5). On the next attempt we
   * re-run fetchSnapshot() to backfill events missed during the outage, then
   * re-open the SSE. seenKeys dedupes the overlap.
   */
  function _scheduleReconnect() {
    if (disposed) return;
    _clearReconnectTimer();
    _clearHeartbeatTimer();
    setConnState('disconnected');
    const delay = Math.min(reconnectDelay, RECONNECT_MAX_MS);
    reconnectTimer = setTimeout(async () => {
      reconnectTimer = null;
      if (disposed) return;
      setConnState('connecting');
      // Backfill missed events before re-subscribing so the gap is filled.
      // This explicit backfill makes the onopen-backfill redundant for this
      // attempt, so clear the flag to avoid fetching the snapshot twice.
      await fetchSnapshot();
      needsBackfillOnOpen = false;
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
      // Bug R5: if this open follows a prior error/silence, the browser may have
      // silently auto-reconnected and we may have missed events while down. Run
      // a snapshot backfill to fill the gap (seenKeys dedupes the overlap).
      if (needsBackfillOnOpen) {
        needsBackfillOnOpen = false;
        fetchSnapshot();
      }
      // Arm the liveness watchdog so a future silent stall is detected.
      _armHeartbeatWatchdog();
    };
    es.onmessage = (ev) => {
      // Any traffic (event OR keepalive comment) proves the stream is alive —
      // reset the watchdog (bug R5).
      _armHeartbeatWatchdog();
      try {
        const event = JSON.parse(ev.data);
        prependRow(event);
      } catch (err) {
        console.warn('[activity-wall] SSE parse error:', err);
      }
    };
    es.onerror = () => {
      // Bug #5/R5: a dropped/closed stream must reconnect with backoff +
      // backfill, and probe for a 401 (session expired) to surface the re-auth
      // modal. Previously this only acted on readyState === CLOSED, so a
      // transient outage that left the browser in CONNECTING (auto-retrying)
      // froze the feed with a permanent gap. We now always show a disconnected
      // state and arm a watchdog; if the browser DOES silently reconnect,
      // es.onopen runs the backfill. If it CLOSED, we drive our own backoff.
      needsBackfillOnOpen = true;
      if (es && es.readyState === EventSource.CLOSED) {
        try { es.close(); } catch (_) { /* ignore */ }
        es = null;
        probeReauthOn401('/api/v1/activity-wall');
        _scheduleReconnect();
        return;
      }
      // Transient (still CONNECTING): surface the disconnect and let the
      // watchdog force a reconnect+backfill if the silence persists. The
      // browser's own auto-reconnect, if it succeeds, fires es.onopen → backfill.
      setConnState('disconnected');
      _armHeartbeatWatchdog();
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
  // I1: mounting === the wall is open. Persist that so board.js can default to
  // open next session (activityWallShouldDefaultOpen).
  _persistOpenState(true);
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
    // I1 NOTE: we do NOT persist "closed" here. board.js calls aw.cleanup() both
    // when the operator toggles the panel closed AND on board-page teardown
    // (navigating away). The component can't tell those apart — only board.js
    // knows the difference — so persisting "closed" on cleanup would wrongly
    // record a close every time the operator merely navigated away. We persist
    // only "open" on mount and default to open (see activityWallShouldDefaultOpen).
    _clearReconnectTimer();
    _clearHeartbeatTimer();
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
