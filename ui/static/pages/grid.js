// @ts-check
// pages/grid.js — Megalodon orchestrator-console default landing page (`/`).
//
// Layout (top → bottom, left → right):
//   1. Mission header (reused v9.3 pattern from dashboard.js)
//   2. Permission banner (polls /api/v1/permission_prompts every 2s)
//   3. Two-column body:
//       Left:  CSS Grid of N terminal panes (N = config.lanes.length)
//       Right: Activity wall placeholder (Task 2.4)
//
// Each pane = createTerminalPane({lane: short, scrollback: 500}).
// Clicking a pane navigates to /lane/<short> via the app.js router.
//
// Cleanup: all pane cleanup() functions are invoked by the returned
// cleanup function (called by app.js mountPage on unmount).
//
// Lane discovery: GET /api/v1/config → config.lanes[*].{name, short}
//
// Security: no innerHTML with user data; all values via textContent / DOM APIs.

import { loadConfig } from "../js/config.js";
import { mountPage } from "../js/app.js";
import { createTerminalPane } from "../components/terminal_pane.js";
import { createActivityWall } from "../components/activity_wall.js";
import { StaleModal } from "../components/stale_modal.js";
import { showConfirmModal } from "../components/confirm_modal.js";

// ---------------------------------------------------------------------------
// Minimal DOM helpers (same pattern as dashboard.js)
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

function clearNode(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

// ---------------------------------------------------------------------------
// Permission banner (v9.3 — adapted from dashboard.js)
// ---------------------------------------------------------------------------

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

async function renderPermissionBanner(container) {
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
      title: "Approves all pending permission prompts simultaneously.",
      onclick: async () => {
        const lanes = prompts.map((p) => p.lane);
        await Promise.all(lanes.map((lane) => respondToPrompt(lane, "approve")));
        await renderPermissionBanner(container);
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
        style: "padding: var(--sp-2); border: 1px solid var(--c-border,#444); border-radius: 4px;",
      },
      el("div", { class: "row", style: "gap: var(--sp-2); align-items: center; flex-wrap: wrap;" },
        el("span", { class: `lane-chip ${p.lane_name || lane}` }, String(p.lane_name || lane)),
        el("span", { class: "mono", style: "font-size: 0.85em; color: var(--color-text-muted, #888);" }, since),
      ),
      el("pre", {
        class: "mono",
        style: "white-space: pre-wrap; word-break: break-word; margin: var(--sp-1) 0; font-size: 0.9em;",
      }, cmd),
      el("div", { class: "row", style: "gap: var(--sp-2); flex-wrap: wrap;" },
        el("button", {
          type: "button",
          class: "button button--primary",
          "data-testid": `permission-approve-${lane}`,
          title: "Approves this tool-use prompt.",
          onclick: async () => {
            const ok = await respondToPrompt(lane, "approve");
            if (ok) await renderPermissionBanner(container);
          },
        }, "Approve"),
        el("button", {
          type: "button",
          class: "button",
          "data-testid": `permission-approve-remember-${lane}`,
          title: "Approves and saves a pattern rule so this command is auto-approved in future sessions.",
          onclick: async () => {
            // 1. Extract a pattern from the command preview via the BE endpoint.
            // The permission watcher prefixes command_preview with "[Bash command] "
            // or "[unknown tool] " — strip that prefix so extract_pattern gets
            // the raw shell command.
            let rawCommand = String(p.command || "");
            const toolPrefixMatch = rawCommand.match(/^\[([^\]]+)\]\s+/);
            if (toolPrefixMatch) {
              rawCommand = rawCommand.slice(toolPrefixMatch[0].length);
            }
            const command = rawCommand;
            let pattern = null;
            try {
              const extractResp = await fetch(
                `/api/v1/approval-rules/extract?command=${encodeURIComponent(command)}`,
                { credentials: "include" }
              );
              if (extractResp.ok) {
                const extractBody = await extractResp.json();
                pattern = extractBody.pattern ?? null;
              }
            } catch (_) { /* non-fatal: proceed without pattern */ }

            const patternDisplay = pattern
              ? `Pattern: ${pattern}`
              : "(No extractable pattern — command may be compound or empty.)";

            // 2. Show confirm modal so operator can verify the pattern.
            const confirmed = await showConfirmModal({
              title: "Approve & remember?",
              message: `${patternDisplay}\n\nThis pattern will be saved to the approval rules list and this prompt will be approved.`,
              confirmLabel: "Save rule and approve",
              cancelLabel: "Cancel",
            });

            if (!confirmed) return;

            // 3. On confirm: POST approval rule (if we have a pattern) AND respond.
            const csrf = getCsrfToken();
            const sessionId = getSessionId();

            let ruleOk = true;
            if (pattern) {
              try {
                const ruleResp = await fetch("/api/v1/approval-rules", {
                  method: "POST",
                  credentials: "include",
                  headers: {
                    "Content-Type": "application/json",
                    ...(csrf ? { "X-CSRF-Token": csrf } : {}),
                  },
                  body: JSON.stringify({ pattern, added_by_session: sessionId }),
                });
                ruleOk = ruleResp.ok;
                if (!ruleOk) {
                  showToast(`Failed to save approval rule — HTTP ${ruleResp.status}`, "error");
                }
              } catch (err) {
                ruleOk = false;
                showToast(`Network error saving rule — ${String(err)}`, "error");
              }
            }

            // Always attempt to approve even if rule save failed (best effort).
            const approved = await respondToPrompt(lane, "approve_remember");
            if (!approved) {
              showToast("Approve & remember: respond endpoint failed.", "error");
              return;
            }

            if (ruleOk) {
              showToast(pattern ? `Rule saved: ${pattern}` : "Approved (no pattern saved).", "info");
            }
            await renderPermissionBanner(container);
          },
        }, "Approve & remember"),
        el("button", {
          type: "button",
          class: "button button--warning",
          "data-testid": `permission-deny-${lane}`,
          title: "Denies this tool-use prompt.",
          onclick: async () => {
            const ok = await respondToPrompt(lane, "deny");
            if (ok) await renderPermissionBanner(container);
          },
        }, "Deny"),
      ),
    ));
  }
  container.appendChild(list);
}

