// @ts-check
// mission.js — Megalodon orchestrator-console `/mission` page.
//
// Spec: findings/agent-1371-D-P2.5-frontend-plan-v2-2026-05-16T15-45Z.md
//   §C5 (control-mode gating) and base P1-D §3 `/mission`.
//
// Sections (top → bottom):
//   1. Mission summary card (id, phase, status, lanes-online count)
//   2. Mission events log (newest first)
//   3. Orchestrator actions panel (gated by ui.controlMode)
//
// Reactive: re-renders on store changes to mission.phase, mission.events,
// mission.missionStatus, status.lanes, ui.controlMode. Each subscription is
// collected and torn down by the returned cleanup function.
//
// Security: no innerHTML for any value sourced from the store or user input.
// All textual data flows through textContent / createElement / appendChild /
// the value property. Only static element creation uses createElement.

import { store } from "../js/store.js";
import {
  API_CHALLENGE, API_FINDINGS, API_INJECT_TASK, API_MISSION_STATUS,
  API_PHASE_FLIP, API_RECLAIM, API_SIGNAL,
} from "../js/constants.js";

// Canonical lane order — mirrors dashboard.js.
const LANE_ORDER = ["AUDIT", "ARCHITECT", "BACKEND", "FRONTEND", "TEST", "META"];

// Canonical v8 phases per MISSION.md:69-77.
// MANUAL_FLIPPABLE = operator can drive via phase-flip form.
// AUTO_OR_ORCHESTRATOR_ONLY = auto-flipped or orchestrator-gated; UI shows but does not offer in `to` select.
// Split per P2.5-D plan-v2 (TEST C6 + FE N1 refinement: COMPLETE excluded from manual because DRAINING→COMPLETE is gated on lane-drain + META capstone + HISTORY quiet >10min per MISSION.md:142).
const MANUAL_FLIPPABLE_PHASES = [
  "PHASE-PLAN",
  "PHASE-CHALLENGE",
  "PHASE-BUILD",
  "PHASE-VERIFY",
  "DRAINING",
];
const AUTO_OR_ORCHESTRATOR_ONLY_PHASES = [
  "INIT",
  "PHASE-RUN",
  "PHASE-HEAL",
  "PHASE-OPERATOR-ACCEPTANCE",
  "COMPLETE",
];
const ALL_PHASES = [...MANUAL_FLIPPABLE_PHASES, ...AUTO_OR_ORCHESTRATOR_ONLY_PHASES];

// Canonical v8 mission statuses per README.md:58-63.
const MISSION_STATUSES = [
  "IDLE",
  "ACTIVE",
  "PHASE-PLAN",
  "PHASE-CHALLENGE",
  "PHASE-BUILD",
  "PHASE-VERIFY",
  "DRAINING",
  "COMPLETE",
];

// inject-task: section options match TASKS.md `##`/`###` headers (server fuzzy-matches).
const TASK_SECTIONS = [
  "PHASE 1 — PLAN",
  "PHASE 2 — CHALLENGE",
  "PHASE 2.5 — Plan-v2 reconciliation",
  "PHASE 3 — BUILD",
  "PHASE 4 — VERIFY",
  "PHASE 5 — RUN",
  "OPERATOR-ACCEPTANCE TASKS",
  "CHALLENGE TASKS",
  "CROSS-LANE / SECONDARY TASK POOL",
];

// inject-task client-side validation: API contract regex MINUS Unicode arrow (v8 Edit 3 ASCII-only).
// Server still accepts `→` via the v7 compat shim; UI blocks per TEST C7 (P2-E-to-D §C7).
const INJECT_TASK_REGEX = /^\[ \] \[[A-Z\-\d]+\] `[A-Za-z0-9\-\.]+` — .+$/;
const UNICODE_ARROW = "→";

// ---- helpers --------------------------------------------------------------

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "dataset") {
        for (const [dk, dv] of Object.entries(v)) node.dataset[dk] = String(dv);
      } else if (k === "value") {
        // Form-control value — set as property, never as attribute (XSS-safe).
        node.value = v;
      } else if (k === "checked") {
        node.checked = !!v;
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

function slugifyUtc(utc) {
  return String(utc || "").replace(/[^A-Za-z0-9_-]/g, "-");
}

function getCsrf() {
  const m = document.querySelector('meta[name="csrf-token"]');
  return (m && m.getAttribute("content")) || "";
}

// ---- POST helper ----------------------------------------------------------

async function postAction(url, body) {
  const csrf = getCsrf();
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    credentials: "same-origin",
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let errBody = {};
    try { errBody = await res.json(); } catch (_) { /* ignore */ }
    throw new Error(errBody.error || `HTTP ${res.status}`);
  }
  return res.json();
}

