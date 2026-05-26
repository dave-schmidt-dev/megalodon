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
import { authedFetch, probeReauthOn401 } from "../js/auth.js";
import { createTerminalPane } from "../components/terminal_pane.js";
import {
  createActivityWall,
  activityWallShouldDefaultOpen,
  persistActivityWallOpenState,
} from "../components/activity_wall.js";
import { StaleModal } from "../components/stale_modal.js";
import { showConfirmModal } from "../components/confirm_modal.js";
import { createAlertBanner } from "../components/alert_banner.js";
import { controlEnabled, onControlMode } from "../js/store.js";

// Deny-loop threshold: a lane retrying ≥ this many times against the governor is
// "stuck" (distinct from a single BLOCKED state). Mirrors the BE deny-loop
// detection window (≥5 denies). Used for the DENY-LOOP per-lane badge and the
// aggregate alarm's deny-loop count.
const DENY_LOOP_THRESHOLD = 5;

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
    // authedFetch awaits the first-load auth exchange (bug #1) and surfaces the
    // re-auth modal on 401 (bug #2) before we fall back to an empty map.
    const resp = await authedFetch("/api/v1/narrative");
    if (!resp.ok) return {};
    const json = await resp.json();
    return json && typeof json.lanes === "object" && json.lanes ? json.lanes : {};
  } catch (_) {
    return {};
  }
}

/**
 * Fetch the UNGATED /api/status (STATUS.md rows) as a reliable baseline for the
 * board (bug #3). This endpoint survives the auth race (it is not session-gated)
 * so we can seed each lane's state/last from the durable STATUS.md state even
 * before the narrator cache warms up or when the narrator is in demo mode —
 * blank-IDLE is then distinguishable from genuinely-no-data.
 *
 * Returns a map keyed by BOTH the STATUS.md "lane" value (e.g. "LANE-A") and,
 * when resolvable, the short code, so the caller can match either.
 * @returns {Promise<Array<{lane: string, agent: string, state: string, last_utc: string, notes: string}>>}
 */
