// @ts-check
// components/stale_modal.js — Stale-lanes modal for Megalodon grid page (v9.4 T2.8).
//
// Shows a modal listing lanes that are stale (silent ≥ threshold) with:
//   - Per-lane: chip, silent duration, last activity source
//   - Buttons: Peek stream, Restart /loop, Respawn (stub)
//
// Usage:
//   import { StaleModal } from "../components/stale_modal.js";
//   const modal = new StaleModal({ navigate, getCsrfToken, showToast, onRefresh });
//   document.body.appendChild(modal.element);
//   modal.open(staleLanes);  // [{lane, silent_seconds, pending_approval, last_activity_source}]
//   modal.close();

// ---------------------------------------------------------------------------
// DOM helper (minimal — same pattern as grid.js / lane_detail.js)
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
// Utility: format seconds as "Xm Ys"
// ---------------------------------------------------------------------------

/**
 * Format a number of seconds as "Xm Ys" (e.g. 1200 → "20m 0s").
 * Returns "—" when seconds is null/undefined.
 * @param {number|null|undefined} seconds
 * @returns {string}
 */
function formatDuration(seconds) {
  if (seconds == null) return "—";
  const s = Math.floor(seconds);
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${m}m ${rem}s`;
}

// ---------------------------------------------------------------------------
// StaleModal class
// ---------------------------------------------------------------------------

/**
 * @typedef {{
 *   navigate: (path: string) => void,
 *   getCsrfToken: () => string,
 *   showToast: (message: string, kind?: "info"|"error", durationMs?: number) => void,
 *   onRefresh: () => Promise<void>,
 * }} StaleModalOptions
 */

/**
 * @typedef {{
 *   lane: string,
 *   silent_seconds: number|null,
 *   pending_approval: boolean,
 *   last_activity_source: string,
 * }} StaleLane
 */

export class StaleModal {
  /**
   * @param {StaleModalOptions} options
   */
  constructor(options) {
    this._navigate = options.navigate;
    this._getCsrfToken = options.getCsrfToken;
    this._showToast = options.showToast;
    this._onRefresh = options.onRefresh;

    // --- backdrop ---
    // Use display:none/flex to toggle visibility. Do NOT use the `hidden`
    // attribute with a display:flex inline style — inline styles win over the
    // UA hidden:display:none rule and the element would intercept pointer events.
    this._backdrop = el("div", {
      "data-testid": "stale-modal-backdrop",
      style: [
        "position: fixed;",
        "inset: 0;",
        "background: rgba(0,0,0,0.65);",
        "display: none;",          // hidden by default
        "align-items: center;",
        "justify-content: center;",
        "z-index: 1000;",
        // pointer-events: none when hidden prevents Playwright hit-test
        // false-positives on position:fixed elements with inset:0.
        "pointer-events: none;",
      ].join(" "),
    });

    // Click outside → close.
    this._backdrop.addEventListener("click", (ev) => {
      if (ev.target === this._backdrop) this.close();
    });

    // --- dialog box ---
    this._dialog = el("div", {
      role: "dialog",
      "aria-modal": "true",
      "aria-labelledby": "stale-modal-title",
      "data-testid": "stale-modal",
      style: [
        "background: #1c1f24;",
        "border: 1px solid #2a2f37;",
        "border-radius: 6px;",
        "width: min(680px, 96vw);",
        "max-height: 80vh;",
        "display: flex;",
        "flex-direction: column;",
        "overflow: hidden;",
        "font-family: ui-monospace, SFMono-Regular, Menlo, monospace;",
        "font-size: 13px;",
        "color: #e6e6e6;",
      ].join(" "),
    });

    // --- header row ---
    this._titleEl = el("span", {
      id: "stale-modal-title",
      "data-testid": "stale-modal-title",
      style: "font-weight: 600; font-size: 14px;",
    }, "Stale Lanes");

    const closeBtn = el("button", {
      type: "button",
      class: "button",
      "data-testid": "stale-modal-close",
      title: "Close stale-lanes modal",
      style: "min-width: 28px; height: 28px; padding: 0; font-size: 16px;",
      onclick: () => this.close(),
    }, "×");

    const headerRow = el("div", {
      style: [
        "display: flex;",
        "align-items: center;",
        "justify-content: space-between;",
        "padding: 12px 16px;",
        "border-bottom: 1px solid #2a2f37;",
        "flex-shrink: 0;",
      ].join(" "),
    }, this._titleEl, closeBtn);

    // --- scrollable body ---
    this._body = el("div", {
      "data-testid": "stale-modal-body",
      style: [
        "overflow-y: auto;",
        "flex: 1;",
        "padding: 12px 16px;",
        "display: flex;",
        "flex-direction: column;",
        "gap: 10px;",
      ].join(" "),
    });

    this._dialog.appendChild(headerRow);
    this._dialog.appendChild(this._body);
    this._backdrop.appendChild(this._dialog);

    /** @type {HTMLElement} */
    this.element = this._backdrop;
  }

  /** @returns {boolean} Whether the modal is currently visible. */
  get isOpen() {
    return this._backdrop.style.display !== "none";
  }

  /**
   * Open the modal and render the given stale lanes.
   * @param {StaleLane[]} staleLanes
   */
  open(staleLanes) {
    this._render(staleLanes);
    this._backdrop.style.display = "flex";
    this._backdrop.style.pointerEvents = "auto";
    // Focus the close button for keyboard accessibility.
    const closeBtn = /** @type {HTMLButtonElement|null} */ (
      this._dialog.querySelector('[data-testid="stale-modal-close"]')
    );
    if (closeBtn) closeBtn.focus();
  }

  /** Close the modal. */
  close() {
    this._backdrop.style.display = "none";
    this._backdrop.style.pointerEvents = "none";
  }

  /**
   * Update the modal contents with fresh stale lanes data.
   * @param {StaleLane[]} staleLanes
   */
  update(staleLanes) {
    if (this.isOpen) {
      this._render(staleLanes);
    }
  }

  /**
   * Internal: render stale lane rows into the body.
   * @param {StaleLane[]} staleLanes
   */
  _render(staleLanes) {
    const count = staleLanes.length;
    this._titleEl.textContent = `Stale Lanes (${count})`;

    clearNode(this._body);

    if (count === 0) {
      this._body.appendChild(
        el("p", {
          "data-testid": "stale-modal-empty",
          style: "color: #9aa0a8; text-align: center; padding: 24px 0;",
        }, "No stale lanes detected.")
      );
      return;
    }

    for (const lane of staleLanes) {
      this._body.appendChild(this._buildLaneRow(lane));
    }
  }

  /**
   * Build a single stale-lane row element.
   * @param {StaleLane} lane
   * @returns {HTMLElement}
   */
  _buildLaneRow(lane) {
    const short = String(lane.lane);
    const durationText = formatDuration(lane.silent_seconds);
    const source = String(lane.last_activity_source || "—");

    // Lane chip.
    const chip = el("span", {
      class: `lane-chip ${short}`,
      "data-testid": `stale-lane-chip-${short}`,
      style: "flex-shrink: 0;",
    }, short);

    // Duration + source info.
    const infoText = el("span", {
      "data-testid": `stale-lane-info-${short}`,
      style: "color: #9aa0a8; flex: 1; min-width: 0;",
    },
      el("span", { "data-testid": `stale-lane-duration-${short}` }, durationText),
      " silent · ",
      el("span", { style: "opacity: 0.7;" }, source),
    );

    // Peek stream button.
    const peekBtn = el("button", {
      type: "button",
      class: "button",
      "data-testid": `stale-peek-${short}`,
      title: `Navigate to the live stream for lane ${short}`,
      onclick: () => {
        this.close();
        this._navigate(`/lane/${encodeURIComponent(short)}`);
      },
    }, "Peek stream");

    // Restart /loop button.
    const restartBtn = el("button", {
      type: "button",
      class: "button button--primary",
      "data-testid": `stale-restart-${short}`,
      title: `Restart the /loop cycle for lane ${short}. Sends the lane's initial_prompt to its tmux session.`,
      onclick: () => this._handleRestart(short, restartBtn),
    }, "Restart /loop");

    // Respawn button (stub — v9.4 scope does not include respawn endpoint).
    const respawnBtn = el("button", {
      type: "button",
      class: "button",
      "data-testid": `stale-respawn-${short}`,
      title: "Respawn is not implemented in v9.4 — use Restart /loop instead",
      disabled: true,
    }, "Respawn");

    const btnRow = el("div", {
      style: "display: flex; gap: 6px; flex-wrap: wrap; flex-shrink: 0;",
    }, peekBtn, restartBtn, respawnBtn);

    return el("div", {
      "data-testid": `stale-lane-row-${short}`,
      style: [
        "display: flex;",
        "align-items: center;",
        "gap: 10px;",
        "padding: 10px 12px;",
        "background: #15181d;",
        "border: 1px solid #2a2f37;",
        "border-radius: 4px;",
        "flex-wrap: wrap;",
      ].join(" "),
    }, chip, infoText, btnRow);
  }

  /**
   * Handle Restart /loop click for a lane.
   * @param {string} short
   * @param {HTMLButtonElement} btn
   */
  async _handleRestart(short, btn) {
    const csrf = this._getCsrfToken();
    btn.disabled = true;

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
        this._showToast(`Restarted /loop for lane ${short}`, "info");
        // Refresh the stale lane list.
        await this._onRefresh();
      } else {
        let detail = `HTTP ${resp.status}`;
        try {
          const body = await resp.json();
          if (body.detail) detail = `${resp.status}: ${body.detail}`;
        } catch (_) { /* ignore */ }
        this._showToast(`Restart failed — ${detail}`, "error");
        btn.disabled = false;
      }
    } catch (err) {
      this._showToast(`Network error — ${String(err)}`, "error");
      btn.disabled = false;
    }
  }
}