// ---- toast ----------------------------------------------------------------

function showToast(message, kind) {
  const region = document.getElementById("toast-region");
  if (!region) return;
  const toast = el(
    "div",
    {
      class: `toast toast--${kind || "info"}`,
      role: kind === "error" ? "alert" : "status",
      "aria-live": kind === "error" ? "assertive" : "polite",
    },
    String(message || "")
  );
  region.appendChild(toast);
  setTimeout(() => {
    if (toast.parentNode === region) region.removeChild(toast);
  }, 4000);
}

// ---- summary card ---------------------------------------------------------

function renderSummaryCard(container) {
  clearNode(container);
  const phase = String(store.get("mission.phase") || "—");
  const missionStatusRaw = store.get("mission.missionStatus");
  // mission.missionStatus may be a plain string or an object that includes id.
  let missionId = "Megalodon Self-Improvement";
  let missionStatus = "—";
  if (missionStatusRaw && typeof missionStatusRaw === "object") {
    missionId = String(missionStatusRaw.id || missionStatusRaw.mission_id || missionId);
    missionStatus = String(missionStatusRaw.status || "—");
  } else if (typeof missionStatusRaw === "string" && missionStatusRaw) {
    missionStatus = missionStatusRaw;
  }
  const lanes = store.get("status.lanes") || [];
  const lanesOnline = lanes.filter((l) => l && l.agent && l.agent !== "—" && l.agent !== null).length;

  container.appendChild(el("h2", { class: "card__title" }, "Mission"));
  container.appendChild(el(
    "div",
    { class: "stack-2" },
    el("div", { class: "row", style: "gap: var(--sp-2); align-items: baseline;" },
      el("span", { class: "mono", "data-testid": "mission-id" }, missionId),
      el("span", { class: `badge mission-status mission-status--${missionStatus}`, "data-testid": "mission-status-badge" }, missionStatus),
    ),
    el("div", { class: "row", style: "gap: var(--sp-3); align-items: baseline; flex-wrap: wrap;" },
      el("div", { class: "stack-1", "data-testid": "current-phase" },
        el("div", { class: "mono", style: "font-size: 11px; opacity: 0.7;" }, "Current phase"),
        el("div", { class: "phase-display", "data-testid": "mission-phase", style: "font-size: 1.5rem; font-weight: 600;" }, phase),
      ),
      el("div", { class: "stack-1" },
        el("div", { class: "mono", style: "font-size: 11px; opacity: 0.7;" }, "Lanes online"),
        el("div", { "data-testid": "lanes-online-count", style: "font-size: 1.5rem; font-weight: 600;" },
          `${lanesOnline} / ${LANE_ORDER.length}`),
      ),
    ),
  ));
}

// ---- events log -----------------------------------------------------------

function parseUtcMillis(utc) {
  if (!utc) return NaN;
  const m = String(utc).match(/^(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})Z$/);
  const iso = m ? `${m[1]}T${m[2]}:${m[3]}:00Z` : String(utc);
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : NaN;
}

