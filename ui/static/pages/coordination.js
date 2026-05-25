// @ts-check
/**
 * /coordination — the cross-lane coordination / handoff / contention view.
 *
 * Answers the operator's core question after a long agent run: "what are they
 * doing, what are they coordinating, what are they handing off?" Three grounded
 * sections fed by `GET /api/v1/coordination`:
 *
 *   1. Who's working what — one row per lane: lane chip, agent, current
 *      working_task (or "idle"), a BLOCKED badge, notes excerpt. Authoritative
 *      who→what.
 *   2. Claims & contention — every claim; contested ones (orphaned/contended,
 *      i.e. nobody working it and not done) flagged prominently.
 *   3. Recent signals (handoffs) — sender→receiver · topic · age, clickable to
 *      a body drawer (mirrors the signals page drawer pattern).
 *
 * Data shape (frozen wire contract):
 *   { lanes:[{lane,agent,state,working_task,blocked,notes_excerpt}],
 *     claims:[{task_id,dirname,has_done,mtime,owner,working_lane,contested}],
 *     signals_recent:[ <signal dict> ] }
 *
 * Refresh: poll every 30s (matches the board cadence) AND subscribe to the
 * activity-wall SSE so a signal/claim/history event triggers an immediate
 * refresh — so the view is live during a run, not snapshot-only.
 *
 * Contract: export render(root) -> cleanup().
 *
 * Security: zero innerHTML. All dynamic text via el() / textContent. No SVG.
 */

import { authedFetch, probeReauthOn401, onReauthSuccess } from "../js/auth.js";

// ---------------------------------------------------------------------------
// DOM helper (mirrors board.js / activity_wall.js)
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
// Time helper — "X ago" from a UTC stamp (ISO or filename dash-form).
// ---------------------------------------------------------------------------

/**
 * @param {string} utc
 * @returns {string}
 */
function utcAgo(utc) {
  if (!utc) return "";
  const s = String(utc);
  let iso = s;
  const m = s.match(/^(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})(?:-(\d{2}))?Z$/);
  if (m) iso = `${m[1]}T${m[2]}:${m[3]}:${m[4] || "00"}Z`;
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "";
  const deltaSec = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (deltaSec < 60) return `${deltaSec}s ago`;
  if (deltaSec < 3600) return `${Math.floor(deltaSec / 60)}m ago`;
  if (deltaSec < 86400) return `${Math.floor(deltaSec / 3600)}h ago`;
  return `${Math.floor(deltaSec / 86400)}d ago`;
}

/**
 * Resolve a signal dict's display fields, preferring server fields and falling
 * back to parsing the filename. Mirrors signals.js (kept local to honor file
 * ownership — coordination renders its own signals_recent list).
 * @param {object} sig
 */
function signalFields(sig) {
  const base = String(sig.filename || "").replace(/\.md$/i, "");
  let from = sig.from_lane || "";
  let to = sig.to_lane || sig.to || "";
  let topic = sig.topic || "";
  let utc = sig.utc || "";
  if (!from || !to || !topic) {
    const m = base.match(
      /^(LANE-[A-Z0-9]+)-to-(LANE-[A-Z0-9]+)-(.+)-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}(?:-\d{2})?Z)$/,
    );
    const legacy = m ? null : base.match(/^(LANE-[A-Z0-9]+)-to-(LANE-[A-Z0-9]+)-(.+)$/);
    const parsed = m || legacy;
    if (parsed) {
      from = from || parsed[1];
      to = to || parsed[2];
      topic = topic || parsed[3];
      if (m) utc = utc || m[4];
    }
  }
  return { from_lane: from || "?", to_lane: to || "?", topic: topic || base || "?", utc };
}

// ---------------------------------------------------------------------------
// Data fetch
// ---------------------------------------------------------------------------

/**
 * @typedef {Object} CoordinationData
 * @property {Array<{lane:string,agent:string,state:string,working_task:string,blocked:boolean,notes_excerpt:string}>} lanes
 * @property {Array<{task_id:string,dirname:string,has_done:boolean,mtime:number,owner:string,working_lane:string,contested:boolean}>} claims
 * @property {Array<object>} signals_recent
 */

/**
 * Fetch the coordination snapshot. Returns null when the endpoint is missing
 * (e.g. backend not yet wired) or errors, so the caller can show a graceful
 * placeholder instead of a broken page.
 * @returns {Promise<CoordinationData|null>}
 */
