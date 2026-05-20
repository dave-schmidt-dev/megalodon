// @ts-check
/**
 * /findings — Findings explorer page.
 *
 * Layout: sticky filter bar (lane chips, severity chips, agent/task search) over
 * a 2-pane split (list left ~40%, preview right ~60%).
 *
 * Data: reads `findings.list` from the store (FindingMeta[]). Fetches full
 * finding bodies lazily from GET /api/v1/findings/{filename}, caching results
 * under `findings.byFilename.<filename>`.
 *
 * Body markdown is delivered as server-rendered HTML. It MUST be sanitized
 * before insertion. See sanitizeHtmlInto() below.
 *
 * Contract: export render(root) -> cleanup().
 * Security: never assigns innerHTML; all dynamic text uses textContent or
 * sanitizeHtmlInto(), which parses via DOMParser and adopts only whitelisted
 * nodes/attributes.
 */

import { store } from "../js/store.js";
import { API_FINDINGS } from "../js/constants.js";
import { loadConfig } from "../js/config.js";

// ---- constants --------------------------------------------------------------

// Fallback lane list used until config resolves (v9.0 back-compat).
const LANES_FALLBACK = ["AUDIT", "ARCHITECT", "BACKEND", "FRONTEND", "TEST", "META"];
const SEVERITIES = ["BLOCKING", "MAJOR", "MINOR", "NIT", "DELTA"];

// Element whitelist for sanitizer. Anything else is dropped, but its safe
// children are still adopted (so a stray <section> still yields its contents).
const SAFE_TAGS = new Set([
  "h1", "h2", "h3", "h4", "h5", "h6",
  "p", "ul", "ol", "li",
  "code", "pre", "strong", "em", "b", "i",
  "blockquote",
  "a",
  "table", "thead", "tbody", "tr", "td", "th",
  "br", "hr",
  "div", "span",
  "details", "summary",
]);

// Attribute policy per tag. Any attribute not listed here is dropped.
const ATTR_POLICY = {
  a: ["href", "data-testid"],
  code: ["class", "data-testid"],
  pre: ["class", "data-testid"],
  div: ["class", "data-testid"],
  span: ["class", "data-testid"],
};
// Tags not in ATTR_POLICY can still carry data-testid if it's a safe value.