function renderEventsLog(container) {
  clearNode(container);
  container.appendChild(el("h2", { class: "card__title" }, "Mission events"));

  const events = (store.get("mission.events") || []).slice();
  // Newest first.
  events.sort((a, b) => {
    const ta = parseUtcMillis(a?.utc) || 0;
    const tb = parseUtcMillis(b?.utc) || 0;
    return tb - ta;
  });

  if (events.length === 0) {
    const empty = el("p", { class: "empty-state", "data-testid": "mission-events-empty" },
      "No phase events yet.");
    container.appendChild(empty);
    return;
  }

  const list = el("ul", {
    class: "stack-1 mission-events-list",
    style: "list-style: none; padding: 0; margin: 0;",
  });
  for (const ev of events) {
    const utc = String(ev.utc || "");
    const fromPhase = String(ev.from_phase || ev.from || "—");
    const toPhase = String(ev.to_phase || ev.to || "—");
    const byAgent = String(ev.by_agent || ev.agent || "—");
    const reason = String(ev.reason || "");

    const li = el(
      "li",
      {
        class: "mission-event mono",
        "data-testid": `mission-event-${slugifyUtc(utc)}`,
        style: "padding: var(--sp-1) 0; border-bottom: 1px solid var(--border, rgba(255,255,255,0.08));",
      },
      el("div", { class: "row", style: "gap: var(--sp-2); align-items: baseline; flex-wrap: wrap;" },
        el("span", { class: "mono", style: "opacity: 0.7;" }, utc || "—"),
        el("span", { class: "phase-transition" },
          el("span", { class: "phase-from" }, fromPhase),
          el("span", { style: "margin: 0 0.25rem;" }, "→"),
          el("span", { class: "phase-to" }, toPhase),
        ),
        el("span", { class: "mono", style: "opacity: 0.8;" }, `by ${byAgent}`),
      ),
      reason
        ? el("div", { class: "mono", style: "opacity: 0.8; margin-top: 2px;" }, reason)
        : null,
    );
    list.appendChild(li);
  }
  container.appendChild(list);
}

// ---- action forms ---------------------------------------------------------

function makeFormCard(testid, title, fields, submitLabel, onSubmit, opts) {
  opts = opts || {};
  // B5 (P2.5-D + TEST C1): optional 2-step confirm for destructive ops.
  const confirmTestId = opts.confirmTestId || "";
  const confirmLabel = opts.confirmLabel || `Confirm: ${submitLabel}`;
  let pendingConfirm = false;

  const errorBoxAttrs = {
    class: "form-error",
    role: "alert",
    "aria-live": "assertive",
    hidden: true,
    style: "color: var(--danger, #f66); font-size: 0.85rem;",
  };
  if (opts.errorTestId) errorBoxAttrs["data-testid"] = opts.errorTestId;
  const errorBox = el("div", errorBoxAttrs);

  const form = el("form", {
    class: "stack-2",
    "data-testid": testid,
    novalidate: "true",
    onsubmit: async (ev) => {
      ev.preventDefault();
      errorBox.hidden = true;
      errorBox.textContent = "";
      const primaryBtn = form.querySelector('button[data-role="primary-submit"]');
      const confirmBtn = form.querySelector('button[data-role="confirm-submit"]');
      const cancelBtn = form.querySelector('button[data-role="cancel-confirm"]');

      // Two-step confirm: first submit reveals confirm UI without POSTing.
      if (confirmTestId && !pendingConfirm) {
        pendingConfirm = true;
        if (primaryBtn) primaryBtn.hidden = true;
        if (confirmBtn) confirmBtn.hidden = false;
        if (cancelBtn) cancelBtn.hidden = false;
        return;
      }

      // POST step.
      if (primaryBtn) primaryBtn.disabled = true;
      if (confirmBtn) confirmBtn.disabled = true;
      try {
        await onSubmit(form);
        form.reset();
      } catch (err) {
        const msg = String((err && err.message) || err || "Submission failed");
        errorBox.textContent = msg;
        errorBox.hidden = false;
        showToast(`${title}: ${msg}`, "error");
      } finally {
        if (primaryBtn) primaryBtn.disabled = false;
        if (confirmBtn) confirmBtn.disabled = false;
        if (confirmTestId) {
          pendingConfirm = false;
          if (primaryBtn) primaryBtn.hidden = false;
          if (confirmBtn) confirmBtn.hidden = true;
          if (cancelBtn) cancelBtn.hidden = true;
        }
      }
    },
  });

  form.appendChild(el("h3", { class: "card__title" }, title));
  for (const f of fields) form.appendChild(f);
  form.appendChild(errorBox);
  form.appendChild(el(
    "button",
    {
      type: "submit",
      "data-role": "primary-submit",
      class: "button button--primary",
      "data-testid": opts.submitTestId || `action-submit-${testid.replace(/^action-/, "")}`,
    },
    submitLabel,
  ));

  if (confirmTestId) {
    form.appendChild(el(
      "button",
      {
        type: "submit",
        "data-role": "confirm-submit",
        class: "button button--danger",
        "data-testid": confirmTestId,
        hidden: true,
      },
      confirmLabel,
    ));
    form.appendChild(el(
      "button",
      {
        type: "button",
        "data-role": "cancel-confirm",
        class: "button",
        hidden: true,
        onclick: () => {
          pendingConfirm = false;
          const pBtn = form.querySelector('button[data-role="primary-submit"]');
          const cBtn = form.querySelector('button[data-role="confirm-submit"]');
          const xBtn = form.querySelector('button[data-role="cancel-confirm"]');
          if (pBtn) pBtn.hidden = false;
          if (cBtn) cBtn.hidden = true;
          if (xBtn) xBtn.hidden = true;
        },
      },
      "Cancel",
    ));
  }

  return el("section", { class: "card stack-2" }, form);
}

