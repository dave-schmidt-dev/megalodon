// @ts-check
// pages/lane_detail.js — Megalodon orchestrator-console /lane/<short> detail page.
//
// Layout (top → bottom):
//   1. Back link to / (grid page), using app.js router (no full reload).
//   2. Lane metadata header — state, agent, current task from /api/v1/status.
//   3. Terminal pane — createTerminalPane({lane: short, scrollback: 5000}).
//      Fills available vertical space (flex layout).
//   4. Inject form — textarea + Send + "Append Enter" checkbox.
//      - Live character count.
//      - Client-side 16384-byte cap.
//      - POST /api/v1/lane/${short}/inject with X-CSRF-Token header.
//      - 6-second Send disable after successful send.
//      - Toast feedback on success/error.
//
// Cleanup: returns a function that disposes the terminal + clears root.
//
// Security: no innerHTML with user data; all values via textContent / DOM APIs.

import { loadConfig } from "../js/config.js";
import { mountPage } from "../js/app.js";
import { createTerminalPane } from "../components/terminal_pane.js";
import { showConfirmModal } from "../components/confirm_modal.js";
import { controlEnabled, onControlMode } from "../js/store.js";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const BYTE_LIMIT = 16384;
const SEND_DEBOUNCE_MS = 6000;

// ---------------------------------------------------------------------------
// DOM helpers (same pattern as board.js)
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
// CSRF token helper — reads from <meta name="csrf-token"> (same as grid.js)
// ---------------------------------------------------------------------------

/**
 * Read the CSRF token from the page's meta tag.
 * @returns {string}
 */
