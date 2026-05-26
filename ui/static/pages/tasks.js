// @ts-check
// tasks.js — /tasks page, kanban-by-phase view.
//
// v9.4 T3.9 rewrite: columns = phases from GET /api/v1/tasks.
// Each card shows id, lane chip, state badge, title/description.
// Click → detail drawer; ESC closes. Lane filter bar at top.
//
// Hard rules:
//   - NO innerHTML. textContent + createElement only.
//   - No external deps.
//   - No real-time polling.
//   - Exports render(root) → cleanup function.
//
// API shape consumed:
//   GET /api/v1/tasks → {phases: [{name, tasks: [{id, lane, state, agent, utc, description}]}]}
//
// Store slices used (for non-canonical claims panel only):
//   claims

import { store } from "../js/store.js";
import { authedFetch } from "../js/auth.js";
import { API_STATE } from "../js/constants.js";

/** Make a DOM element with attrs and optional textContent. No innerHTML. */
function el(tag, opts) {
  const node = document.createElement(tag);
  if (!opts) return node;
  if (opts.class) node.className = opts.class;
  if (opts.text != null) node.textContent = String(opts.text);
  if (opts.title) node.setAttribute("title", String(opts.title));
  if (opts.testid) node.setAttribute("data-testid", String(opts.testid));
  if (opts.attrs) {
    for (const k of Object.keys(opts.attrs)) {
      node.setAttribute(k, String(opts.attrs[k]));
    }
  }
  return node;
}