function labeledInput(label, name, opts) {
  opts = opts || {};
  const id = `f-${name}-${Math.random().toString(36).slice(2, 8)}`;
  const inputAttrs = {
    id,
    name,
    type: opts.type || "text",
    class: "input",
    required: !!opts.required,
    placeholder: opts.placeholder || "",
  };
  if (opts.testid) inputAttrs["data-testid"] = opts.testid;
  const input = el("input", inputAttrs);
  return el("label", { class: "stack-1", for: id },
    el("span", { class: "mono", style: "font-size: 11px; opacity: 0.8;" },
      `${label}${opts.required ? " *" : ""}`),
    input,
  );
}

function labeledTextarea(label, name, opts) {
  opts = opts || {};
  const id = `f-${name}-${Math.random().toString(36).slice(2, 8)}`;
  const taAttrs = {
    id,
    name,
    class: "input",
    rows: opts.rows || 3,
    required: !!opts.required,
    placeholder: opts.placeholder || "",
  };
  if (opts.testid) taAttrs["data-testid"] = opts.testid;
  const ta = el("textarea", taAttrs);
  return el("label", { class: "stack-1", for: id },
    el("span", { class: "mono", style: "font-size: 11px; opacity: 0.8;" },
      `${label}${opts.required ? " *" : ""}`),
    ta,
  );
}

function labeledSelect(label, name, options, opts) {
  opts = opts || {};
  const id = `f-${name}-${Math.random().toString(36).slice(2, 8)}`;
  const selAttrs = { id, name, class: "input", required: !!opts.required };
  if (opts.testid) selAttrs["data-testid"] = opts.testid;
  const sel = el("select", selAttrs);
  for (const o of options) {
    sel.appendChild(el("option", { value: o }, o));
  }
  return el("label", { class: "stack-1", for: id },
    el("span", { class: "mono", style: "font-size: 11px; opacity: 0.8;" },
      `${label}${opts.required ? " *" : ""}`),
    sel,
  );
}

function labeledCheckbox(label, name) {
  const id = `f-${name}-${Math.random().toString(36).slice(2, 8)}`;
  return el("label", {
    class: "row",
    for: id,
    style: "gap: var(--sp-1); align-items: center;",
  },
    el("input", { id, name, type: "checkbox", class: "checkbox" }),
    el("span", { class: "mono", style: "font-size: 11px; opacity: 0.8;" }, label),
  );
}

function radioGroup(name, options) {
  const group = el("div", { class: "row", style: "gap: var(--sp-2); flex-wrap: wrap;", role: "radiogroup" });
  options.forEach((o, i) => {
    const id = `f-${name}-${i}-${Math.random().toString(36).slice(2, 8)}`;
    const radio = el("input", { id, type: "radio", name, value: o, required: true });
    if (i === 0) radio.checked = true;
    group.appendChild(el("label", {
      for: id,
      class: "row",
      style: "gap: 4px; align-items: center;",
    },
      radio,
      el("span", { class: "mono" }, o),
    ));
  });
  return group;
}