// ---------------------------------------------------------------------------
// Navigate helper — uses the app.js router so navigation is consistent.
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
// CSRF token helper — mirrors lane_detail.js getCsrfToken()
// ---------------------------------------------------------------------------

/**
 * Read the CSRF token from the page's meta tag.
 * @returns {string}
 */
function getCsrfToken() {
  return document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") || "";
}

// ---------------------------------------------------------------------------
// Session id helper — reads from mui_session cookie
// ---------------------------------------------------------------------------

/**
 * Read the session id from the mui_session cookie.
 * @returns {string}
 */
function getSessionId() {
  const m = document.cookie.match(/mui_session=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

// ---------------------------------------------------------------------------
// Toast helper — mirrors lane_detail.js showToast()
// ---------------------------------------------------------------------------

/**
 * Show a brief toast message. Reuses #toast-region from index.html.
 * @param {string} message
 * @param {"info"|"error"} [kind]
 * @param {number} [durationMs]
 */
function showToast(message, kind = "info", durationMs = 3500) {
  const region = document.getElementById("toast-region");
  if (!region) return;

  // Remove previous toasts.
  while (region.firstChild) region.removeChild(region.firstChild);

  const span = document.createElement("span");
  span.textContent = message;
  span.style.cssText = [
    "display: inline-block;",
    "padding: 6px 12px;",
    "border-radius: 4px;",
    "font-size: 13px;",
    kind === "error"
      ? "background: #4a1515; color: #f99; border: 1px solid #d04848;"
      : "background: #152a1e; color: #9ef; border: 1px solid #2a6644;",
  ].join(" ");
  region.appendChild(span);

  setTimeout(() => {
    if (region.contains(span)) {
      region.removeChild(span);
    }
  }, durationMs);
}

// ---------------------------------------------------------------------------
// Stale lanes: fetch, badge, modal
// ---------------------------------------------------------------------------

/**
 * Fetch the stale lanes from the server.
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

/**
 * Build the stale-lanes badge chip element.
 * Initially hidden (display:none). Caller updates via updateStaleBadge().
 *
 * Note: do NOT use the `hidden` HTML attribute together with a `display:X`
 * inline style — inline styles beat the UA stylesheet's `display:none` for
 * `[hidden]`, so the element stays visible despite the attribute. Control
 * visibility via `element.style.display` instead.
 * @returns {HTMLElement}
 */
function buildStaleBadge() {
  const badge = el("button", {
    type: "button",
    "data-testid": "stale-badge",
    title: "Click to view stale lanes",
    style: [
      "display: none;",           // hidden by default — changed via updateStaleBadge
      "align-items: center;",
      "gap: 4px;",
      "padding: 3px 10px;",
      "border-radius: 12px;",
      "font-size: 12px;",
      "font-weight: 600;",
      "font-family: ui-monospace, SFMono-Regular, Menlo, monospace;",
      "cursor: pointer;",
      "border: 1px solid #7a1a1a;",
      "background: #4a1515;",
      "color: #f99;",
      "line-height: 1;",
    ].join(" "),
  }, "0 stale");
  return badge;
}

/**
 * Update the badge text and visibility.
 * Uses style.display (not the `hidden` attribute) to avoid CSS specificity
 * conflicts with inline styles.
 * @param {HTMLElement} badge
 * @param {number} count
 */
function updateStaleBadge(badge, count) {
  if (count > 0) {
    badge.textContent = `${count} stale`;
    badge.style.display = "inline-flex";
  } else {
    badge.style.display = "none";
  }
}

// ---------------------------------------------------------------------------
// Pane grid
// ---------------------------------------------------------------------------

/**
 * Build a single lane pane wrapper with click-to-navigate behavior.
 *
 * @param {{ name: string, short: string }} lane
 * @param {{ element: HTMLElement, cleanup: () => void }} termComponent
 * @returns {HTMLElement}
 */
function buildPaneWrapper(lane, termComponent) {
  const short = lane.short;
  const wrapper = document.createElement("div");
  wrapper.className = "grid-pane";
  wrapper.setAttribute("data-pane-lane", short);
  wrapper.setAttribute("data-testid", `grid-pane-${short}`);
  wrapper.style.cssText = [
    "background: #15181d;",
    "border: 1px solid #2a2f37;",
    "display: flex;",
    "flex-direction: column;",
    "min-height: 200px;",
    "overflow: hidden;",
    "cursor: pointer;",
    "border-radius: 4px;",
  ].join(" ");

  // Header bar with lane name + short code.
  const header = el(
    "div",
    {
      class: "grid-pane__header",
      style: [
        "padding: 4px 8px;",
        "font-size: 12px;",
        "background: #1f242c;",
        "border-bottom: 1px solid #2a2f37;",
        "display: flex;",
        "justify-content: space-between;",
        "align-items: center;",
        "font-family: ui-monospace, SFMono-Regular, Menlo, monospace;",
        "color: #e6e6e6;",
        "user-select: none;",
      ].join(" "),
    },
    el("span", null, `${lane.name}`),
    el("span", { style: "opacity: 0.6;" }, short),
  );

  // Terminal element fills remaining height.
  const termHost = document.createElement("div");
  termHost.style.cssText = "flex: 1; min-height: 0; overflow: hidden;";
  termHost.appendChild(termComponent.element);

  wrapper.appendChild(header);
  wrapper.appendChild(termHost);

  // Click anywhere on the pane → navigate to /lane/<short>.
  wrapper.addEventListener("click", () => {
    navigate(`/lane/${encodeURIComponent(short)}`);
  });
  // Keyboard: Enter / Space also navigate.
  wrapper.setAttribute("tabindex", "0");
  wrapper.setAttribute("role", "button");
  wrapper.setAttribute("aria-label", `Open lane ${lane.name} (${short})`);
  wrapper.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      navigate(`/lane/${encodeURIComponent(short)}`);
    }
  });

  return wrapper;
}

