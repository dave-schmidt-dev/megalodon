// @ts-check
// components/alert_banner.js — Fleet alert banner/toast stack (Wave 3 FE task 5).
//
// Surfaces active fleet alerts from GET /api/v1/alerts as a stack of dismissible
// banners pinned to the top-right. Alerts are deduped by (lane, kind): a newer
// alert for the same (lane, kind) replaces the older one in place rather than
// stacking a duplicate. Clicking an alert deep-links to its lane.
//
// Contract (FROZEN, BE-owned):
//   GET /api/v1/alerts → { "alerts": [ {ts, lane, kind, severity, evidence:[...],
//   message}, ... ] }  newest-first.  kinds: CRASHED | HUNG | STATUS-STALE |
//   STREAM-LOG-SIZE.
//
// This component owns ONLY rendering + dedupe + dismiss state. The caller polls
// /api/v1/alerts (via authedFetch) and feeds the array to update(). Dismissed
// (lane,kind) pairs stay suppressed until a NEWER ts arrives for that pair, so a
// re-fire after the operator dismisses re-surfaces, but a steady-state poll does
// not spam.
//
// Security: no innerHTML; all text via textContent / DOM APIs.

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

/** Map an alert kind to a severity token color (border + accent). */
function kindColor(kind) {
  switch (String(kind || "").toUpperCase()) {
    case "CRASHED":
      return "var(--sev-blocking)";
    case "HUNG":
      return "var(--sev-major)";
    case "STATUS-STALE":
      return "var(--stale-stale)";
    case "STREAM-LOG-SIZE":
      return "var(--sev-minor)";
    default:
      return "var(--sev-major)";
  }
}

/** Stable dedupe key for an alert. */
function alertKey(a) {
  return `${a.lane}|${a.kind}`;
}

/**
 * Create the alert banner stack.
 *
 * @param {{ onNavigate?: (path: string) => void }} [opts]
 * @returns {{
 *   element: HTMLElement,
 *   update: (alerts: Array<{ts:string, lane:string, kind:string, severity?:string, evidence?:string[], message:string}>) => void,
 *   cleanup: () => void,
 * }}
 */
