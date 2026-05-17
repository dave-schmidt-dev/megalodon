// @ts-check
// dashboard.js — Megalodon orchestrator-console `/` dashboard page.
//
// Spec: findings/agent-1371-D-P2.5-frontend-plan-v2-2026-05-16T15-45Z.md §3 `/` Dashboard.
//
// Sections (top → bottom):
//   1. Lane grid (6 cards in canonical order)
//   2. Activity sparkline (60-minute buckets, SVG ~200×40)
//   3. Recent HISTORY tail (last 10 mission events, newest first)
//   4. Stale-row warning panel (visible only when any lane is stale)
//
// Reactive: re-renders on store changes to status.lanes, mission.events,
// mission.phase, ui.controlMode. Each subscription is collected and torn down
// by the returned cleanup function.
//
// Security: no innerHTML for any value sourced from the store. All textual
// store data flows through textContent / createElement / appendChild. The
// only innerHTML touch-point is static, hard-coded SVG-attribute defaults
// (and we avoid even that — we use createElementNS exclusively for SVG).

import { store } from "../js/store.js";
import { STALE_THRESHOLD_SECONDS, API_RECLAIM } from "../js/constants.js";

const SVG_NS = "http://www.w3.org/2000/svg";

const LANE_ORDER = ["AUDIT", "ARCHITECT", "BACKEND", "FRONTEND", "TEST", "META"];

// Staleness thresholds in seconds.
const FRESH_MAX = 5 * 60;       // < 5 min → fresh
const STALE_MAX = STALE_THRESHOLD_SECONDS;      // 5–15 min → stale; >15 → dead

// Sparkline geometry.
const SPARK_W = 200;
const SPARK_H = 40;
const SPARK_BUCKETS = 60; // one per minute, last 60 min

// ---- helpers --------------------------------------------------------------

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

function svgEl(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      node.setAttribute(k, String(v));
    }
  }
  return node;
}

function clearNode(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function stalenessBand(seconds) {
  const s = Number.isFinite(seconds) ? Math.max(0, seconds) : 0;
  if (s < FRESH_MAX) return "fresh";
  if (s < STALE_MAX) return "stale";
  return "dead";
}

function fmtAge(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  return `${h}h${m % 60}m ago`;
}

function truncate(str, n) {
  const s = String(str ?? "");
  return s.length > n ? s.slice(0, Math.max(0, n - 1)) + "…" : s;
}

function parseUtcMillis(utc) {
  if (!utc) return NaN;
  // Server emits canonical `2026-05-16T15-45Z` — replace dash-time with colons.
  const m = String(utc).match(/^(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})Z$/);
  const iso = m ? `${m[1]}T${m[2]}:${m[3]}:00Z` : String(utc);
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : NaN;
}

function severityOf(ev) {
  return ev?.severity || ev?.sev || "";
}

// ---- lane grid ------------------------------------------------------------

