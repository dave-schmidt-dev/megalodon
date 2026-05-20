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
import { loadConfig } from "../js/config.js";

const SVG_NS = "http://www.w3.org/2000/svg";

// LANE_ORDER is loaded from config at render time. This fallback is only used
// if config resolution fails unexpectedly before render proceeds.
const LANE_ORDER_FALLBACK = ["AUDIT", "ARCHITECT", "BACKEND", "FRONTEND", "TEST", "META"];

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

// Parse "52k/200k" or "52000/200000" into [used, total] numbers. Returns null if unparseable.
function parseTokenCtx(tokenCtx) {
  if (!tokenCtx) return null;
  const m = String(tokenCtx).match(/^([\d.]+)(k?)\s*\/\s*([\d.]+)(k?)$/i);
  if (!m) return null;
  const used = parseFloat(m[1]) * (m[2].toLowerCase() === "k" ? 1000 : 1);
  const total = parseFloat(m[3]) * (m[4].toLowerCase() === "k" ? 1000 : 1);
  if (!Number.isFinite(used) || !Number.isFinite(total) || total <= 0) return null;
  return [used, total];
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

// Extract the UTC string from a finding filename (agent-X-L-PHASE-topic-UTC.md).
function utcFromFilename(filename) {
  const m = (filename || "").match(/(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})(?:-(\d{2}))?Z/);
  if (!m) return null;
  return `${m[1]}T${m[2]}-${m[3]}Z`;
}

// S-LIVE-ACTIVITY: fetch per-lane activity summary from BE. Gracefully returns null on 404
// (endpoint may not be implemented yet).
async function fetchActivitySummary(short) {
  try {
    const res = await fetch(`/api/v1/lane/${encodeURIComponent(short)}/activity_summary`);
    if (!res.ok) return null;
    return await res.json();
  } catch (_) {
    return null;
  }
}

// Poll all lanes that have a short code; update store.activitySummaries keyed by lane name.
async function pollActivitySummaries(configByLane) {
  const entries = Object.values(configByLane || {});
  if (entries.length === 0) return;
  const current = store.get("activitySummaries") || {};
  const next = Object.assign({}, current);
  for (const laneConfig of entries) {
    const short = laneConfig?.short;
    const name = laneConfig?.name;
    if (!short || !name) continue;
    const summary = await fetchActivitySummary(short);
    if (summary != null) next[name] = summary;
  }
  store.set("activitySummaries", next);
}

