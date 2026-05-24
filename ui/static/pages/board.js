// @ts-check
// pages/board.js — Megalodon "Narrator-Driven Summary Board" (Task 3.2).
//
// The fleet summary view. One row per lane, each carrying:
//   - lane-chip + model/state
//   - a state pill (BLOCKED / STALE / RUNNING / IDLE)
//   - a 3-line Last / Now / Goal block (Now uses the narrator phrase)
//   - tokens
//   - an actions cell (terminal ▸ seam, wired in Task 3.3)
//
// Three data sources at three cadences:
//   1. Narrative — initial GET /api/v1/narrative for instant first paint, then
//      an EventSource("/api/v1/narrative-stream") that pushes the SAME uniform
//      frame shape `{ lanes: { <short>: <per-lane payload>, ... } }`.
//   2. Stale — GET /api/v1/lanes/stale on a 30s poll → STALE pill treatment.
//   3. Permission prompts — driven by the permission-banner component's own 2s
//      poll via its onPromptsChange callback (single source of truth; board.js
//      does NOT open a second prompts poll). The set of prompt.lane values is
//      the BLOCKED-lane set.
//
// Pill precedence (CV-8), single explicit rule:
//   BLOCKED (pending prompt) > STALE (>threshold silent) > RUNNING/IDLE.
// A lane with a pending permission prompt ALWAYS shows BLOCKED; the narrative
// SSE handler must NOT overwrite a blocked lane's pill. STALE overlays
// RUNNING/IDLE only when no prompt is pending.
//
// Narrator-offline: a status dot turns "offline" when a payload carries
// narrator_ok=false or when the stream closes.
//
// Cleanup (grid teardown contract): close the EventSource, stop the stale
// timer, call banner.cleanup(), dispose any open terminal drawer (Task 3.3
// seam), and remove any body-appended modal. No leaked EventSources/timers.
//
// Page contract: `async render(root, params) -> cleanup` (same as grid.js).
//
// Security: no innerHTML with dynamic data; all values via el() / textContent.

import { loadConfig } from "../js/config.js";
import { mountPage } from "../js/app.js";
import { createPermissionBanner } from "../components/permission_banner.js";
import { createTerminalPane } from "../components/terminal_pane.js";
import { createActivityWall } from "../components/activity_wall.js";