/** Slugify a string for safe use in data-testid/class names. */
function slugify(s) {
  return String(s || "")
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/** Truncate a string for display; keep full text in title. */
function truncate(s, n) {
  const str = String(s || "");
  if (str.length <= n) return str;
  return str.slice(0, n - 1) + "…";
}

/** Count done / total tasks in a phase. "done" matches the server task state. */
export function phaseProgress(tasks) {
  const list = Array.isArray(tasks) ? tasks : [];
  let done = 0;
  for (const t of list) {
    if (String(t && t.state).toLowerCase() === "done") done += 1;
  }
  return { done, total: list.length };
}

/**
 * Whether a phase column matches the mission's current phase. The mission phase
 * (from /api/v1/state → mission.phase) can be a canonical token ("PHASE-EXEC")
 * or a human header ("PHASE 2 — BUILD"); column names come from TASKS.md
 * headers. Compare case-insensitively and accept either being a substring of
 * the other so both formats line up. Empty current phase never matches.
 */
export function isCurrentPhase(phaseName, currentPhase) {
  const a = String(phaseName || "").trim().toLowerCase();
  const b = String(currentPhase || "").trim().toLowerCase();
  if (!a || !b) return false;
  if (a === b) return true;
  return a.includes(b) || b.includes(a);
}

// ---------------------------------------------------------------------------
// Non-canonical claims helpers (carried over from v9.3)
// ---------------------------------------------------------------------------

/** Return a Set of claim dir names from the store. */
function buildClaimsSet() {
  const claims = store.get("claims");
  if (!claims) return null;
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

/** Collect claim dirs not matching any known task id. */
function computeNonCanonical(claimsList, allTaskIds) {
  if (!Array.isArray(claimsList)) return [];
  const taskIdSet = new Set(allTaskIds);
  const out = [];
  for (const c of claimsList) {
    if (!c) continue;
    const dirname = typeof c === "string" ? c : (c.dirname || c.name || c.id || "");
    if (!dirname) continue;
    if (!taskIdSet.has(dirname)) out.push({ dirname, mtime: typeof c === "object" ? (c.mtime || c.utc || "") : "" });
  }
  return out;
}

function rawClaimsList() {
  const claims = store.get("claims");
  if (!claims) return [];
  if (Array.isArray(claims)) return claims;
  return claims.list || claims.dirs || [];
}

// ---------------------------------------------------------------------------
// Card detail drawer
// ---------------------------------------------------------------------------

/** Build the full-detail drawer node (always rendered, visibility via display). */
function renderDrawer(task, isOpen, onClose) {
  const overlay = el("div", {
    testid: "task-drawer-overlay",
    attrs: {
      role: "dialog",
      "aria-modal": "true",
      "aria-label": `Task ${task.id} details`,
      style: [
        "position:fixed",
        "inset:0",
        "z-index:100",
        "display:" + (isOpen ? "flex" : "none"),
        "align-items:flex-start",
        "justify-content:flex-end",
        "background:rgba(0,0,0,0.45)",
        "padding:var(--sp-4)",
      ].join(";"),
    },
  });

  const panel = el("div", {
    testid: `task-drawer-${task.id}`,
    attrs: {
      style: [
        "width:min(480px,90vw)",
        "max-height:90vh",
        "overflow-y:auto",
        "background:var(--surface-1,#1a1a1a)",
        "border-radius:var(--r-2,6px)",
        "padding:var(--sp-4,16px)",
        "display:flex",
        "flex-direction:column",
        "gap:var(--sp-3,12px)",
      ].join(";"),
    },
  });

  // Close button row
  const closeRow = el("div", { attrs: { style: "display:flex; justify-content:space-between; align-items:center" } });
  const titleNode = el("h2", {
    class: "mono",
    text: task.id,
    attrs: { style: "font-size:var(--fs-lg,1.1rem); margin:0" },
  });
  const closeBtn = el("button", {
    text: "✕",
    title: "Close drawer (ESC)",
    testid: "task-drawer-close",
    attrs: {
      type: "button",
      "aria-label": "Close",
      style: "background:none; border:none; cursor:pointer; font-size:1.2rem; color:var(--text-muted); padding:0 var(--sp-1)",
    },
  });
  closeBtn.addEventListener("click", onClose);
  closeRow.appendChild(titleNode);
  closeRow.appendChild(closeBtn);
  panel.appendChild(closeRow);

  // Lane + state
  const chipRow = el("div", { attrs: { style: "display:flex; gap:var(--sp-2,8px); flex-wrap:wrap" } });
  if (task.lane) {
    chipRow.appendChild(el("span", {
      class: `lane-chip ${slugify(task.lane)}`,
      text: task.lane,
      title: `Lane: ${task.lane}`,
    }));
  }
  const stateSlug = slugify(task.state || "open");
  chipRow.appendChild(el("span", {
    class: `badge badge--${stateSlug}`,
    text: task.state || "open",
    title: `State: ${task.state || "open"}`,
    attrs: { "data-state": task.state || "open" },
  }));
  panel.appendChild(chipRow);

  // Description
  const desc = task.description || "(no description)";
  const descNode = el("p", {
    attrs: { style: "white-space:pre-wrap; margin:0" },
    text: desc,
  });
  panel.appendChild(descNode);

  // Metadata
  if (task.agent || task.utc) {
    const meta = el("dl", { attrs: { style: "margin:0; display:grid; grid-template-columns:auto 1fr; gap:var(--sp-1) var(--sp-2)" } });
    if (task.agent) {
      meta.appendChild(el("dt", { class: "mono text-muted", text: "agent", attrs: { style: "margin:0" } }));
      meta.appendChild(el("dd", { class: "mono", text: task.agent, attrs: { style: "margin:0" } }));
    }
    if (task.utc) {
      meta.appendChild(el("dt", { class: "mono text-muted", text: "utc", attrs: { style: "margin:0" } }));
      meta.appendChild(el("dd", { class: "mono", text: task.utc, attrs: { style: "margin:0" } }));
    }
    panel.appendChild(meta);
  }

  // Close overlay on backdrop click
  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay) onClose();
  });

  overlay.appendChild(panel);
  return overlay;
}

// ---------------------------------------------------------------------------
// Task card
// ---------------------------------------------------------------------------

/** Build one task card. */
function renderTaskCard(task, onCardClick) {
  const card = el("article", {
    class: "card",
    testid: `task-card-${task.id}`,
    attrs: {
      tabindex: "0",
      role: "button",
      "aria-label": `Task ${task.id}`,
      "data-lane": task.lane || "",
      "data-state": task.state || "open",
    },
  });

  // Header: id + lane chip
  const head = el("div", { class: "row", attrs: { style: "gap:var(--sp-2,8px); align-items:center; flex-wrap:wrap" } });
  head.appendChild(el("span", {
    class: "mono",
    text: task.id,
    attrs: { style: "font-weight:600; flex-shrink:0" },
    title: task.id,
  }));
  if (task.lane) {
    head.appendChild(el("span", {
      class: `lane-chip ${slugify(task.lane)}`,
      text: task.lane,
      title: `Lane: ${task.lane}`,
    }));
  }
  // State badge (right side)
  const stateSlug = slugify(task.state || "open");
  const spacer = el("span", { attrs: { style: "flex:1 1 auto" } });
  head.appendChild(spacer);
  head.appendChild(el("span", {
    class: `badge badge--${stateSlug}`,
    text: task.state || "open",
    title: `State: ${task.state || "open"}`,
    attrs: { "data-state": task.state || "open" },
  }));
  card.appendChild(head);

  // Description (truncated)
  const desc = task.description || "";
  if (desc) {
    card.appendChild(el("p", {
      class: "truncate text-muted",
      text: truncate(desc, 100),
      title: desc,
      attrs: { style: "margin-top:var(--sp-2,8px)" },
    }));
  }

  card.addEventListener("click", () => onCardClick(task.id));
  card.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      onCardClick(task.id);
    }
  });

  return card;
}

