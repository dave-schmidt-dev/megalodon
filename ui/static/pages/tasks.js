// @ts-check
// tasks.js — /tasks page for the Megalodon orchestrator console.
//
// Spec:
//   - findings/agent-1371-D-P2.5-frontend-plan-v2-2026-05-16T15-45Z.md §C4
//   - findings/agent-1371-D-P1-frontend-plan-2026-05-16T1532Z.md §3 /tasks
//
// Sections rendered (top → bottom):
//   1. Phase tab bar (4 tabs; default = store.mission.phase)
//   2. Kanban grouped by lane (6 columns)
//   3. Non-canonical claims panel (collapsible)
//   4. CROSS task pool
//
// Hard rules followed here:
//   - NO innerHTML (security-hook gated). textContent + createElement only.
//   - No external deps.
//   - Module exports a single render(root) → cleanup function.
//   - Subscribes to: tasks.phases, tasks.cross, mission.phase, claims
//
// Task shape (per spec / store.js):
//   { task_id|id, lane, description, claim_state, claim_agent?, claim_utc?,
//     finding_filename?, phase? }

import { store } from "../js/store.js";
import { loadConfig } from "../js/config.js";

const PHASE_TABS = [
  { id: "PHASE-PLAN", label: "Plan" },
  { id: "PHASE-CHALLENGE", label: "Challenge" }, // covers P2 + P2.5
  { id: "PHASE-BUILD", label: "Build" },
  { id: "PHASE-VERIFY", label: "Verify" },
];

// Fallback lane list used until config resolves (v9.0 back-compat).
const LANES_FALLBACK = ["AUDIT", "ARCHITECT", "BACKEND", "FRONTEND", "TEST", "META"];

/** Return the canonical id for a task (spec says task_id; store may use id). */
function taskKey(t) {
  return t && (t.task_id || t.id || "") + "";
}

