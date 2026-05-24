// @ts-check
// pages/approval_rules.js — /approval-rules management page (v9.4 T3.5).
//
// Layout:
//   - Page title "Approval Rules"
//   - Table of current rules: Pattern | Added | By Session | Action
//     - Each row has a [Remove] button (DELETE with CSRF)
//   - "Add manual rule" form: text input + Add button (POST with CSRF)
//   - Empty state: "No approval rules yet."
//
// Session id: derived from the mui_session cookie, matching the convention
// used in grid.js Approve&remember flow.
//
// No innerHTML. All DOM via textContent / createElement.

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
// Auth helpers
// ---------------------------------------------------------------------------

/** @returns {string} */
function getCsrfToken() {
  return document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") || "";
}

/** @returns {string} */
function getSessionId() {
  const m = document.cookie.match(/mui_session=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

// ---------------------------------------------------------------------------
// Toast helper (same pattern as grid.js)
// ---------------------------------------------------------------------------

/**
 * @param {string} message
 * @param {"info"|"error"} [kind]
 * @param {number} [durationMs]
 */
function showToast(message, kind = "info", durationMs = 3500) {
  const region = document.getElementById("toast-region");
  if (!region) return;
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
    if (region.contains(span)) region.removeChild(span);
  }, durationMs);
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

/**
 * Fetch current approval rules from the server.
 * @returns {Promise<Array<{pattern: string, added_at_utc: string, added_by_session: string}>>}
 */
async function fetchRules() {
  try {
    const resp = await fetch("/api/v1/approval-rules", { credentials: "include" });
    if (!resp.ok) return [];
    const json = await resp.json();
    return Array.isArray(json.rules) ? json.rules : [];
  } catch (_) {
    return [];
  }
}

/**
 * POST a new approval rule.
 * @param {string} pattern
 * @param {string} sessionId
 * @returns {Promise<{ok: boolean, status: number, body: any}>}
 */
async function postRule(pattern, sessionId) {
  const csrf = getCsrfToken();
  try {
    const resp = await fetch("/api/v1/approval-rules", {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        ...(csrf ? { "X-CSRF-Token": csrf } : {}),
      },
      body: JSON.stringify({ pattern, added_by_session: sessionId }),
    });
    let body = null;
    try { body = await resp.json(); } catch (_) { /* ignore */ }
    return { ok: resp.ok, status: resp.status, body };
  } catch (err) {
    return { ok: false, status: 0, body: String(err) };
  }
}

/**
 * DELETE an approval rule by exact pattern.
 * @param {string} pattern
 * @returns {Promise<{ok: boolean, status: number}>}
 */
async function deleteRule(pattern) {
  const csrf = getCsrfToken();
  try {
    const resp = await fetch(
      `/api/v1/approval-rules?pattern=${encodeURIComponent(pattern)}`,
      {
        method: "DELETE",
        credentials: "include",
        headers: csrf ? { "X-CSRF-Token": csrf } : {},
      }
    );
    return { ok: resp.status === 204, status: resp.status };
  } catch (err) {
    return { ok: false, status: 0 };
  }
}

// ---------------------------------------------------------------------------
// Table rendering
// ---------------------------------------------------------------------------

/**
 * Format an ISO-8601 UTC string as a readable local date/time.
 * @param {string} iso
 * @returns {string}
 */
function formatDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch (_) {
    return iso;
  }
}

/**
 * Render the rules table into `tableContainer`.
 * @param {HTMLElement} tableContainer
 * @param {Array<{pattern: string, added_at_utc: string, added_by_session: string}>} rules
 * @param {(pattern: string) => Promise<void>} onRemove
 */