function getCsrfToken() {
  return document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") || "";
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

  // Clear previous content.
  clearNode(region);

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
// Lane metadata header
// ---------------------------------------------------------------------------

/**
 * Fetch lane status from /api/v1/status and return the matching lane row.
 * Matches by short code using the config lanes list for the name→short mapping.
 *
 * @param {string} short
 * @returns {Promise<{state: string, agent: string, working_task_id: string, notes: string}|null>}
 */
async function fetchLaneStatus(short) {
  try {
    // Load config to map short → full lane name.
    const config = await loadConfig();
    const laneConfig = Array.isArray(config.lanes)
      ? config.lanes.find((l) => String(l.short) === short)
      : null;

    if (!laneConfig) return null;
    const laneName = String(laneConfig.name);

    const resp = await fetch("/api/v1/status", { credentials: "include" });
    if (!resp.ok) return null;
    const json = await resp.json();
    const lanes = Array.isArray(json.lanes) ? json.lanes : [];
    const row = lanes.find((r) => String(r.lane) === laneName);
    if (!row) return null;

    // Extract working_task_id from state string like "working: T2".
    let working_task_id = "";
    const stateStr = String(row.state || "");
    const workingMatch = stateStr.match(/working[:\s]+(\S+)/i);
    if (workingMatch) working_task_id = workingMatch[1];

    return {
      state: stateStr,
      agent: String(row.agent || "—"),
      working_task_id,
      notes: String(row.notes || ""),
    };
  } catch (_) {
    return null;
  }
}

/**
 * Build the metadata header element.
 * @param {string} short
 * @param {string} laneName
 * @param {{state: string, agent: string, working_task_id: string, notes: string}|null} status
 * @returns {HTMLElement}
 */
function buildMetaHeader(short, laneName, status) {
  const stateText = status ? status.state : "—";
  const agentText = status ? status.agent : "—";
  const taskText = status ? (status.working_task_id || "—") : "—";
  const notesText = status ? status.notes : "";

  return el(
    "div",
    {
      "data-testid": "lane-detail-meta",
      style: [
        "background: #1c1f24;",
        "border: 1px solid #2a2f37;",
        "border-radius: 4px;",
        "padding: 10px 14px;",
        "display: flex;",
        "flex-wrap: wrap;",
        "gap: 16px;",
        "align-items: center;",
        "font-family: ui-monospace, SFMono-Regular, Menlo, monospace;",
        "font-size: 13px;",
      ].join(" "),
    },
    el(
      "span",
      {
        class: `lane-chip ${short}`,
        "data-testid": "lane-detail-chip",
        title: `Lane: ${laneName} (short: ${short})`,
        style: "font-weight: 600;",
      },
      laneName || short
    ),
    el(
      "span",
      {
        "data-testid": "lane-detail-state",
        title: `Current lane state`,
        style: "color: #e6e6e6;",
      },
      `state: ${stateText}`
    ),
    el(
      "span",
      {
        "data-testid": "lane-detail-agent",
        title: `Agent ID running in this lane`,
        style: "color: #9aa0a8;",
      },
      `agent: ${agentText}`
    ),
    el(
      "span",
      {
        "data-testid": "lane-detail-task",
        title: `Current working task`,
        style: "color: #9aa0a8;",
      },
      `task: ${taskText}`
    ),
    notesText
      ? el(
          "span",
          {
            "data-testid": "lane-detail-notes",
            title: `Lane notes: ${notesText}`,
            style: "color: #6b7280; font-size: 11px; max-width: 400px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;",
          },
          notesText
        )
      : false
  );
}

// ---------------------------------------------------------------------------
// Inject form
// ---------------------------------------------------------------------------

/**
 * Build the inject form element.
 *
 * @param {string} short
 * @returns {{ element: HTMLElement, cleanup: () => void }}
 */
function buildInjectForm(short) {
  // --- textarea ---
  const textarea = /** @type {HTMLTextAreaElement} */ (el("textarea", {
    "data-testid": "inject-textarea",
    placeholder: "Type a message to inject into the lane…",
    rows: "4",
    title: "Message to inject into the lane terminal. Maximum 16384 bytes.",
    style: [
      "width: 100%;",
      "box-sizing: border-box;",
      "resize: vertical;",
      "background: #15181d;",
      "color: #e6e6e6;",
      "border: 1px solid #2a2f37;",
      "border-radius: 4px;",
      "padding: 8px 10px;",
      "font-family: ui-monospace, SFMono-Regular, Menlo, monospace;",
      "font-size: 13px;",
      "line-height: 1.4;",
    ].join(" "),
  }));

  // --- char/byte count ---
  const byteCountEl = el("span", {
    "data-testid": "inject-byte-count",
    style: "font-size: 12px; color: #9aa0a8; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;",
  }, "0 / 16384 bytes");

  // --- byte limit warning ---
  const limitWarning = el("span", {
    "data-testid": "inject-limit-warning",
    hidden: true,
    style: "font-size: 12px; color: #d04848; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-weight: 600;",
  }, "Over byte limit — message too long");

  // --- append-enter checkbox ---
  const checkboxId = `inject-enter-${short}`;
  const enterCheckbox = /** @type {HTMLInputElement} */ (el("input", {
    type: "checkbox",
    id: checkboxId,
    "data-testid": "inject-enter-checkbox",
    checked: true,
    title: "When checked, a newline (Enter) is appended to the injected text, which causes the agent to process the input immediately.",
  }));
  // Set checked property directly since attribute only sets initial value.
  enterCheckbox.checked = true;

  const checkboxLabel = el("label", {
    for: checkboxId,
    title: "When checked, a newline (Enter) is appended to the injected text, which causes the agent to process the input immediately.",
    style: "font-size: 13px; color: #e6e6e6; cursor: pointer; user-select: none;",
  }, "Append Enter");

  // --- send button ---
  const sendBtn = /** @type {HTMLButtonElement} */ (el("button", {
    type: "button",
    "data-testid": "inject-send",
    class: "button button--primary",
    title: "Send injected text to the lane terminal. Disabled for 6 seconds after a successful send.",
    style: [
      "padding: 7px 20px;",
      "background: #1e4e8c;",
      "color: #e6e6e6;",
      "border: 1px solid #2a6ca0;",
      "border-radius: 4px;",
      "cursor: pointer;",
      "font-size: 13px;",
      "font-family: ui-monospace, SFMono-Regular, Menlo, monospace;",
    ].join(" "),
  }, "Send"));

  // --- encoder for byte counting ---
  const encoder = new TextEncoder();

  /** @type {ReturnType<typeof setTimeout>|null} */
  let debounceTimer = null;

  // Control-mode gate: in READ-ONLY mode (the safe default) the inject form is a
  // no-op affordance — Send + textarea are disabled and visually marked. Flip to
  // CONTROL to enable. Tracked here so updateCount() never re-enables Send while
  // read-only.
  let control = controlEnabled();

  /** @returns {number} */
  function getByteLen() {
    return encoder.encode(textarea.value).length;
  }

  function updateCount() {
    const len = getByteLen();
    const over = len > BYTE_LIMIT;
    byteCountEl.textContent = `${len} / ${BYTE_LIMIT} bytes`;
    byteCountEl.style.color = over ? "#d04848" : "#9aa0a8";
    limitWarning.hidden = !over;
    // Disable Send when read-only, over limit, or while a debounce is active.
    if (!control || over) {
      sendBtn.disabled = true;
    } else if (!debounceTimer) {
      sendBtn.disabled = false;
    }
  }

  /** Apply the control-mode posture to the inject affordances. */
  function applyControl(on) {
    control = on;
    sendBtn.dataset.readonlyGated = on ? "false" : "true";
    if (!on) {
      sendBtn.title = "Enable Control mode to act.";
      sendBtn.style.opacity = "0.5";
      sendBtn.style.cursor = "not-allowed";
      textarea.disabled = true;
    } else {
      sendBtn.title = "Send injected text to the lane terminal. Disabled for 6 seconds after a successful send.";
      sendBtn.style.opacity = "";
      sendBtn.style.cursor = "pointer";
      textarea.disabled = false;
    }
    updateCount();
  }

  const unsubControl = onControlMode(applyControl);

  textarea.addEventListener("input", updateCount);

  async function handleSend() {
    // Read-only safety: refuse to act even if the button is reached. Re-read the
    // live control-mode at action time via controlEnabled() — the single source
    // of truth — rather than the `control` closure var, mirroring board.js
    // handleKill / the restart-loop handler. The closure var still drives the
    // button's disabled/visual posture (updateCount/applyControl); this guard is
    // the authoritative last-line check and avoids any stale-closure risk.
    if (!controlEnabled()) {
      showToast("Read-only mode — enable Control mode to inject", "error");
      return;
    }

    const text = textarea.value;
    const byteLen = encoder.encode(text).length;

    // Client-side guard.
    if (byteLen > BYTE_LIMIT) {
      showToast("Message exceeds 16384-byte limit", "error");
      return;
    }

    // Confirm before the destructive/mutating action (control-mode AND modal).
    const ok = await showConfirmModal({
      title: "Inject into lane?",
      message: `Send this text to lane ${short}'s terminal?`,
      confirmLabel: "Inject",
      cancelLabel: "Cancel",
    });
    if (!ok) return;

    const enter = enterCheckbox.checked;
    const csrf = getCsrfToken();

    sendBtn.disabled = true;

    try {
      const resp = await fetch(`/api/v1/lane/${encodeURIComponent(short)}/inject`, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          ...(csrf ? { "X-CSRF-Token": csrf } : {}),
        },
        body: JSON.stringify({ text, enter }),
      });

      if (resp.status === 202) {
        // Success: clear textarea, show toast, start debounce.
        // Arm the debounce timer BEFORE updateCount() runs: updateCount()'s
        // re-enable branch is guarded by `!debounceTimer`, so the timer must
        // already be set or updateCount() would immediately re-enable Send and
        // defeat the debounce entirely.
        debounceTimer = setTimeout(() => {
          debounceTimer = null;
          const stillOver = getByteLen() > BYTE_LIMIT;
          if (!stillOver) {
            sendBtn.disabled = false;
          }
        }, SEND_DEBOUNCE_MS);

        textarea.value = "";
        updateCount();
        showToast("Injected successfully", "info");
      } else {
        // Error: show status + detail.
        let detail = `HTTP ${resp.status}`;
        try {
          const body = await resp.json();
          if (body.detail) detail = `${resp.status}: ${body.detail}`;
        } catch (_) { /* ignore parse error */ }
        showToast(`Inject failed — ${detail}`, "error");
        // Re-enable button so user can retry.
        sendBtn.disabled = false;
      }
    } catch (err) {
      showToast(`Network error — ${String(err)}`, "error");
      sendBtn.disabled = false;
    }
  }

  sendBtn.addEventListener("click", handleSend);

  // --- layout ---
  const countRow = el(
    "div",
    {
      style: "display: flex; align-items: center; gap: 12px; flex-wrap: wrap;",
    },
    byteCountEl,
    limitWarning
  );

  const controlRow = el(
    "div",
    {
      style: "display: flex; align-items: center; gap: 12px; flex-wrap: wrap;",
    },
    el("div", {
      style: "display: flex; align-items: center; gap: 6px;",
    },
      enterCheckbox,
      checkboxLabel
    ),
    sendBtn
  );

  const formEl = el(
    "div",
    {
      "data-testid": "inject-form",
      style: [
        "background: #1c1f24;",
        "border: 1px solid #2a2f37;",
        "border-radius: 4px;",
        "padding: 12px 14px;",
        "display: flex;",
        "flex-direction: column;",
        "gap: 8px;",
      ].join(" "),
    },
    el("div", { style: "font-size: 12px; color: #9aa0a8; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; margin-bottom: 2px;" },
      `Inject text into lane ${short}`),
    textarea,
    countRow,
    controlRow
  );

  function cleanup() {
    if (debounceTimer) {
      clearTimeout(debounceTimer);
      debounceTimer = null;
    }
    try { unsubControl(); } catch (_) { /* ignore */ }
  }

  return { element: formEl, cleanup };
}