/** Slugify a non-canonical claim dirname for use in data-testid. */
function slugify(s) {
  return String(s || "")
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/** Truncate a long string for visual display while keeping the full text in title. */
function truncate(s, n) {
  const str = String(s || "");
  if (str.length <= n) return str;
  return str.slice(0, n - 1) + "…";
}

/** Cross-check a task's claim_state against claims/<id>/ existence. */
function indicatorState(task, claimsSet) {
  const tid = taskKey(task);
  const state = task.claim_state || "open";
  // If we have no claims signal at all, skip the cross-check.
  if (!claimsSet) {
    if (state === "claimed") return { kind: "claimed", symbol: "○", color: "var(--accent)" };
    if (state === "done") return { kind: "done", symbol: "✓", color: "var(--stale-fresh)" };
    return { kind: "open", symbol: "·", color: "var(--text-muted)" };
  }
  const claimDirExists = claimsSet.has(tid);
  if (state === "claimed" || state === "done") {
    if (claimDirExists) {
      return { kind: "green", symbol: "✓", color: "var(--stale-fresh)" };
    }
    return { kind: "red", symbol: "✗", color: "var(--sev-blocking)" };
  }
  // open
  return { kind: "open", symbol: "·", color: "var(--text-muted)" };
}

/** Compute the set of non-canonical claim dirs (dirs not matching any TASKS id). */
function computeNonCanonical(claimsList, allTaskIds) {
  if (!Array.isArray(claimsList)) return [];
  const taskIdSet = new Set(allTaskIds);
  const out = [];
  for (const c of claimsList) {
    if (!c) continue;
    const dirname = typeof c === "string" ? c : (c.dirname || c.name || c.id || "");
    if (!dirname) continue;
    if (!taskIdSet.has(dirname)) {
      out.push({
        dirname,
        mtime: typeof c === "object" ? (c.mtime || c.utc || "") : "",
      });
    }
  }
  return out;
}

/** Make a DOM element with attrs + textContent. No innerHTML. */
function el(tag, opts) {
  const node = document.createElement(tag);
  if (opts) {
    if (opts.class) node.className = opts.class;
    if (opts.text != null) node.textContent = String(opts.text);
    if (opts.title) node.setAttribute("title", String(opts.title));
    if (opts.testid) node.setAttribute("data-testid", String(opts.testid));
    if (opts.attrs) {
      for (const k of Object.keys(opts.attrs)) {
        node.setAttribute(k, String(opts.attrs[k]));
      }
    }
  }
  return node;
}

/** Build one task card. Returns the card element. */
function renderTaskCard(task, indicator, onClick, drawerOpenFor) {
  const tid = taskKey(task);
  const card = el("article", {
    class: "card",
    testid: `task-card-${tid}`,
    attrs: { tabindex: "0", role: "button", "aria-label": `Task ${tid}` },
  });

  // Header row: id + lane chip
  const head = el("div", { class: "row" });
  head.appendChild(el("span", { class: "mono", text: tid, attrs: { style: "font-weight:600" } }));
  if (task.lane) {
    head.appendChild(el("span", { class: `lane-chip ${task.lane}`, text: task.lane }));
  }
  // Claim-state indicator (right-aligned)
  const spacer = el("span", { attrs: { style: "flex:1 1 auto" } });
  head.appendChild(spacer);
  const ind = el("span", {
    class: "badge",
    testid: `task-claim-indicator-${tid}`,
    text: indicator.symbol,
    attrs: {
      "data-claim-state": indicator.kind,
      "aria-label": `claim state: ${indicator.kind}`,
      style: `color:${indicator.color}; font-weight:700`,
    },
  });
  head.appendChild(ind);
  card.appendChild(head);

  // Description (truncated; full in title)
  const desc = task.description || "(no description)";
  const descNode = el("p", {
    class: "truncate text-muted",
    text: truncate(desc, 110),
    title: desc,
    attrs: { style: "margin-top:var(--sp-2)" },
  });
  card.appendChild(descNode);

  // Claim metadata
  if (task.claim_agent || task.claim_utc) {
    const meta = el("p", {
      class: "text-muted mono",
      attrs: { style: "font-size:var(--fs-xs); margin-top:var(--sp-1)" },
    });
    const agent = task.claim_agent ? String(task.claim_agent) : "?";
    const utc = task.claim_utc ? String(task.claim_utc) : "?";
    meta.textContent = `${agent} @ ${utc}`;
    card.appendChild(meta);
  }

  // Finding link
  if (task.finding_filename) {
    const linkRow = el("p", { attrs: { style: "margin-top:var(--sp-1)" } });
    const a = el("a", {
      text: truncate(task.finding_filename, 60),
      title: task.finding_filename,
      attrs: { href: `/findings#${task.finding_filename}` },
    });
    linkRow.appendChild(a);
    card.appendChild(linkRow);
  }

  // Drawer (full description + claim history)
  const isOpen = drawerOpenFor === tid;
  const drawer = el("div", {
    testid: `task-drawer-${tid}`,
    attrs: {
      "aria-expanded": isOpen ? "true" : "false",
      style: `margin-top:var(--sp-2); padding:var(--sp-2); background:var(--surface-2); border-radius:var(--r-1); display:${isOpen ? "block" : "none"}`,
    },
  });
  if (isOpen) {
    drawer.appendChild(el("p", {
      attrs: { style: "white-space:pre-wrap" },
      text: desc,
    }));
    const history = el("ul", { attrs: { style: "margin-top:var(--sp-2)" } });
    const items = [
      `claim_state: ${task.claim_state || "open"}`,
      task.claim_agent ? `claim_agent: ${task.claim_agent}` : "",
      task.claim_utc ? `claim_utc: ${task.claim_utc}` : "",
      task.phase ? `phase: ${task.phase}` : "",
    ].filter(Boolean);
    for (const item of items) {
      history.appendChild(el("li", { class: "mono text-muted", text: item }));
    }
    drawer.appendChild(history);
  }
  card.appendChild(drawer);

  // Card click toggles drawer
  card.addEventListener("click", (ev) => {
    // Allow link clicks to behave normally.
    if (ev.target && (ev.target instanceof HTMLAnchorElement)) return;
    onClick(tid);
  });
  card.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      onClick(tid);
    }
  });

  return card;
}

/** Build the phase tab bar. */
function renderPhaseTabs(selectedPhase, onSelect) {
  const bar = el("div", {
    class: "row",
    attrs: {
      role: "tablist",
      "aria-label": "Phase",
      style: "margin-bottom:var(--sp-4)",
    },
  });
  for (const tab of PHASE_TABS) {
    const isActive = tab.id === selectedPhase;
    const btn = el("button", {
      class: "button" + (isActive ? " button--primary" : ""),
      text: tab.label,
      testid: `phase-tab-${tab.id}`,
      attrs: {
        role: "tab",
        "aria-selected": isActive ? "true" : "false",
        "data-phase": tab.id,
      },
    });
    btn.addEventListener("click", () => onSelect(tab.id));
    bar.appendChild(btn);
  }
  return bar;
}