const CLASS_ALLOWLIST = /^(?:language-[\w-]+|markdown-body)$/;
const TESTID_ALLOWLIST = /^[A-Za-z0-9_-]{1,64}$/;
const HREF_ALLOWLIST = /^(?:https?:\/\/|\/|#)/;

// ---- sanitizer --------------------------------------------------------------

/**
 * Parse `html` into a detached document via DOMParser, then walk its body and
 * adopt only safe nodes into `target`. Always replaces existing children of
 * `target`. Drops disallowed tags (but keeps their safe children) and strips
 * every attribute that isn't explicitly whitelisted.
 *
 * @param {Element} target
 * @param {string} html
 */
export function sanitizeHtmlInto(target, html) {
  clearChildren(target);
  if (typeof html !== "string" || html.length === 0) return;
  let doc;
  try {
    doc = new DOMParser().parseFromString(html, "text/html");
  } catch (_) {
    return;
  }
  if (!doc || !doc.body) return;
  adoptChildren(doc.body, target);
}

/**
 * Adopt every child of `src` into `dst`, sanitizing on the way.
 * @param {Node} src
 * @param {Node} dst
 */
function adoptChildren(src, dst) {
  const children = src.childNodes;
  for (let i = 0; i < children.length; i++) {
    const node = children[i];
    if (node.nodeType === Node.TEXT_NODE) {
      // Text is inherently safe; clone with textContent only.
      dst.appendChild(document.createTextNode(node.nodeValue || ""));
      continue;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) {
      // Comments, CDATA, etc. are dropped silently.
      continue;
    }
    const el2 = /** @type {Element} */ (node);
    const tag = el2.tagName.toLowerCase();
    if (!SAFE_TAGS.has(tag)) {
      // Drop the wrapper, keep its children (depth-first salvage).
      adoptChildren(el2, dst);
      continue;
    }
    const safe = document.createElement(tag);
    copySafeAttrs(el2, safe, tag);
    adoptChildren(el2, safe);
    dst.appendChild(safe);
  }
}

/**
 * Copy only allowed attributes from `src` to `dst`. Validates each value
 * against its policy. Never copies style, on*, or generic data-*.
 * @param {Element} src
 * @param {Element} dst
 * @param {string} tag
 */
function copySafeAttrs(src, dst, tag) {
  const policy = ATTR_POLICY[tag] || ["data-testid"];
  for (const attr of policy) {
    if (!src.hasAttribute(attr)) continue;
    const raw = src.getAttribute(attr) || "";
    if (attr === "href") {
      if (!HREF_ALLOWLIST.test(raw)) continue;
      dst.setAttribute("href", raw);
      // Add safe rel for external links.
      if (/^https?:\/\//i.test(raw)) {
        dst.setAttribute("rel", "noopener noreferrer");
        dst.setAttribute("target", "_blank");
      }
    } else if (attr === "class") {
      const cls = raw.split(/\s+/).filter((c) => CLASS_ALLOWLIST.test(c));
      if (cls.length) dst.setAttribute("class", cls.join(" "));
    } else if (attr === "data-testid") {
      if (TESTID_ALLOWLIST.test(raw)) dst.setAttribute("data-testid", raw);
    }
  }
}

// ---- DOM helpers ------------------------------------------------------------

function clearChildren(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function el(tag, opts) {
  const node = document.createElement(tag);
  if (!opts) return node;
  if (opts.cls) node.className = opts.cls;
  if (opts.testid) node.setAttribute("data-testid", opts.testid);
  if (opts.text != null) node.textContent = String(opts.text);
  if (opts.attrs) {
    for (const k of Object.keys(opts.attrs)) {
      node.setAttribute(k, String(opts.attrs[k]));
    }
  }
  return node;
}

function slugifyFilename(filename) {
  // Strip .md and anything else unsafe for use in a data-testid.
  const base = String(filename || "").replace(/\.md$/i, "");
  return base.replace(/[^A-Za-z0-9_-]/g, "-").slice(0, 64);
}

/**
 * Parse a finding filename into structured parts.
 *
 * Grammar (from v9.2 protocol):
 *   agent-XXXX-L-PHASE-topic-slug-YYYY-MM-DDTHH-MMZ.md
 * - agent-XXXX → agent-id (4 hex chars)
 * - L → lane short code (A|B|C|D|E|F)
 * - PHASE → phase short (P1, P2, P2.5, P3, etc.)
 * - topic-slug → free-form topic
 * - YYYY-MM-DDTHH-MMZ or YYYY-MM-DDTHH-MM-SSZ → UTC stamp
 *
 * Returns {agent, lane, phase, topic, utc} with empty strings for any part
 * the filename does not include. Never throws.
 */
export function parseFindingFilename(filename) {
  const out = { agent: "", lane: "", phase: "", topic: "", utc: "" };
  const base = String(filename || "").replace(/\.md$/i, "").replace(/\.scratch$/i, "");
  if (!base) return out;
  // Greedy tail match on the UTC stamp.
  const utcRe = /-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}(?:-\d{2})?Z)$/;
  const m = base.match(utcRe);
  let head = base;
  if (m) {
    out.utc = m[1];
    head = base.slice(0, base.length - m[0].length);
  }
  // head looks like: agent-XXXX-L-PHASE-topic-slug
  const headRe = /^agent-([A-Za-z0-9]+)-([A-Z])-(P\d+(?:\.\d+)?)-(.*)$/;
  const hm = head.match(headRe);
  if (hm) {
    out.agent = `agent-${hm[1]}`;
    out.lane = hm[2];
    out.phase = hm[3];
    out.topic = hm[4];
  } else {
    // Best-effort partial parse.
    const partial = head.match(/^agent-([A-Za-z0-9]+)(?:-([A-Z]))?(?:-(P\d+(?:\.\d+)?))?(?:-(.*))?$/);
    if (partial) {
      if (partial[1]) out.agent = `agent-${partial[1]}`;
      if (partial[2]) out.lane = partial[2];
      if (partial[3]) out.phase = partial[3];
      if (partial[4]) out.topic = partial[4];
    }
  }
  return out;
}

// Map single-letter lane shorts → canonical lane names. Falls back to the
// raw short if unknown so we never display blank chips.
const LANE_SHORT_TO_NAME = {
  A: "AUDIT",
  B: "ARCHITECT",
  C: "BACKEND",
  D: "FRONTEND",
  E: "TEST",
  F: "META",
};

function laneShortToName(short) {
  const k = String(short || "").toUpperCase();
  return LANE_SHORT_TO_NAME[k] || k;
}