function renderLaneCard(row, expanded, onToggle) {
  const lane = row.lane;
  const band = stalenessBand(row.staleness_seconds);
  const state = String(row.state || "idle");

  const chip = el("span", { class: `lane-chip ${lane}` }, lane);
  const agent = el("span", { class: "mono" }, String(row.agent || "—"));
  const stateBadge = el(
    "span",
    { class: `badge state-${state}`, dataset: { state } },
    state
  );
  const last = el(
    "span",
    {
      class: `staleness ${band}`,
      title: row.last_utc || "",
      "data-testid": "last-utc",
    },
    row.last_utc ? `${row.last_utc} (${fmtAge(row.staleness_seconds)})` : "—"
  );
  const notesText = String(row.notes || "");
  const notes = el(
    "div",
    { class: "truncate", title: notesText },
    truncate(notesText, 120) || (row.working_task_id ? `working ${row.working_task_id}` : "—")
  );

  const header = el(
    "div",
    { class: "row", style: "justify-content: space-between; align-items: center;" },
    chip,
    stateBadge
  );
  const meta = el(
    "div",
    { class: "row stack-1", style: "gap: var(--sp-3); flex-wrap: wrap;" },
    agent,
    last
  );

  const toggleBtn = el(
    "button",
    {
      type: "button",
      class: "button",
      "aria-expanded": expanded ? "true" : "false",
      "aria-controls": `lane-drawer-${lane}`,
      "data-testid": `action-toggle-lane-${lane}`,
      onclick: (ev) => {
        ev.stopPropagation();
        onToggle(lane);
      },
    },
    expanded ? "Hide details" : "Show details"
  );

  const drawer = el(
    "div",
    {
      class: "lane-drawer",
      hidden: !expanded,
      dataset: { testid: `lane-drawer-${lane}` },
      "data-testid": `lane-drawer-${lane}`,
      id: `lane-drawer-${lane}`,
      role: "region",
      "aria-label": `${lane} details`,
    },
    el("div", { class: "stack-1" },
      el("div", { class: "mono" }, `task: ${row.working_task_id || "—"}`),
      el("pre", { class: "mono", style: "white-space: pre-wrap; margin: 0;" }, notesText || "(no notes)")
    )
  );

  const isStale = !!row.is_stale || band !== "fresh";
  const card = el(
    "article",
    {
      class: "card stack-2",
      dataset: { testid: `lane-row-${lane}`, stale: isStale ? "true" : "false" },
      "data-testid": `lane-row-${lane}`,
      "data-stale": isStale ? "true" : "false",
      tabindex: "0",
      onclick: () => onToggle(lane),
      onkeydown: (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          onToggle(lane);
        }
      },
    },
    header,
    meta,
    notes,
    toggleBtn,
    drawer
  );

  return card;
}

function laneRowByLane(lanes, lane) {
  const found = (lanes || []).find((l) => l && l.lane === lane);
  if (found) return found;
  // Synthesize an "unknown" placeholder so the 6-card grid stays stable.
  return {
    lane,
    agent: "—",
    state: "idle",
    last_utc: "",
    notes: "",
    staleness_seconds: 0,
    is_stale: false,
    working_task_id: "",
  };
}

function renderLaneGrid(container, expanded, onToggle) {
  clearNode(container);
  const lanes = store.get("status.lanes") || [];
  for (const lane of LANE_ORDER) {
    const row = laneRowByLane(lanes, lane);
    container.appendChild(renderLaneCard(row, expanded.has(lane), onToggle));
  }
}

// ---- sparkline ------------------------------------------------------------

function bucketCounts(events) {
  const now = Date.now();
  const buckets = new Array(SPARK_BUCKETS).fill(0);
  for (const ev of events || []) {
    const t = parseUtcMillis(ev?.utc);
    if (!Number.isFinite(t)) continue;
    const ageMin = Math.floor((now - t) / 60000);
    if (ageMin < 0 || ageMin >= SPARK_BUCKETS) continue;
    // Bucket 0 (leftmost) = oldest; bucket SPARK_BUCKETS-1 = newest.
    const idx = SPARK_BUCKETS - 1 - ageMin;
    buckets[idx] += 1;
  }
  return buckets;
}

function renderSparkline(container) {
  clearNode(container);
  const events = store.get("mission.events") || [];
  const buckets = bucketCounts(events);
  const peak = buckets.reduce((m, v) => (v > m ? v : m), 0);
  const total = buckets.reduce((a, b) => a + b, 0);

  if (total === 0) {
    container.appendChild(el("p", { class: "empty-state" }, "no activity yet"));
    return;
  }

  const svg = svgEl("svg", {
    width: SPARK_W,
    height: SPARK_H,
    viewBox: `0 0 ${SPARK_W} ${SPARK_H}`,
    role: "img",
    "aria-label": `Activity over last 60 minutes: ${total} events, peak ${peak}/min`,
  });

  const barW = SPARK_W / SPARK_BUCKETS;
  for (let i = 0; i < SPARK_BUCKETS; i++) {
    const v = buckets[i];
    if (v <= 0) continue;
    const h = peak > 0 ? Math.max(1, Math.round((v / peak) * (SPARK_H - 2))) : 0;
    const rect = svgEl("rect", {
      x: (i * barW).toFixed(2),
      y: (SPARK_H - h).toFixed(2),
      width: Math.max(1, barW - 1).toFixed(2),
      height: h,
      fill: "currentColor",
      opacity: "0.85",
    });
    svg.appendChild(rect);
  }
  container.appendChild(svg);

  const caption = el(
    "div",
    { class: "mono", style: "font-size: 11px; opacity: 0.7;" },
    `${total} events · peak ${peak}/min · 60m window`
  );
  container.appendChild(caption);
}

