// @ts-check
// pages/mission.js — Megalodon orchestrator-console `/mission` page.
//
// Spec: v9.4 T3.8 — aggregate-endpoint rewrite.
//
// Sections (top → bottom):
//   1. Mission summary card  — id, phase, status from GET /api/v1/state
//   2. Mission events log    — last-50 rows from state.mission.events
//   3. Mission config view   — <details> (collapsed by default) from GET /api/v1/config
//
// Data sources:
//   GET /api/v1/state  → { mission: { id, status, phase, events } }
//   GET /api/v1/config → raw config object shown as formatted JSON
//
// No SSE, no polling, no new backend endpoints.
//
// Security: no innerHTML for any value sourced from the server. All textual
// data flows through textContent / createElement / appendChild / the value
// property.

import { API_STATE, API_CONFIG } from "../js/constants.js";

// ---------------------------------------------------------------------------
// DOM helpers (same pattern used across grid.js / lane_detail.js)
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
      } else if (k === "value") {
        node.value = v;
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

function clearNode(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

// ---------------------------------------------------------------------------
// Fetch helpers
// ---------------------------------------------------------------------------

/**
 * Fetch /api/v1/state and return the parsed JSON body.
 * Throws on network error or non-2xx.
 * @returns {Promise<any>}
 */
async function fetchState() {
  const resp = await fetch(API_STATE, { credentials: "same-origin" });
  if (!resp.ok) throw new Error(`GET ${API_STATE} → HTTP ${resp.status}`);
  return resp.json();
}

/**
 * Fetch /api/v1/config and return the parsed JSON body.
 * Throws on network error or non-2xx.
 * @returns {Promise<any>}
 */
async function fetchConfig() {
  const resp = await fetch(API_CONFIG, { credentials: "same-origin" });
  if (!resp.ok) throw new Error(`GET ${API_CONFIG} → HTTP ${resp.status}`);
  return resp.json();
}

// ---------------------------------------------------------------------------
// Summary card — renders id, phase, status into `container`.
// ---------------------------------------------------------------------------

/**
 * @param {HTMLElement} container
 * @param {{ id?: string, phase?: string, status?: string }} mission
 */
function renderSummaryCard(container, mission) {
  clearNode(container);

  const id = String(mission.id || "—");
  const phase = String(mission.phase || "—");
  const status = String(mission.status || "—");

  container.appendChild(el("h2", { class: "card__title" }, "Mission"));

  container.appendChild(
    el(
      "div",
      { class: "stack-2" },
      // Row 1: id + status badge
      el(
        "div",
        { class: "row", style: "gap: var(--sp-2); align-items: baseline; flex-wrap: wrap;" },
        el(
          "span",
          {
            class: "mono",
            "data-mission-id": id,
            "data-testid": "mission-id",
            title: `Mission id: ${id}`,
          },
          id,
        ),
        el(
          "span",
          {
            class: `badge mission-status mission-status--${status}`,
            "data-testid": "mission-status-badge",
            title: `Mission status: ${status}`,
          },
          status,
        ),
      ),
      // Row 2: phase display
      el(
        "div",
        { class: "stack-1", "data-testid": "current-phase" },
        el(
          "div",
          { class: "mono", style: "font-size: 11px; opacity: 0.7;" },
          "Current phase",
        ),
        el(
          "div",
          {
            class: "phase-display",
            "data-testid": "mission-phase",
            title: `Current phase: ${phase}`,
            style: "font-size: 1.5rem; font-weight: 600;",
          },
          phase,
        ),
      ),
    ),
  );
}

// ---------------------------------------------------------------------------
// Events log — renders last 50 rows from state.mission.events.
// The list is newest-first (server already returns newest-first from
// _read_mission_events_tail). Each row renders the raw line or parsed fields.
// ---------------------------------------------------------------------------

/**
 * @param {HTMLElement} container
 * @param {Array<any>} events
 */
function renderEventsLog(container, events) {
  clearNode(container);
  container.appendChild(el("h2", { class: "card__title" }, "Mission events"));

  if (!Array.isArray(events) || events.length === 0) {
    container.appendChild(
      el(
        "p",
        { class: "empty-state", "data-testid": "mission-events-empty" },
        "No mission events.",
      ),
    );
    return;
  }

  // Outer wrapper provides bounded scroll when many events are present.
  const wrapper = el("div", {
    "data-testid": "mission-events-scroll",
    style: [
      "max-height: 400px;",
      "overflow-y: auto;",
      "overflow-x: hidden;",
    ].join(" "),
  });

  const list = el("ul", {
    class: "stack-1 mission-events-list",
    "data-testid": "mission-events-list",
    style: "list-style: none; padding: 0; margin: 0;",
  });

  for (let i = 0; i < events.length; i++) {
    const ev = events[i];
    // Each event is either a parsed dict (with "raw" fallback) or a raw string.
    let text = "";
    if (ev && typeof ev === "object") {
      if (typeof ev.raw === "string") {
        // Free-form text line (pre-v9.3 format): render verbatim.
        text = ev.raw;
      } else {
        // Structured object: reconstruct a readable line.
        const utc = String(ev.utc || ev.ts || "");
        const from = String(ev.from_phase || ev.from || "");
        const to = String(ev.to_phase || ev.to || "");
        const by = String(ev.by_agent || ev.agent || "");
        const reason = String(ev.reason || "");
        const parts = [];
        if (utc) parts.push(utc);
        if (from && to) parts.push(`${from}->${to}`);
        else if (to) parts.push(to);
        if (by) parts.push(`by ${by}`);
        if (reason) parts.push(`-- ${reason}`);
        text = parts.join(" ");
      }
    } else if (typeof ev === "string") {
      text = ev;
    }

    const li = el(
      "li",
      {
        class: "mission-event mono",
        "data-testid": `mission-event-row-${i}`,
        style: [
          "padding: 3px 0;",
          "border-bottom: 1px solid var(--border, rgba(255,255,255,0.08));",
          "white-space: pre-wrap;",
          "word-break: break-all;",
          "font-size: 0.82rem;",
        ].join(" "),
        title: text || "(empty event)",
      },
      text || "(empty event)",
    );
    list.appendChild(li);
  }

  wrapper.appendChild(list);
  container.appendChild(wrapper);
}

// ---------------------------------------------------------------------------
// Config view — <details> element, collapsed by default.
// ---------------------------------------------------------------------------

/**
 * @param {HTMLElement} container
 * @param {any} config   raw config object from /api/v1/config
 */
function renderConfigView(container, config) {
  clearNode(container);

  // Format config as indented JSON; sanitised by JSON.stringify (no innerHTML).
  let formatted = "";
  try {
    formatted = JSON.stringify(config, null, 2);
  } catch (_) {
    formatted = String(config);
  }

  const pre = el(
    "pre",
    {
      class: "mono",
      "data-testid": "mission-config-json",
      style: [
        "font-size: 0.78rem;",
        "white-space: pre-wrap;",
        "word-break: break-word;",
        "max-height: 480px;",
        "overflow-y: auto;",
        "padding: var(--sp-2);",
        "background: var(--bg-deep, #0d1117);",
        "border-radius: 4px;",
        "margin: 0;",
      ].join(" "),
    },
    formatted,
  );

  const summary = el(
    "summary",
    {
      style: "cursor: pointer; user-select: none;",
      title: "Click to expand or collapse the mission configuration view.",
    },
    el("span", { class: "card__title", style: "display: inline;" }, "Mission config"),
  );

  const details = el(
    "details",
    {
      "data-testid": "mission-config-details",
      // no `open` attribute — starts collapsed
    },
    summary,
    pre,
  );

  container.appendChild(details);
}

// ---------------------------------------------------------------------------
// Error banner
// ---------------------------------------------------------------------------

/**
 * @param {HTMLElement} container
 * @param {string} message
 */
function renderError(container, message) {
  clearNode(container);
  container.appendChild(
    el(
      "p",
      {
        class: "empty-state",
        role: "alert",
        style: "color: var(--danger, #f66);",
      },
      message,
    ),
  );
}

// ---------------------------------------------------------------------------
// Top-level render — contract: async function render(root) → cleanup
// ---------------------------------------------------------------------------

/**
 * Render the mission page into `root`.
 *
 * @param {HTMLElement} root
 * @returns {Promise<() => void>} cleanup function
 */
export async function render(root, _params) {
  // Show a loading skeleton while the two fetches run.
  const skeleton = document.createElement("div");
  skeleton.className = "loading-skeleton";
  skeleton.textContent = "Loading mission…";
  root.appendChild(skeleton);

  // Section containers — built now so we can populate them after fetch.
  const summaryCard = el("section", {
    class: "card stack-2",
    "data-testid": "mission-summary-card",
  });

  const eventsCard = el("section", {
    class: "card stack-1",
    "data-testid": "mission-events-log",
  });

  const configCard = el("section", {
    class: "card",
    "data-testid": "mission-config-card",
  });

  // Kick off both fetches concurrently.
  let stateData = null;
  let configData = null;
  const [stateResult, configResult] = await Promise.allSettled([
    fetchState(),
    fetchConfig(),
  ]);

  if (stateResult.status === "fulfilled") {
    stateData = stateResult.value;
  }
  if (configResult.status === "fulfilled") {
    configData = configResult.value;
  }

  // Clear skeleton and build the page.
  clearNode(root);

  const page = el(
    "div",
    { class: "mission-page stack-3", "data-testid": "mission-page" },
    summaryCard,
    eventsCard,
    configCard,
  );
  root.appendChild(page);

  // Populate summary card.
  if (stateData) {
    const mission = (stateData && stateData.mission) || {};
    renderSummaryCard(summaryCard, mission);
  } else {
    renderError(
      summaryCard,
      `Failed to load mission state: ${stateResult.reason || "unknown error"}`,
    );
  }

  // Populate events log.
  const events = (stateData && stateData.mission && stateData.mission.events) || [];
  renderEventsLog(eventsCard, events);

  // Populate config view.
  if (configData) {
    renderConfigView(configCard, configData);
  } else {
    renderError(
      configCard,
      `Failed to load mission config: ${configResult.reason || "unknown error"}`,
    );
  }

  return function cleanup() {
    clearNode(root);
  };
}

export default render;