// B4: async-fetched finding-picker for CHALLENGE form (per TEST C1 `challenge-finding-picker` testid).
function buildFindingPickerField() {
  // REPAIR-7-ACTION-FORM-WIRING: spec test does `.locator('option').nth(1).click()`
  // which only works when options are visible. Native `<select>` hides options
  // until dropdown is opened. `size="6"` renders as a multi-line listbox where
  // all options are visible and individually clickable.
  const id = `f-finding-filename-${Math.random().toString(36).slice(2, 8)}`;
  const select = el("select", {
    id,
    name: "finding_filename",
    class: "input",
    required: true,
    size: 6,
    "data-testid": "challenge-finding-picker",
  });
  select.appendChild(el("option", { value: "", disabled: true, selected: true }, "Loading findings…"));

  fetch(API_FINDINGS, { headers: { "Accept": "application/json" }, credentials: "same-origin" })
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
    .then((data) => {
      const findings = Array.isArray(data && data.findings) ? data.findings : [];
      while (select.firstChild) select.removeChild(select.firstChild);
      select.appendChild(el(
        "option",
        { value: "", disabled: true, selected: true },
        `Pick a finding to CHALLENGE (${findings.length})`,
      ));
      findings
        .slice()
        .sort((a, b) => String((b && b.utc) || "").localeCompare(String((a && a.utc) || "")))
        .forEach((f) => {
          const sev = f && f.severity ? `[${f.severity}] ` : "";
          const lane = f && f.lane ? ` (${f.lane})` : "";
          const opt = el("option", { value: (f && f.filename) || "" }, `${sev}${(f && f.filename) || "?"}${lane}`);
          select.appendChild(opt);
        });
      if (findings.length === 0) {
        select.appendChild(el("option", { value: "", disabled: true }, "No findings available"));
      }
    })
    .catch((err) => {
      while (select.firstChild) select.removeChild(select.firstChild);
      select.appendChild(el(
        "option",
        { value: "", disabled: true, selected: true },
        `Error loading findings: ${String((err && err.message) || err)}`,
      ));
    });

  return el("label", { class: "stack-1", for: id },
    el("span", { class: "mono", style: "font-size: 11px; opacity: 0.8;" }, "finding_filename *"),
    select,
  );
}

function buildChallengeForm() {
  return makeFormCard(
    "action-inject-challenge",
    "Inject CHALLENGE",
    [
      buildFindingPickerField(),
      labeledTextarea("description (optional)", "description", { rows: 3 }),
    ],
    "Inject CHALLENGE",
    async (form) => {
      const fd = new FormData(form);
      const finding_filename = String(fd.get("finding_filename") || "").trim();
      const description = String(fd.get("description") || "").trim();
      if (!finding_filename) throw new Error("finding_filename is required");
      const body = description ? { finding_filename, description } : { finding_filename };
      const out = await postAction(API_CHALLENGE, body);
      showToast(`CHALLENGE injected: task ${out.task_id || "?"}`, "success");
    },
    { submitTestId: "submit-challenge" },
  );
}

function buildReclaimForm() {
  return makeFormCard(
    "action-reclaim-lane",
    "Reclaim Lane",
    [
      labeledSelect("lane", "lane", LANE_ORDER, { required: true }),
      labeledCheckbox("force", "force"),
    ],
    "Reclaim lane",
    async (form) => {
      const fd = new FormData(form);
      const lane = String(fd.get("lane") || "");
      const force = fd.get("force") === "on" || fd.get("force") === "true";
      if (!lane) throw new Error("lane is required");
      const out = await postAction(API_RECLAIM, { lane, force });
      showToast(`Reclaimed ${lane}: ${out.action || "ok"}`, "success");
    },
    { confirmTestId: "confirm-reclaim", confirmLabel: "Confirm reclaim" },
  );
}

function buildSignalForm() {
  // SIGNAL_FROM_OPTIONS: ORCH = orchestrator broadcast; LANE-A..F = per-lane source.
  const SIGNAL_FROM_OPTIONS = ["ORCH", ...LANE_ORDER];
  return makeFormCard(
    "action-post-signal",
    "Post SIGNAL",
    [
      labeledSelect("from_lane", "from_lane", SIGNAL_FROM_OPTIONS, { required: true, testid: "signal-from" }),
      labeledSelect("to_lane", "to_lane", LANE_ORDER, { required: true, testid: "signal-to" }),
      labeledTextarea("claim", "claim", { required: true, rows: 2, placeholder: "Short factual claim", testid: "signal-text" }),
      labeledInput("evidence", "evidence", { required: true, placeholder: "path/to/file.md:42", testid: "signal-cite" }),
    ],
    "Post SIGNAL",
    async (form) => {
      const fd = new FormData(form);
      const from_lane = String(fd.get("from_lane") || "");
      const to_lane = String(fd.get("to_lane") || "");
      const claim = String(fd.get("claim") || "").trim();
      const evidence = String(fd.get("evidence") || "").trim();
      if (!from_lane) throw new Error("from_lane is required");
      if (!to_lane) throw new Error("to_lane is required");
      if (!claim) throw new Error("claim is required");
      if (!evidence) throw new Error("evidence is required");
      if (!/^[^:\s]+:\d+/.test(evidence)) {
        throw new Error("evidence must be path:line format");
      }
      await postAction(API_SIGNAL, { from_lane, to_lane, claim, evidence });
      showToast(`SIGNAL posted ${from_lane} → ${to_lane}`, "success");
    },
    { submitTestId: "submit-signal", errorTestId: "signal-error" },
  );
}

