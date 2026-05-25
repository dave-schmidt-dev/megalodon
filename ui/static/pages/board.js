// @ts-check
// pages/board.js — Megalodon "Narrator-Driven Summary Board" (Task 3.2).
//
// The fleet summary view. One row per lane, each carrying:
//   - lane-chip + model/state
//   - a state pill (BLOCKED / STALE / RUNNING / IDLE)
//   - an UNGOVERNED indicator (orthogonal to the pill — see below)
//   - a 3-line Last / Now / Goal block (Now uses the narrator phrase)
//   - tokens
//   - an actions cell (terminal ▸ seam, wired in Task 3.3)
//
// Three data sources at three cadences:
//   1. Narrative — initial GET /api/v1/narrative for instant first paint, then
//      an EventSource("/api/v1/narrative-stream") that pushes the SAME uniform
//      frame shape `{ lanes: { <short>: <per-lane payload>, ... } }`.
//   2. Stale — GET /api/v1/lanes/stale on a 30s poll. The response carries a
//      `stale_lanes` array (→ STALE pill treatment) AND a top-level
//      `governor_blocked` array of lanes the governor is deny-looping
//      (≥5 denies/60s, excluded from stale_lanes server-side) → BLOCKED pill.
//      This single poll is the source of both the stale set and the BLOCKED set.
//
// Pill precedence (CV-8), single explicit rule:
//   BLOCKED (governor deny-loop) > STALE (>threshold silent) > RUNNING/IDLE.
// A governor-blocked lane ALWAYS shows BLOCKED (§8.7 mitigation: a
// governor-stalled lane must not be mis-read as merely stale); the narrative
// SSE handler must NOT overwrite a blocked lane's pill. STALE overlays
// RUNNING/IDLE only when the lane is not governor-blocked.
//
// UNGOVERNED indicator (§3.3): governance is ORTHOGONAL to the pill. The
// per-lane narrative payload carries `governed` (bool) — the provenance of the
// lane's live process under the Claude Code PreToolUse governor hook. A lane
// that is actively running but `governed === false` (a reattached pre-governor
// process, a non-claude harness lane, or a kill-switch-off fleet) gets a
// distinct amber UNGOVERNED chip shown ALONGSIDE its pill (a lane can be
// RUNNING and ungoverned at once). Strict `=== false` + the running-state guard
// avoids false-flagging idle/absent lanes (whose `governed` defaults to false).
//
// Narrator-offline: a status dot turns "offline" when a payload carries
// narrator_ok=false or when the stream closes.
//
// Cleanup (grid teardown contract): close the EventSource, stop the stale
// timer, dispose any open terminal drawer (Task 3.3 seam), and remove any
// body-appended modal. No leaked EventSources/timers.
//
// Page contract: `async render(root, params) -> cleanup` (same as grid.js).
//
// Security: no innerHTML with dynamic data; all values via el() / textContent.

import { loadConfig } from "../js/config.js";
import { mountPage } from "../js/app.js";
import { createTerminalPane } from "../components/terminal_pane.js";
import { createActivityWall } from "../components/activity_wall.js";
import { StaleModal } from "../components/stale_modal.js";

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
 * @typedef {Object} StaleResponse
 * @property {Array<{lane: string, silent_seconds: number|null, last_activity_source: string}>} stale_lanes
 *   lanes silent ≥ threshold (governor-blocked lanes are excluded server-side).
 * @property {Array<{lane: string, deny_count: number, window_seconds: number, last_category: string, last_reason: string}>} governor_blocked
 *   lanes the governor is deny-looping (≥5 denies/60s) → BLOCKED pill.
 */

/**
 * Fetch the /api/v1/lanes/stale response (stale_lanes + governor_blocked).
 * Returns a normalized shape so callers can read both arrays without re-checking.
 * @returns {Promise<StaleResponse>}
 */
async function fetchStaleLanes() {
  try {
    const resp = await fetch("/api/v1/lanes/stale", { credentials: "include" });
    if (!resp.ok) return { stale_lanes: [], governor_blocked: [] };
    const json = await resp.json();
    return {
      stale_lanes: Array.isArray(json.stale_lanes) ? json.stale_lanes : [],
      governor_blocked: Array.isArray(json.governor_blocked) ? json.governor_blocked : [],
    };
  } catch (_) {
    return { stale_lanes: [], governor_blocked: [] };
  }
}

