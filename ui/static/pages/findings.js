// @ts-check
/**
 * /findings — Findings explorer page (v9.4 rewrite).
 *
 * Renders a flat list of findings fetched once on mount. Clicking a row opens
 * a side drawer with the full finding body. ESC or the close button dismisses
 * the drawer.
 *
 * Contract: export async function render(root, params) → cleanup().
 * Security: no innerHTML with user-sourced data — all text via textContent.
 */

import { API_FINDINGS } from "../js/constants.js";
import { loadConfig } from "../js/config.js";
import { authedFetch } from "../js/auth.js";

// ---------------------------------------------------------------------------
// DOM helpers (same pattern as board.js / lane_detail.js)
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

function clearNode(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

// ---------------------------------------------------------------------------
// parseFindingFilename — parse structured filename into metadata parts.
//
// Grammar (v9.2 protocol):
//   agent-XXXX-L-PHASE-topic-slug-YYYY-MM-DDTHH-MMZ.md
//
// Returns {agent, lane, phase, topic, utc} — empty strings for missing parts.
// Never throws.
// ---------------------------------------------------------------------------

/**
 * @param {string} filename
 * @returns {{ agent: string, lane: string, phase: string, topic: string, utc: string }}
 */
export { buildLaneResolver };

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

// Last-resort lane short→name map. Only consulted when /api/v1/config is
// unavailable (network failure / pre-auth) AND the finding carries no usable
// lane field. The live source of truth is config.lanes (see buildLaneResolver),
// so a mission with non-default lanes still resolves correctly. Kept as a
// fallback so the no-config case never regresses to bare short codes.
const LANE_SHORT_TO_NAME_FALLBACK = {
  A: "AUDIT",
  B: "ARCHITECT",
  C: "BACKEND",
  D: "FRONTEND",
  E: "TEST",
  F: "META",
};

/**
 * Build a short-code → lane-name resolver from the loaded config's `lanes`
 * array (`[{ name, short }]`). Falls back to LANE_SHORT_TO_NAME_FALLBACK for
 * any short code the config does not define, then to the short code itself.
 *
 * @param {{lanes?: Array<{name?: string, short?: string}>}|null} config
 * @returns {(short: string) => string}
 */
function buildLaneResolver(config) {
  /** @type {Record<string, string>} */
  const fromConfig = {};
  const lanes = config && Array.isArray(config.lanes) ? config.lanes : [];
  for (const lane of lanes) {
    const short = String((lane && lane.short) || "").toUpperCase();
    const name = String((lane && lane.name) || "").trim();
    if (short && name) fromConfig[short] = name;
  }
  return function laneShortToName(short) {
    const k = String(short || "").toUpperCase();
    return fromConfig[k] || LANE_SHORT_TO_NAME_FALLBACK[k] || k;
  };
}

/**
 * Render a "time ago" string from a UTC stamp string.
 * Accepts both `YYYY-MM-DDTHH-MMZ` (filename form) and ISO 8601.
 * Returns "" for unparseable input.
 *
 * @param {string} utc
 * @returns {string}
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

// ---------------------------------------------------------------------------
// Drawer
// ---------------------------------------------------------------------------

/**
 * Build the side drawer element. Initially hidden.
 * Returns { drawerEl, open(finding), close() }.
 *
 * @param {() => void} onClose  Called when the drawer is dismissed.
 */
function buildDrawer(onClose) {
  const bodyEl = el("pre", {
    "data-testid": "finding-drawer-body",
    style: [
      "white-space: pre-wrap;",
      "word-break: break-word;",
      "margin: 0;",
      "font-family: ui-monospace, SFMono-Regular, Menlo, monospace;",
      "font-size: 12px;",
      "line-height: 1.6;",
      "color: var(--c-text, #e6e6e6);",
    ].join(" "),
  });

  const header = el("div", {
    style: [
      "display: flex;",
      "justify-content: space-between;",
      "align-items: center;",
      "padding: 8px 12px;",
      "border-bottom: 1px solid var(--c-border, #2a2f37);",
      "gap: 8px;",
    ].join(" "),
  },
    el("span", {
      "data-testid": "finding-drawer-filename",
      style: "font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; opacity: 0.8; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;",
    }),
    el("button", {
      type: "button",
      "data-testid": "finding-drawer-close",
      title: "Close finding drawer (ESC)",
      style: [
        "background: none;",
        "border: none;",
        "color: var(--c-text-muted, #9aa0a8);",
        "cursor: pointer;",
        "font-size: 18px;",
        "line-height: 1;",
        "padding: 0 4px;",
        "flex-shrink: 0;",
      ].join(" "),
      onclick: () => close(),
    }, "×"),
  );

  const drawerEl = el("aside", {
    "data-finding-drawer": "",
    "data-testid": "finding-drawer",
    style: [
      "display: none;",
      "position: fixed;",
      "top: 0;",
      "right: 0;",
      "width: 480px;",
      "max-width: 90vw;",
      "height: 100vh;",
      "background: #1a1e24;",
      "border-left: 1px solid var(--c-border, #2a2f37);",
      "flex-direction: column;",
      "z-index: 200;",
      "overflow: hidden;",
    ].join(" "),
  },
    header,
    el("div", {
      style: "flex: 1; overflow-y: auto; padding: 12px;",
    }, bodyEl),
  );

  const filenameSpan = drawerEl.querySelector('[data-testid="finding-drawer-filename"]');

  function open(finding) {
    if (filenameSpan) filenameSpan.textContent = finding.filename || "";
    bodyEl.textContent = finding.body || "";
    drawerEl.style.display = "flex";
  }

  function close() {
    drawerEl.style.display = "none";
    onClose();
  }

  return { drawerEl, open, close };
}

// ---------------------------------------------------------------------------
// Row builder
// ---------------------------------------------------------------------------

/**
 * @param {{ filename: string, severity?: string, lane?: string, agent?: string, utc?: string }} meta
 * @param {(meta: object) => void} onClick
 * @param {(short: string) => string} laneShortToName  config-derived short→name resolver
 * @returns {HTMLElement}
 */
function buildRow(meta, onClick, laneShortToName) {
  const parsed = parseFindingFilename(meta.filename);

  // Resolve fields: prefer server-supplied over filename-parsed.
  const laneFull = String(meta.lane || "").toUpperCase();
  // Server may return "LANE-A" form; extract the letter.
  const laneRaw = laneFull.startsWith("LANE-") ? laneFull.slice(5) : laneFull;
  const laneShort = laneRaw || parsed.lane;
  const laneName = laneShortToName(laneShort);
  const agent = meta.agent || parsed.agent || "—";
  const phase = parsed.phase || "";
  const utcRaw = meta.utc || parsed.utc || "";
  const ago = utcAgo(utcRaw);
  const sevValue = String(meta.severity || "DELTA").toUpperCase();
  const topic = parsed.topic || "";

  const row = el("div", {
    class: "finding-row",
    "data-testid": `finding-row-${meta.filename}`,
    "data-finding-lane": laneShort || laneName || "—",
    "data-finding-agent": agent,
    "data-finding-phase": phase || "—",
    role: "button",
    tabindex: "0",
    title: `${meta.filename} — click to view`,
    style: [
      "display: flex;",
      "flex-direction: column;",
      "gap: 4px;",
      "padding: 10px 12px;",
      "border-bottom: 1px solid var(--c-border, #2a2f37);",
      "cursor: pointer;",
    ].join(" "),
    onclick: () => onClick(meta),
    onkeydown: (/** @type {KeyboardEvent} */ e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onClick(meta); }
    },
  });

  // Header row: severity badge + lane chip + phase badge.
  const headerRow = el("div", {
    style: "display: flex; gap: 6px; align-items: center; flex-wrap: wrap;",
  },
    el("span", {
      class: `severity-badge ${sevValue}`,
      title: `severity: ${sevValue}`,
    }, sevValue),
    el("span", {
      class: `lane-chip ${laneName}`,
      title: `lane: ${laneShort}${laneName !== laneShort ? ` (${laneName})` : ""}`,
    }, laneName || laneShort || "—"),
  );
  if (phase) {
    headerRow.appendChild(el("span", {
      class: "badge",
      title: `phase: ${phase}`,
    }, phase));
  }
  row.appendChild(headerRow);

  // Topic / title line.
  if (topic) {
    row.appendChild(el("div", {
      style: "font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;",
      title: topic,
    }, topic));
  }

  // Meta row: agent + UTC with relative age.
  const metaRow = el("div", {
    style: "display: flex; gap: 8px; flex-wrap: wrap; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; opacity: 0.65;",
  },
    el("span", { title: `agent: ${agent}` }, agent),
  );
  if (utcRaw) {
    const utcText = ago ? `${utcRaw} · ${ago}` : utcRaw;
    metaRow.appendChild(el("span", { title: `utc: ${utcRaw}` }, utcText));
  }
  row.appendChild(metaRow);

  return row;
}