function buildPhaseFlipTargetButtons() {
  // Per spec test_orchestrator_actions:62-67: target selection is button-per-phase
  // with data-testid="flip-target-{PHASE}". Click sets the hidden input value.
  const hiddenTo = el("input", { type: "hidden", name: "to", value: "", id: `f-flip-to-${Math.random().toString(36).slice(2, 8)}` });
  const targetGroup = el("div", { class: "row", style: "gap: var(--sp-2); flex-wrap: wrap;", role: "radiogroup", "aria-label": "Target phase" });
  for (const phase of MANUAL_FLIPPABLE_PHASES) {
    const btn = el("button", {
      type: "button",
      class: "button",
      "data-testid": `flip-target-${phase}`,
      "aria-pressed": "false",
      "data-phase": phase,
      onclick: () => {
        hiddenTo.value = phase;
        const all = targetGroup.querySelectorAll('button[data-testid^="flip-target-"]');
        all.forEach((b) => {
          const on = b.getAttribute("data-phase") === phase;
          b.setAttribute("aria-pressed", on ? "true" : "false");
          b.classList.toggle("button--primary", on);
        });
      },
    }, phase);
    targetGroup.appendChild(btn);
  }
  return el("label", { class: "stack-1" },
    el("span", { class: "mono", style: "font-size: 11px; opacity: 0.8;" }, "to *"),
    targetGroup,
    hiddenTo,
  );
}

function buildPhaseFlipForm() {
  return makeFormCard(
    "action-flip-mission",
    "Phase Flip",
    [
      labeledSelect("from", "from", ALL_PHASES, { required: true }),
      buildPhaseFlipTargetButtons(),
      labeledInput("reason", "reason", { required: true, placeholder: "Why is this flip happening?" }),
    ],
    "Flip phase",
    async (form) => {
      const fd = new FormData(form);
      const from = String(fd.get("from") || "");
      const to = String(fd.get("to") || "");
      const reason = String(fd.get("reason") || "").trim();
      if (!from || !to) throw new Error("from and to are required (pick a target phase)");
      if (from === to) throw new Error("from and to must differ");
      if (!reason) throw new Error("reason is required");
      await postAction(API_PHASE_FLIP, { from, to, reason });
      showToast(`Phase flipped ${from} → ${to}`, "success");
      // REPAIR-7: optimistic store update — SSE may lag; test asserts immediately.
      store.set("mission.phase", to);
    },
    { confirmTestId: "confirm-flip", confirmLabel: "Confirm phase flip" },
  );
}

function buildMissionStatusForm() {
  return makeFormCard(
    "action-mission-status",
    "Mission Status",
    [labeledSelect("status", "status", MISSION_STATUSES, { required: true, testid: "mission-status-value" })],
    "Update mission status",
    async (form) => {
      const fd = new FormData(form);
      const status = String(fd.get("status") || "");
      if (!status) throw new Error("status is required");
      await postAction(API_MISSION_STATUS, { status });
      showToast(`Mission status: ${status}`, "success");
      // REPAIR-7: optimistic store update — SSE may lag; badge re-renders on subscribe.
      store.set("mission.missionStatus", status);
    },
    { submitTestId: "submit-mission-status" },
  );
}