// ---------------------------------------------------------------------------
// Pill rendering
// ---------------------------------------------------------------------------

/**
 * Whether a lane's narrative `state` represents an active/working lane (the
 * "RUNNING" set). Shared by the pill (RUNNING vs IDLE) and the UNGOVERNED
 * indicator (which only surfaces on a running lane) so the two never diverge.
 * @param {string|null|undefined} state
 * @returns {boolean}
 */
function isRunningState(state) {
  const s = String(state || "").toLowerCase();
  return (
    s === "claimed" || s === "running" || s === "active" || s === "working" || s === "in_progress"
  );
}

/**
 * Resolve the effective pill for a lane given precedence:
 *   BLOCKED > STALE > RUNNING/IDLE.
 * @param {{ blocked: boolean, stale: boolean, state: string|null|undefined }} args
 * @returns {{ label: string, kind: "blocked"|"stale"|"running"|"idle" }}
 */
function resolvePill({ blocked, stale, state }) {
  // CR-4: a blocked task (state === "blocked") OR a lane the governor is
  // deny-looping (governor-blocked, from /lanes/stale's governor_blocked list)
  // both surface as BLOCKED. Precedence: BLOCKED > STALE > RUNNING/IDLE.
  if (blocked || String(state || "").toLowerCase() === "blocked") {
    return { label: "BLOCKED", kind: "blocked" };
  }
  if (stale) return { label: "STALE", kind: "stale" };
  // Treat any active/working state as RUNNING; otherwise IDLE.
  return isRunningState(state)
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
  // STALE pill is clickable (opens the staleness modal). Give it a pointer
  // cursor and a helpful title so the affordance is discoverable.
  if (resolved.kind === "stale") {
    pill.style.cursor = "pointer";
    pill.title = "Click to view staleness details";
  } else {
    pill.style.cursor = "";
    pill.title = "";
  }
}

/**
 * Show/hide the UNGOVERNED chip. The chip is rendered ALONGSIDE the pill
 * (governance is orthogonal to the pill kind). Visible iff the lane is in a
 * running state AND its governance is strictly `false` — so a frame missing the
 * `governed` field (or an idle/absent lane, whose `governed` defaults false)
 * never false-flags as ungoverned.
 * @param {HTMLElement} chip
 * @param {{ state: string|null|undefined, governed: boolean|undefined }} args
 */
function paintUngoverned(chip, { state, governed }) {
  const show = governed === false && isRunningState(state);
  chip.style.display = show ? "" : "none";
}

// ---------------------------------------------------------------------------
// Row construction
// ---------------------------------------------------------------------------

/**
 * @typedef {Object} LaneRowRefs
 * @property {HTMLElement} root      the row container
 * @property {HTMLElement} pill      the state pill span
 * @property {HTMLElement} ungovChip the UNGOVERNED indicator chip
 * @property {HTMLElement} lastEl    Last line text node holder
 * @property {HTMLElement} nowEl     Now line text node holder
 * @property {HTMLElement} goalEl    Goal line text node holder
 * @property {HTMLElement} tokensEl  tokens cell
 * @property {string|null|undefined} state  last-known narrative state (for pill re-eval)
 * @property {boolean|undefined} governed  last-known governance provenance (for indicator re-eval)
 */

/**
 * Build a lane row and return refs for in-place updates.
 * @param {{ name: string, short: string }} lane
 * @param {(short: string) => void} onToggleTerminal  called when the terminal button is clicked
 * @param {(short: string) => void} onStalePillClick  called when a STALE pill is clicked
 * @returns {LaneRowRefs}
 */