async function fetchCoordination() {
  try {
    const resp = await authedFetch("/api/v1/coordination");
    if (!resp.ok) return null;
    const json = await resp.json();
    return {
      lanes: Array.isArray(json.lanes) ? json.lanes : [],
      claims: Array.isArray(json.claims) ? json.claims : [],
      signals_recent: Array.isArray(json.signals_recent) ? json.signals_recent : [],
    };
  } catch (_) {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------

/**
 * @param {HTMLElement} root
 * @param {Record<string, any>} [_params]
 * @returns {Promise<() => void>} cleanup
 */
export async function render(root, _params) {
  clearNode(root);

  let disposed = false;

  const page = el("div", { class: "coordination-page stack-3", "data-testid": "coordination-page" });
  root.appendChild(page);

  // ---- shared drawer (signal body) ----------------------------------------
  const drawerOverlay = el("div", {
    class: "coordination-drawer-overlay",
    "data-testid": "coordination-drawer-overlay",
    style: "display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.4); z-index: 200;",
  });
  const drawer = el("div", {
    class: "coordination-drawer",
    "data-testid": "coordination-drawer",
    role: "dialog",
    "aria-modal": "true",
    "aria-label": "Signal detail",
    style: [
      "display: none;",
      "position: fixed;",
      "top: 0; right: 0; bottom: 0;",
      "width: min(480px, 90vw);",
      "background: var(--surface, #15181d);",
      "border-left: 1px solid var(--border, #2a2e35);",
      "z-index: 201;",
      "flex-direction: column;",
    ].join(" "),
  });
  const drawerHeader = el("div", {
    style: "display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; border-bottom: 1px solid var(--border, #2a2e35);",
  });
  const drawerTitle = el("span", {
    class: "mono",
    "data-testid": "coordination-drawer-title",
    style: "font-size: 13px; font-weight: 600; color: var(--text, #e7e9ec);",
  }, "Signal");
  const drawerClose = el("button", {
    type: "button",
    class: "button",
    "data-testid": "coordination-drawer-close",
    "aria-label": "Close drawer",
    style: "height: 28px; padding: 0 10px; font-size: 13px;",
  }, "×");
  drawerHeader.appendChild(drawerTitle);
  drawerHeader.appendChild(drawerClose);
  const drawerBody = el("div", {
    "data-testid": "coordination-drawer-body",
    style: "flex: 1; overflow-y: auto; padding: 16px;",
  });
  drawer.appendChild(drawerHeader);
  drawer.appendChild(drawerBody);
  root.appendChild(drawerOverlay);
  root.appendChild(drawer);

  function openSignalDrawer(sig) {
    const f = signalFields(sig);
    clearNode(drawerTitle);
    drawerTitle.appendChild(el("span", { class: "lane-chip" }, f.from_lane));
    drawerTitle.appendChild(el("span", { style: "color: var(--text-muted);" }, " → "));
    drawerTitle.appendChild(el("span", { class: "lane-chip" }, f.to_lane));
    drawerTitle.appendChild(el("span", { class: "mono text-muted", style: "margin-left: 6px;" }, `· ${f.topic}`));
    clearNode(drawerBody);
    const body = String(sig.body || sig.excerpt || "");
    if (body.trim()) {
      drawerBody.appendChild(el("pre", {
        style: "font-family: var(--font-mono, ui-monospace, monospace); font-size: 12px; color: var(--text, #e7e9ec); white-space: pre-wrap; word-break: break-word; margin: 0;",
      }, body));
    } else {
      drawerBody.appendChild(el("p", { class: "text-muted" }, "(no body)"));
    }
    drawer.style.display = "flex";
    drawerOverlay.style.display = "block";
  }
  function closeSignalDrawer() {
    drawer.style.display = "none";
    drawerOverlay.style.display = "none";
  }
  drawerClose.addEventListener("click", closeSignalDrawer);
  drawerOverlay.addEventListener("click", closeSignalDrawer);
  function onKeydown(e) {
    if (e.key === "Escape" && drawer.style.display !== "none") closeSignalDrawer();
  }
  window.addEventListener("keydown", onKeydown);

  // ---- section scaffolding -------------------------------------------------

  function sectionCard(title, testid, bodyEl) {
    return el(
      "div",
      { class: "card stack-2", "data-testid": testid },
      el("div", { class: "mono", style: "font-size: var(--fs-sm); color: var(--text); font-weight: 600;" }, title),
      bodyEl,
    );
  }

  const lanesBody = el("div", { class: "stack-1", "data-testid": "coordination-lanes" });
  const claimsBody = el("div", { class: "stack-1", "data-testid": "coordination-claims" });
  const signalsBody = el("div", { class: "stack-1", "data-testid": "coordination-signals" });

  const placeholder = el("div", {
    class: "empty-state",
    "data-testid": "coordination-empty",
    style: "display: none;",
  }, "Coordination data unavailable.");

  page.appendChild(placeholder);
  page.appendChild(sectionCard("Who's working what", "coordination-section-lanes", lanesBody));
  page.appendChild(sectionCard("Claims & contention", "coordination-section-claims", claimsBody));
  page.appendChild(sectionCard("Recent signals (handoffs)", "coordination-section-signals", signalsBody));

  // ---- section renderers ---------------------------------------------------

  /** @param {CoordinationData['lanes']} lanes */
  function renderLanes(lanes) {
    clearNode(lanesBody);
    if (!lanes.length) {
      lanesBody.appendChild(el("div", { class: "text-muted", style: "font-size: var(--fs-sm);" }, "No lanes."));
      return;
    }
    for (const ln of lanes) {
      const lane = String(ln.lane || "?");
      const workingTask = ln.working_task ? String(ln.working_task) : "idle";
      const row = el(
        "div",
        {
          class: "row",
          "data-testid": `coordination-lane-${lane}`,
          "data-lane": lane,
          style: "gap: var(--sp-2); padding: 4px 0; flex-wrap: nowrap;",
        },
        el("span", { class: "lane-chip", style: "flex: 0 0 auto;" }, lane),
        el("span", { class: "text-muted", style: "font-size: var(--fs-xs); flex: 0 0 auto; min-width: 90px;" }, ln.agent ? String(ln.agent) : "—"),
        el("span", {
          class: ln.working_task ? "mono" : "mono text-muted",
          style: "flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;",
          title: workingTask,
        }, workingTask),
      );
      if (ln.blocked) {
        row.appendChild(el("span", {
          class: "badge",
          "data-testid": `coordination-lane-blocked-${lane}`,
          style: "flex: 0 0 auto; background: var(--sev-blocking, #d04848); color: var(--bg, #0e0f12); border: 1px solid var(--sev-blocking, #d04848); font-weight: 600; letter-spacing: 0.4px;",
        }, "BLOCKED"));
      }
      if (ln.notes_excerpt) {
        row.appendChild(el("span", {
          class: "text-muted",
          style: "flex: 2 1 0; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: var(--fs-xs);",
          title: String(ln.notes_excerpt),
        }, String(ln.notes_excerpt)));
      }
      lanesBody.appendChild(row);
    }
  }

  /** @param {CoordinationData['claims']} claims */
  function renderClaims(claims) {
    clearNode(claimsBody);
    if (!claims.length) {
      claimsBody.appendChild(el("div", { class: "text-muted", style: "font-size: var(--fs-sm);" }, "No active claims."));
      return;
    }
    // Contested claims float to the top so contention is the first thing seen.
    const sorted = claims.slice().sort((a, b) => Number(!!b.contested) - Number(!!a.contested));
    for (const c of sorted) {
      const taskId = String(c.task_id || c.dirname || "?");
      const row = el("div", {
        class: "row",
        "data-testid": `coordination-claim-${taskId}`,
        "data-contested": c.contested ? "true" : "false",
        style: [
          "gap: var(--sp-2); padding: 4px 6px; flex-wrap: nowrap;",
          c.contested
            ? "border-left: 3px solid var(--sev-blocking, #d04848); background: rgba(208,72,72,0.08);"
            : "border-left: 3px solid transparent;",
        ].join(" "),
      });
      row.appendChild(el("span", {
        class: "mono",
        style: "flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;",
        title: taskId,
      }, taskId));
      if (c.contested) {
        row.appendChild(el("span", {
          class: "badge",
          "data-testid": `coordination-claim-contested-${taskId}`,
          title: "Contested: claimed but nobody is working it and it is not done.",
          style: "flex: 0 0 auto; background: var(--sev-blocking, #d04848); color: var(--bg, #0e0f12); border: 1px solid var(--sev-blocking, #d04848); font-weight: 600; letter-spacing: 0.4px;",
        }, "CONTESTED"));
      }
      if (c.working_lane) {
        row.appendChild(el("span", { class: "lane-chip", style: "flex: 0 0 auto;", title: `working: ${c.working_lane}` }, String(c.working_lane)));
      }
      if (c.owner) {
        row.appendChild(el("span", { class: "text-muted", style: "flex: 0 0 auto; font-size: var(--fs-xs);", title: `owner: ${c.owner}` }, `owner: ${c.owner}`));
      }
      row.appendChild(el("span", {
        class: c.has_done ? "badge" : "text-muted",
        style: c.has_done
          ? "flex: 0 0 auto; background: var(--surface-2); color: #4ec9b0; border: 1px solid var(--border); font-size: var(--fs-xs);"
          : "flex: 0 0 auto; font-size: var(--fs-xs);",
      }, c.has_done ? "done" : "open"));
      claimsBody.appendChild(row);
    }
  }

  /** @param {Array<object>} signals */
  function renderSignals(signals) {
    clearNode(signalsBody);
    if (!signals.length) {
      signalsBody.appendChild(el("div", { class: "text-muted", style: "font-size: var(--fs-sm);" }, "No recent signals."));
      return;
    }
    for (const sig of signals) {
      const f = signalFields(sig);
      const age = utcAgo(f.utc) || f.utc || "";
      const row = el("div", {
        class: "row",
        role: "button",
        tabindex: "0",
        "data-testid": "coordination-signal-row",
        "data-signal-filename": sig.filename || "",
        style: "gap: var(--sp-2); padding: 4px 0; cursor: pointer; flex-wrap: nowrap;",
        title: `Open signal: ${sig.filename || f.topic}`,
      },
        el("span", { class: "lane-chip", style: "flex: 0 0 auto;" }, f.from_lane),
        el("span", { class: "text-muted", style: "flex: 0 0 auto;" }, "→"),
        el("span", { class: "lane-chip", style: "flex: 0 0 auto;" }, f.to_lane),
        el("span", {
          class: "mono text-muted",
          style: "flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;",
          title: f.topic,
        }, `· ${f.topic}`),
        el("span", { class: "mono text-muted", style: "flex: 0 0 auto; font-size: var(--fs-xs);" }, age),
      );
      row.addEventListener("click", () => openSignalDrawer(sig));
      row.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openSignalDrawer(sig); }
      });
      signalsBody.appendChild(row);
    }
  }

  // ---- refresh -------------------------------------------------------------

  async function refresh() {
    if (disposed) return;
    const data = await fetchCoordination();
    if (disposed) return;
    if (!data) {
      placeholder.style.display = "";
      return;
    }
    placeholder.style.display = "none";
    renderLanes(data.lanes);
    renderClaims(data.claims);
    renderSignals(data.signals_recent);
  }

  await refresh();
  if (disposed) return () => {};

  // Poll on the board cadence (30s).
  const pollTimer = setInterval(refresh, 30_000);

  // ---- live: refresh on activity-wall signal/claim/history events ----------
  let es = null;
  let reconnectDelay = 500;
  const RECONNECT_MAX_MS = 30_000;
  /** @type {ReturnType<typeof setTimeout>|null} */
  let reconnectTimer = null;

  function _clearReconnectTimer() {
    if (reconnectTimer !== null) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  }
  function _scheduleReconnect() {
    if (disposed) return;
    _clearReconnectTimer();
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      if (disposed) return;
      startSSE();
    }, Math.min(reconnectDelay, RECONNECT_MAX_MS));
    reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
  }
  function startSSE() {
    if (disposed) return;
    es = new EventSource("/api/v1/activity-wall", { withCredentials: true });
    es.onopen = () => { reconnectDelay = 500; };
    es.onmessage = (ev) => {
      try {
        const event = JSON.parse(ev.data);
        const t = event && event.type;
        // A signal/claim/history event changes coordination state — re-pull.
        if (t === "signal" || t === "history" || t === "queue") refresh();
      } catch (_) { /* ignore */ }
    };
    es.onerror = () => {
      if (!es || es.readyState !== EventSource.CLOSED) return;
      try { es.close(); } catch (_) { /* ignore */ }
      es = null;
      probeReauthOn401("/api/v1/activity-wall");
      _scheduleReconnect();
    };
  }
  startSSE();

  const offReauth = onReauthSuccess(() => {
    if (disposed) return;
    _clearReconnectTimer();
    reconnectDelay = 500;
    if (es) { try { es.close(); } catch (_) { /* ignore */ } es = null; }
    refresh();
    startSSE();
  });

  // ---- cleanup -------------------------------------------------------------
  return function cleanup() {
    disposed = true;
    clearInterval(pollTimer);
    _clearReconnectTimer();
    try { offReauth(); } catch (_) { /* ignore */ }
    if (es) { try { es.close(); } catch (_) { /* ignore */ } es = null; }
    window.removeEventListener("keydown", onKeydown);
    clearNode(root);
  };
}

export default { render };