// ---------------------------------------------------------------------------
// Minimal DOM helpers (same pattern as grid.js / activity_wall.js — each page
// carries its own, per convention).
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
      if (k === "class") node.className = v;
      else if (k === "dataset") {
        for (const [dk, dv] of Object.entries(v)) node.dataset[dk] = String(dv);
      } else if (k.startsWith("on") && typeof v === "function") {
        node.addEventListener(k.slice(2).toLowerCase(), v);
      } else if (v === true) {
        node.setAttribute(k, "");
      } else {
        node.setAttribute(k, String(v));
      }
    }
  }
  for (const c of children) {
    if (c == null || c === false) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

/** @param {HTMLElement} node */
function clearNode(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

// ---------------------------------------------------------------------------
// Navigation helper (mirrors grid.js navigate()).
// ---------------------------------------------------------------------------

/**
 * Navigate to a path using history.pushState + app.js mountPage.
 * @param {string} path
 */
function navigate(path) {
  if (location.pathname !== path) {
    history.pushState({}, "", path);
  }
  mountPage(path);
}

// ---------------------------------------------------------------------------
// Data helpers
// ---------------------------------------------------------------------------

/**
 * Fetch the narrative snapshot for instant first paint.
 * @returns {Promise<Record<string, any>>} map of <short> → per-lane payload
 */
async function fetchNarrative() {
  try {
    const resp = await fetch("/api/v1/narrative", { credentials: "include" });
    if (!resp.ok) return {};
    const json = await resp.json();
    return json && typeof json.lanes === "object" && json.lanes ? json.lanes : {};
  } catch (_) {
    return {};
  }
}

/**
 * Fetch the stale lanes from the server (mirrors grid.js fetchStaleLanes()).
 * @returns {Promise<Array<{lane: string, silent_seconds: number|null, pending_approval: boolean, last_activity_source: string}>>}
 */
async function fetchStaleLanes() {
  try {
    const resp = await fetch("/api/v1/lanes/stale", { credentials: "include" });
    if (!resp.ok) return [];
    const json = await resp.json();
    return Array.isArray(json.stale_lanes) ? json.stale_lanes : [];
  } catch (_) {
    return [];
  }
}

// ---------------------------------------------------------------------------
// Pill rendering
// ---------------------------------------------------------------------------

/**
 * Resolve the effective pill for a lane given precedence:
 *   BLOCKED > STALE > RUNNING/IDLE.
 * @param {{ blocked: boolean, stale: boolean, state: string|null|undefined }} args
 * @returns {{ label: string, kind: "blocked"|"stale"|"running"|"idle" }}
 */
function resolvePill({ blocked, stale, state }) {
  if (blocked) return { label: "BLOCKED", kind: "blocked" };
  if (stale) return { label: "STALE", kind: "stale" };
  const s = String(state || "").toLowerCase();
  // Treat any active/working state as RUNNING; otherwise IDLE.
  const running =
    s === "claimed" || s === "running" || s === "active" || s === "working" || s === "in_progress";
  return running
    ? { label: "RUNNING", kind: "running" }
    : { label: "IDLE", kind: "idle" };
}

/**
 * Apply pill appearance to an existing pill <span>.
 * @param {HTMLElement} pill
 * @param {{ label: string, kind: string }} resolved
 */
function paintPill(pill, resolved) {
  pill.textContent = resolved.label;
  pill.dataset.pill = resolved.kind;
  // Map kind → token colors (no ad-hoc hex). Severity/staleness tokens.
  let bg = "var(--surface-2)";
  let fg = "var(--text-muted)";
  let border = "var(--border)";
  if (resolved.kind === "blocked") {
    bg = "var(--sev-blocking)";
    fg = "var(--bg)";
    border = "var(--sev-blocking)";
  } else if (resolved.kind === "stale") {
    bg = "var(--surface-2)";
    fg = "var(--stale-stale)";
    border = "var(--stale-stale)";
  } else if (resolved.kind === "running") {
    bg = "var(--surface-2)";
    fg = "var(--accent)";
    border = "var(--accent)";
  }
  pill.style.background = bg;
  pill.style.color = fg;
  pill.style.borderColor = border;
}

// ---------------------------------------------------------------------------
// Row construction
// ---------------------------------------------------------------------------

/**
 * @typedef {Object} LaneRowRefs
 * @property {HTMLElement} root      the row container
 * @property {HTMLElement} pill      the state pill span
 * @property {HTMLElement} lastEl    Last line text node holder
 * @property {HTMLElement} nowEl     Now line text node holder
 * @property {HTMLElement} goalEl    Goal line text node holder
 * @property {HTMLElement} tokensEl  tokens cell
 * @property {string|null|undefined} state  last-known narrative state (for pill re-eval)
 */

/**
 * Build a lane row and return refs for in-place updates.
 * @param {{ name: string, short: string }} lane
 * @param {(short: string) => void} onToggleTerminal  called when the terminal button is clicked
 * @returns {LaneRowRefs}
 */
function buildRow(lane, onToggleTerminal) {
  const laneName = lane.name;
  const short = lane.short;

  const pill = el("span", {
    class: "badge",
    "data-testid": `board-pill-${short}`,
    style: [
      "font-weight: 600;",
      "letter-spacing: 0.4px;",
      "text-transform: uppercase;",
      "border-width: 1px;",
      "border-style: solid;",
    ].join(" "),
  });
  paintPill(pill, { label: "IDLE", kind: "idle" });

  const laneChip = el("span", { class: `lane-chip ${laneName}` }, laneName);

  const headCell = el(
    "div",
    { class: "row", style: "gap: var(--sp-2); min-width: 200px; flex: 0 0 auto;" },
    laneChip,
    pill,
  );

  // 3-line Last / Now / Goal block.
  const labelStyle = "color: var(--text-muted); font-size: var(--fs-xs); width: 40px; flex: 0 0 auto;";
  const lastEl = el("span", { class: "truncate", style: "flex: 1 1 auto; color: var(--text-muted);" }, "—");
  const nowEl = el("span", { class: "truncate", style: "flex: 1 1 auto; color: var(--text);" }, "—");
  const goalEl = el("span", { class: "truncate", style: "flex: 1 1 auto; color: var(--text-muted);" }, "—");

  const lastNowGoalBlock = el(
    "div",
    { class: "stack-1", style: "flex: 1 1 auto; min-width: 240px;" },
    el("div", { class: "row", style: "gap: var(--sp-2); flex-wrap: nowrap;" },
      el("span", { style: labelStyle }, "Last"), lastEl),
    el("div", { class: "row", style: "gap: var(--sp-2); flex-wrap: nowrap;" },
      el("span", { style: labelStyle }, "Now"), nowEl),
    el("div", { class: "row", style: "gap: var(--sp-2); flex-wrap: nowrap;" },
      el("span", { style: labelStyle }, "Goal"), goalEl),
  );

  const tokensEl = el("span", {
    class: "mono",
    "data-testid": `board-tokens-${short}`,
    style: "color: var(--text-muted); font-size: var(--fs-sm); flex: 0 0 auto; min-width: 70px; text-align: right;",
  }, "—");

  // Actions cell — terminal drawer toggle. stopPropagation prevents the row's
  // /lane/:short navigation from firing when the button is clicked.
  const terminalBtn = el("button", {
    type: "button",
    class: "button",
    "data-testid": `board-terminal-${short}`,
    "data-terminal-seam": "true",
    title: "Toggle terminal drawer.",
    onclick: (/** @type {Event} */ ev) => {
      ev.stopPropagation();
      onToggleTerminal(short);
    },
  }, "terminal ▸");

  const actionsCell = el(
    "div",
    { class: "row", style: "gap: var(--sp-2); flex: 0 0 auto;" },
    terminalBtn,
  );

  const root = el(
    "div",
    {
      class: "card row",
      "data-testid": `board-row-${short}`,
      "data-lane": short,
      role: "button",
      tabindex: "0",
      style: [
        "gap: var(--sp-3);",
        "align-items: center;",
        "cursor: pointer;",
        "padding: var(--sp-2) var(--sp-3);",
      ].join(" "),
      onclick: () => navigate(`/lane/${encodeURIComponent(short)}`),
      onkeydown: (/** @type {KeyboardEvent} */ ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          navigate(`/lane/${encodeURIComponent(short)}`);
        }
      },
    },
    headCell,
    lastNowGoalBlock,
    tokensEl,
    actionsCell,
  );

  return { root, pill, lastEl, nowEl, goalEl, tokensEl, state: null };
}

/**
 * Apply a per-lane narrative payload to a row's text cells (NOT the pill —
 * pill precedence is handled separately so BLOCKED is never overwritten).
 * @param {LaneRowRefs} refs
 * @param {any} payload  per-lane payload from narrative .lanes[<short>]
 */
function applyNarrativeText(refs, payload) {
  const last = payload && payload.last;
  const now = payload && payload.now;
  const goal = payload && payload.goal;

  // Last from last.desc (+ task_id when present).
  if (last && last.desc) {
    const id = last.task_id ? `${last.task_id} · ` : "";
    refs.lastEl.textContent = `${id}${last.desc}`;
  } else {
    refs.lastEl.textContent = "—";
  }

  // Now from now.phrase (narrator), fall back to now.desc when phrase is null.
  if (now && (now.phrase || now.desc)) {
    refs.nowEl.textContent = String(now.phrase || now.desc);
  } else {
    refs.nowEl.textContent = "—";
  }

  // Goal.
  refs.goalEl.textContent = goal ? String(goal) : "—";

  // Tokens.
  const tokens = payload ? payload.tokens : null;
  refs.tokensEl.textContent = tokens == null ? "—" : `${Number(tokens).toLocaleString()} tok`;

  // Stash the narrative state for later pill re-evaluation.
  refs.state = payload ? payload.state : null;
}

// ---------------------------------------------------------------------------
// Page entry
// ---------------------------------------------------------------------------

/**
 * Render the summary board page.
 * @param {HTMLElement} root
 * @param {Record<string, any>} [_params]
 * @returns {Promise<() => void>} cleanup
 */
export async function render(root, _params) {
  // --- lane discovery (same pattern as grid.js) ---
  /** @type {Array<{ name: string, short: string }>} */
  let lanes = [];
  try {
    const config = await loadConfig();
    if (Array.isArray(config.lanes) && config.lanes.length > 0) {
      lanes = config.lanes.map((l) => ({
        name: String(l.name || l),
        short: String(l.short || l),
      }));
    }
  } catch (err) {
    console.warn("[board] config load failed:", err);
  }

  if (lanes.length === 0) {
    clearNode(root);
    const errMsg = document.createElement("p");
    errMsg.className = "empty-state";
    errMsg.textContent = "Failed to load mission config — cannot render summary board.";
    root.appendChild(errMsg);
    return () => {};  // app.js clears root on the next mount; see cleanup note below.
  }

  // --- clear skeleton; build page ---
  clearNode(root);

  // Liveness guard: app.js may supersede this mount (clear root, bump mount
  // seq) while an awaited fetch is suspended. `alive` lets resolved awaits and
  // queued callbacks bail out instead of writing into a foreign page. Mirrors
  // the `active` pattern in permission_banner.js. cleanup() flips it false.
  let alive = true;

  // --- shared precedence state (declared before the banner so the
  //     onPromptsChange closure's dependencies are visible). ---
  /** @type {Record<string, LaneRowRefs>} */
  const rowRefs = {};
  /** @type {Set<string>} lanes with a pending permission prompt → BLOCKED */
  let blockedLanes = new Set();
  /** @type {Set<string>} lanes flagged stale by /api/v1/lanes/stale */
  let staleLanes = new Set();

  // --- narrator-offline status dot ---
  const narratorDot = el("span", {
    "data-testid": "narrator-status-dot",
    "data-narrator": "ok",
    title: "Narrator online.",
    style: [
      "display: inline-block;",
      "width: 8px;",
      "height: 8px;",
      "border-radius: 50%;",
      "background: var(--stale-fresh);",
      "flex: 0 0 auto;",
    ].join(" "),
  });

  /** @param {boolean} ok */
  function setNarratorOk(ok) {
    narratorDot.dataset.narrator = ok ? "ok" : "offline";
    narratorDot.style.background = ok ? "var(--stale-fresh)" : "var(--sev-blocking)";
    narratorDot.title = ok ? "Narrator online." : "Narrator offline — phrases may be stale.";
  }

  // --- activity-wall panel state (independent of terminal drawer) ---
  /**
   * @type {{ element: HTMLElement, aw: { element: HTMLElement, cleanup: () => void } }|null}
   */
  let activityPanel = null;

  /** Dispose the activity panel if open (cleanup SSE then remove element). */
  function _closeActivityPanel() {
    if (!activityPanel) return;
    try { activityPanel.aw.cleanup(); } catch (_) { /* ignore */ }
    if (activityPanel.element.parentNode) {
      activityPanel.element.parentNode.removeChild(activityPanel.element);
    }
    activityPanel = null;
  }

  // Activity toggle button — placed in the header, built before missionHeader so
  // the onclick closure can reference _closeActivityPanel / activityPanel.
  const activityToggleBtn = el("button", {
    type: "button",
    class: "button",
    "data-testid": "board-activity-toggle",
    title: "Toggle activity wall.",
    onclick: () => {
      if (activityPanel) {
        _closeActivityPanel();
        return;
      }

      // Close button (built before panelEl so it can be passed to el()).
      const closeBtn = el("button", {
        type: "button",
        class: "button",
        "data-testid": "board-activity-close",
        title: "Close activity wall.",
        style: "flex: 0 0 auto;",
      }, "× close");
      closeBtn.addEventListener("click", _closeActivityPanel);

      // Panel header.
      const panelHeader = el(
        "div",
        {
          class: "row",
          style: [
            "gap: var(--sp-2);",
            "align-items: center;",
            "padding: var(--sp-2) var(--sp-3);",
            "border-bottom: 1px solid var(--border);",
            "background: var(--surface-2);",
            "flex: 0 0 auto;",
          ].join(" "),
        },
        el("span", {
          class: "mono",
          style: "flex: 1 1 auto; font-size: var(--fs-sm); color: var(--text);",
        }, "Activity wall"),
        closeBtn,
      );

      // Panel container — gives the wall a scrollable sized box mirroring grid.js.
      const panelEl = el(
        "div",
        {
          "data-testid": "board-activity-panel",
          style: [
            "display: flex;",
            "flex-direction: column;",
            "width: 320px;",
            "min-width: 280px;",
            "max-width: 340px;",
            "height: calc(100vh - 180px);",
            "max-height: 900px;",
            "border: 1px solid var(--border);",
            "border-radius: var(--r-1);",
            "background: var(--surface);",
            "position: fixed;",
            "top: 90px;",
            "right: var(--sp-3, 12px);",
            "z-index: 100;",
            "overflow: hidden;",
          ].join(" "),
        },
        panelHeader,
      );

      // Mount the wall component into the panel.
      const aw = createActivityWall({ container: panelEl });
      panelEl.appendChild(aw.element);

      document.body.appendChild(panelEl);
      activityPanel = { element: panelEl, aw };
    },
  }, "activity ▸");

  const missionHeader = el(
    "div",
    {
      "data-testid": "mission-header",
      class: "row",
      style: "padding: var(--sp-2) 0 var(--sp-1) 0;",
    },
    el("span", {
      class: "mono",
      style: "font-size: var(--fs-sm); color: var(--text-muted);",
    }, "Summary board"),
    narratorDot,
    activityToggleBtn,
  );

  // --- permission banner (Task 3.1) — single prompts poll, drives BLOCKED ---
  const banner = createPermissionBanner({
    onPromptsChange: (prompts) => {
      const next = new Set(prompts.map((p) => String(p.lane)));
      blockedLanes = next;
      // Re-evaluate every row's pill (cheap; lane count is small).
      for (const short of Object.keys(rowRefs)) reevaluatePill(short);
    },
  });

  // --- terminal drawer seam (Task 3.3) ---
  // Single-drawer invariant: only one terminal drawer may be open at a time.
  // State lives here in render() so it is scoped to this page instance and
  // is torn down with cleanup().

  /** @type {(() => void)|null} */
  let disposeTerminalDrawer = null;

  /**
   * @type {{ short: string, element: HTMLElement, cleanup: () => void }|null}
   */
  let currentDrawer = null;

  /** Dispose the currently-open drawer, if any. */
  function _closeCurrentDrawer() {
    if (!currentDrawer) return;
    try { currentDrawer.cleanup(); } catch (_) { /* ignore */ }
    if (currentDrawer.element.parentNode) {
      currentDrawer.element.parentNode.removeChild(currentDrawer.element);
    }
    currentDrawer = null;
    disposeTerminalDrawer = null;
  }

  /**
   * Toggle a terminal drawer for the given lane short code.
   * - Same short while open → close (toggle).
   * - Different short while one is open → dispose previous, open new.
   * - Closed → open new.
   * @param {string} short
   */
  function toggleTerminal(short) {
    // Toggle: clicking the same row's button while its drawer is open closes it.
    if (currentDrawer && currentDrawer.short === short) {
      _closeCurrentDrawer();
      return;
    }

    // Dispose any previously-open drawer first (single-drawer invariant).
    if (currentDrawer) {
      _closeCurrentDrawer();
    }

    // Build the pane.
    const pane = createTerminalPane({ lane: short, scrollback: 1000 });

    // Lane display name (fall back to short if not found in config).
    const laneConfig = lanes.find((l) => l.short === short);
    const displayName = laneConfig ? laneConfig.name : short;

    // Close button.
    const closeBtn = el("button", {
      type: "button",
      class: "button",
      "data-testid": "board-drawer-close",
      title: "Close terminal drawer.",
      style: "flex: 0 0 auto;",
    }, "× close");
    closeBtn.addEventListener("click", () => toggleTerminal(short)); // re-entrant toggle

    // Drawer header: lane name + close button.
    const drawerHeader = el(
      "div",
      {
        class: "row",
        style: [
          "gap: var(--sp-2);",
          "align-items: center;",
          "padding: var(--sp-2) var(--sp-3);",
          "border-bottom: 1px solid var(--border);",
          "background: var(--surface-2);",
          "flex: 0 0 auto;",
        ].join(" "),
      },
      el("span", {
        class: "mono",
        style: "flex: 1 1 auto; font-size: var(--fs-sm); color: var(--text);",
      }, displayName),
      closeBtn,
    );

    // Drawer container.
    const drawerEl = el(
      "div",
      {
        "data-testid": "board-drawer",
        "data-board-modal": "true",
        style: [
          "display: flex;",
          "flex-direction: column;",
          "border: 1px solid var(--border);",
          "border-radius: var(--r-1);",
          "background: var(--surface);",
          "margin-top: var(--sp-2);",
          "overflow: hidden;",
          "min-height: 280px;",
        ].join(" "),
      },
      drawerHeader,
      pane.element,
    );

    // Append to the page root so it appears below the rows (not to document.body,
    // to keep it scoped inside the page and avoid z-index / scroll issues).
    root.appendChild(drawerEl);

    currentDrawer = { short, element: drawerEl, cleanup: pane.cleanup };

    // Register teardown so page cleanup() (step 4) tears the drawer down.
    disposeTerminalDrawer = _closeCurrentDrawer;
  }

  // --- rows container ---
  const rowsContainer = el("div", {
    "data-testid": "board-rows",
    class: "stack-1",
  });

  for (const lane of lanes) {
    const refs = buildRow(lane, toggleTerminal);
    rowRefs[lane.short] = refs;
    rowsContainer.appendChild(refs.root);
  }

  // --- page root with the required test sentinel ---
  const page = el(
    "div",
    {
      class: "board-page stack-2",
      "data-testid": "board-page",
    },
    missionHeader,
    banner.element,
    rowsContainer,
  );
  root.appendChild(page);

  /**
   * Re-paint one lane's pill using current blocked/stale sets + narrative state.
   * Enforces BLOCKED > STALE > RUNNING/IDLE. Never overwrites BLOCKED.
   * @param {string} short
   */
  function reevaluatePill(short) {
    const refs = rowRefs[short];
    if (!refs) return;
    const resolved = resolvePill({
      blocked: blockedLanes.has(short),
      stale: staleLanes.has(short),
      state: refs.state,
    });
    paintPill(refs.pill, resolved);
  }

  // --- narrative: initial paint, then SSE ---

  /**
   * Apply a `{ lanes: {...} }` frame: update text cells for each known lane,
   * track narrator_ok, then re-evaluate pills (precedence-aware — SSE state
   * cannot overwrite a BLOCKED lane).
   *
   * `updateStatus` is false for the initial snapshot paint: an empty/partial
   * first snapshot (server not yet ready) must NOT flip the dot green — the dot
   * keeps its constructed state until the first real SSE frame.
   * @param {Record<string, any>} laneMap
   * @param {{ updateStatus?: boolean }} [opts]
   */
  function applyFrame(laneMap, { updateStatus = true } = {}) {
    let anyOk = true;
    for (const [short, payload] of Object.entries(laneMap)) {
      const refs = rowRefs[short];
      if (!refs) continue; // lane not in config — ignore
      applyNarrativeText(refs, payload);
      if (payload && payload.narrator_ok === false) anyOk = false;
      reevaluatePill(short);
    }
    if (updateStatus) setNarratorOk(anyOk);
  }

  // Instant first paint before the first SSE frame. The await above may have
  // been superseded by a newer mount — bail out (C1) before touching the page.
  const initial = await fetchNarrative();
  if (!alive) return () => {};
  applyFrame(initial, { updateStatus: false });

  // Live stream — same uniform `{ lanes: {...} }` shape every frame.
  const es = new EventSource("/api/v1/narrative-stream", { withCredentials: true });

  es.onmessage = (ev) => {
    // Keep-alive comment frames (": ka") never reach onmessage; guard the rest.
    try {
      const data = JSON.parse(ev.data);
      if (data && typeof data.lanes === "object" && data.lanes) {
        applyFrame(data.lanes);
      }
    } catch (_) {
      // malformed event — ignore (mirror terminal_pane.js)
    }
  };

  es.onerror = () => {
    // A queued error callback may fire after cleanup()'s es.close() — bail so we
    // don't paint a detached dot.
    if (!alive) return;
    // Stream disruption: only treat a fully CLOSED stream as narrator-offline.
    if (es.readyState === EventSource.CLOSED) {
      setNarratorOk(false);
    }
  };

  // --- start the banner poll (drives blockedLanes via onPromptsChange) ---
  banner.start();

  // --- stale lanes: initial fetch + poll every 30s ---
  async function pollStale() {
    if (!alive) return;
    const list = await fetchStaleLanes();
    if (!alive) return; // await may resolve after cleanup
    staleLanes = new Set(list.map((s) => String(s.lane)));
    for (const short of Object.keys(rowRefs)) reevaluatePill(short);
  }
  pollStale();
  const staleTimer = setInterval(pollStale, 30_000);

  // --- cleanup (grid teardown contract) ---
  return function cleanup() {
    // 0. Mark dead first so any in-flight awaits / queued callbacks bail out.
    alive = false;
    // 1. Close the EventSource.
    try { es.close(); } catch (_) { /* ignore */ }
    // 2. Stop the stale poll timer.
    clearInterval(staleTimer);
    // 3. Tear down the permission banner (stops its 2s poll).
    try { banner.cleanup(); } catch (_) { /* ignore */ }
    // 4. Dispose any open terminal drawer (Task 3.3 seam).
    if (disposeTerminalDrawer) {
      try { disposeTerminalDrawer(); } catch (_) { /* ignore */ }
      disposeTerminalDrawer = null;
    }
    // 4b. Dispose the activity panel if open (closes its SSE, removes from body).
    if (activityPanel) {
      try { _closeActivityPanel(); } catch (_) { /* ignore */ }
    }
    // 5. Defensive element-only sweep for any body-appended modal. The primary
    //    teardown is step 4's disposer, which closes the terminal pane's SSE +
    //    xterm. This sweep only REMOVES the element — it does NOT call any
    //    cleanup() — so it is a safety net for true body-level modals, not a
    //    substitute for the explicit disposer. The board's drawer is appended to
    //    `root` (not body), so step 6's clearNode handles it; this sweep is for
    //    the contract's sake.
    const orphan = document.body.querySelector('[data-board-modal="true"]');
    if (orphan && orphan.parentNode) orphan.parentNode.removeChild(orphan);
    // NOTE: do NOT clearNode(root) here. app.js clears the mount root before
    // every page render (mountPage); a page cleanup that clears root can wipe a
    // *newer* page when app.js discards a stale render's cleanup — the WebKit
    // back-navigation blank-board bug. Cleanup releases only this page's own
    // resources (timers, EventSources, banner, drawers, body-level modals).
  };
}