// ---------------------------------------------------------------------------
// Top-level render
// ---------------------------------------------------------------------------

/**
 * Render the lane detail page into `root`.
 *
 * @param {HTMLElement} root
 * @param {{ short: string }} params
 * @returns {Promise<() => void>} cleanup function
 */
export async function render(root, { short }) {
  // --- loading skeleton ---
  const skeleton = document.createElement("div");
  skeleton.className = "loading-skeleton";
  skeleton.textContent = `Loading lane ${short}…`;
  root.appendChild(skeleton);

  // --- resolve lane name from config ---
  let laneName = short;
  try {
    const config = await loadConfig();
    if (Array.isArray(config.lanes)) {
      const match = config.lanes.find((l) => String(l.short) === short);
      if (match) laneName = String(match.name || short);
    }
  } catch (_) {
    // non-fatal; use short code as fallback
  }

  // --- fetch lane status (non-blocking; page still renders on failure) ---
  const status = await fetchLaneStatus(short);

  // --- clear skeleton; build page ---
  clearNode(root);

  // --- back link (real <a> with click-intercept, same as grid.js navigate pattern) ---
  const backLink = el("a", {
    href: "/",
    "data-testid": "lane-detail-back",
    title: "Return to the lane grid overview",
    style: [
      "display: inline-flex;",
      "align-items: center;",
      "gap: 4px;",
      "color: #6db8ff;",
      "text-decoration: none;",
      "font-size: 13px;",
      "font-family: ui-monospace, SFMono-Regular, Menlo, monospace;",
    ].join(" "),
  }, "← Back to grid");

  // app.js global click handler already intercepts any <a href="/..."> click
  // (attachRouter in app.js:133-145), so no per-element listener needed.
  // We add one here as an explicit belt-and-suspenders to ensure pushState
  // behavior regardless of whether the global handler fires first.
  backLink.addEventListener("click", (ev) => {
    ev.preventDefault();
    navigate("/");
  });

  // --- Restart /loop toolbar button ---
  const restartLoopBtn = /** @type {HTMLButtonElement} */ (el("button", {
    type: "button",
    class: "button button--primary",
    "data-testid": "lane-detail-restart-loop",
    title: `Restart the /loop cycle for lane ${short}. Sends the lane's initial_prompt to its tmux session. Requires confirmation.`,
    style: [
      "padding: 5px 14px;",
      "font-size: 12px;",
      "font-family: ui-monospace, SFMono-Regular, Menlo, monospace;",
    ].join(" "),
  }, "Restart /loop"));

  // Control-mode gate for the Restart /loop button (state-changing action).
  function applyRestartControl(on) {
    restartLoopBtn.dataset.readonlyGated = on ? "false" : "true";
    restartLoopBtn.disabled = !on;
    if (!on) {
      restartLoopBtn.title = "Enable Control mode to act.";
      restartLoopBtn.style.opacity = "0.5";
      restartLoopBtn.style.cursor = "not-allowed";
    } else {
      restartLoopBtn.title = `Restart the /loop cycle for lane ${short}. Sends the lane's initial_prompt to its tmux session. Requires confirmation.`;
      restartLoopBtn.style.opacity = "";
      restartLoopBtn.style.cursor = "";
    }
  }
  const unsubRestartControl = onControlMode(applyRestartControl);

  restartLoopBtn.addEventListener("click", async () => {
    if (!controlEnabled()) {
      showToast("Read-only mode — enable Control mode to restart", "error");
      return;
    }
    const ok = await showConfirmModal({
      title: "Restart /loop?",
      message: `Restart the /loop cycle for lane ${short}? This sends the lane's initial prompt to its tmux session.`,
      confirmLabel: "Restart /loop",
      cancelLabel: "Cancel",
    });
    if (!ok) return;

    const csrf = getCsrfToken();
    restartLoopBtn.disabled = true;

    try {
      const resp = await fetch(`/api/v1/lane/${encodeURIComponent(short)}/restart-loop`, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          ...(csrf ? { "X-CSRF-Token": csrf } : {}),
        },
        body: JSON.stringify({}),
      });

      if (resp.status === 202) {
        showToast(`Restarted /loop for lane ${short}`, "info");
      } else {
        let detail = `HTTP ${resp.status}`;
        try {
          const body = await resp.json();
          if (body.detail) detail = `${resp.status}: ${body.detail}`;
        } catch (_) { /* ignore */ }
        showToast(`Restart failed — ${detail}`, "error");
      }
    } catch (err) {
      showToast(`Network error — ${String(err)}`, "error");
    } finally {
      // Re-enable only if still in control mode (a flip to read-only mid-flight
      // must keep the button disabled).
      restartLoopBtn.disabled = !controlEnabled();
    }
  });

  // --- meta header ---
  const metaHeader = buildMetaHeader(short, laneName, status);

  // --- terminal pane ---
  const termComponent = createTerminalPane({ lane: short, scrollback: 5000 });
  const termHost = el("div", {
    "data-testid": "lane-detail-terminal",
    style: [
      "flex: 1;",
      "min-height: 0;",
      "overflow: hidden;",
      "background: #0b0d10;",
      "border: 1px solid #2a2f37;",
      "border-radius: 4px;",
    ].join(" "),
  });
  termHost.appendChild(termComponent.element);

  // --- inject form ---
  const injectForm = buildInjectForm(short);

  // --- toolbar row: back link + restart-loop button ---
  const toolbar = el("div", {
    "data-testid": "lane-detail-toolbar",
    style: [
      "display: flex;",
      "align-items: center;",
      "gap: 10px;",
      "flex-wrap: wrap;",
    ].join(" "),
  }, backLink, restartLoopBtn);

  // --- page layout ---
  const page = el("div", {
    "data-testid": "lane-detail-page",
    style: [
      "display: flex;",
      "flex-direction: column;",
      "height: calc(100vh - 120px);",
      "gap: 8px;",
      "padding: 12px 16px;",
    ].join(" "),
  },
    toolbar,
    metaHeader,
    termHost,
    injectForm.element
  );

  root.appendChild(page);

  // --- cleanup ---
  function cleanup() {
    termComponent.cleanup();
    injectForm.cleanup();
    try { unsubRestartControl(); } catch (_) { /* ignore */ }
    // Do NOT clearNode(root): app.js clears the mount root before every page
    // render. A stale page's cleanup that clears root can wipe a newer page
    // when app.js discards a superseded render's cleanup (WebKit back-nav bug).
  }

  // Expose cleanup on root for external lifecycle hooks.
  /** @type {any} */ (root)._cleanups = [termComponent.cleanup, injectForm.cleanup];

  return cleanup;
}

export default render;