// ---- history tail ---------------------------------------------------------

function renderHistoryTail(container) {
  clearNode(container);
  const events = (store.get("mission.events") || []).slice();
  events.sort((a, b) => {
    const ta = parseUtcMillis(a?.utc) || 0;
    const tb = parseUtcMillis(b?.utc) || 0;
    return tb - ta;
  });
  const recent = events.slice(0, 10);
  if (recent.length === 0) {
    container.appendChild(el("p", { class: "empty-state" }, "no HISTORY entries yet"));
    return;
  }
  const ul = el("ul", { class: "stack-1", style: "list-style: none; padding: 0; margin: 0;" });
  for (const ev of recent) {
    const utc = String(ev.utc || "—");
    const agent = String(ev.agent || ev.from || "—");
    const lane = String(ev.lane || ev.from_lane || "—");
    const task = String(ev.task || ev.task_id || ev.kind || "—");
    const sev = severityOf(ev);
    const li = el(
      "li",
      { class: "mono truncate", title: `${utc} | ${agent} | ${lane} | ${task} | ${sev}` },
      `${utc} | ${agent} | ${lane} | ${task}`
    );
    if (sev) {
      li.appendChild(document.createTextNode(" "));
      li.appendChild(el("span", { class: `severity-badge ${sev}` }, sev));
    }
    ul.appendChild(li);
  }
  container.appendChild(ul);
}

// ---- stale-row panel ------------------------------------------------------