function buildInjectTaskForm() {
  return makeFormCard(
    "action-inject-task",
    "Inject Task",
    [
      labeledTextarea(
        "task_text (format: [ ] [LANE-X] `task-id` — description)",
        "task_text",
        { required: true, rows: 3, placeholder: "[ ] [LANE-D] `P3.5-D-followup` — describe the new task here", testid: "inject-task-text" },
      ),
      labeledSelect("section", "section", TASK_SECTIONS, { required: true, testid: "inject-task-section" }),
    ],
    "Inject task",
    async (form) => {
      const fd = new FormData(form);
      const task_text = String(fd.get("task_text") || "").trim();
      const section = String(fd.get("section") || "");
      if (!task_text) throw new Error("task_text is required");
      if (!section) throw new Error("section is required");
      if (task_text.includes(UNICODE_ARROW)) {
        throw new Error(
          "task_text contains Unicode arrow `→` — v8 Edit 3 mandates ASCII-only task IDs; use `-to-` instead (e.g. `P2-A-to-F`)",
        );
      }
      if (!INJECT_TASK_REGEX.test(task_text)) {
        throw new Error(
          'task_text must match: [ ] [LANE-X] `task-id` — description (ASCII task-id only; em-dash separator required)',
        );
      }
      await postAction(API_INJECT_TASK, { task_text, section });
      showToast(`Task injected into ${section}`, "success");
    },
    { submitTestId: "submit-inject-task" },
  );
}

function renderActionsPanel(container) {
  clearNode(container);
  const controlMode = !!store.get("ui.controlMode");
  container.appendChild(el("h2", { class: "card__title" }, "Orchestrator actions"));

  if (!controlMode) {
    container.appendChild(el("p", {
      class: "empty-state",
      "data-testid": "actions-panel-disabled",
    }, "Enable Control Mode in the header to access orchestrator actions."));
    return;
  }

  const grid = el("div", {
    "data-testid": "actions-panel-enabled",
    style: "display: grid; gap: var(--sp-2); grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));",
  });
  grid.appendChild(buildChallengeForm());
  grid.appendChild(buildReclaimForm());
  grid.appendChild(buildSignalForm());
  grid.appendChild(buildPhaseFlipForm());
  grid.appendChild(buildMissionStatusForm());
  grid.appendChild(buildInjectTaskForm());
  container.appendChild(grid);
}

// ---- stuck-phase-flip warning panel ---------------------------------------
// T-FX-FAILMODE-a: surface a warning when a phase-flip lock exists but
// .mission-events doesn't reflect the implied next phase. Element is always
// present in DOM (toBeAttached) but hidden when no stuck-lock data is available.

function detectStuckFlip() {
  // Reads `mission.stuckFlipLock` if BE exposes it. Shape (proposed):
  //   { from: "PHASE-PLAN", to: "PHASE-CHALLENGE", lock_age_seconds: 90 }
  // If absent, returns null and the panel stays hidden.
  const raw = store.get("mission.stuckFlipLock");
  if (!raw || typeof raw !== "object") return null;
  const from = String(raw.from_phase || raw.from || "");
  const to = String(raw.to_phase || raw.to || "");
  if (!from || !to) return null;
  return { from, to, age: Number(raw.lock_age_seconds || raw.age_seconds || 0) };
}

async function completeStuckFlip() {
  const lock = detectStuckFlip();
  if (!lock) { showToast("No stuck flip detected", "info"); return; }
  try {
    await postAction(API_PHASE_FLIP, { to_phase: lock.to, reason: "operator: complete-stuck-flip" });
    showToast(`Completed stuck flip ${lock.from} → ${lock.to}`, "info");
  } catch (err) {
    showToast(`Complete-flip failed: ${err.message || err}`, "error");
  }
}

function renderStuckFlipPanel(container) {
  clearNode(container);
  const lock = detectStuckFlip();
  // Always render the elements so testid locators resolve. Visibility/text
  // gate on data presence.
  const warning = el(
    "div",
    {
      class: "warning warning--stuck-flip",
      "data-testid": "warning-stuck-phase-flip",
      role: "alert",
      hidden: !lock,
    },
    lock ? `Stuck phase flip: ${lock.from} → ${lock.to} (lock held ${lock.age}s)` : ""
  );
  const action = el(
    "button",
    {
      type: "button",
      class: "button button--warning",
      "data-testid": "action-complete-stuck-flip",
      disabled: !lock,
      hidden: false, // attached but visually inert when no lock
      onclick: completeStuckFlip,
    },
    "Complete stuck flip"
  );
  container.appendChild(warning);
  container.appendChild(action);
}

// ---- HISTORY tail with drift detection ------------------------------------
// T-FX-FAILMODE-c: surface HISTORY drift entries with a warning glyph.
// A "drift" entry is one whose `from_phase` does not match the preceding
// event's `to_phase` (i.e. a non-monotonic phase transition that suggests
// HISTORY was edited or events were lost).

