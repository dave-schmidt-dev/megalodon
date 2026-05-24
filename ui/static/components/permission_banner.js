// @ts-check
// components/permission_banner.js — Megalodon permission-approval banner component.
//
// Polls GET /api/v1/permission_prompts every 2 s, renders per-lane approve /
// deny / approve-remember controls, and drives:
//   - POST /api/v1/permission_prompts/{lane}/respond
//   - GET  /api/v1/approval-rules/extract?command=…
//   - POST /api/v1/approval-rules
//
// Public export:
//   createPermissionBanner({ container, onPromptsChange }) → { element, start, stop, cleanup }
//
//   onPromptsChange (optional): invoked after each poll render with the current
//   prompts array. Used by board.js to drive BLOCKED-pill precedence from a
//   single prompts poll (no second permission_prompts fetch). Only fired while
//   the component is active (post-stop renders do not call it). Omitting it is
//   a no-op — default behavior is unchanged.
//
// Constraints:
//   - No innerHTML with dynamic data; all DOM via el() / textContent.
//   - CSRF header (X-CSRF-Token) sent on mutating requests (harmless if not
//     required by the endpoint; matches R11 convention).
//   - start() is idempotent: calling it twice does not stack timers.
//   - stop() halts the poll; element and its contents are left intact.
//   - cleanup() calls stop() and performs any remaining teardown.

import { showConfirmModal } from "./confirm_modal.js";

// ---------------------------------------------------------------------------
// Minimal DOM helpers (same pattern as activity_wall.js / confirm_modal.js)
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

function clearNode(/** @type {HTMLElement} */ node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

// ---------------------------------------------------------------------------
// CSRF token helper
// ---------------------------------------------------------------------------

/**
 * Read the CSRF token from the page's meta tag.
 * @returns {string}
 */
function getCsrfToken() {
  return document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") || "";
}

// ---------------------------------------------------------------------------
// Session id helper
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
// Toast helper
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
// API helpers
// ---------------------------------------------------------------------------

/**
 * @returns {Promise<Array<{lane: string, lane_name?: string, command?: string, detected_at?: string}>>}
 */
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

/**
 * @param {string} laneShort
 * @param {"approve"|"approve_remember"|"deny"} action
 * @returns {Promise<boolean>}
 */
async function respondToPrompt(laneShort, action) {
  try {
    const csrf = getCsrfToken();
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

// ---------------------------------------------------------------------------
// Main export
// ---------------------------------------------------------------------------

/**
 * Create the permission-approval banner component.
 *
 * The caller is responsible for inserting `element` into the DOM.
 * Call `start()` once the element is mounted to begin polling.
 *
 * @param {{ container?: HTMLElement, onPromptsChange?: (prompts: Array<{lane: string, lane_name?: string, command?: string, detected_at?: string}>) => void }} [opts]
 *   `container` is accepted for API-shape consistency with createActivityWall;
 *   it is not used (component owns its root). `onPromptsChange` is an optional
 *   callback invoked with the current prompts array after each poll render
 *   (only while active); omitting it leaves default behavior unchanged.
 * @returns {{ element: HTMLElement, start: () => void, stop: () => void, cleanup: () => void }}
 */
export function createPermissionBanner({ container: _container, onPromptsChange } = {}) {
  // ---- root element -------------------------------------------------------
  const element = el("section", {
    class: "card stack-1",
    "data-testid": "permission-panel",
    hidden: true,
  });

  // ---- poll timer ---------------------------------------------------------
  /** @type {ReturnType<typeof setInterval>|null} */
  let timer = null;
  // Mounted guard: a render() (or button-handler) that resolves its awaited
  // fetch after stop()/cleanup() must not touch the (possibly detached) element.
  let active = false;

  // ---- render -------------------------------------------------------------

  /**
   * Fetch current prompts and redraw the banner contents.
   * Closes over `element` so recursive re-renders after approve/deny work
   * without passing the container around.
   */
  async function render() {
    const prompts = await fetchPermissionPrompts();
    if (!active) return;
    // Single source of truth for the pending-prompt lane set: notify the
    // optional subscriber (board.js) before redrawing. Guarded by `active`
    // above so a post-stop render does not fire it.
    onPromptsChange?.(prompts);
    clearNode(element);
    if (prompts.length === 0) {
      element.hidden = true;
      return;
    }
    element.hidden = false;

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
          await render();
        },
      }, `Approve all (${prompts.length})`),
    );
    element.appendChild(headerRow);

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
              if (ok) await render();
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
              await render();
            },
          }, "Approve & remember"),
          el("button", {
            type: "button",
            class: "button button--warning",
            "data-testid": `permission-deny-${lane}`,
            title: "Denies this tool-use prompt.",
            onclick: async () => {
              const ok = await respondToPrompt(lane, "deny");
              if (ok) await render();
            },
          }, "Deny"),
        ),
      ));
    }
    element.appendChild(list);
  }

  // ---- start / stop / cleanup ---------------------------------------------

  /** Begin polling (idempotent — calling twice does not stack timers). */
  function start() {
    if (timer !== null) return;
    active = true;
    render();
    timer = setInterval(render, 2_000);
  }

  /** Clear the poll timer; element and current contents are left intact. */
  function stop() {
    active = false;
    if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  }

  /**
   * Stop polling and clean up. The confirm modal self-cleans via its own
   * backdrop removal, so nothing extra is needed here.
   */
  function cleanup() {
    stop();
  }

  return { element, start, stop, cleanup };
}