/** Build the kanban for a phase. */
function renderKanban(tasksForPhase, claimsSet, openDrawerId, onCardClick, lanes) {
  const laneList = lanes || LANES_FALLBACK;
  const wrap = el("section", { attrs: { "aria-label": "Kanban" } });
  const grid = el("div", {
    attrs: {
      style: `display:grid; grid-template-columns:repeat(${laneList.length}, minmax(0,1fr)); gap:var(--sp-3)`,
    },
  });
  const byLane = {};
  for (const lane of laneList) byLane[lane] = [];
  for (const t of (tasksForPhase || [])) {
    const lane = (t.lane || "").toUpperCase();
    if (byLane[lane]) byLane[lane].push(t);
  }
  for (const lane of laneList) {
    const col = el("div", { class: "stack-2", attrs: { "data-lane": lane } });
    const header = el("h3", {
      class: `lane-chip ${lane}`,
      text: `${lane} (${byLane[lane].length})`,
      attrs: { style: "width:100%; justify-content:flex-start" },
    });
    col.appendChild(header);
    if (byLane[lane].length === 0) {
      const empty = el("p", { class: "empty-state", text: "No tasks" });
      col.appendChild(empty);
    } else {
      for (const task of byLane[lane]) {
        const ind = indicatorState(task, claimsSet);
        col.appendChild(renderTaskCard(task, ind, onCardClick, openDrawerId));
      }
    }
    grid.appendChild(col);
  }
  wrap.appendChild(grid);
  return wrap;
}

/** Build the non-canonical claims collapsible panel. */
function renderNonCanonicalPanel(rows, expanded, onToggle) {
  const panel = el("section", {
    class: "card",
    testid: "panel-non-canonical-claims",
    attrs: {
      style: "margin-top:var(--sp-4)",
      "aria-expanded": expanded ? "true" : "false",
      "data-testid-legacy": "non-canonical-claims-panel",
    },
  });

  const header = el("button", {
    class: "row",
    attrs: {
      type: "button",
      "aria-controls": "non-canonical-claims-body",
      "aria-expanded": expanded ? "true" : "false",
      style: "width:100%; justify-content:space-between; cursor:pointer",
    },
  });
  const title = el("span", {
    text: `Non-canonical claims (potential protocol drift) — ${rows.length}`,
    attrs: { style: "font-weight:600" },
  });
  const chev = el("span", {
    class: "text-muted mono",
    text: expanded ? "▼" : "▶",
  });
  header.appendChild(title);
  header.appendChild(chev);
  header.addEventListener("click", onToggle);
  panel.appendChild(header);

  const body = el("div", {
    attrs: {
      id: "non-canonical-claims-body",
      style: `margin-top:var(--sp-2); display:${expanded ? "block" : "none"}`,
    },
  });
  if (rows.length === 0) {
    body.appendChild(el("p", {
      class: "empty-state",
      text: "No non-canonical claim directories detected.",
    }));
  } else {
    const list = el("ul", { class: "stack-1" });
    for (const r of rows) {
      const slug = slugify(r.dirname);
      const li = el("li", {
        class: "row",
        testid: `non-canonical-claim-${slug}`,
        attrs: {
          style: "padding:var(--sp-2); background:var(--surface-2); border-radius:var(--r-1)",
        },
      });
      li.appendChild(el("span", {
        class: "badge",
        text: "?",
        attrs: { style: "color:var(--text-muted)" },
      }));
      li.appendChild(el("span", { class: "mono", text: r.dirname }));
      if (r.mtime) {
        li.appendChild(el("span", {
          class: "text-muted mono",
          attrs: { style: "font-size:var(--fs-xs); margin-left:auto" },
          text: r.mtime,
        }));
      }
      list.appendChild(li);
    }
    body.appendChild(list);
  }
  panel.appendChild(body);
  return panel;
}

/** Build the CROSS task pool section. */
function renderCrossPool(crossTasks, claimsSet, openDrawerId, onCardClick) {
  const section = el("section", { attrs: { style: "margin-top:var(--sp-4)" } });
  const h = el("h2", {
    text: `CROSS task pool (${(crossTasks || []).length})`,
    attrs: { style: "font-size:var(--fs-lg); margin-bottom:var(--sp-2)" },
  });
  section.appendChild(h);
  if (!crossTasks || crossTasks.length === 0) {
    section.appendChild(el("p", { class: "empty-state", text: "No CROSS tasks." }));
    return section;
  }
  const grid = el("div", {
    attrs: {
      style: "display:grid; grid-template-columns:repeat(auto-fill, minmax(280px,1fr)); gap:var(--sp-3)",
    },
  });
  for (const t of crossTasks) {
    const ind = indicatorState(t, claimsSet);
    grid.appendChild(renderTaskCard(t, ind, onCardClick, openDrawerId));
  }
  section.appendChild(grid);
  return section;
}

/**
 * Mount the /tasks page into the given root.
 * @param {HTMLElement} root
 * @returns {Promise<() => void>} cleanup
 */