async function fetchStatusBaseline() {
  try {
    const resp = await fetch("/api/status", { credentials: "same-origin" });
    if (!resp.ok) return [];
    const json = await resp.json();
    return Array.isArray(json) ? json : [];
  } catch (_) {
    return [];
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
    const resp = await authedFetch("/api/v1/lanes/stale");
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

/**
 * Show/hide the liveness pill (DEAD / EXITED), orthogonal to the state pill —
 * a lane's process can be DEAD while STATUS.md still shows a stale "working".
 * "dead" → red DEAD pill (the process crashed/vanished, invisible until now).
 * "exited" → muted EXITED pill (the process left cleanly). "running"/"unknown"
 * (or a missing field) → nothing.
 * @param {HTMLElement} pill
 * @param {string|null|undefined} liveness
 */
function paintLiveness(pill, liveness) {
  const v = String(liveness || "").toLowerCase();
  if (v === "dead") {
    pill.textContent = "DEAD";
    pill.dataset.liveness = "dead";
    pill.style.background = "var(--sev-blocking)";
    pill.style.color = "var(--bg)";
    pill.style.borderColor = "var(--sev-blocking)";
    pill.style.display = "";
  } else if (v === "exited") {
    pill.textContent = "EXITED";
    pill.dataset.liveness = "exited";
    pill.style.background = "var(--surface-2)";
    pill.style.color = "var(--text-muted)";
    pill.style.borderColor = "var(--border)";
    pill.style.display = "";
  } else {
    pill.dataset.liveness = v || "unknown";
    pill.style.display = "none";
  }
}

/**
 * Show/hide the DENY-LOOP per-lane badge. A lane stuck retrying against the
 * governor (consecutive_denies ≥ threshold) is distinct from a one-shot BLOCKED:
 * it is actively burning denies in a loop. Visible iff denies ≥ threshold.
 * @param {HTMLElement} badge
 * @param {number} denies
 */
function paintDenyLoop(badge, denies) {
  const n = Number(denies) || 0;
  if (n >= DENY_LOOP_THRESHOLD) {
    badge.textContent = `⟳ DENY×${n}`;
    badge.title = `Lane is stuck retrying against the governor (${n} consecutive denies).`;
    badge.style.display = "";
  } else {
    badge.style.display = "none";
  }
}

// ---------------------------------------------------------------------------
// Row construction
// ---------------------------------------------------------------------------

/**
 * @typedef {Object} LaneRowRefs
 * @property {HTMLElement} root      the row container
 * @property {HTMLElement} pill      the state pill span
 * @property {HTMLElement} ungovChip the UNGOVERNED indicator chip
 * @property {HTMLElement} livePill  the DEAD/EXITED liveness pill
 * @property {HTMLElement} denyBadge the DENY-LOOP badge
 * @property {HTMLElement} lastEl    Last line text node holder
 * @property {HTMLElement} nowEl     Now line text node holder
 * @property {HTMLElement} goalEl    Goal line text node holder
 * @property {HTMLElement} tokensEl  tokens cell
 * @property {string|null|undefined} state  last-known narrative state (for pill re-eval)
 * @property {boolean|undefined} governed  last-known governance provenance (for indicator re-eval)
 * @property {string|null|undefined} liveness  last-known process liveness (for the DEAD/EXITED pill)
 * @property {number} denies  last-known consecutive_denies (for the DENY-LOOP badge)
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

  // Liveness pill (DEAD / EXITED) — orthogonal to the state pill. Hidden unless
  // liveness is "dead" or "exited". A DEAD lane was invisible before this; the
  // red pill makes process death obvious within the board's poll cadence.
  const livePill = el("span", {
    class: "badge",
    "data-testid": `board-liveness-${short}`,
    title: "Lane process liveness (independent of the reported task state).",
    style: [
      "display: none;",
      "font-weight: 700;",
      "letter-spacing: 0.4px;",
      "text-transform: uppercase;",
      "border-width: 1px;",
      "border-style: solid;",
    ].join(" "),
  });

  // DENY-LOOP badge — lane stuck retrying against the governor. Distinct from
  // BLOCKED (a one-shot deny vs an active retry loop). Hidden unless denies ≥ threshold.
  const denyBadge = el("span", {
    class: "badge",
    "data-testid": `board-denyloop-${short}`,
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
  });

  const laneChip = el("span", { class: `lane-chip ${laneName}` }, laneName);

  const headCell = el(
    "div",
    { class: "row", style: "gap: var(--sp-2); min-width: 200px; flex: 0 0 auto; flex-wrap: wrap;" },
    laneChip,
    pill,
    livePill,
    denyBadge,
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

  return {
    root, pill, ungovChip, livePill, denyBadge,
    lastEl, nowEl, goalEl, tokensEl,
    state: null, governed: undefined, liveness: undefined, denies: 0,
  };
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
  // A real narrative phrase clears any "narrator warming up…" baseline marker
  // (bug #3 overlay): drop the italic + baseline flag so the live phrase reads
  // as authoritative.
  if (now && (now.phrase || now.desc)) {
    refs.nowEl.textContent = String(now.phrase || now.desc);
    refs.nowEl.style.fontStyle = "";
    delete refs.nowEl.dataset.baseline;
  } else if (refs.nowEl.dataset.baseline === "true") {
    // Keep the baseline "warming up" hint rather than overwriting it with "—".
    /* leave as-is */
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
  // Stash liveness for the DEAD/EXITED pill re-eval. Mirrors `governed` — comes
  // on the same per-lane narrative payload. Left undefined when absent.
  refs.liveness = payload ? payload.liveness : undefined;
}

/**
 * Map a STATUS.md `state` cell (e.g. "idle", "working: T2", "blocked") to the
 * narrative-style state vocabulary the pill resolver understands. STATUS.md is
 * the durable on-disk truth, so this is what the board shows as a baseline
 * before (or instead of) narrator phrases.
 * @param {string|null|undefined} statusState
 * @returns {string} narrative-style state
 */
function statusStateToNarrative(statusState) {
  const s = String(statusState || "").trim().toLowerCase();
  if (!s) return "open";
  if (s.startsWith("working")) return "claimed"; // "working: T2" → RUNNING
  if (s === "blocked") return "blocked";
  if (s === "idle" || s === "done" || s === "open") return s === "idle" ? "open" : s;
  return s;
}

/**
 * Format an ISO-8601Z timestamp as a short relative/absolute label for the
 * baseline "Last" line. Falls back to the raw string on parse failure.
 * @param {string} iso
 * @returns {string}
 */
function formatBaselineLast(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return `last seen ${d.toLocaleString()}`;
  } catch (_) {
    return iso;
  }
}