/**
 * Render a "time ago" string from a UTC stamp. Returns "" for unparseable input.
 * Accepts both YYYY-MM-DDTHH-MMZ (filename form) and ISO (YYYY-MM-DDTHH:MM:SSZ).
 */
function utcAgo(utc) {
  if (!utc) return "";
  const s = String(utc);
  // Convert filename-form to ISO.
  let iso = s;
  const m = s.match(/^(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})(?:-(\d{2}))?Z$/);
  if (m) {
    iso = `${m[1]}T${m[2]}:${m[3]}:${m[4] || "00"}Z`;
  }
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "";
  const deltaSec = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (deltaSec < 60) return `${deltaSec}s ago`;
  if (deltaSec < 3600) return `${Math.floor(deltaSec / 60)}m ago`;
  if (deltaSec < 86400) return `${Math.floor(deltaSec / 3600)}h ago`;
  return `${Math.floor(deltaSec / 86400)}d ago`;
}

// ---- filter state -----------------------------------------------------------

function makeFilterState() {
  return {
    lanes: new Set(), // empty = all
    severities: new Set(), // empty = all
    agent: "",
    task: "",
    selected: null, // filename or null
    includeScratch: false, // default: hide scratch files; chip toggles inclusion
  };
}

function isScratchFinding(meta) {
  const fn = String(meta?.filename || "");
  if (/\.scratch\.md$/i.test(fn)) return true;
  const ft = String(meta?.finding_type || meta?.["finding-type"] || "").toLowerCase();
  return ft === "scratch";
}

function passesFilters(meta, filters) {
  if (!filters.includeScratch && isScratchFinding(meta)) return false;
  if (filters.lanes.size > 0 && !filters.lanes.has(meta.lane)) return false;
  if (filters.severities.size > 0 && !filters.severities.has(meta.severity)) return false;
  if (filters.agent) {
    const hay = String(meta.agent || "").toLowerCase();
    if (!hay.includes(filters.agent.toLowerCase())) return false;
  }
  if (filters.task) {
    const hay = String(meta.task || "").toLowerCase();
    if (!hay.includes(filters.task.toLowerCase())) return false;
  }
  return true;
}

function sortByUtcDesc(list) {
  return list.slice().sort((a, b) => {
    const au = String(a.utc || "");
    const bu = String(b.utc || "");
    if (au < bu) return 1;
    if (au > bu) return -1;
    return 0;
  });
}

// ---- main render ------------------------------------------------------------

/**
 * @param {HTMLElement} root
 * @returns {Promise<() => void>} cleanup
 */