export async function render(root) {
  // PM-2 mitigation: show a loading skeleton until config resolves.
  const skeletonDiv = document.createElement("div");
  skeletonDiv.className = "loading-skeleton";
  skeletonDiv.textContent = "Loading mission config…";
  root.appendChild(skeletonDiv);

  // Load lane order from config; fall back to defaults on error.
  let lanes = LANES_FALLBACK;
  try {
    const config = await loadConfig();
    if (Array.isArray(config.lanes) && config.lanes.length > 0) {
      lanes = config.lanes.map((l) => (typeof l === "string" ? l : String(l.name || l)));
    }
  } catch (err) {
    console.warn("[tasks] config load failed, using fallback lanes:", err);
  }

  // Clear skeleton before building real page structure.
  while (root.firstChild) root.removeChild(root.firstChild);

  // View-local UI state. Persists across re-renders within this mount.
  const ui = {
    selectedPhase: store.get("mission.phase") || PHASE_TABS[1].id, // default to CHALLENGE if unset
    openDrawerId: null,
    panelExpanded: false,
  };

  function buildClaimsSet() {
    const claims = store.get("claims");
    if (!claims) return null; // signal "no cross-check available"
    const list = Array.isArray(claims) ? claims : (claims.list || claims.dirs || []);
    if (!Array.isArray(list)) return null;
    const set = new Set();
    for (const c of list) {
      if (!c) continue;
      const d = typeof c === "string" ? c : (c.dirname || c.name || c.id || "");
      if (d) set.add(d);
    }
    return set;
  }

  function allTaskIdsForCrosscheck() {
    const ids = [];
    const phases = store.get("tasks.phases") || {};
    for (const k of Object.keys(phases)) {
      for (const t of (phases[k] || [])) ids.push(taskKey(t));
    }
    for (const t of (store.get("tasks.cross") || [])) ids.push(taskKey(t));
    return ids.filter(Boolean);
  }

  function rawClaimsList() {
    const claims = store.get("claims");
    if (!claims) return [];
    if (Array.isArray(claims)) return claims;
    return claims.list || claims.dirs || [];
  }

  function onCardClick(tid) {
    ui.openDrawerId = ui.openDrawerId === tid ? null : tid;
    rerender();
  }

  function onTogglePanel() {
    ui.panelExpanded = !ui.panelExpanded;
    rerender();
  }

  function onSelectPhase(phaseId) {
    ui.selectedPhase = phaseId;
    ui.openDrawerId = null;
    rerender();
  }

  function rerender() {
    while (root.firstChild) root.removeChild(root.firstChild);

    const claimsSet = buildClaimsSet();
    const phases = store.get("tasks.phases") || {};
    const tasksForPhase = phases[ui.selectedPhase] || [];
    const crossTasks = store.get("tasks.cross") || [];

    // Phase header
    const hdr = el("header", { attrs: { style: "margin-bottom:var(--sp-3)" } });
    hdr.appendChild(el("h1", {
      text: "Tasks",
      attrs: { style: "font-size:var(--fs-xl); margin-bottom:var(--sp-2)" },
    }));
    hdr.appendChild(renderPhaseTabs(ui.selectedPhase, onSelectPhase));
    root.appendChild(hdr);

    // Kanban — pass config-driven lane list so columns match the loaded config.
    root.appendChild(renderKanban(tasksForPhase, claimsSet, ui.openDrawerId, onCardClick, lanes));

    // Non-canonical panel
    const ncRows = computeNonCanonical(rawClaimsList(), allTaskIdsForCrosscheck());
    root.appendChild(renderNonCanonicalPanel(ncRows, ui.panelExpanded, onTogglePanel));

    // CROSS pool
    root.appendChild(renderCrossPool(crossTasks, claimsSet, ui.openDrawerId, onCardClick));
  }

  // Initial render
  rerender();

  // Subscribe to relevant slices; each callback triggers a re-render.
  const unsubs = [
    store.subscribe("tasks.phases", rerender),
    store.subscribe("tasks.cross", rerender),
    store.subscribe("mission.phase", (next) => {
      // Only follow the mission phase if the user hasn't manually picked one
      // that differs — but per spec the default-selected is the current phase,
      // so we update only if user is on a known PHASE tab.
      if (next && PHASE_TABS.some((p) => p.id === next)) {
        ui.selectedPhase = next;
        rerender();
      }
    }),
    store.subscribe("claims", rerender),
  ];

  return function cleanup() {
    for (const u of unsubs) {
      try { u(); } catch (_) { /* ignore */ }
    }
    while (root.firstChild) root.removeChild(root.firstChild);
  };
}

export default { render };