// ---------------------------------------------------------------------------
// Lane filter bar
// ---------------------------------------------------------------------------

/** Build lane filter chip bar. Returns {el, getSelected, setSelected} */
function renderLaneFilter(allLanes, selectedLanes, onToggle) {
  const bar = el("div", {
    testid: "lane-filter-bar",
    attrs: {
      role: "group",
      "aria-label": "Filter by lane",
      title: "Click a lane chip to filter tasks by lane",
      style: "display:flex; flex-wrap:wrap; gap:var(--sp-2,8px); margin-bottom:var(--sp-3,12px); align-items:center",
    },
  });

  const label = el("span", {
    class: "text-muted",
    text: "Lane:",
    attrs: { style: "font-size:var(--fs-sm); flex-shrink:0" },
  });
  bar.appendChild(label);

  for (const lane of allLanes) {
    const isActive = selectedLanes.has(lane);
    const chip = el("button", {
      class: `lane-chip ${slugify(lane)}` + (isActive ? " active" : ""),
      text: lane,
      title: `Filter to ${lane} lane${isActive ? " (active)" : ""}`,
      testid: `lane-filter-${slugify(lane)}`,
      attrs: {
        type: "button",
        "aria-pressed": isActive ? "true" : "false",
        "data-lane": lane,
        style: isActive ? "outline:2px solid currentColor; outline-offset:1px" : "opacity:0.6",
      },
    });
    chip.addEventListener("click", () => onToggle(lane));
    bar.appendChild(chip);
  }

  return bar;
}

// ---------------------------------------------------------------------------
// Non-canonical claims panel (retained from v9.3)
// ---------------------------------------------------------------------------