function buildRow(lane, onToggleTerminal, onStalePillClick) {
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
  // Pill click: when kind === "stale", open the staleness modal for this lane.
  // stopPropagation prevents the row's /lane/:short navigation from firing.
  pill.addEventListener("click", (ev) => {
    if (pill.dataset.pill === "stale") {
      ev.stopPropagation();
      onStalePillClick(short);
    }
  });
  paintPill(pill, { label: "IDLE", kind: "idle" });

  // UNGOVERNED indicator (§3.3) — orthogonal to the pill. Amber warning tone,
  // distinct from the BLOCKED pill's --sev-blocking. Hidden by default; shown
  // only for a running, strictly-ungoverned lane (see paintUngoverned).
  const ungovChip = el("span", {
    class: "badge",
    "data-testid": `board-ungoverned-${short}`,
    title: "Live process is not running under the governor hook.",
    style: [
      "display: none;",
      "font-weight: 600;",
      "letter-spacing: 0.4px;",
      "text-transform: uppercase;",
      "border-width: 1px;",
      "border-style: solid;",
      "background: var(--surface-2);",
      "color: var(--sev-major);",
      "border-color: var(--sev-major);",
    ].join(" "),
  }, "⚠ UNGOV");

  const laneChip = el("span", { class: `lane-chip ${laneName}` }, laneName);

  const headCell = el(
    "div",
    { class: "row", style: "gap: var(--sp-2); min-width: 200px; flex: 0 0 auto;" },
    laneChip,
    pill,
    ungovChip,
  );

  // 3-line Last / Now / Goal block.
  const labelStyle = "color: var(--text-muted); font-size: var(--fs-xs); width: 40px; flex: 0 0 auto;";
  // min-width: 0 lets these flex items shrink below their content's intrinsic
  // width so `.truncate` (overflow:hidden + ellipsis) can clip. Without it the
  // flexbox default min-width:auto pins a long unbroken token (e.g. a finding
  // path in a Now phrase) at full width, widening the row and the whole page.
  const lastEl = el("span", { class: "truncate", style: "flex: 1 1 auto; min-width: 0; color: var(--text-muted);" }, "—");
  const nowEl = el("span", { class: "truncate", style: "flex: 1 1 auto; min-width: 0; color: var(--text);" }, "—");
  const goalEl = el("span", { class: "truncate", style: "flex: 1 1 auto; min-width: 0; color: var(--text-muted);" }, "—");

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

  return { root, pill, ungovChip, lastEl, nowEl, goalEl, tokensEl, state: null, governed: undefined };
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

  // Last from last.phrase (narrator advisory, OQ1), falling back to last.desc
  // when phrase is null (mirrors how Now renders phrase-or-desc). task_id is
  // prefixed only on the deterministic desc fallback, not the narrator phrase.
  if (last && last.phrase) {
    refs.lastEl.textContent = String(last.phrase);
  } else if (last && last.desc) {
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
  // Stash the governance provenance for the UNGOVERNED indicator re-eval. Left
  // undefined when the payload omits the field (so it can't false-flag).
  refs.governed = payload ? payload.governed : undefined;
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
  // queued callbacks bail out instead of writing into a foreign page.
  // cleanup() flips it false.
  let alive = true;

  // --- shared precedence state ---
  /** @type {Record<string, LaneRowRefs>} */
  const rowRefs = {};
  /** @type {Set<string>} lanes in a governor deny-loop (governor_blocked from /lanes/stale) → BLOCKED */
  let blockedLanes = new Set();
  /** @type {Set<string>} lanes flagged stale by /api/v1/lanes/stale */
  let staleLanes = new Set();
  /**
   * Full stale-lane records from the last /api/v1/lanes/stale response.
   * Keyed by lane short code for O(1) lookup when opening the modal.
   * @type {Record<string, {lane: string, silent_seconds: number|null, last_activity_source: string}>}
   */
  let staleData = {};

  // --- CSRF helper (reads the page meta tag) ---
  function _getCsrfToken() {
    const meta = /** @type {HTMLMetaElement|null} */ (
      document.querySelector('meta[name="csrf-token"]')
    );
    return meta ? (meta.getAttribute("content") ?? "") : "";
  }

  // --- toast helper (minimal — same pattern as grid.js) ---
  /** @param {string} message @param {"info"|"error"} [kind] */
  function _showToast(message, kind = "info") {
    const toast = el("div", {
      "data-board-modal": "true",
      style: [
        "position: fixed;",
        "bottom: var(--sp-3, 12px);",
        "right: var(--sp-3, 12px);",
        "z-index: 2000;",
        "padding: 8px 14px;",
        `background: ${kind === "error" ? "var(--sev-blocking)" : "var(--accent)"};`,
        "color: var(--bg);",
        "border-radius: 4px;",
        "font-size: var(--fs-sm);",
        "pointer-events: none;",
      ].join(" "),
    }, message);
    document.body.appendChild(toast);
    setTimeout(() => {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 3500);
  }

  // --- stale modal (single instance, body-appended, data-board-modal for cleanup) ---
  const staleModal = new StaleModal({
    navigate,
    getCsrfToken: _getCsrfToken,
    showToast: _showToast,
    onRefresh: async () => {
      await pollStale();
    },
  });
  staleModal.element.setAttribute("data-board-modal", "true");
  staleModal.element.setAttribute("data-testid", "board-stale-modal");
  document.body.appendChild(staleModal.element);

  /**
   * Open the stale modal for a specific lane (or all stale lanes if short is null).
   * Exported via the pill's click handler.
   * @param {string} short
   */
  function openStaleModal(short) {
    const entry = staleData[short];
    const lanes = entry ? [entry] : Object.values(staleData);
    staleModal.open(lanes);
  }

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
    const refs = buildRow(lane, toggleTerminal, openStaleModal);
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

  /**
   * Re-paint one lane's UNGOVERNED indicator from its current narrative state +
   * governance flag. Orthogonal to the pill: a lane shows it iff it is running
   * AND `governed === false`. Repainted on every frame so it disappears when a
   * lane stops running or becomes governed.
   * @param {string} short
   */
  function reevaluateGovernance(short) {
    const refs = rowRefs[short];
    if (!refs) return;
    paintUngoverned(refs.ungovChip, { state: refs.state, governed: refs.governed });
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
      reevaluateGovernance(short);
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

  // --- stale lanes: initial fetch + poll every 30s ---
  // This single poll drives BOTH the STALE set (stale_lanes) and the BLOCKED
  // set (governor_blocked — lanes the governor is deny-looping). Re-evaluating
  // pills here applies the BLOCKED > STALE > RUNNING/IDLE precedence.
  async function pollStale() {
    if (!alive) return;
    const { stale_lanes: list, governor_blocked: governorBlocked } = await fetchStaleLanes();
    if (!alive) return; // await may resolve after cleanup
    staleLanes = new Set(list.map((s) => String(s.lane)));
    // Governor deny-loop lanes drive the BLOCKED pill.
    blockedLanes = new Set(governorBlocked.map((g) => String(g.lane)));
    // Rebuild the staleData map for the modal.
    staleData = Object.fromEntries(list.map((s) => [String(s.lane), s]));
    for (const short of Object.keys(rowRefs)) reevaluatePill(short);
    // Keep the modal fresh if it is already open (content may change).
    staleModal.update(list);
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
    // 3. Dispose any open terminal drawer (Task 3.3 seam).
    if (disposeTerminalDrawer) {
      try { disposeTerminalDrawer(); } catch (_) { /* ignore */ }
      disposeTerminalDrawer = null;
    }
    // 4b. Dispose the activity panel if open (closes its SSE, removes from body).
    if (activityPanel) {
      try { _closeActivityPanel(); } catch (_) { /* ignore */ }
    }
    // 4c. Close and remove the stale modal (body-appended).
    try { staleModal.close(); } catch (_) { /* ignore */ }
    if (staleModal.element.parentNode) {
      staleModal.element.parentNode.removeChild(staleModal.element);
    }
    // 5. Defensive element-only sweep for any remaining body-appended modals
    //    (e.g. toast notifications). Removes ALL matching elements, not just one.
    //    The stale modal itself is handled explicitly in step 4c; this sweep is
    //    a safety net for toast elements and any other data-board-modal nodes.
    document.body.querySelectorAll('[data-board-modal="true"]').forEach((orphan) => {
      if (orphan.parentNode) orphan.parentNode.removeChild(orphan);
    });
    // NOTE: do NOT clearNode(root) here. app.js clears the mount root before
    // every page render (mountPage); a page cleanup that clears root can wipe a
    // *newer* page when app.js discards a stale render's cleanup — the WebKit
    // back-navigation blank-board bug. Cleanup releases only this page's own
    // resources (timers, EventSources, drawers, body-level modals).
  };
}