function annotateHistoryDrift(events) {
  const sorted = events.slice().sort((a, b) => {
    const ta = parseUtcMillis(a?.utc) || 0;
    const tb = parseUtcMillis(b?.utc) || 0;
    return ta - tb; // oldest first for drift comparison
  });
  let prevTo = null;
  return sorted.map((ev, i) => {
    const from = String(ev.from_phase || ev.from || "");
    const to = String(ev.to_phase || ev.to || "");
    const drift = i > 0 && prevTo && from && prevTo !== from;
    prevTo = to || prevTo;
    return { ev, drift };
  });
}

function renderHistoryTail(container) {
  clearNode(container);
  container.appendChild(el("h2", { class: "card__title" }, "HISTORY tail"));
  const events = store.get("mission.events") || [];
  const annotated = annotateHistoryDrift(events).reverse(); // newest first for display
  if (annotated.length === 0) {
    container.appendChild(el("p", { class: "empty-state" }, "no HISTORY entries"));
    return;
  }
  const list = el("ul", {
    class: "stack-1 history-tail",
    style: "list-style: none; padding: 0; margin: 0;",
  });
  for (const { ev, drift } of annotated) {
    const utc = String(ev.utc || "");
    const from = String(ev.from_phase || ev.from || "—");
    const to = String(ev.to_phase || ev.to || "—");
    const li = el(
      "li",
      {
        class: drift ? "history-entry history-entry--drift mono" : "history-entry mono",
        "data-testid": `history-entry-${slugifyUtc(utc)}`,
        "data-drift": drift ? "true" : "false",
        style: "padding: 2px 0;",
      },
      drift ? el("span", { class: "drift-glyph", title: "phase drift detected" }, "⚠ ") : null,
      el("span", { class: "mono", style: "opacity: 0.7;" }, utc),
      " ",
      el("span", { class: "phase-from" }, from),
      el("span", { style: "margin: 0 0.25rem;" }, "→"),
      el("span", { class: "phase-to" }, to),
    );
    list.appendChild(li);
  }
  container.appendChild(list);
}

// ---- top-level render -----------------------------------------------------

export function render(root) {
  // Page skeleton (static structure only — store data goes through textContent).
  const summaryCard = el("section", {
    class: "card stack-2",
    "data-testid": "mission-summary-card",
  });

  const eventsCard = el("section", {
    class: "card stack-1",
    "data-testid": "mission-events-log",
  });

  const actionsCard = el("section", {
    class: "card stack-2",
    "data-testid": "orchestrator-actions-panel",
  });

  const stuckFlipCard = el("section", {
    class: "card stack-2",
    "data-testid": "stuck-flip-card",
  });

  const historyCard = el("section", {
    class: "card stack-1",
    "data-testid": "history-tail-card",
  });

  const page = el("div", { class: "mission-page stack-3" },
    summaryCard,
    stuckFlipCard,
    eventsCard,
    historyCard,
    actionsCard,
  );
  root.appendChild(page);

  // Initial paint.
  renderSummaryCard(summaryCard);
  renderStuckFlipPanel(stuckFlipCard);
  renderEventsLog(eventsCard);
  renderHistoryTail(historyCard);
  renderActionsPanel(actionsCard);

  // Subscriptions. Each call returns an unsubscribe; collect for cleanup.
  const unsubs = [];
  unsubs.push(store.subscribe("mission.phase", () => {
    renderSummaryCard(summaryCard);
  }));
  unsubs.push(store.subscribe("mission.events", () => {
    renderEventsLog(eventsCard);
    renderHistoryTail(historyCard);
  }));
  unsubs.push(store.subscribe("mission.missionStatus", () => {
    renderSummaryCard(summaryCard);
  }));
  unsubs.push(store.subscribe("status.lanes", () => {
    renderSummaryCard(summaryCard);
  }));
  unsubs.push(store.subscribe("ui.controlMode", () => {
    renderActionsPanel(actionsCard);
  }));
  unsubs.push(store.subscribe("mission.stuckFlipLock", () => {
    renderStuckFlipPanel(stuckFlipCard);
  }));

  return () => {
    for (const u of unsubs) {
      try { u(); } catch (_) { /* ignore */ }
    }
    clearNode(root);
  };
}

export default render;