function renderNonCanonicalPanel(rows, expanded, onToggle) {
  const panel = el("section", {
    class: "card",
    testid: "panel-non-canonical-claims",
    attrs: {
      style: "margin-top:var(--sp-4,16px)",
      "aria-expanded": expanded ? "true" : "false",
    },
  });

  const header = el("button", {
    class: "row",
    attrs: {
      type: "button",
      "aria-controls": "non-canonical-claims-body",
      "aria-expanded": expanded ? "true" : "false",
      style: "width:100%; justify-content:space-between; cursor:pointer",
      title: "Toggle non-canonical claims panel",
    },
  });
  header.appendChild(el("span", {
    text: `Non-canonical claims (potential protocol drift) — ${rows.length}`,
    attrs: { style: "font-weight:600" },
  }));
  header.appendChild(el("span", { class: "text-muted mono", text: expanded ? "▼" : "▶" }));
  header.addEventListener("click", onToggle);
  panel.appendChild(header);

  const body = el("div", {
    attrs: {
      id: "non-canonical-claims-body",
      style: `margin-top:var(--sp-2,8px); display:${expanded ? "block" : "none"}`,
    },
  });
  if (rows.length === 0) {
    body.appendChild(el("p", { class: "empty-state", text: "No non-canonical claim directories detected." }));
  } else {
    const list = el("ul", { class: "stack-1" });
    for (const r of rows) {
      const slug = slugify(r.dirname);
      const li = el("li", {
        class: "row",
        testid: `non-canonical-claim-${slug}`,
        attrs: { style: "padding:var(--sp-2,8px); background:var(--surface-2); border-radius:var(--r-1)" },
      });
      li.appendChild(el("span", { class: "badge", text: "?", attrs: { style: "color:var(--text-muted)" } }));
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

// ---------------------------------------------------------------------------
// Main render
// ---------------------------------------------------------------------------

/**
 * Mount the /tasks page into root.
 * @param {HTMLElement} root
 * @returns {Promise<() => void>} cleanup
 */
export async function render(root, _params) {
  // Loading skeleton
  const skeleton = el("div", { class: "loading-skeleton", text: "Loading tasks…" });
  root.appendChild(skeleton);

  // Fetch tasks + state in parallel. The /state read gives us the mission's
  // current phase so the kanban can highlight the matching column. authedFetch
  // awaits the auth bootstrap and surfaces the re-auth modal on 401, so a
  // mid-session gate tightening recovers instead of silently emptying the board.
  let phases = [];
  let currentPhase = "";
  const [tasksResult, stateResult] = await Promise.allSettled([
    authedFetch("/api/v1/tasks"),
    authedFetch(API_STATE),
  ]);
  if (tasksResult.status === "fulfilled" && tasksResult.value.ok) {
    try {
      const data = await tasksResult.value.json();
      if (Array.isArray(data.phases)) phases = data.phases;
    } catch (err) {
      console.warn("[tasks] tasks parse failed:", err);
    }
  } else if (tasksResult.status === "rejected") {
    console.warn("[tasks] fetch failed:", tasksResult.reason);
  }
  if (stateResult.status === "fulfilled" && stateResult.value.ok) {
    try {
      const stateData = await stateResult.value.json();
      currentPhase = String((stateData && stateData.mission && stateData.mission.phase) || "");
    } catch (_) {
      /* non-fatal — no current-phase highlight */
    }
  }

  while (root.firstChild) root.removeChild(root.firstChild);

  // Collect all known task ids (for non-canonical panel)
  const allTaskIds = [];
  for (const phase of phases) {
    for (const t of (phase.tasks || [])) allTaskIds.push(t.id);
  }

  // Collect all lanes present
  const laneSet = new Set();
  for (const phase of phases) {
    for (const t of (phase.tasks || [])) {
      if (t.lane) laneSet.add(t.lane);
    }
  }
  const allLanes = Array.from(laneSet).sort();

  // UI state
  const ui = {
    openDrawerId: null,
    selectedLanes: new Set(), // empty = show all
    panelExpanded: false,
  };

  // ESC handler (attached to document while mounted)
  function onKeyDown(ev) {
    if (ev.key === "Escape" && ui.openDrawerId !== null) {
      ev.preventDefault();
      ui.openDrawerId = null;
      rerender();
    }
  }
  document.addEventListener("keydown", onKeyDown);

  function onCardClick(taskId) {
    ui.openDrawerId = ui.openDrawerId === taskId ? null : taskId;
    rerender();
  }

  function onLaneToggle(lane) {
    if (ui.selectedLanes.has(lane)) {
      ui.selectedLanes.delete(lane);
    } else {
      ui.selectedLanes.add(lane);
    }
    rerender();
  }

  function onTogglePanel() {
    ui.panelExpanded = !ui.panelExpanded;
    rerender();
  }

  function rerender() {
    while (root.firstChild) root.removeChild(root.firstChild);

    // Page header
    const hdr = el("header", { attrs: { style: "margin-bottom:var(--sp-3,12px)" } });
    hdr.appendChild(el("h1", {
      text: "Tasks",
      attrs: { style: "font-size:var(--fs-xl,1.5rem); margin-bottom:var(--sp-3,12px)" },
    }));
    root.appendChild(hdr);

    // Lane filter bar (only if lanes exist)
    if (allLanes.length > 0) {
      root.appendChild(renderLaneFilter(allLanes, ui.selectedLanes, onLaneToggle));
    }

    // Kanban: one column per phase
    if (phases.length === 0) {
      const empty = el("section", {
        class: "card empty-state",
        testid: "tasks-empty-banner",
        attrs: {
          role: "status",
          "aria-live": "polite",
          style: "padding:var(--sp-3,12px); text-align:center",
        },
      });
      empty.appendChild(el("p", { text: "No tasks loaded yet.", attrs: { style: "font-weight:600" } }));
      empty.appendChild(el("p", {
        class: "text-muted",
        text: "Waiting for the backend to parse TASKS.md.",
      }));
      root.appendChild(empty);
    } else {
      const kanban = el("section", {
        attrs: {
          "aria-label": "Task kanban by phase",
          style: [
            "display:grid",
            `grid-template-columns:repeat(${Math.min(phases.length, 4)}, minmax(0, 1fr))`,
            "gap:var(--sp-3,12px)",
            "align-items:start",
          ].join(";"),
        },
      });

      for (const phase of phases) {
        const phaseName = phase.name || "(unnamed)";
        const phaseSlug = slugify(phaseName);
        const tasks = phase.tasks || [];

        // Filter tasks by selected lanes
        const visibleTasks = ui.selectedLanes.size === 0
          ? tasks
          : tasks.filter((t) => ui.selectedLanes.has(t.lane));

        // Per-phase progress (over ALL tasks in the phase, not lane-filtered, so
        // the count stays meaningful while a lane filter is active).
        const { done, total } = phaseProgress(tasks);
        const isCurrent = isCurrentPhase(phaseName, currentPhase);

        const col = el("div", {
          class: "stack-2" + (isCurrent ? " phase-col--current" : ""),
          testid: `phase-col-${phaseSlug}`,
          attrs: {
            "data-phase-name": phaseName,
            "data-phase-done": String(done),
            "data-phase-total": String(total),
            "data-current-phase": isCurrent ? "true" : "false",
            title: `Phase: ${phaseName} — ${done}/${total} done`
              + (isCurrent ? " (current phase)" : ""),
            ...(isCurrent
              ? { style: "outline:2px solid var(--accent,#4a9eff); outline-offset:2px; border-radius:var(--r-1,4px)" }
              : {}),
          },
        });

        // Column header: phase name + done/total progress; current phase marked.
        const colHeader = el("h2", {
          class: "lane-chip" + (isCurrent ? " active" : ""),
          testid: `phase-header-${phaseSlug}`,
          title: `Phase: ${phaseName} — ${done}/${total} done`
            + (isCurrent ? " (current phase)" : ""),
          attrs: {
            "data-current-phase": isCurrent ? "true" : "false",
            style: "width:100%; justify-content:flex-start; gap:var(--sp-2,8px); font-size:var(--fs-sm,0.85rem); margin-bottom:var(--sp-2,8px)",
          },
        });
        // Current-phase dot indicator (left of the name).
        if (isCurrent) {
          colHeader.appendChild(el("span", {
            testid: `phase-current-indicator-${phaseSlug}`,
            title: "Current mission phase",
            attrs: { "aria-label": "current phase", style: "flex-shrink:0" },
            text: "●",
          }));
        }
        colHeader.appendChild(el("span", {
          text: phaseName,
          attrs: { style: "flex:1 1 auto; overflow:hidden; text-overflow:ellipsis; white-space:nowrap" },
        }));
        colHeader.appendChild(el("span", {
          class: "mono",
          testid: `phase-progress-${phaseSlug}`,
          text: `${done}/${total}`,
          title: `${done} of ${total} tasks done`,
          attrs: { style: "flex-shrink:0; opacity:0.85" },
        }));
        col.appendChild(colHeader);

        if (visibleTasks.length === 0) {
          col.appendChild(el("p", {
            class: "empty-state",
            text: "No tasks",
            title: ui.selectedLanes.size > 0 ? "No tasks match the active lane filter" : "No tasks in this phase",
          }));
        } else {
          for (const task of visibleTasks) {
            col.appendChild(renderTaskCard(task, onCardClick));
          }
        }

        kanban.appendChild(col);
      }

      root.appendChild(kanban);
    }

    // Non-canonical claims panel
    const ncRows = computeNonCanonical(rawClaimsList(), allTaskIds);
    root.appendChild(renderNonCanonicalPanel(ncRows, ui.panelExpanded, onTogglePanel));

    // Detail drawer (outside kanban flow; fixed overlay)
    if (ui.openDrawerId !== null) {
      // Find the task
      let openTask = null;
      for (const phase of phases) {
        for (const t of (phase.tasks || [])) {
          if (t.id === ui.openDrawerId) { openTask = t; break; }
        }
        if (openTask) break;
      }
      if (openTask) {
        const drawer = renderDrawer(openTask, true, () => {
          ui.openDrawerId = null;
          rerender();
        });
        root.appendChild(drawer);
      }
    }
  }

  // Initial render
  rerender();

  // Subscribe to claims changes only (no polling on tasks)
  const unsub = store.subscribe("claims", () => rerender());

  return function cleanup() {
    document.removeEventListener("keydown", onKeyDown);
    try { unsub(); } catch (_) { /* ignore */ }
    while (root.firstChild) root.removeChild(root.firstChild);
  };
}

export default { render };