// ---------------------------------------------------------------------------
// Main render
// ---------------------------------------------------------------------------

/**
 * Render the /findings page into `root`.
 *
 * @param {HTMLElement} root
 * @param {Record<string, any>} _params
 * @returns {Promise<() => void>} cleanup
 */
export async function render(root, _params) {
  // Page skeleton.
  const page = el("div", {
    class: "findings-page",
    "data-testid": "findings-page",
    style: "display: flex; flex-direction: column; height: 100%;",
  });

  // Header.
  page.appendChild(el("h1", {
    "data-testid": "findings-heading",
    style: "font-size: 16px; font-weight: 600; padding: 12px 16px 8px; margin: 0; border-bottom: 1px solid var(--c-border, #2a2f37);",
  }, "Findings"));

  // List container.
  const listEl = el("div", {
    "data-testid": "findings-list",
    style: "flex: 1; overflow-y: auto;",
  });
  page.appendChild(listEl);

  root.appendChild(page);

  // Build drawer (appended to document.body so it overlays freely).
  let currentFilename = "";
  let drawerOpen = false;

  const drawer = buildDrawer(() => {
    drawerOpen = false;
    currentFilename = "";
  });
  document.body.appendChild(drawer.drawerEl);

  // ESC listener.
  function onKeyDown(e) {
    if (e.key === "Escape" && drawerOpen) {
      drawerOpen = false;
      currentFilename = "";
      drawer.drawerEl.style.display = "none";
    }
  }
  document.addEventListener("keydown", onKeyDown);

  // Fetch findings + config in parallel. Config drives the lane short→name
  // resolver (single-flight cached). A config failure is non-fatal: the
  // resolver falls back to the static map so lane names never regress.
  let findings = [];
  /** @type {(short: string) => string} */
  let laneShortToName = buildLaneResolver(null);
  const [findingsResult, configResult] = await Promise.allSettled([
    authedFetch(API_FINDINGS, { headers: { Accept: "application/json" } }).then((r) =>
      r.ok ? r.json() : null,
    ),
    loadConfig(),
  ]);
  if (findingsResult.status === "fulfilled" && findingsResult.value) {
    findings = Array.isArray(findingsResult.value.findings) ? findingsResult.value.findings : [];
  } else if (findingsResult.status === "rejected") {
    console.warn("[findings] fetch failed:", findingsResult.reason);
  }
  if (configResult.status === "fulfilled") {
    laneShortToName = buildLaneResolver(configResult.value);
  } else {
    console.warn("[findings] config load failed; using fallback lane names:", configResult.reason);
  }

  // Render list.
  clearNode(listEl);

  if (findings.length === 0) {
    listEl.appendChild(el("div", {
      class: "empty-state",
      "data-testid": "findings-empty",
      style: "padding: 32px 16px; text-align: center; opacity: 0.6;",
    }, "No findings yet."));
  } else {
    for (const meta of findings) {
      listEl.appendChild(buildRow(meta, async (clickedMeta) => {
        currentFilename = clickedMeta.filename;
        drawerOpen = true;

        // Show drawer immediately with filename, fetch body.
        drawer.open({ filename: clickedMeta.filename, body: "Loading…" });

        try {
          const resp = await authedFetch(
            `${API_FINDINGS}/${encodeURIComponent(clickedMeta.filename)}`,
            { headers: { Accept: "application/json" } },
          );
          if (resp.ok) {
            const detail = await resp.json();
            // Only update if this is still the active finding.
            if (currentFilename === clickedMeta.filename && drawerOpen) {
              drawer.open({ filename: detail.filename, body: detail.body || "" });
            }
          } else {
            if (currentFilename === clickedMeta.filename && drawerOpen) {
              drawer.open({ filename: clickedMeta.filename, body: `Error: HTTP ${resp.status}` });
            }
          }
        } catch (err) {
          if (currentFilename === clickedMeta.filename && drawerOpen) {
            drawer.open({ filename: clickedMeta.filename, body: `Error: ${err && err.message ? err.message : "unknown"}` });
          }
        }
      }, laneShortToName));
    }
  }

  // Cleanup.
  return function cleanup() {
    document.removeEventListener("keydown", onKeyDown);
    // Close and remove drawer.
    drawer.drawerEl.style.display = "none";
    if (document.body.contains(drawer.drawerEl)) {
      document.body.removeChild(drawer.drawerEl);
    }
    // Do NOT clearNode(root): app.js clears the mount root before every render;
    // a stale cleanup clearing root can wipe a newer page (WebKit back-nav bug).
  };
}

export default render;