async function reclaimLane(lane) {
  const toast = document.getElementById("toast-region");
  try {
    const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") || "";
    const res = await fetch(API_RECLAIM, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(csrf ? { "X-CSRF-Token": csrf } : {}),
      },
      body: JSON.stringify({ lane }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  } catch (err) {
    if (toast) toast.textContent = `Reclaim ${lane} failed: ${String(err.message || err)}`;
  }
}

// Module-level pending-reclaim state — set by action-reclaim click, executed by confirm-reclaim click.
let _pendingReclaimLane = null;

function isLaneStale(l) {
  if (!l) return false;
  if (l.is_stale === true) return true;
  return stalenessBand(l.staleness_seconds) !== "fresh";
}

function renderStalePanel(container) {
  clearNode(container);
  const lanes = store.get("status.lanes") || [];
  const stale = lanes.filter(isLaneStale);
  if (stale.length === 0) {
    container.hidden = true;
    return;
  }
  container.hidden = false;
  const controlMode = !!store.get("ui.controlMode");

  container.appendChild(el("h2", { class: "card__title" }, "Stale lanes"));
  const list = el("ul", { class: "stack-1", style: "list-style: none; padding: 0; margin: 0;" });
  for (const row of stale) {
    const lane = String(row.lane);
    const items = [
      el("span", { class: `lane-chip ${lane}` }, lane),
      el("span", { class: "mono" }, String(row.agent || "—")),
      el("span", { class: `staleness ${stalenessBand(row.staleness_seconds)}` },
        fmtAge(row.staleness_seconds)),
    ];
    if (controlMode) {
      items.push(el(
        "button",
        {
          type: "button",
          class: "button button--primary",
          "data-testid": `action-reclaim-${lane}`,
          onclick: () => {
            _pendingReclaimLane = lane;
            const confirmBtn = document.querySelector('[data-testid="confirm-reclaim"]');
            if (confirmBtn) {
              confirmBtn.hidden = false;
              confirmBtn.textContent = `Confirm reclaim ${lane}`;
            }
          },
        },
        "Reclaim"
      ));
    }
    list.appendChild(el(
      "li",
      { class: "row", style: "gap: var(--sp-2); align-items: center; flex-wrap: wrap;" },
      ...items
    ));
  }
  // Single confirm-reclaim button (always rendered; hidden until an action-reclaim is clicked).
  const confirmBtn = el(
    "button",
    {
      type: "button",
      class: "button button--warning",
      "data-testid": "confirm-reclaim",
      hidden: !_pendingReclaimLane,
      onclick: async () => {
        const lane = _pendingReclaimLane;
        if (!lane) return;
        _pendingReclaimLane = null;
        confirmBtn.hidden = true;
        await reclaimLane(lane);
      },
    },
    _pendingReclaimLane ? `Confirm reclaim ${_pendingReclaimLane}` : "Confirm reclaim"
  );
  container.appendChild(list);
  container.appendChild(confirmBtn);
}

// ---- top-level render -----------------------------------------------------

export function render(root) {
  const expanded = new Set(); // lanes whose drawer is open

  // Page skeleton (static structure only — store data goes through textContent).
  const laneGrid = el("section", {
    class: "lane-grid stack-2",
    "aria-label": "Lane status",
    style:
      "display: grid; gap: var(--sp-2); grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));",
  });

  const sparkCard = el("section", { class: "card stack-1" },
    el("h2", { class: "card__title" }, "Activity (last 60 min)")
  );
  const sparkBody = el("div", { "data-testid": "activity-sparkline" });
  sparkCard.appendChild(sparkBody);

  const historyCard = el("section", { class: "card stack-1", "data-testid": "history-tail" },
    el("h2", { class: "card__title" }, "Recent HISTORY")
  );
  const historyBody = el("div");
  historyCard.appendChild(historyBody);

  const stalePanel = el("section", {
    class: "card stack-1",
    "data-testid": "stale-row-panel",
    hidden: true,
  });

  const page = el("div", { class: "dashboard stack-3" },
    laneGrid,
    sparkCard,
    historyCard,
    stalePanel
  );
  root.appendChild(page);

  // Toggle handler — re-renders the grid so only the targeted card's drawer flips.
  const onToggle = (lane) => {
    if (expanded.has(lane)) expanded.delete(lane);
    else expanded.add(lane);
    renderLaneGrid(laneGrid, expanded, onToggle);
  };

  // Initial paint.
  renderLaneGrid(laneGrid, expanded, onToggle);
  renderSparkline(sparkBody);
  renderHistoryTail(historyBody);
  renderStalePanel(stalePanel);

  // Subscriptions. Each call returns an unsubscribe; collect for cleanup.
  const unsubs = [];
  unsubs.push(store.subscribe("status.lanes", () => {
    renderLaneGrid(laneGrid, expanded, onToggle);
    renderStalePanel(stalePanel);
  }));
  unsubs.push(store.subscribe("mission.events", () => {
    renderSparkline(sparkBody);
    renderHistoryTail(historyBody);
  }));
  unsubs.push(store.subscribe("mission.phase", () => {
    // Phase changes can affect lane "state" framing; cheap to re-render.
    renderLaneGrid(laneGrid, expanded, onToggle);
  }));
  unsubs.push(store.subscribe("ui.controlMode", () => {
    renderStalePanel(stalePanel);
  }));

  // Sparkline window shifts over wall-clock time even without new events.
  const sparkTimer = setInterval(() => renderSparkline(sparkBody), 30_000);

  return () => {
    clearInterval(sparkTimer);
    for (const u of unsubs) {
      try { u(); } catch (_) { /* ignore */ }
    }
    clearNode(root);
  };
}

export default render;