function renderTable(tableContainer, rules, onRemove) {
  clearNode(tableContainer);

  if (rules.length === 0) {
    tableContainer.appendChild(el("p", {
      "data-testid": "approval-rules-empty",
      style: "color: #9aa0a8; padding: 12px 0;",
    }, "No approval rules yet."));
    return;
  }

  const table = el("table", {
    "data-testid": "approval-rules-table",
    style: [
      "width: 100%;",
      "border-collapse: collapse;",
      "font-size: 13px;",
      "font-family: ui-monospace, SFMono-Regular, Menlo, monospace;",
    ].join(" "),
  });

  // Header row.
  const thead = document.createElement("thead");
  const headerTr = document.createElement("tr");
  for (const label of ["Pattern", "Added", "By Session", "Action"]) {
    const th = el("th", {
      style: [
        "text-align: left;",
        "padding: 6px 10px;",
        "border-bottom: 1px solid #2a2f37;",
        "color: #9aa0a8;",
        "font-weight: 600;",
      ].join(" "),
    }, label);
    headerTr.appendChild(th);
  }
  thead.appendChild(headerTr);
  table.appendChild(thead);

  // Body rows.
  const tbody = document.createElement("tbody");
  for (const rule of rules) {
    const tr = el("tr", {
      "data-testid": `approval-rule-row`,
      "data-pattern": rule.pattern,
      style: "border-bottom: 1px solid #1e2228;",
    });

    const patternTd = el("td", {
      style: "padding: 8px 10px; word-break: break-all;",
      title: rule.pattern,
    }, rule.pattern);

    const addedTd = el("td", {
      style: "padding: 8px 10px; color: #9aa0a8; white-space: nowrap;",
      title: rule.added_at_utc,
    }, formatDate(rule.added_at_utc));

    const sessionTd = el("td", {
      style: "padding: 8px 10px; color: #9aa0a8; max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;",
      title: rule.added_by_session,
    }, rule.added_by_session || "—");

    const removeBtn = /** @type {HTMLButtonElement} */ (el("button", {
      type: "button",
      class: "button button--warning",
      "data-testid": "approval-rule-remove",
      title: `Remove approval rule: ${rule.pattern}`,
      onclick: async () => {
        removeBtn.disabled = true;
        await onRemove(rule.pattern);
      },
    }, "Remove"));

    const actionTd = el("td", { style: "padding: 8px 10px;" }, removeBtn);

    tr.appendChild(patternTd);
    tr.appendChild(addedTd);
    tr.appendChild(sessionTd);
    tr.appendChild(actionTd);
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  tableContainer.appendChild(table);
}

// ---------------------------------------------------------------------------
// Top-level render (contract: async function render(root, params) → cleanup)
// ---------------------------------------------------------------------------

/**
 * Render the approval rules management page into `root`.
 *
 * @param {HTMLElement} root
 * @param {Record<string, any>} _params
 * @returns {Promise<() => void>} cleanup function
 */
export async function render(root, _params) {
  // --- page structure ---
  const page = el("div", {
    "data-testid": "approval-rules-page",
    style: [
      "padding: 20px;",
      "max-width: 900px;",
      "font-family: ui-monospace, SFMono-Regular, Menlo, monospace;",
    ].join(" "),
  });

  const heading = el("h1", {
    "data-testid": "approval-rules-heading",
    style: "font-size: 18px; font-weight: 700; color: #e6e6e6; margin: 0 0 16px 0;",
  }, "Approval Rules");

  // --- table container (filled by renderTable) ---
  const tableContainer = el("div", {
    "data-testid": "approval-rules-table-container",
    style: "margin-bottom: 24px;",
  });

  // --- add form ---
  const patternInput = /** @type {HTMLInputElement} */ (el("input", {
    type: "text",
    "data-testid": "approval-rules-pattern-input",
    placeholder: "e.g. Bash(npm run *)",
    title: "Approval rule pattern (e.g. Bash(find:*) or Bash(curl http://host/*))",
    style: [
      "flex: 1;",
      "background: #15181d;",
      "color: #e6e6e6;",
      "border: 1px solid #2a2f37;",
      "border-radius: 4px;",
      "padding: 7px 10px;",
      "font-size: 13px;",
      "font-family: ui-monospace, SFMono-Regular, Menlo, monospace;",
    ].join(" "),
  }));

  const addBtn = /** @type {HTMLButtonElement} */ (el("button", {
    type: "button",
    class: "button button--primary",
    "data-testid": "approval-rules-add-btn",
    title: "Add a manual approval rule pattern.",
  }, "Add"));

  const addForm = el("div", {
    "data-testid": "approval-rules-add-form",
    style: [
      "display: flex;",
      "gap: 8px;",
      "align-items: center;",
      "padding: 14px;",
      "background: #1c1f24;",
      "border: 1px solid #2a2f37;",
      "border-radius: 4px;",
      "flex-wrap: wrap;",
    ].join(" "),
  },
    el("span", { style: "color: #9aa0a8; font-size: 12px; flex-basis: 100%;" }, "Add manual rule"),
    patternInput,
    addBtn,
  );

  page.appendChild(heading);
  page.appendChild(tableContainer);
  page.appendChild(addForm);
  root.appendChild(page);

  // --- load + render table ---
  async function refresh() {
    const rules = await fetchRules();
    renderTable(tableContainer, rules, async (pattern) => {
      const result = await deleteRule(pattern);
      if (result.ok) {
        await refresh();
      } else {
        showToast(`Remove failed — HTTP ${result.status}`, "error");
        // Re-enable any Remove button that was disabled.
        const rows = tableContainer.querySelectorAll('[data-testid="approval-rule-remove"]');
        rows.forEach((btn) => {
          if (btn instanceof HTMLButtonElement) btn.disabled = false;
        });
      }
    });
  }

  await refresh();

  // --- add button handler ---
  async function handleAdd() {
    const pattern = patternInput.value.trim();
    if (!pattern) {
      showToast("Pattern is required.", "error");
      return;
    }
    addBtn.disabled = true;
    const sessionId = getSessionId();
    const result = await postRule(pattern, sessionId);
    addBtn.disabled = false;
    if (result.ok) {
      patternInput.value = "";
      await refresh();
    } else {
      const detail = result.body?.detail || `HTTP ${result.status}`;
      showToast(`Add failed — ${detail}`, "error");
    }
  }

  addBtn.addEventListener("click", handleAdd);
  patternInput.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") handleAdd();
  });

  return function cleanup() {
    // No own resources to release. Do NOT clearNode(root): app.js clears the
    // mount root before every render, and a stale cleanup clearing root can wipe
    // a newer page (WebKit back-nav bug).
  };
}

export default render;