export function createAlertBanner({ onNavigate } = {}) {
  // IN-FLOW stack (front-door fix). Previously this was a body-level
  // `position: fixed; top: 64px; right: 12px` overlay, which physically covered
  // and intercepted pointer events on the header bar — the `activity ▸` toggle
  // (the only control that opens the activity wall), the `mission` /
  // `approval-rules` nav links, and the `board-kill-switch`. A right-aligned
  // fixed overlay covers right-aligned header controls at ANY top offset, so we
  // render the stack in normal document flow instead: the caller (board.js)
  // inserts it BELOW the header controls (after the mission header / alarm
  // strip), where it can never overlap them. Cards stay clickable (they
  // deep-link to lanes); they simply sit in the content column rather than
  // floating over the chrome.
  //
  // data-board-modal so the board's cleanup sweep can remove it if the page is
  // torn down without an explicit cleanup() call.
  const stack = el("div", {
    "data-testid": "alert-banner-stack",
    "data-board-modal": "true",
    style: [
      "display: flex;",
      "flex-direction: column;",
      "gap: var(--sp-2, 8px);",
      "width: 100%;",
      "max-width: 480px;",
      "margin-left: auto;", // right-align the stack within the content column
      // pointer-events: auto on the container is fine now — it occupies its own
      // flow box and never overlaps the header. Cards remain individually
      // clickable.
    ].join(" "),
  });

  /**
   * Dismissed (lane,kind) → the ts that was dismissed. A later poll with a
   * strictly-newer ts for the same key clears the suppression (alert re-fired).
   * @type {Map<string, string>}
   */
  const dismissed = new Map();

  /**
   * Rendered banner elements by key, so update() can replace in place.
   * @type {Map<string, HTMLElement>}
   */
  const rendered = new Map();

  /** Render (or re-render) one alert banner element. */
  function buildBanner(a) {
    const color = kindColor(a.kind);

    const dismissBtn = el("button", {
      type: "button",
      class: "button",
      "data-testid": `alert-dismiss-${a.lane}-${a.kind}`,
      title: "Dismiss this alert.",
      style: "flex: 0 0 auto; min-width: 24px; height: 24px; padding: 0; font-size: 14px; line-height: 1;",
    }, "×");
    dismissBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      dismissed.set(alertKey(a), String(a.ts || ""));
      removeBanner(alertKey(a));
    });

    const kindEl = el("span", {
      style: [
        "font-weight: 600;",
        "text-transform: uppercase;",
        "letter-spacing: 0.4px;",
        "font-size: var(--fs-xs, 11px);",
        `color: ${color};`,
        "flex: 0 0 auto;",
      ].join(" "),
    }, String(a.kind || "ALERT"));

    const laneEl = el("span", {
      class: "mono",
      style: "font-size: var(--fs-xs, 11px); color: var(--text-muted); flex: 0 0 auto;",
    }, String(a.lane || "?"));

    const head = el(
      "div",
      { style: "display: flex; align-items: center; gap: var(--sp-2, 8px);" },
      kindEl,
      laneEl,
      el("span", { style: "flex: 1 1 auto;" }),
      dismissBtn,
    );

    const msg = el("div", {
      style: "color: var(--text, #e6e6e6); font-size: var(--fs-sm, 13px); line-height: 1.4; word-break: break-word;",
    }, String(a.message || ""));

    const banner = el("div", {
      "data-testid": `alert-banner-${a.lane}-${a.kind}`,
      "data-alert-lane": String(a.lane || ""),
      "data-alert-kind": String(a.kind || ""),
      role: "button",
      tabindex: "0",
      title: `Open lane ${a.lane}.`,
      style: [
        "pointer-events: auto;",
        "cursor: pointer;",
        "background: var(--surface, #15171b);",
        `border: 1px solid ${color};`,
        `border-left: 3px solid ${color};`,
        "border-radius: var(--r-1, 4px);",
        "padding: var(--sp-2, 8px) var(--sp-3, 12px);",
        "box-shadow: 0 4px 16px rgba(0,0,0,0.45);",
        "display: flex;",
        "flex-direction: column;",
        "gap: var(--sp-1, 4px);",
        "font-family: ui-monospace, SFMono-Regular, Menlo, monospace;",
      ].join(" "),
      onclick: () => {
        if (typeof onNavigate === "function" && a.lane) {
          onNavigate(`/lane/${encodeURIComponent(a.lane)}`);
        }
      },
      onkeydown: (/** @type {KeyboardEvent} */ ev) => {
        if ((ev.key === "Enter" || ev.key === " ") && typeof onNavigate === "function" && a.lane) {
          ev.preventDefault();
          onNavigate(`/lane/${encodeURIComponent(a.lane)}`);
        }
      },
    }, head, msg);

    return banner;
  }

  function removeBanner(key) {
    const node = rendered.get(key);
    if (node && node.parentNode) node.parentNode.removeChild(node);
    rendered.delete(key);
  }

  /**
   * Apply a fresh alerts array (newest-first). Dedupe by (lane,kind): keep only
   * the newest per key. Suppress keys the operator dismissed unless a newer ts
   * arrived. Banners render newest-first (top of stack).
   * @param {Array<any>} alerts
   */
  function update(alerts) {
    const list = Array.isArray(alerts) ? alerts : [];
    // Dedupe by key keeping the FIRST occurrence (input is newest-first).
    /** @type {Map<string, any>} */
    const byKey = new Map();
    for (const a of list) {
      if (!a || !a.lane || !a.kind) continue;
      const k = alertKey(a);
      if (!byKey.has(k)) byKey.set(k, a);
    }

    // Drop any rendered banner whose key is no longer active.
    for (const k of [...rendered.keys()]) {
      if (!byKey.has(k)) removeBanner(k);
    }

    // Render active alerts newest-first. Stack order follows iteration order of
    // byKey (insertion = newest-first), so prepend isn't needed — we re-append
    // in order after clearing positions that changed.
    for (const [k, a] of byKey) {
      const ts = String(a.ts || "");
      // Suppressed by an earlier dismiss with a >= ts? skip.
      const dz = dismissed.get(k);
      if (dz !== undefined) {
        if (ts && dz && ts > dz) {
          dismissed.delete(k); // re-fired with a newer ts → re-surface
        } else {
          // Still dismissed (no newer ts). Ensure not shown.
          removeBanner(k);
          continue;
        }
      }
      const existing = rendered.get(k);
      if (existing && existing.dataset.alertTs === ts) {
        continue; // unchanged — leave in place
      }
      // (Re)build and (re)insert in correct order.
      removeBanner(k);
      const banner = buildBanner(a);
      banner.dataset.alertTs = ts;
      rendered.set(k, banner);
      stack.appendChild(banner);
    }
  }

  function cleanup() {
    rendered.clear();
    dismissed.clear();
    if (stack.parentNode) stack.parentNode.removeChild(stack);
  }

  return { element: stack, update, cleanup };
}
