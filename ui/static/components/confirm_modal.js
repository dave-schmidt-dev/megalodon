// @ts-check
// components/confirm_modal.js — Generic confirm/cancel modal (v9.4 T3.5).
//
// Usage:
//   import { showConfirmModal } from "../components/confirm_modal.js";
//   const confirmed = await showConfirmModal({
//     title: "Approve & remember?",
//     message: "Save pattern: Bash(curl http://127.0.0.1/*)",
//     confirmLabel: "Save rule and approve",
//     cancelLabel: "Cancel",
//   });
//   if (confirmed) { /* do the action */ }
//
// Resolves false on: cancel button, ESC key, click outside, X button.
// Resolves true on: confirm button.
//
// No innerHTML; all DOM via textContent / createElement.

// ---------------------------------------------------------------------------
// DOM helper (same pattern as grid.js / lane_detail.js)
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
      else if (k.startsWith("on") && typeof v === "function") {
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

// ---------------------------------------------------------------------------
// showConfirmModal
// ---------------------------------------------------------------------------

/**
 * @typedef {{
 *   title?: string,
 *   message: string,
 *   confirmLabel?: string,
 *   cancelLabel?: string,
 * }} ConfirmModalOptions
 */

/**
 * Show a centered overlay modal. Returns a Promise that resolves true if the
 * operator clicks confirm, false otherwise (cancel, ESC, click outside, X).
 *
 * @param {ConfirmModalOptions} options
 * @returns {Promise<boolean>}
 */
export function showConfirmModal({
  title = "Confirm",
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
}) {
  return new Promise((resolve) => {
    let settled = false;

    function settle(result) {
      if (settled) return;
      settled = true;
      document.removeEventListener("keydown", onKeydown);
      if (document.body.contains(backdrop)) {
        document.body.removeChild(backdrop);
      }
      resolve(result);
    }

    // --- backdrop ---
    const backdrop = el("div", {
      "data-testid": "confirm-modal-backdrop",
      style: [
        "position: fixed;",
        "inset: 0;",
        "background: rgba(0,0,0,0.65);",
        "display: flex;",
        "align-items: center;",
        "justify-content: center;",
        "z-index: 1200;",
      ].join(" "),
      onclick: (/** @type {MouseEvent} */ ev) => {
        if (ev.target === backdrop) settle(false);
      },
    });

    // --- dialog box ---
    const dialog = el("div", {
      role: "dialog",
      "aria-modal": "true",
      "aria-labelledby": "confirm-modal-title",
      "data-testid": "confirm-modal",
      style: [
        "background: #1c1f24;",
        "border: 1px solid #2a2f37;",
        "border-radius: 6px;",
        "width: min(480px, 94vw);",
        "display: flex;",
        "flex-direction: column;",
        "gap: 0;",
        "font-family: ui-monospace, SFMono-Regular, Menlo, monospace;",
        "font-size: 13px;",
        "color: #e6e6e6;",
        "box-shadow: 0 8px 32px rgba(0,0,0,0.6);",
      ].join(" "),
    });

    // --- header row ---
    const titleEl = el("span", {
      id: "confirm-modal-title",
      "data-testid": "confirm-modal-title",
      style: "font-weight: 600; font-size: 14px;",
    }, title);

    const closeBtn = el("button", {
      type: "button",
      class: "button",
      "data-testid": "confirm-modal-close",
      title: "Cancel and close",
      style: "min-width: 28px; height: 28px; padding: 0; font-size: 16px; line-height: 1;",
      onclick: () => settle(false),
    }, "×");

    const headerRow = el("div", {
      style: [
        "display: flex;",
        "align-items: center;",
        "justify-content: space-between;",
        "padding: 12px 16px;",
        "border-bottom: 1px solid #2a2f37;",
      ].join(" "),
    }, titleEl, closeBtn);

    // --- message body ---
    const msgEl = el("div", {
      "data-testid": "confirm-modal-message",
      style: [
        "padding: 16px;",
        "color: #c8cdd4;",
        "white-space: pre-wrap;",
        "word-break: break-word;",
        "line-height: 1.5;",
      ].join(" "),
    }, message);

    // --- button row ---
    const cancelBtn = el("button", {
      type: "button",
      class: "button",
      "data-testid": "confirm-modal-cancel",
      title: cancelLabel,
      onclick: () => settle(false),
    }, cancelLabel);

    const confirmBtn = el("button", {
      type: "button",
      class: "button button--primary",
      "data-testid": "confirm-modal-confirm",
      title: confirmLabel,
      onclick: () => settle(true),
    }, confirmLabel);

    const btnRow = el("div", {
      style: [
        "display: flex;",
        "justify-content: flex-end;",
        "gap: 8px;",
        "padding: 12px 16px;",
        "border-top: 1px solid #2a2f37;",
      ].join(" "),
    }, cancelBtn, confirmBtn);

    dialog.appendChild(headerRow);
    dialog.appendChild(msgEl);
    dialog.appendChild(btnRow);
    backdrop.appendChild(dialog);
    document.body.appendChild(backdrop);

    // --- ESC closes ---
    function onKeydown(/** @type {KeyboardEvent} */ ev) {
      if (ev.key === "Escape") {
        ev.preventDefault();
        settle(false);
      }
    }
    document.addEventListener("keydown", onKeydown);

    // Focus the confirm button so ESC/Enter work naturally.
    // Use setTimeout 0 so the DOM is settled before focus.
    setTimeout(() => {
      confirmBtn.focus();
    }, 0);
  });
}