/**
 * Whether a STATUS.md note is inter-lane coordination-signal routing chatter
 * (``[SIG ...]`` / ``SIG-FROM-LANE-X: ...``) rather than a description of the
 * lane's current work. Mirrors board_state._is_signal_note (I3) so the board
 * never renders a routing signal as the Now/Goal line.
 * @param {string|null|undefined} note
 * @returns {boolean}
 */
function isSignalNote(note) {
  return /(?:^\s*\[?\s*SIG\b|\bSIG-FROM-LANE-)/i.test(String(note || ""));
}

/**
 * Seed a row from a STATUS.md baseline record (bug #3). This populates state +
 * Last so an empty/warming narrator does not render bare "—"/IDLE for a lane
 * that STATUS.md says is actually working. Narrative frames overlay this later.
 *
 * B2: a lane STATUS reports ``working: <id>`` should show BASIC progress in the
 * Now line WITHOUT waiting on the LLM narrator. Use the clean STATUS note (or,
 * when the note is a routing signal / empty, the state string itself) as the
 * baseline Now line instead of a permanent "narrator warming up…". A genuinely
 * idle/unknown lane still gets the warming-up hint. The narrative frame (which
 * carries the resolved task DESCRIPTION + any narrator phrase) overlays this.
 * @param {LaneRowRefs} refs
 * @param {{state: string, last_utc: string, agent: string, notes: string}} row
 */
function applyStatusBaseline(refs, row) {
  refs.state = statusStateToNarrative(row.state);
  const lastLabel = formatBaselineLast(row.last_utc);
  // Only fill cells the narrator has not already populated (text still "—").
  if (refs.lastEl.textContent === "—" || refs.lastEl.textContent === "") {
    refs.lastEl.textContent = lastLabel || "—";
  }
  if (refs.nowEl.textContent === "—" || refs.nowEl.textContent === "") {
    const stateStr = String(row.state || "").trim();
    const isWorking = stateStr.toLowerCase().startsWith("working");
    const note = String(row.notes || "").trim();
    const cleanNote = isSignalNote(note) ? "" : note;
    if (isWorking) {
      // Show real progress from STATUS, not "warming up". Prefer the clean note,
      // else the state string (e.g. "working: P4-A"). Mark as baseline so a
      // later narrative phrase/desc still overlays it.
      refs.nowEl.textContent = cleanNote || stateStr;
      refs.nowEl.style.fontStyle = "";
      refs.nowEl.dataset.baseline = "true";
    } else {
      // Lane is idle/non-working per STATUS.md (now=None / no active task).
      // Show a neutral placeholder — "narrator warming up…" only makes sense
      // for an ACTIVELY-working lane whose narrator hasn't spoken yet.
      // Fix: only set "warming up" when the lane appears to be running but the
      // narrator hasn't delivered a phrase yet; for a genuinely idle lane use
      // a neutral dash so the board never says "warming up…" indefinitely.
      refs.nowEl.textContent = "— idle";
      refs.nowEl.style.fontStyle = "italic";
      refs.nowEl.dataset.baseline = "true";
    }
  }
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

  /** Record an explicit operator close so the wall stays closed next mount (I1). */
  function _persistActivityWallClosed() {
    try { persistActivityWallOpenState(false); } catch (_) { /* ignore */ }
  }

  /** Dispose the activity panel if open (cleanup SSE then remove element). */
  function _closeActivityPanel() {
    if (!activityPanel) return;
    try { activityPanel.aw.cleanup(); } catch (_) { /* ignore */ }
    if (activityPanel.element.parentNode) {
      activityPanel.element.parentNode.removeChild(activityPanel.element);
    }
    activityPanel = null;
    // Release the right gutter reserved for the open panel (see _openActivityPanel).
    try { page.style.paddingRight = ""; } catch (_) { /* ignore */ }
  }

  /**
   * Open the activity-wall panel (mount the wall + SSE). No-op if already open.
   * Extracted from the toggle's onclick so it can be invoked both on click AND
   * programmatically on mount (I1: honour the persisted "default open" choice).
   * Persists "open" via the component's own mount-time persistence.
   */
  function _openActivityPanel() {
    if (activityPanel) return;

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
          // `top` is set dynamically below to sit BELOW the mission-header row,
          // so the auto-opened panel never covers the activity toggle / kill-
          // switch / nav (front-door fix). Fallback 96px = header(56)+nav(40).
          "top: 96px;",
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

    // Front-door fix: anchor the panel's top to the BOTTOM of the mission-header
    // row so the fixed panel never overlaps the activity toggle / kill-switch
    // (both right-aligned in missionHeader) or the nav above it. Without this,
    // an auto-opened panel at a fixed top-right would intercept clicks on those
    // controls exactly like the old alert-banner overlay did.
    try {
      const r = missionHeader.getBoundingClientRect();
      const top = Math.max(96, Math.round(r.bottom) + 8);
      panelEl.style.top = `${top}px`;
      panelEl.style.height = `calc(100vh - ${top + 24}px)`;
    } catch (_) { /* keep the static fallback top */ }

    // The panel is a fixed top-right overlay; without reserving space it would
    // sit OVER the right edge of every lane row and intercept clicks on the
    // row's right-aligned controls (terminal toggle, STALE pill). Reserve a
    // right gutter on the board page so the rows reflow narrower and their
    // controls stay left of (and clickable beside) the panel. Removed on close.
    try { page.style.paddingRight = "352px"; } catch (_) { /* ignore */ }
  }

  // Activity toggle button — placed in the header, built before missionHeader so
  // the onclick closure can reference _closeActivityPanel / _openActivityPanel.
  // Fix R3-6: add aria-expanded and toggle label text to reflect open/closed state.
  const activityToggleBtn = el("button", {
    type: "button",
    class: "button",
    "data-testid": "board-activity-toggle",
    "aria-expanded": "false",
    title: "Toggle activity wall.",
    onclick: () => {
      if (activityPanel) {
        // Closing via the toggle is an explicit operator choice — persist it so
        // the wall stays closed next mount (I1). The component only persists
        // "open" on mount; it cannot tell a toggle-close from a navigate-away
        // teardown, so board.js records the explicit close here.
        _persistActivityWallClosed();
        _closeActivityPanel();
        activityToggleBtn.setAttribute("aria-expanded", "false");
        activityToggleBtn.textContent = "activity ▸";
        return;
      }
      _openActivityPanel();
      activityToggleBtn.setAttribute("aria-expanded", "true");
      activityToggleBtn.textContent = "activity ▾";
    },
  }, "activity ▸");

  // --- kill-switch ("Stop fleet") — control-mode gated + confirm modal ---
  // DELETE /api/v1/fleet stops ALL lanes and shuts the server down. Disabled in
  // read-only mode (the default). Requires a confirm before firing.
  const killBtn = /** @type {HTMLButtonElement} */ (el("button", {
    type: "button",
    class: "button",
    "data-testid": "board-kill-switch",
    style: [
      "flex: 0 0 auto;",
      "border-color: var(--sev-blocking);",
      "color: var(--sev-blocking);",
    ].join(" "),
  }, "■ Stop fleet"));

  async function handleKill() {
    if (!controlEnabled()) {
      _showToast("Read-only mode — enable Control mode to stop the fleet", "error");
      return;
    }
    const ok = await showConfirmModal({
      title: "Stop the entire fleet?",
      message:
        "This stops ALL lanes and shuts the orchestrator down. " +
        "This cannot be undone from the UI.",
      confirmLabel: "Stop ALL lanes",
      cancelLabel: "Cancel",
    });
    if (!ok) return;
    killBtn.disabled = true;
    try {
      const csrf = _getCsrfToken();
      const resp = await authedFetch("/api/v1/fleet", {
        method: "DELETE",
        headers: { ...(csrf ? { "X-CSRF-Token": csrf } : {}) },
      });
      if (resp.ok) {
        _showToast("Fleet stopping — orchestrator shutting down", "info");
      } else {
        _showToast(`Stop fleet failed — HTTP ${resp.status}`, "error");
        killBtn.disabled = !controlEnabled();
      }
    } catch (err) {
      _showToast(`Network error — ${String(err)}`, "error");
      killBtn.disabled = !controlEnabled();
    }
  }
  killBtn.addEventListener("click", handleKill);

  /** Apply control-mode posture to the kill-switch. */
  function applyKillControl(on) {
    killBtn.dataset.readonlyGated = on ? "false" : "true";
    killBtn.disabled = !on;
    killBtn.title = on
      ? "Stop ALL lanes (DELETE /api/v1/fleet). Requires confirmation."
      : "Enable Control mode to act.";
    killBtn.style.opacity = on ? "" : "0.5";
    killBtn.style.cursor = on ? "pointer" : "not-allowed";
  }
  const unsubKillControl = onControlMode(applyKillControl);

  const missionHeader = el(
    "div",
    {
      "data-testid": "mission-header",
      class: "row",
      style: "padding: var(--sp-2) 0 var(--sp-1) 0;",
    },
    el("span", {
      class: "mono",
      style: "font-size: var(--fs-sm); color: var(--text-muted); flex: 0 0 auto;",
    }, "Summary board"),
    narratorDot,
    el("span", { style: "flex: 1 1 auto;" }),
    activityToggleBtn,
    killBtn,
  );

  // --- aggregate alarm strip ---
  // Summarizes BLOCKED / STALE / UNGOVERNED / DEAD / DENY-LOOP counts across the
  // fleet. Shown prominently (red) when any CRITICAL count (dead/blocked/
  // deny-loop) > 0; hidden when all-clear. Also drives document.title.
  const alarmStrip = el("div", {
    "data-testid": "board-alarm-strip",
    role: "status",
    "aria-live": "polite",
    style: [
      "display: none;",
      "align-items: center;",
      "gap: var(--sp-3);",
      "padding: var(--sp-2) var(--sp-3);",
      "border-radius: var(--r-1);",
      "border: 1px solid var(--sev-blocking);",
      "background: color-mix(in srgb, var(--sev-blocking) 14%, var(--surface));",
      "font-family: ui-monospace, SFMono-Regular, Menlo, monospace;",
      "font-size: var(--fs-sm);",
      "flex-wrap: wrap;",
    ].join(" "),
  });

  const alarmTitle = el("span", {
    style: "font-weight: 700; text-transform: uppercase; letter-spacing: 0.4px; color: var(--sev-blocking); flex: 0 0 auto;",
  }, "⚠ FLEET ALARM");

  /**
   * Build a labelled count chip. Returns the chip + a setter that updates the
   * number and hides the chip when zero.
   * @param {string} label
   * @param {string} testid
   * @param {string} color
   */
  function makeCountChip(label, testid, color) {
    const numEl = el("span", { style: `font-weight: 700; color: ${color};` }, "0");
    const chip = el(
      "span",
      {
        "data-testid": testid,
        style: "display: none; align-items: center; gap: 4px; flex: 0 0 auto; color: var(--text-muted);",
      },
      numEl,
      el("span", { style: "text-transform: uppercase; letter-spacing: 0.3px; font-size: var(--fs-xs);" }, label),
    );
    return {
      chip,
      set(n) {
        numEl.textContent = String(n);
        chip.style.display = n > 0 ? "inline-flex" : "none";
      },
    };
  }

  const cBlocked = makeCountChip("blocked", "alarm-count-blocked", "var(--sev-blocking)");
  const cDead = makeCountChip("dead", "alarm-count-dead", "var(--sev-blocking)");
  const cDeny = makeCountChip("deny-loop", "alarm-count-denyloop", "var(--sev-major)");
  const cStale = makeCountChip("stale", "alarm-count-stale", "var(--stale-stale)");
  const cUngov = makeCountChip("ungoverned", "alarm-count-ungoverned", "var(--sev-major)");

  alarmStrip.appendChild(alarmTitle);
  alarmStrip.appendChild(cBlocked.chip);
  alarmStrip.appendChild(cDead.chip);
  alarmStrip.appendChild(cDeny.chip);
  alarmStrip.appendChild(cStale.chip);
  alarmStrip.appendChild(cUngov.chip);

  // Original document title, restored when all-clear.
  const _baseTitle = document.title;

  /**
   * Recompute the aggregate alarm from current per-lane indicator state. Counts:
   *   BLOCKED   — governor deny-loop OR blocked state (the BLOCKED pill set)
   *   STALE     — lanes flagged stale
   *   UNGOVERNED— running + governed === false
   *   DEAD      — liveness === "dead"
   *   DENY-LOOP — consecutive_denies ≥ threshold
   * CRITICAL = dead + blocked + deny-loop. The strip shows + title gets an (N)
   * prefix iff CRITICAL > 0; otherwise the strip hides and the title is restored.
   */
  function recomputeAlarm() {
    let blocked = 0, dead = 0, deny = 0, stale = 0, ungov = 0;
    for (const short of Object.keys(rowRefs)) {
      const refs = rowRefs[short];
      if (blockedLanes.has(short)) blocked++;
      if (staleLanes.has(short)) stale++;
      if (String(refs.liveness || "").toLowerCase() === "dead") dead++;
      if ((Number(refs.denies) || 0) >= DENY_LOOP_THRESHOLD) deny++;
      if (refs.governed === false && isRunningState(refs.state)) ungov++;
    }
    cBlocked.set(blocked);
    cDead.set(dead);
    cDeny.set(deny);
    cStale.set(stale);
    cUngov.set(ungov);

    const critical = dead + blocked + deny;
    alarmStrip.style.display = critical > 0 ? "flex" : "none";
    document.title = critical > 0 ? `(${critical}) ${_baseTitle}` : _baseTitle;
  }

  // --- alert banner (polls GET /api/v1/alerts) ---
  // Front-door fix: the banner stack renders IN-FLOW inside the board page,
  // inserted below the mission-header controls + alarm strip (see page
  // composition), NOT as a body-level fixed overlay. The old fixed
  // top-right overlay physically covered and intercepted clicks on the
  // `activity ▸` toggle, the mission/approval-rules nav links, and the
  // kill-switch. An in-flow stack can never overlap the header chrome.
  const alertBanner = createAlertBanner({ onNavigate: navigate });

  async function pollAlerts() {
    if (!alive) return;
    try {
      const resp = await authedFetch("/api/v1/alerts");
      if (!alive) return;
      if (!resp.ok) return;
      const json = await resp.json();
      const alerts = json && Array.isArray(json.alerts) ? json.alerts : [];
      alertBanner.update(alerts);
    } catch (_) {
      /* transient — next poll retries */
    }
  }

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
    alarmStrip,
    alertBanner.element,
    rowsContainer,
  );
  root.appendChild(page);

  // I1: honour the persisted open/closed choice. The activity wall is the
  // "see what agents are doing" surface; default to OPEN when no preference is
  // stored, and re-open it on mount whenever the operator's last choice was
  // open (or unset). Closing it via the toggle persists "closed" so it stays
  // closed next mount. This uses the same open path the toggle uses.
  if (activityWallShouldDefaultOpen()) {
    _openActivityPanel();
    activityToggleBtn.setAttribute("aria-expanded", "true");
    activityToggleBtn.textContent = "activity ▾";
  }

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
    // Liveness pill (DEAD/EXITED) rides the same narrative payload as governance,
    // so re-paint it on every frame too.
    paintLiveness(refs.livePill, refs.liveness);
    recomputeAlarm();
  }

  /**
   * Re-paint one lane's DENY-LOOP badge from its current consecutive_denies
   * (sourced from /api/v1/lanes/stale governor_blocked). Distinct from BLOCKED.
   * @param {string} short
   */
  function reevaluateDenyLoop(short) {
    const refs = rowRefs[short];
    if (!refs) return;
    paintDenyLoop(refs.denyBadge, refs.denies);
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

  /**
   * Seed every row from the UNGATED /api/status baseline (bug #3) so the board
   * shows STATUS.md's durable state/last instead of a uniform blank-IDLE while
   * the narrator cache is empty/warming. Narrative frames overlay this. Matches
   * a status row to a config lane by short code OR full lane name (STATUS.md's
   * "Lane" column is the full name, e.g. "LANE-A").
   * @param {Array<{lane: string, agent: string, state: string, last_utc: string, notes: string}>} rows
   */
  function applyBaseline(rows) {
    const byKey = {};
    for (const r of rows) byKey[String(r.lane).toUpperCase()] = r;
    for (const lane of lanes) {
      const refs = rowRefs[lane.short];
      if (!refs) continue;
      const row =
        byKey[String(lane.short).toUpperCase()] || byKey[String(lane.name).toUpperCase()];
      if (!row) continue;
      applyStatusBaseline(refs, row);
      reevaluatePill(lane.short);
      reevaluateGovernance(lane.short);
    }
  }

  // Instant first paint before the first SSE frame. The await above may have
  // been superseded by a newer mount — bail out (C1) before touching the page.
  // Fetch the ungated baseline AND the gated narrative in parallel; apply the
  // baseline first so the narrative (when present) overlays it.
  const [baseline, initial] = await Promise.all([
    fetchStatusBaseline(),
    fetchNarrative(),
  ]);
  if (!alive) return () => {};
  applyBaseline(baseline);
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
      // Probe whether the close was a 401 (session expired / server restart) and
      // surface the shared re-auth modal so the board can recover instead of
      // sitting silently offline forever (audit bug #2).
      probeReauthOn401("/api/v1/narrative-stream");
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
    // Capture consecutive_denies per lane for the DENY-LOOP badge + alarm.
    // Reset every lane's deny count first, then set the lanes reported.
    for (const short of Object.keys(rowRefs)) rowRefs[short].denies = 0;
    for (const g of governorBlocked) {
      const refs = rowRefs[String(g.lane)];
      if (refs) refs.denies = Number(g.consecutive_denies) || 0;
    }
    for (const short of Object.keys(rowRefs)) {
      reevaluatePill(short);
      reevaluateDenyLoop(short);
    }
    recomputeAlarm();
    // Keep the modal fresh if it is already open (content may change).
    staleModal.update(list);
  }
  pollStale();
  const staleTimer = setInterval(pollStale, 30_000);

  // --- alert banner: initial fetch + poll on the board's ~30s cadence ---
  pollAlerts();
  const alertTimer = setInterval(pollAlerts, 30_000);

  // Initial alarm paint (rows start all-clear; this hides the strip + keeps the
  // base title until the first indicator arrives).
  recomputeAlarm();

  // --- cleanup (grid teardown contract) ---
  return function cleanup() {
    // 0. Mark dead first so any in-flight awaits / queued callbacks bail out.
    alive = false;
    // 1. Close the EventSource.
    try { es.close(); } catch (_) { /* ignore */ }
    // 2. Stop the stale poll timer.
    clearInterval(staleTimer);
    // 2b. Stop the alert poll timer + tear down the alert banner; unsubscribe
    //     the kill-switch control-mode listener; restore the document title.
    clearInterval(alertTimer);
    try { unsubKillControl(); } catch (_) { /* ignore */ }
    try { alertBanner.cleanup(); } catch (_) { /* ignore */ }
    try { document.title = _baseTitle; } catch (_) { /* ignore */ }
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