function renderLaneCard(row, expanded, onToggle, configLane, activitySummary) {
  const lane = row.lane;
  const band = stalenessBand(row.staleness_seconds);
  const state = String(row.state || "idle");

  const chip = el("span", { class: `lane-chip ${lane}` }, lane);
  const agent = el("span", { class: "mono" }, String(row.agent || "—"));
  const lastTickDesc = row.last_utc ? `${fmtAge(row.staleness_seconds)} ago` : "never";
  const stateTitle = state === "working"
    ? `Working on ${row.working_task_id || "unknown task"} — last tick ${lastTickDesc}`
    : state === "blocked"
      ? `Blocked — last tick ${lastTickDesc}. May be waiting on a dependency, permission prompt, or tool call.`
      : `Idle since ${lastTickDesc}; no active task`;
  const stateBadge = el(
    "span",
    { class: `badge state-${state}`, dataset: { state }, title: stateTitle },
    state
  );
  const last = el(
    "span",
    {
      class: `staleness ${band}`,
      title: row.last_utc || "",
      "data-testid": "lane-last-tick",
    },
    row.last_utc ? fmtAge(row.staleness_seconds) : "—"
  );
  const notesText = String(row.notes || "");
  const notes = el(
    "div",
    { class: "truncate", title: notesText },
    truncate(notesText, 120) || (row.working_task_id ? `working ${row.working_task_id}` : "—")
  );

  // S-LANE-CARD-DETAILS: default-show model and cadence from mission config.
  const modelText = configLane?.harness?.model ? String(configLane.harness.model).split("/").pop() : null;
  const cadenceMins = configLane?.cadence_seconds ? Math.round(configLane.cadence_seconds / 60) : null;

  const header = el(
    "div",
    { class: "row", style: "justify-content: space-between; align-items: center;" },
    chip,
    stateBadge
  );

  const metaChildren = [agent, last];
  if (modelText) {
    metaChildren.push(el("span", { class: "mono", style: "opacity:0.65; font-size:11px;", "data-testid": "lane-model" }, modelText));
  }
  if (cadenceMins) {
    metaChildren.push(el("span", { class: "mono", style: "opacity:0.65; font-size:11px;" }, `every ${cadenceMins}m`));
  }
  const meta = el(
    "div",
    { class: "row stack-1", style: "gap: var(--sp-3); flex-wrap: wrap;" },
    ...metaChildren
  );

  const toggleBtn = el(
    "button",
    {
      type: "button",
      class: "button",
      "aria-expanded": expanded ? "true" : "false",
      "aria-controls": `lane-drawer-${lane}`,
      "data-testid": `action-toggle-lane-${lane}`,
      title: `${expanded ? "Collapse" : "Expand"} details for ${lane}: model, cadence, current task, notes, and live activity`,
      onclick: (ev) => {
        ev.stopPropagation();
        onToggle(lane);
      },
    },
    expanded ? "Hide details" : "Show details"
  );

  // S-LIVE-ACTIVITY: build activity summary section for the expanded drawer.
  let activitySection = null;
  if (activitySummary) {
    const summaryChildren = [];
    const status = String(activitySummary.status || "unknown");
    const lastMs = parseUtcMillis(activitySummary.last_activity_utc);
    const ageText = Number.isFinite(lastMs) ? fmtAge((Date.now() - lastMs) / 1000) : "—";
    summaryChildren.push(el("div", { class: "row", style: "gap: var(--sp-2); align-items: center; flex-wrap: wrap;" },
      el("span", { class: `badge state-${status}`, "data-testid": "activity-status" }, status),
      el("span", { class: "mono", style: "font-size:11px; opacity:0.7;", "data-testid": "activity-last-tick" }, ageText)
    ));
    if (activitySummary.last_text) {
      summaryChildren.push(el("div", {
        class: "mono truncate",
        style: "font-size:11px;",
        "data-testid": "activity-last-text",
        title: String(activitySummary.last_text),
      }, `Currently: ${truncate(String(activitySummary.last_text), 80)}`));
    }
    const tokens = parseTokenCtx(activitySummary.token_ctx);
    if (tokens) {
      const [used, total] = tokens;
      const pct = Math.min(1, used / total);
      const usedK = Math.round(used / 1000);
      const totalK = Math.round(total / 1000);
      const track = el("div", { style: "height:6px; flex:1; border-radius:3px; background:var(--c-border,#333); overflow:hidden;" });
      const fill = el("div", { style: `height:100%; width:${(pct * 100).toFixed(1)}%; background:var(--c-accent,#4a9eff); border-radius:3px;` });
      track.appendChild(fill);
      summaryChildren.push(el("div", { class: "row", style: "gap:var(--sp-1); align-items:center;", "data-testid": "activity-token-bar" },
        track,
        el("span", { class: "mono", style: "font-size:10px; opacity:0.7; white-space:nowrap;" }, `${usedK}k / ${totalK}k`)
      ));
    }
    activitySection = el("div", {
      class: "stack-1",
      style: "margin-top:var(--sp-1); padding-top:var(--sp-1); border-top:1px solid var(--c-border,#333);",
    }, ...summaryChildren);
  }

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
      configLane?.role ? el("div", { class: "mono", style: "opacity:0.7; font-size:11px;" }, configLane.role) : null,
      el("pre", { class: "mono", style: "white-space: pre-wrap; margin: 0;" }, notesText || "(no notes)"),
      activitySection
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

function renderLaneGrid(container, expanded, onToggle, laneOrder, configByLane) {
  clearNode(container);
  const lanes = store.get("status.lanes") || [];
  const activitySummaries = store.get("activitySummaries") || {};
  for (const lane of (laneOrder || LANE_ORDER_FALLBACK)) {
    // FIX(bug-empty-lane-cards): BE's parse_status emits the SHORT code
    // (`"A"`, `"B"`, …) per the v9 status-row regex, but laneOrder is built
    // from config.lanes[*].name (long names like `"AUDIT"`). Looking up
    // `lanes.find(l => l.lane === "AUDIT")` never matched, so every card
    // fell through to the synthesized placeholder with `agent: "—"` —
    // exactly the "no per-agent activity" symptom the operator was seeing.
    //
    // Resolve via the config map (short ↔ name) to find the row, then
    // re-key `row.lane` to the long name so chip/testid/aria-label/onToggle
    // all use the form e2e tests already expect (lane-row-AUDIT, etc.).
    const configLane = configByLane ? (configByLane[lane] || null) : null;
    const lookupKey = configLane?.short || lane;
    const rawRow = laneRowByLane(lanes, lookupKey);
    const row = { ...rawRow, lane };
    const activitySummary = activitySummaries[lane] || null;
    container.appendChild(renderLaneCard(row, expanded.has(lane), onToggle, configLane, activitySummary));
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

// Convert epoch seconds (float) to the canonical UTC string `YYYY-MM-DDTHH-MMZ`.
function epochToUtc(epochSec) {
  if (!epochSec) return null;
  const d = new Date(epochSec * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}-${pad(d.getUTCMinutes())}Z`;
}

function renderSparkline(container) {
  clearNode(container);
  // Bug-3 fix: agents in /loop mode write findings + claims, not mission events.
  // Merge mission events with findings (UTC from filename) and claims (mtime) as the activity feed.
  const events = store.get("mission.events") || [];
  const findings = store.get("findings.list") || [];
  const claimList = store.get("claims.list") || [];
  const findingEvents = findings.map((f) => ({ utc: utcFromFilename(f.filename) })).filter((e) => e.utc);
  const claimEvents = claimList.map((c) => ({ utc: epochToUtc(c.mtime) })).filter((e) => e.utc);
  const allEvents = [...events, ...findingEvents, ...claimEvents];
  const buckets = bucketCounts(allEvents);
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

// Parse agent/lane/task from a finding filename: agent-{id}-{short}-{phase}-{topic}-{UTC}.md
function parseFilenameFields(filename) {
  const base = (filename || "").replace(/\.md$/, "");
  // Remove the UTC suffix, then split remaining parts
  const noUtc = base.replace(/-?\d{4}-\d{2}-\d{2}T\d{2}-\d{2}(?:-\d{2})?Z$/, "");
  const parts = noUtc.split("-");
  // Expect: agent, {id}, {short/lane}, {phase...}, {topic...}
  const agentId = parts.length >= 2 ? `${parts[0]}-${parts[1]}` : "—";
  const laneShort = parts[2] || "—";
  const phase = parts[3] || "—";
  return { agentId, laneShort, phase };
}

function renderHistoryTail(container) {
  clearNode(container);
  const missionEvents = (store.get("mission.events") || []).slice();
  missionEvents.sort((a, b) => (parseUtcMillis(b?.utc) || 0) - (parseUtcMillis(a?.utc) || 0));

  if (missionEvents.length > 0) {
    const ul = el("ul", { class: "stack-1", style: "list-style: none; padding: 0; margin: 0;" });
    for (const ev of missionEvents.slice(0, 10)) {
      const utc = String(ev.utc || "—");
      const agent = String(ev.agent || ev.from || "—");
      const lane = String(ev.lane || ev.from_lane || "—");
      const task = String(ev.task || ev.task_id || ev.kind || "—");
      const sev = severityOf(ev);
      const li = el("li", { class: "mono truncate", title: `${utc} | ${agent} | ${lane} | ${task}` },
        `${utc} | ${agent} | ${lane} | ${task}`);
      if (sev) {
        li.appendChild(document.createTextNode(" "));
        li.appendChild(el("span", { class: `severity-badge ${sev}` }, sev));
      }
      ul.appendChild(li);
    }
    container.appendChild(ul);
    return;
  }

  // Bug-4 fix: /loop agents write findings, not HISTORY.md. Use findings as proxy.
  const findings = (store.get("findings.list") || []).slice();
  findings.sort((a, b) => {
    const ta = parseUtcMillis(utcFromFilename(a?.filename)) || 0;
    const tb = parseUtcMillis(utcFromFilename(b?.filename)) || 0;
    return tb - ta;
  });
  const recent = findings.slice(0, 10);
  if (recent.length === 0) {
    container.appendChild(el("p", { class: "empty-state" }, "no activity yet — findings will appear here"));
    return;
  }
  const label = el("p", { class: "mono", style: "opacity:0.6; font-size:11px; margin-bottom:4px;" }, "recent findings (proxy for HISTORY)");
  container.appendChild(label);
  const ul = el("ul", { class: "stack-1", style: "list-style: none; padding: 0; margin: 0;" });
  for (const f of recent) {
    const utc = utcFromFilename(f.filename) || "—";
    const { agentId, laneShort, phase } = parseFilenameFields(f.filename);
    const li = el("li", { class: "mono truncate", title: f.filename || "" },
      `${utc} | ${agentId} | ${laneShort} | ${phase}`);
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
          title: `Forces ownership of the stale ${lane} lane back to ORCHESTRATOR. The agent will be told 'STALE-RECLAIMED' on next tick. Use when a lane is hung > 10 min.`,
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
      title: "Confirms the forced reclaim. The lane's current claim will be released and the agent notified on its next tick. The agent may lose in-progress work.",
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

// ---- v9.3 permission-prompt panel ----------------------------------------
//
// Surfaces Claude REPL approval prompts from every lane's pipe-pane stream.
// The BE permission_watcher tails each lane's stream log; the FE polls
// /api/v1/permission_prompts every 2s and renders pending prompts here with
// Approve/Deny buttons that POST to .../permission_prompts/{lane}/respond.
//
// Hidden when no prompts are pending.

async function fetchPermissionPrompts() {
  try {
    const resp = await fetch("/api/v1/permission_prompts", { credentials: "include" });
    if (!resp.ok) return [];
    const json = await resp.json();
    return Array.isArray(json.prompts) ? json.prompts : [];
  } catch (_) {
    return [];
  }
}

async function respondToPrompt(laneShort, action) {
  try {
    const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") || "";
    const resp = await fetch(
      `/api/v1/permission_prompts/${encodeURIComponent(laneShort)}/respond`,
      {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          ...(csrf ? { "X-CSRF-Token": csrf } : {}),
        },
        body: JSON.stringify({ action }),
      }
    );
    return resp.ok;
  } catch (_) {
    return false;
  }
}

async function renderPermissionPanel(container) {
  const prompts = await fetchPermissionPrompts();
  clearNode(container);
  if (prompts.length === 0) {
    container.hidden = true;
    return;
  }
  container.hidden = false;
  const headerRow = el(
    "div",
    { class: "row", style: "gap: var(--sp-2); align-items: center; justify-content: space-between; flex-wrap: wrap;" },
    el("h2", { class: "card__title", style: "color: var(--color-warning, #f59e0b); margin: 0;" },
      `⚠  ${prompts.length} agent${prompts.length === 1 ? "" : "s"} awaiting approval`),
    el("button", {
      type: "button",
      class: "button button--primary",
      "data-testid": "permission-approve-all",
      title: "Approves all pending permission prompts simultaneously. Use when multiple agents are waiting and all commands are safe.",
      onclick: async () => {
        const lanes = prompts.map((p) => p.lane);
        await Promise.all(lanes.map((lane) => respondToPrompt(lane, "approve")));
        await renderPermissionPanel(container);
      },
    }, `Approve all (${prompts.length})`),
  );
  container.appendChild(headerRow);
  const list = el("ul", { class: "stack-1", style: "list-style: none; padding: 0; margin: 0;" });
  for (const p of prompts) {
    const lane = String(p.lane);
    const cmd = String(p.command || "<unknown>");
    const since = String(p.detected_at || "");
    list.appendChild(el(
      "li",
      {
        class: "stack-1",
        "data-testid": `permission-prompt-${lane}`,
        style: "padding: var(--sp-2); border: 1px solid var(--color-border, #444); border-radius: 4px;",
      },
      el("div", { class: "row", style: "gap: var(--sp-2); align-items: center; flex-wrap: wrap;" },
        el("span", { class: `lane-chip ${p.lane_name || lane}` }, String(p.lane_name || lane)),
        el("span", { class: "mono", style: "font-size: 0.85em; color: var(--color-text-muted, #888);" }, since),
      ),
      el("pre", {
        class: "mono",
        style: "white-space: pre-wrap; word-break: break-word; margin: var(--sp-1) 0; font-size: 0.9em; color: var(--color-text, #ddd);",
      }, cmd),
      el("div", { class: "row", style: "gap: var(--sp-2); flex-wrap: wrap;" },
        el("button", {
          type: "button",
          class: "button button--primary",
          "data-testid": `permission-approve-${lane}`,
          title: "Approves this tool-use prompt. The agent's pending action will proceed.",
          onclick: async () => {
            const ok = await respondToPrompt(lane, "approve");
            if (ok) await renderPermissionPanel(container);
          },
        }, "Approve"),
        el("button", {
          type: "button",
          class: "button",
          "data-testid": `permission-approve-remember-${lane}`,
          title: "Approves and remembers this pattern for the session. Future prompts matching the same tool pattern won't require re-approval.",
          onclick: async () => {
            const ok = await respondToPrompt(lane, "approve_remember");
            if (ok) await renderPermissionPanel(container);
          },
        }, "Approve & remember"),
        el("button", {
          type: "button",
          class: "button button--warning",
          "data-testid": `permission-deny-${lane}`,
          title: "Denies this tool-use prompt. The agent will receive an error and may retry or skip the action.",
          onclick: async () => {
            const ok = await respondToPrompt(lane, "deny");
            if (ok) await renderPermissionPanel(container);
          },
        }, "Deny"),
      ),
    ));
  }
  container.appendChild(list);
}

// ---- v9.3 active-claims panel --------------------------------------------
//
// Surfaces claims.list from /api/v1/state. The BE populates this from the
// on-disk claims/ directory; each entry has dirname + mtime + has_done.
// Proves agents are doing work even when STATUS.md hasn't been updated.

function fmtAgo(seconds) {
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h`;
}

function renderClaimsPanel(container) {
  clearNode(container);
  const claims = store.get("claims.list") || [];
  const open = claims.filter((c) => !c.has_done);
  if (open.length === 0) {
    container.hidden = true;
    return;
  }
  container.hidden = false;
  container.appendChild(el("h2", { class: "card__title" }, `Active claims (${open.length})`));
  const list = el("ul", { class: "stack-1", style: "list-style: none; padding: 0; margin: 0;" });
  const now = Date.now() / 1000;
  const sorted = [...open].sort((a, b) => (b.mtime || 0) - (a.mtime || 0));
  for (const c of sorted) {
    const age = c.mtime ? now - c.mtime : 0;
    list.appendChild(el(
      "li",
      {
        class: "row",
        "data-testid": `active-claim-${c.dirname}`,
        style: "gap: var(--sp-2); align-items: center; flex-wrap: wrap; padding: var(--sp-1) 0;",
        title: `Task: ${c.dirname} — claimed ${fmtAgo(age)} ago. Full task ID shown; hover here for details.`,
      },
      el("span", { class: "mono", style: "font-weight: 600;" }, String(c.dirname)),
      el("span", { class: "mono", style: "color: var(--color-text-muted, #888); font-size: 0.85em;" },
        `claimed ${fmtAgo(age)} ago`),
    ));
  }
  container.appendChild(list);
}

// ---- tasks summary panel --------------------------------------------------
//
// Compact per-phase breakdown of open/active/done task counts from
// store.get("tasks.phases"). Requires no new BE work — data is already in
// the state API response. Part of S-HYBRID-DASHBOARD orchestration visibility.

function renderTasksSummary(container) {
  clearNode(container);
  const phases = store.get("tasks.phases") || [];
  if (!Array.isArray(phases) || phases.length === 0) {
    container.hidden = true;
    return;
  }
  const rows = [];
  for (const phase of phases) {
    const tasks = Array.isArray(phase.tasks) ? phase.tasks : [];
    if (tasks.length === 0) continue;
    const open = tasks.filter((t) => t.state === "open").length;
    const claimed = tasks.filter((t) => t.state === "claimed").length;
    const done = tasks.filter((t) => t.state === "done").length;
    rows.push({ name: String(phase.name || "—"), open, claimed, done, total: tasks.length });
  }
  if (rows.length === 0) {
    container.hidden = true;
    return;
  }
  container.hidden = false;
  const list = el("div", { class: "stack-1", style: "font-size:12px;" });
  for (const r of rows) {
    const safeId = r.name.replace(/\s+/g, "-").replace(/[^a-zA-Z0-9-]/g, "");
    const badges = [];
    if (r.claimed > 0) {
      badges.push(el("span", { class: "badge state-working" }, `${r.claimed} active`));
    }
    if (r.open > 0) {
      badges.push(el("span", { class: "mono", style: "opacity:0.6; font-size:11px;" }, `${r.open} open`));
    }
    badges.push(el("span", { class: "mono", style: "opacity:0.45; font-size:11px;" }, `${r.done}/${r.total} done`));
    list.appendChild(el("div", {
      class: "row",
      style: "gap:var(--sp-2); flex-wrap:wrap; align-items:center; padding:var(--sp-1) 0;",
      "data-testid": `tasks-phase-${safeId}`,
    },
      el("span", { class: "mono", style: "min-width:110px; font-size:11px; opacity:0.7;" }, r.name),
      ...badges
    ));
  }
  container.appendChild(list);
}

// ---- phase navigator reconciliation (OW-4 + CR-10) -----------------------
//
// Reconciles the back-compat HTML fallback <li data-testid="phase-segment-*">
// elements against the live config.phases array:
//   - Default phases not in config.phases → hidden (display:none) so existing
//     e2e selectors still find them in the DOM.
//   - config.phases entries absent from the default 10 → new <li> appended in
//     config order, each with data-testid="phase-segment-<PHASE>" and
//     data-phase="<PHASE>".
//
// The navigator is the <ol class="phase-strip"> element in index.html.
// Default phase keys are derived from each <li>'s data-testid attribute by
// stripping the "phase-segment-" prefix; text content is NOT used as the key
// because some labels differ (e.g., "OP-ACK" vs "PHASE-OPERATOR-ACCEPTANCE").

function reconcilePhaseNavigator(configPhases) {
  if (!Array.isArray(configPhases) || configPhases.length === 0) return;

  const navigator = document.querySelector("ol.phase-strip") ||
                    document.querySelector('[data-role="phase-navigator"]') ||
                    document.querySelector("#phase-navigator");
  if (!navigator) return;

  const defaultLis = Array.from(
    navigator.querySelectorAll('li[data-testid^="phase-segment-"]')
  );

  // Build a map from phase key → li for existing default elements.
  // Phase key = data-testid stripped of "phase-segment-" prefix.
  const PREFIX = "phase-segment-";
  const defaultPhaseKeys = new Set();
  for (const li of defaultLis) {
    const testid = li.dataset.testid || li.getAttribute("data-testid") || "";
    const key = testid.startsWith(PREFIX) ? testid.slice(PREFIX.length) : "";
    if (key) {
      defaultPhaseKeys.add(key);
      li.style.display = configPhases.includes(key) ? "" : "none";
    }
  }

  // Append config phases that have no matching default element.
  for (const phase of configPhases) {
    if (defaultPhaseKeys.has(phase)) continue;
    const li = document.createElement("li");
    li.className = "phase-segment";
    li.setAttribute("data-testid", `${PREFIX}${phase}`);
    li.dataset.phase = phase;
    li.textContent = phase;
    navigator.appendChild(li);
  }
}

// ---- top-level render -----------------------------------------------------

export async function render(root) {
  const expanded = new Set(); // lanes whose drawer is open

  // PM-2 mitigation: show a loading skeleton until config resolves.
  const skeletonDiv = document.createElement("div");
  skeletonDiv.className = "loading-skeleton";
  skeletonDiv.textContent = "Loading mission config…";
  root.appendChild(skeletonDiv);

  // Load lane order and phase list from config; fall back to defaults on error.
  let laneOrder = LANE_ORDER_FALLBACK;
  // S-LANE-CARD-DETAILS: map from lane name → config lane object for model/cadence display.
  let configByLane = {};
  try {
    const config = await loadConfig();
    if (Array.isArray(config.lanes) && config.lanes.length > 0) {
      laneOrder = config.lanes.map((l) => (typeof l === "string" ? l : String(l.name || l)));
      for (const laneConfig of config.lanes) {
        if (laneConfig && laneConfig.name) configByLane[laneConfig.name] = laneConfig;
      }
    }
    // OW-4 + CR-10: reconcile phase navigator against config.phases.
    const configPhases = (config.phases || []).map((p) =>
      typeof p === "string" ? p : String(p.name || p)
    );
    reconcilePhaseNavigator(configPhases);
  } catch (err) {
    console.warn("[dashboard] config load failed, using fallback lanes:", err);
  }

  // Clear skeleton before building real page structure.
  clearNode(root);

  // Page skeleton (static structure only — store data goes through textContent).
  const laneGrid = el("section", {
    class: "lane-grid stack-2",
    "aria-label": "Lane status",
    style:
      "display: grid; gap: var(--sp-2); grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));",
  });

  const tasksCard = el("section", { class: "card stack-1", "data-testid": "tasks-summary" },
    el("h2", { class: "card__title" }, "Tasks")
  );
  const tasksSummaryBody = el("div");
  tasksCard.appendChild(tasksSummaryBody);

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

  // v9.3 permission-prompt panel — top of page, hidden when no prompts pending.
  const permissionPanel = el("section", {
    class: "card stack-1",
    "data-testid": "permission-panel",
    hidden: true,
  });

  // v9.3 active-claims panel — shows in-progress task claims even when STATUS.md is stale.
  const claimsPanel = el("section", {
    class: "card stack-1",
    "data-testid": "active-claims-panel",
    hidden: true,
  });

  const page = el("div", { class: "dashboard stack-3" },
    permissionPanel,
    laneGrid,
    claimsPanel,
    tasksCard,
    sparkCard,
    historyCard,
    stalePanel
  );
  root.appendChild(page);

  // Toggle handler — re-renders the grid so only the targeted card's drawer flips.
  const onToggle = (lane) => {
    if (expanded.has(lane)) expanded.delete(lane);
    else expanded.add(lane);
    renderLaneGrid(laneGrid, expanded, onToggle, laneOrder, configByLane);
  };

  // Initial paint.
  renderPermissionPanel(permissionPanel);
  renderLaneGrid(laneGrid, expanded, onToggle, laneOrder, configByLane);
  renderClaimsPanel(claimsPanel);
  renderTasksSummary(tasksSummaryBody);
  renderSparkline(sparkBody);
  renderHistoryTail(historyBody);
  renderStalePanel(stalePanel);

  // Subscriptions. Each call returns an unsubscribe; collect for cleanup.
  const unsubs = [];
  unsubs.push(store.subscribe("status.lanes", () => {
    renderLaneGrid(laneGrid, expanded, onToggle, laneOrder, configByLane);
    renderStalePanel(stalePanel);
  }));
  unsubs.push(store.subscribe("tasks.phases", () => renderTasksSummary(tasksSummaryBody)));
  unsubs.push(store.subscribe("mission.events", () => {
    renderSparkline(sparkBody);
    renderHistoryTail(historyBody);
  }));
  // Bug-3/4 fix: react to findings and claims updates for activity feed.
  unsubs.push(store.subscribe("findings.list", () => {
    renderSparkline(sparkBody);
    renderHistoryTail(historyBody);
  }));
  unsubs.push(store.subscribe("claims.list", () => {
    renderSparkline(sparkBody);
    renderClaimsPanel(claimsPanel);
  }));
  unsubs.push(store.subscribe("mission.phase", () => {
    // Phase changes can affect lane "state" framing; cheap to re-render.
    renderLaneGrid(laneGrid, expanded, onToggle, laneOrder, configByLane);
  }));
  unsubs.push(store.subscribe("ui.controlMode", () => {
    renderStalePanel(stalePanel);
  }));

  // Sparkline window shifts over wall-clock time even without new events.
  const sparkTimer = setInterval(() => renderSparkline(sparkBody), 30_000);

  // S-LIVE-ACTIVITY: poll per-lane activity summaries every 15s; initial fetch is immediate.
  pollActivitySummaries(configByLane);
  const activityTimer = setInterval(() => pollActivitySummaries(configByLane), 15_000);
  unsubs.push(store.subscribe("activitySummaries", () => {
    renderLaneGrid(laneGrid, expanded, onToggle, laneOrder, configByLane);
  }));

  // v9.3: poll permission prompts every 2s.
  const permTimer = setInterval(() => renderPermissionPanel(permissionPanel), 2_000);

  return () => {
    clearInterval(sparkTimer);
    clearInterval(activityTimer);
    clearInterval(permTimer);
    for (const u of unsubs) {
      try { u(); } catch (_) { /* ignore */ }
    }
    clearNode(root);
  };
}

export default render;