// ---------------------------------------------------------------------------
// Top-level render (contract: async function render(root, params) → cleanup)
// ---------------------------------------------------------------------------

/**
 * Render the grid page into `root`.
 *
 * @param {HTMLElement} root
 * @param {Record<string, any>} _params - not used; grid takes no URL params
 * @returns {Promise<() => void>} cleanup function
 */
export async function render(root, _params) {
  // --- loading skeleton ---
  const skeleton = document.createElement("div");
  skeleton.className = "loading-skeleton";
  skeleton.textContent = "Loading mission config…";
  root.appendChild(skeleton);

  // --- load config (single-flight via config.js cache) ---
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
    console.warn("[grid] config load failed:", err);
  }

  if (lanes.length === 0) {
    clearNode(root);
    const errMsg = document.createElement("p");
    errMsg.className = "empty-state";
    errMsg.textContent = "Failed to load mission config — cannot render lane grid.";
    root.appendChild(errMsg);
    return () => { clearNode(root); };
  }

  // --- clear skeleton; build page ---
  clearNode(root);

  // --- stale-lanes badge ---
  const staleBadge = buildStaleBadge();

  // --- stale-lanes modal ---
  /** @type {Array<{lane: string, silent_seconds: number|null, pending_approval: boolean, last_activity_source: string}>} */
  let currentStaleLanes = [];

  const staleModal = new StaleModal({
    navigate,
    getCsrfToken,
    showToast,
    onRefresh: async () => {
      currentStaleLanes = await fetchStaleLanes();
      updateStaleBadge(staleBadge, currentStaleLanes.length);
      staleModal.update(currentStaleLanes);
    },
  });
  document.body.appendChild(staleModal.element);

  staleBadge.addEventListener("click", () => {
    staleModal.open(currentStaleLanes);
  });

  // --- mission header with stale badge ---
  const missionHeader = el("div", {
    "data-testid": "mission-header",
    style: [
      "display: flex;",
      "align-items: center;",
      "gap: 10px;",
      "padding: 8px 0 4px 0;",
      "flex-wrap: wrap;",
    ].join(" "),
  },
    el("span", {
      style: [
        "font-family: ui-monospace, SFMono-Regular, Menlo, monospace;",
        "font-size: 13px;",
        "color: #9aa0a8;",
      ].join(" "),
    }, "Mission grid"),
    staleBadge,
  );

  // Permission banner (v9.3 pattern — hidden until prompts exist).
  const permissionBanner = el("section", {
    class: "card stack-1",
    "data-testid": "permission-panel",
    hidden: true,
  });

  // CSS Grid layout: responsive columns, roughly 2 per row on typical screens.
  // Clamp min column width so very narrow screens still show 1 col.
  const colCount = Math.max(1, Math.ceil(Math.sqrt(lanes.length)));
  const gridContainer = el("div", {
    "data-testid": "lane-grid",
    style: [
      "display: grid;",
      `grid-template-columns: repeat(${colCount}, 1fr);`,
      "gap: 6px;",
    ].join(" "),
  });

  // Activity wall placeholder container (Task 2.4) — component mounted after paneCleanups is ready.
  // Explicit height so the inner aw-list can scroll (flex:1 needs an anchored parent).
  const activityWallPlaceholder = el("div", {
    "data-component": "activity-wall",
    "data-testid": "activity-wall",
    style: [
      "min-height: 200px;",
      "min-width: 280px;",
      "max-width: 340px;",
      "width: 320px;",
      "height: calc(100vh - 180px);",
      "max-height: 900px;",
      "display: flex;",
      "flex-direction: column;",
    ].join(" "),
  });

  // Outer body: grid left, activity wall right.
  const body = el("div", {
    "data-testid": "grid-body",
    style: [
      "display: grid;",
      "grid-template-columns: 1fr 320px;",
      "gap: 8px;",
      "align-items: start;",
    ].join(" "),
  });
  body.appendChild(gridContainer);
  body.appendChild(activityWallPlaceholder);

  // Page root.
  const page = el("div", {
    class: "grid-page stack-2",
    "data-testid": "grid-page",
  },
    missionHeader,
    permissionBanner,
    body,
  );
  root.appendChild(page);

  // --- mount terminal panes ---
  /** @type {Array<() => void>} */
  const paneCleanups = [];

  for (const lane of lanes) {
    const termComponent = createTerminalPane({ lane: lane.short, scrollback: 500 });
    paneCleanups.push(termComponent.cleanup);
    const wrapper = buildPaneWrapper(lane, termComponent);
    gridContainer.appendChild(wrapper);
  }

  // --- mount activity wall component (Task 2.4) ---
  const awComponent = createActivityWall({ container: activityWallPlaceholder });
  activityWallPlaceholder.appendChild(awComponent.element);
  paneCleanups.push(awComponent.cleanup);

  // Expose pane cleanups on root for any external lifecycle hooks.
  /** @type {any} */ (root)._gridCleanups = paneCleanups;

  // --- permission banner polling (v9.3 — every 2s) ---
  renderPermissionBanner(permissionBanner);
  const permTimer = setInterval(() => renderPermissionBanner(permissionBanner), 2_000);

  // --- stale lanes: initial fetch + poll every 30s ---
  async function pollStale() {
    currentStaleLanes = await fetchStaleLanes();
    updateStaleBadge(staleBadge, currentStaleLanes.length);
    staleModal.update(currentStaleLanes);
  }
  pollStale();
  const staleTimer = setInterval(pollStale, 30_000);

  // --- cleanup ---
  return function cleanup() {
    clearInterval(permTimer);
    clearInterval(staleTimer);
    // Remove modal from DOM.
    if (document.body.contains(staleModal.element)) {
      document.body.removeChild(staleModal.element);
    }
    for (const fn of paneCleanups) {
      try { fn(); } catch (_) { /* ignore */ }
    }
    /** @type {any} */ (root)._gridCleanups = null;
    clearNode(root);
  };
}

export default render;