export async function render(root) {
  // PM-2 mitigation: show a loading skeleton until config resolves.
  const skeletonDiv = document.createElement("div");
  skeletonDiv.className = "loading-skeleton";
  skeletonDiv.textContent = "Loading mission config…";
  root.appendChild(skeletonDiv);

  // Load lane list from config; fall back to defaults on error.
  let lanes = LANES_FALLBACK;
  try {
    const config = await loadConfig();
    if (Array.isArray(config.lanes) && config.lanes.length > 0) {
      lanes = config.lanes.map((l) => (typeof l === "string" ? l : String(l.name || l)));
    }
  } catch (err) {
    console.warn("[findings] config load failed, using fallback lanes:", err);
  }

  // Clear skeleton before building real page structure.
  clearChildren(root);
  const page = el("div", { cls: "findings-page", testid: "findings-page" });
  root.appendChild(page);

  const filters = makeFilterState();

  // ---- filter bar ---------------------------------------------------------
  const bar = el("div", { cls: "findings-filterbar card" });
  page.appendChild(bar);

  const laneRow = el("div", { cls: "row findings-filter-row" });
  laneRow.appendChild(el("span", { cls: "text-muted mono", text: "lane:" }));
  const laneChips = {};
  for (const lane of lanes) {
    const chip = el("button", {
      cls: `lane-chip ${lane} findings-filter-chip`,
      testid: `lane-filter-${lane}`,
      text: lane,
      attrs: { type: "button", "aria-pressed": "false" },
    });
    chip.addEventListener("click", () => {
      if (filters.lanes.has(lane)) filters.lanes.delete(lane);
      else filters.lanes.add(lane);
      chip.setAttribute("aria-pressed", filters.lanes.has(lane) ? "true" : "false");
      chip.classList.toggle("is-active", filters.lanes.has(lane));
      renderList();
    });
    laneChips[lane] = chip;
    laneRow.appendChild(chip);
  }
  bar.appendChild(laneRow);

  const sevRow = el("div", { cls: "row findings-filter-row" });
  sevRow.appendChild(el("span", { cls: "text-muted mono", text: "severity:" }));
  const sevChips = {};
  for (const sev of SEVERITIES) {
    const chip = el("button", {
      cls: `severity-badge ${sev} findings-filter-chip`,
      testid: `filter-severity-${sev}`,
      text: sev,
      attrs: { type: "button", "aria-pressed": "false", "data-severity": sev },
    });
    chip.addEventListener("click", () => {
      if (filters.severities.has(sev)) filters.severities.delete(sev);
      else filters.severities.add(sev);
      chip.setAttribute("aria-pressed", filters.severities.has(sev) ? "true" : "false");
      chip.classList.toggle("is-active", filters.severities.has(sev));
      renderList();
    });
    sevChips[sev] = chip;
    sevRow.appendChild(chip);
  }
  const scratchChip = el("button", {
    cls: "findings-filter-chip badge",
    testid: "filter-scratch",
    text: "scratch",
    attrs: { type: "button", "aria-pressed": "false", title: "Toggle scratch-file visibility" },
  });
  scratchChip.addEventListener("click", () => {
    filters.includeScratch = !filters.includeScratch;
    scratchChip.setAttribute("aria-pressed", filters.includeScratch ? "true" : "false");
    scratchChip.classList.toggle("is-active", filters.includeScratch);
    renderList();
  });
  sevRow.appendChild(scratchChip);
  bar.appendChild(sevRow);

  const searchRow = el("div", { cls: "row findings-filter-row" });
  const agentInput = el("input", {
    cls: "findings-search",
    testid: "findings-agent-search",
    attrs: { type: "search", placeholder: "agent…", "aria-label": "Filter by agent id" },
  });
  agentInput.addEventListener("input", () => {
    filters.agent = agentInput.value || "";
    renderList();
  });
  const taskInput = el("input", {
    cls: "findings-search",
    testid: "findings-task-search",
    attrs: { type: "search", placeholder: "task…", "aria-label": "Filter by task id" },
  });
  taskInput.addEventListener("input", () => {
    filters.task = taskInput.value || "";
    renderList();
  });
  const clearBtn = el("button", {
    cls: "button",
    testid: "action-clear-filters",
    text: "Clear filters",
    attrs: { type: "button" },
  });
  clearBtn.addEventListener("click", () => {
    filters.lanes.clear();
    filters.severities.clear();
    filters.agent = "";
    filters.task = "";
    agentInput.value = "";
    taskInput.value = "";
    for (const lane of lanes) {
      laneChips[lane].setAttribute("aria-pressed", "false");
      laneChips[lane].classList.remove("is-active");
    }
    for (const sev of SEVERITIES) {
      sevChips[sev].setAttribute("aria-pressed", "false");
      sevChips[sev].classList.remove("is-active");
    }
    renderList();
  });
  searchRow.appendChild(agentInput);
  searchRow.appendChild(taskInput);
  searchRow.appendChild(clearBtn);
  bar.appendChild(searchRow);

  // ---- split pane ---------------------------------------------------------
  const split = el("div", { cls: "findings-split" });
  page.appendChild(split);

  const listPane = el("div", { cls: "findings-listpane", testid: "findings-list" });
  listPane.setAttribute("role", "listbox");
  listPane.setAttribute("aria-label", "Findings list");
  split.appendChild(listPane);

  const previewPane = el("div", { cls: "findings-preview card", testid: "finding-preview" });
  split.appendChild(previewPane);

  renderEmptyPreview();

  // ---- list rendering -----------------------------------------------------

  function renderList() {
    const raw = store.get("findings.list") || [];
    const filtered = raw.filter((m) => passesFilters(m, filters));
    const sorted = sortByUtcDesc(filtered);

    clearChildren(listPane);

    if (sorted.length === 0) {
      const empty = el("div", {
        cls: "empty-state",
        text: raw.length === 0
          ? "No findings yet."
          : "No findings match the current filters.",
      });
      listPane.appendChild(empty);
      return;
    }

    for (const meta of sorted) {
      listPane.appendChild(renderRow(meta));
    }
  }

  function renderRow(meta) {
    const slug = slugifyFilename(meta.filename);
    const scratch = isScratchFinding(meta);
    // Fall back to filename-parsed fields when the BE-supplied meta is sparse.
    // Real-world: many finding records arrive with only `filename` populated;
    // unlabeled chips were the symptom.
    const parsed = parseFindingFilename(meta.filename);
    const sevValue = meta.severity || "DELTA";
    const laneShort = String(meta.lane || parsed.lane || "").toUpperCase();
    const laneName = laneShortToName(laneShort);
    const agent = meta.agent || parsed.agent || "—";
    const task = meta.task || "";
    const phase = parsed.phase || "";
    const topic = parsed.topic || "";
    const utcRaw = meta.utc || parsed.utc || "";
    const ago = utcAgo(utcRaw);

    const row = el("div", {
      cls: "finding-row",
      testid: `finding-row-${slug}`,
      attrs: { role: "option", tabindex: "0", "data-scratch": scratch ? "true" : "false" },
    });
    const isSelected = filters.selected === meta.filename;
    row.setAttribute("aria-selected", isSelected ? "true" : "false");
    if (isSelected) row.classList.add("is-selected");

    const header = el("div", { cls: "row finding-row__header" });
    const sev = el("span", {
      cls: `severity-badge ${sevValue}`,
      text: meta.severity || "DELTA",
      attrs: { title: `severity: ${meta.severity || "DELTA"}`, "aria-label": `severity ${meta.severity || "DELTA"}` },
    });
    const lane = el("span", {
      cls: `lane-chip ${laneName}`,
      text: laneName || laneShort || "—",
      attrs: {
        title: laneShort ? `lane: ${laneShort} (${laneName})` : "lane: unknown",
        "aria-label": `lane ${laneName || laneShort || "unknown"}`,
      },
    });
    header.appendChild(sev);
    header.appendChild(lane);
    if (phase) {
      header.appendChild(el("span", {
        cls: "badge",
        text: phase,
        attrs: { title: `phase: ${phase}`, "aria-label": `phase ${phase}` },
      }));
    }
    if (meta.has_reconciliation) {
      const recon = el("span", {
        cls: "badge",
        text: "reconciled",
        attrs: { title: "Has reconciliation note" },
      });
      header.appendChild(recon);
    }
    row.appendChild(header);

    // Topic / title — prefer FE-extracted topic when meta has no title.
    const title = el("div", {
      cls: "finding-row__title truncate",
      text: meta.title || topic || meta.filename || "(untitled)",
      attrs: { title: meta.title || topic || meta.filename || "" },
    });
    row.appendChild(title);

    // Meta row: agent / task / utc (with "ago" hint).
    const meta1 = el("div", { cls: "row finding-row__meta mono text-muted" });
    meta1.appendChild(el("span", { text: agent, attrs: { title: `agent: ${agent}` } }));
    if (task) {
      meta1.appendChild(el("span", { text: task, attrs: { title: `task: ${task}` } }));
    }
    if (utcRaw) {
      const utcText = ago ? `${utcRaw} · ${ago}` : utcRaw;
      meta1.appendChild(el("span", { text: utcText, attrs: { title: `utc: ${utcRaw}` } }));
    } else {
      meta1.appendChild(el("span", { text: "—" }));
    }
    row.appendChild(meta1);

    // Filename row (subtle, full filename for operator confidence).
    row.appendChild(el("div", {
      cls: "finding-row__filename text-muted mono truncate",
      text: meta.filename || "",
      attrs: {
        title: meta.filename || "",
        style: "font-size: var(--fs-xs); opacity: 0.7;",
      },
    }));

    row.addEventListener("click", () => selectFinding(meta.filename));
    row.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        selectFinding(meta.filename);
      }
    });
    return row;
  }

  // ---- preview rendering --------------------------------------------------

  function renderEmptyPreview() {
    clearChildren(previewPane);
    const empty = el("div", {
      cls: "empty-state",
      text: "Select a finding to preview",
    });
    previewPane.appendChild(empty);
  }

  function renderLoadingPreview(filename) {
    clearChildren(previewPane);
    const head = el("div", { cls: "row" });
    head.appendChild(el("span", { cls: "mono text-muted", text: filename }));
    previewPane.appendChild(head);
    previewPane.appendChild(el("div", { cls: "empty-state", text: "Loading…" }));
  }

  function renderErrorPreview(filename, msg) {
    clearChildren(previewPane);
    previewPane.appendChild(el("span", { cls: "mono text-muted", text: filename }));
    previewPane.appendChild(el("div", {
      cls: "empty-state",
      text: `Failed to load finding: ${msg}`,
    }));
  }

  function renderFullPreview(finding) {
    clearChildren(previewPane);
    const fm = (finding && finding.frontmatter) || {};

    const header = el("div", { cls: "card finding-preview__header stack-2" });
    header.appendChild(el("div", {
      cls: "card__title mono",
      text: finding.filename || "",
    }));

    const fmRow = el("div", { cls: "row finding-preview__fm" });
    const fmFields = [
      ["lane", fm.lane],
      ["severity", fm.severity],
      ["agent", fm.agent],
      ["task", fm.task],
      ["utc", fm.utc],
    ];
    for (const [k, v] of fmFields) {
      if (v == null || v === "") continue;
      const cell = el("div", { cls: "finding-preview__fmcell" });
      cell.appendChild(el("div", { cls: "text-muted mono", text: k }));
      if (k === "lane") {
        cell.appendChild(el("span", { cls: `lane-chip ${v}`, text: String(v) }));
      } else if (k === "severity") {
        cell.appendChild(el("span", { cls: `severity-badge ${v}`, text: String(v) }));
      } else {
        cell.appendChild(el("div", { cls: "mono", text: String(v) }));
      }
      fmRow.appendChild(cell);
    }
    header.appendChild(fmRow);
    previewPane.appendChild(header);

    const body = el("div", { cls: "finding-preview__body markdown-body" });
    body.setAttribute("data-testid", "finding-preview-body");
    sanitizeHtmlInto(body, finding.body_markdown || "");
    previewPane.appendChild(body);
  }

  /** @type {AbortController|null} */
  let pendingFetch = null;

  function selectFinding(filename) {
    if (!filename) return;
    filters.selected = filename;
    try {
      if (location.hash !== `#${filename}`) {
        history.replaceState(null, "", `#${filename}`);
      }
    } catch (_) { /* ignore */ }
    // Refresh aria-selected on rows without full re-render of preview ordering.
    const rows = listPane.querySelectorAll(".finding-row");
    rows.forEach((r) => {
      const want = r.getAttribute("data-testid") === `finding-row-${slugifyFilename(filename)}`;
      r.setAttribute("aria-selected", want ? "true" : "false");
      r.classList.toggle("is-selected", want);
    });

    // Check cache first.
    const cached = store.get(`findings.byFilename.${filename}`);
    if (cached && cached.body_markdown != null) {
      renderFullPreview(cached);
      return;
    }

    renderLoadingPreview(filename);
    if (pendingFetch) {
      try { pendingFetch.abort(); } catch (_) { /* ignore */ }
    }
    pendingFetch = new AbortController();
    const signal = pendingFetch.signal;
    fetch(`${API_FINDINGS}/${encodeURIComponent(filename)}`, {
      headers: { Accept: "application/json" },
      signal,
    })
      .then((resp) => {
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return resp.json();
      })
      .then((finding) => {
        if (signal.aborted) return;
        store.set(`findings.byFilename.${filename}`, finding);
        if (filters.selected === filename) renderFullPreview(finding);
      })
      .catch((err) => {
        if (signal.aborted) return;
        if (filters.selected === filename) {
          renderErrorPreview(filename, err && err.message ? err.message : "unknown");
        }
      });
  }

  // ---- subscriptions ------------------------------------------------------

  const unsubList = store.subscribe("findings.list", () => renderList());
  const unsubByFilename = store.subscribe("findings.byFilename", () => {
    // If the cache was populated for the currently-selected item, paint it.
    if (!filters.selected) return;
    const cached = store.get(`findings.byFilename.${filters.selected}`);
    if (cached && cached.body_markdown != null) renderFullPreview(cached);
  });

  function onHashChange() {
    const h = String(location.hash || "");
    if (h.length > 1) {
      const fn = h.slice(1);
      if (fn !== filters.selected) selectFinding(fn);
    }
  }
  window.addEventListener("hashchange", onHashChange);

  // Initial paint.
  renderList();
  if (location.hash && location.hash.length > 1) {
    selectFinding(location.hash.slice(1));
  }

  // ---- cleanup ------------------------------------------------------------
  return function cleanup() {
    try { unsubList(); } catch (_) { /* ignore */ }
    try { unsubByFilename(); } catch (_) { /* ignore */ }
    window.removeEventListener("hashchange", onHashChange);
    if (pendingFetch) {
      try { pendingFetch.abort(); } catch (_) { /* ignore */ }
      pendingFetch = null;
    }
    clearChildren(root);
  };
}

export default { render, sanitizeHtmlInto };
