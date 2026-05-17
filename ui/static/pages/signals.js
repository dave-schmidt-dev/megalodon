// @ts-check
/**
 * /signals — Inter-agent SIGNAL timeline (swim-lane SVG).
 *
 * Layout: filter chips bar (sender lanes, recipient lanes, kinds, time slider,
 * clear button) over an SVG swim-lane chart with 7 horizontal lanes. Each Signal
 * is drawn as a glyph on its sender lane at its UTC x-position, with a curved
 * arrow to the recipient lane. A hidden <table> below provides a screen-reader
 * text fallback.
 *
 * Data: reads `signals.list` from the store (Signal[]). Re-renders on change.
 *
 * Contract: export render(root) -> cleanup().
 *
 * Security: zero innerHTML. All dynamic text uses textContent, all attribute
 * values pass through setAttribute with stringified values (no HTML-context
 * interpolation), all SVG nodes are built via createElementNS.
 *
 * Per FRONTEND plan-v2 §C7 (per-glyph role=img + aria-label + hidden <table>
 * fallback) and P1-D §4 (visual design).
 */

import { store } from "../js/store.js";

// ---- constants --------------------------------------------------------------

const SVG_NS = "http://www.w3.org/2000/svg";

// Lane stack, top to bottom. ORCH first per spec; "ORCH" is the orchestrator
// virtual lane (no dedicated CSS color var, falls back to neutral text color).
const LANES = ["ORCH", "AUDIT", "ARCHITECT", "BACKEND", "FRONTEND", "TEST", "META"];
const KINDS = ["SIGNAL", "ACK-VERIFIED", "DISSENT", "DEFER"];

// SVG geometry.
const SVG_WIDTH = 1200;          // intrinsic; container CSS may scale
const LANE_HEIGHT = 56;
const LANE_LABEL_WIDTH = 110;
const PAD_TOP = 28;
const PAD_BOTTOM = 24;
const PAD_RIGHT = 24;
const GLYPH_R = 8;               // glyph base radius
const CLUSTER_BUCKET_PX = 12;    // §"Cluster algorithm" bucket size at zoom=1
const ZOOM_MIN = 0.25;
const ZOOM_MAX = 16;
const EDGE_AUTOSCROLL_THRESHOLD_PX = 24;

// ---- DOM helpers ------------------------------------------------------------

function clearChildren(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function el(tag, opts) {
  const node = document.createElement(tag);
  applyOpts(node, opts);
  return node;
}

function svgEl(tag, opts) {
  const node = document.createElementNS(SVG_NS, tag);
  applyOpts(node, opts);
  return node;
}

function applyOpts(node, opts) {
  if (!opts) return;
  if (opts.cls) node.setAttribute("class", opts.cls);
  if (opts.testid) node.setAttribute("data-testid", opts.testid);
  if (opts.text != null) node.textContent = String(opts.text);
  if (opts.attrs) {
    for (const k of Object.keys(opts.attrs)) {
      const v = opts.attrs[k];
      if (v == null) continue;
      node.setAttribute(k, String(v));
    }
  }
}

// ---- safe attribute slugs ---------------------------------------------------

function safeSlug(s) {
  // Keep alnum, dash, underscore. Replace everything else with "-".
  return String(s || "").replace(/[^A-Za-z0-9_-]/g, "-").slice(0, 96);
}

function laneClass(lane) {
  // Whitelist: known lanes only; otherwise empty.
  return LANES.indexOf(lane) >= 0 ? lane : "";
}

// ---- signal helpers ---------------------------------------------------------

function utcToMs(utc) {
  if (!utc) return NaN;
  // Accept the canonical "2026-05-16T15-40Z" form (dash for colons) as well
  // as ISO "2026-05-16T15:40Z" / "2026-05-16T15:40:00Z".
  let s = String(utc);
  // Replace "T15-40Z" or "T15-40-05Z" -> "T15:40Z" / "T15:40:05Z".
  const m = s.match(/^(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})(?:-(\d{2}))?Z$/);
  if (m) {
    s = `${m[1]}T${m[2]}:${m[3]}${m[4] ? ":" + m[4] : ""}Z`;
  }
  const t = Date.parse(s);
  return Number.isFinite(t) ? t : NaN;
}

function clamp(v, lo, hi) {
  return v < lo ? lo : v > hi ? hi : v;
}

function arrayUniq(xs) {
  const seen = new Set();
  const out = [];
  for (const x of xs) {
    if (!seen.has(x)) { seen.add(x); out.push(x); }
  }
  return out;
}

// ---- filter state -----------------------------------------------------------

function makeFilterState(signals) {
  const tBounds = signalsTimeBounds(signals);
  return {
    fromLanes: new Set(),   // empty = all
    toLanes: new Set(),     // empty = all
    kinds: new Set(),       // empty = all
    tMin: tBounds.min,
    tMax: tBounds.max,
    boundsMin: tBounds.min,
    boundsMax: tBounds.max,
  };
}

function signalsTimeBounds(signals) {
  let min = Infinity;
  let max = -Infinity;
  for (const s of signals) {
    const t = utcToMs(s.utc);
    if (!Number.isFinite(t)) continue;
    if (t < min) min = t;
    if (t > max) max = t;
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) {
    const now = Date.now();
    return { min: now - 60_000, max: now };
  }
  if (min === max) {
    return { min: min - 30_000, max: max + 30_000 };
  }
  return { min, max };
}

function passesFilters(sig, f) {
  if (f.fromLanes.size > 0 && !f.fromLanes.has(sig.from_lane)) return false;
  if (f.toLanes.size > 0 && !f.toLanes.has(sig.to)) return false;
  if (f.kinds.size > 0 && !f.kinds.has(sig.kind)) return false;
  const t = utcToMs(sig.utc);
  if (Number.isFinite(t)) {
    if (t < f.tMin || t > f.tMax) return false;
  }
  return true;
}

// ---- aria-label composition (safe: textContent only at render time) ---------

function composeAriaLabel(sig) {
  // Build via array join — never inlined into HTML. Caller setAttribute()s it.
  const claim = sig.claim ? `regarding '${sig.claim}'` : "";
  const ev = sig.evidence || {};
  const evPath = ev.path ? `evidence ${ev.path}${ev.line ? ":" + ev.line : ""}` : "";
  return [
    `${sig.from_lane || "?"} sent ${sig.kind || "SIGNAL"} to ${sig.to || "?"}`,
    `at ${sig.utc || "?"}`,
    claim,
    evPath ? `— ${evPath}` : "",
  ].filter(Boolean).join(" ");
}

// ---- main render ------------------------------------------------------------

/**
 * @param {HTMLElement} root
 * @returns {() => void} cleanup
 */
export function render(root) {
  clearChildren(root);

  const page = el("div", { cls: "signals-page", testid: "signals-page" });
  root.appendChild(page);

  // Local view state for zoom/pan and live-mode follow.
  const view = {
    zoom: 1,
    panX: 0,        // px offset added to plot-space coordinates
    follow: true,   // auto-scroll right edge in live mode
    hoverGlyphId: null,
  };

  // ---- filter bar ---------------------------------------------------------

  const bar = el("div", { cls: "card signals-filterbar stack-2", testid: "signals-filterbar" });
  page.appendChild(bar);

  const fromRow = el("div", { cls: "row signals-filter-row" });
  fromRow.appendChild(el("span", { cls: "text-muted mono", text: "from:" }));
  const fromChips = {};
  for (const lane of LANES) {
    const chip = el("button", {
      cls: `lane-chip ${laneClass(lane)} signals-filter-chip`,
      testid: `signal-filter-from-${safeSlug(lane)}`,
      text: lane,
      attrs: { type: "button", "aria-pressed": "false" },
    });
    chip.addEventListener("click", () => {
      if (filters.fromLanes.has(lane)) filters.fromLanes.delete(lane);
      else filters.fromLanes.add(lane);
      chip.setAttribute("aria-pressed", filters.fromLanes.has(lane) ? "true" : "false");
      chip.classList.toggle("is-active", filters.fromLanes.has(lane));
      rerender();
    });
    fromChips[lane] = chip;
    fromRow.appendChild(chip);
  }
  bar.appendChild(fromRow);

  const toRow = el("div", { cls: "row signals-filter-row" });
  toRow.appendChild(el("span", { cls: "text-muted mono", text: "to:" }));
  const toChips = {};
  for (const lane of LANES) {
    const chip = el("button", {
      cls: `lane-chip ${laneClass(lane)} signals-filter-chip`,
      testid: `signal-filter-to-${safeSlug(lane)}`,
      text: lane,
      attrs: { type: "button", "aria-pressed": "false" },
    });
    chip.addEventListener("click", () => {
      if (filters.toLanes.has(lane)) filters.toLanes.delete(lane);
      else filters.toLanes.add(lane);
      chip.setAttribute("aria-pressed", filters.toLanes.has(lane) ? "true" : "false");
      chip.classList.toggle("is-active", filters.toLanes.has(lane));
      rerender();
    });
    toChips[lane] = chip;
    toRow.appendChild(chip);
  }
  bar.appendChild(toRow);

  const kindRow = el("div", { cls: "row signals-filter-row" });
  kindRow.appendChild(el("span", { cls: "text-muted mono", text: "kind:" }));
  const kindChips = {};
  for (const kind of KINDS) {
    const chip = el("button", {
      cls: `badge signals-filter-chip kind-${safeSlug(kind)}`,
      testid: `signal-filter-kind-${safeSlug(kind)}`,
      text: kind,
      attrs: { type: "button", "aria-pressed": "false" },
    });
    chip.addEventListener("click", () => {
      if (filters.kinds.has(kind)) filters.kinds.delete(kind);
      else filters.kinds.add(kind);
      chip.setAttribute("aria-pressed", filters.kinds.has(kind) ? "true" : "false");
      chip.classList.toggle("is-active", filters.kinds.has(kind));
      rerender();
    });
    kindChips[kind] = chip;
    kindRow.appendChild(chip);
  }
  bar.appendChild(kindRow);

  // Time-range slider — 2-thumb range built from two synced <input type=range>.
  const sliderRow = el("div", { cls: "row signals-filter-row signals-time-slider", testid: "signal-time-slider" });
  sliderRow.appendChild(el("span", { cls: "text-muted mono", text: "time:" }));
  const sliderMin = el("input", {
    cls: "signals-time-thumb signals-time-thumb-min",
    testid: "signal-time-slider-min",
    attrs: { type: "range", min: "0", max: "1000", value: "0", step: "1", "aria-label": "Filter start time" },
  });
  const sliderMax = el("input", {
    cls: "signals-time-thumb signals-time-thumb-max",
    testid: "signal-time-slider-max",
    attrs: { type: "range", min: "0", max: "1000", value: "1000", step: "1", "aria-label": "Filter end time" },
  });
  const sliderLabel = el("span", {
    cls: "mono text-muted signals-time-label",
    testid: "signal-time-slider-label",
    text: "",
  });
  sliderRow.appendChild(sliderMin);
  sliderRow.appendChild(sliderMax);
  sliderRow.appendChild(sliderLabel);
  bar.appendChild(sliderRow);

  function applySliderToFilters() {
    const span = filters.boundsMax - filters.boundsMin;
    if (span <= 0) {
      filters.tMin = filters.boundsMin;
      filters.tMax = filters.boundsMax;
      return;
    }
    let a = Number(sliderMin.value);
    let b = Number(sliderMax.value);
    if (a > b) { const t = a; a = b; b = t; }
    filters.tMin = filters.boundsMin + (span * a) / 1000;
    filters.tMax = filters.boundsMin + (span * b) / 1000;
    const fmt = (ms) => {
      const d = new Date(ms);
      return Number.isFinite(d.getTime()) ? d.toISOString().replace(/\.\d+Z$/, "Z") : "?";
    };
    sliderLabel.textContent = `${fmt(filters.tMin)} → ${fmt(filters.tMax)}`;
  }
  sliderMin.addEventListener("input", () => { applySliderToFilters(); rerender(); });
  sliderMax.addEventListener("input", () => { applySliderToFilters(); rerender(); });

  const clearBtn = el("button", {
    cls: "button",
    testid: "action-clear-signal-filters",
    text: "Clear filters",
    attrs: { type: "button" },
  });
  clearBtn.addEventListener("click", () => {
    filters.fromLanes.clear();
    filters.toLanes.clear();
    filters.kinds.clear();
    for (const lane of LANES) {
      fromChips[lane].setAttribute("aria-pressed", "false");
      fromChips[lane].classList.remove("is-active");
      toChips[lane].setAttribute("aria-pressed", "false");
      toChips[lane].classList.remove("is-active");
    }
    for (const kind of KINDS) {
      kindChips[kind].setAttribute("aria-pressed", "false");
      kindChips[kind].classList.remove("is-active");
    }
    sliderMin.value = "0";
    sliderMax.value = "1000";
    applySliderToFilters();
    rerender();
  });
  bar.appendChild(clearBtn);

  // ---- timeline container -------------------------------------------------

  const timelineWrap = el("div", { cls: "signals-timeline-wrap card", testid: "signals-timeline" });
  page.appendChild(timelineWrap);

  // Wrap the SVG in a scrollable viewport so live-mode autoscroll has somewhere
  // to scroll. The SVG's intrinsic width grows with zoom; the wrap is the port.
  const scrollPort = el("div", { cls: "signals-scrollport" });
  timelineWrap.appendChild(scrollPort);

  const svg = svgEl("svg", {
    cls: "signals-svg",
    attrs: {
      xmlns: SVG_NS,
      role: "group",
      "aria-label": "Inter-agent signals timeline",
    },
  });
  scrollPort.appendChild(svg);

  // Persistent tooltip (DOM, not SVG) — positioned absolutely over the wrap.
  const tooltip = el("div", {
    cls: "signals-tooltip",
    testid: "signal-tooltip",
    attrs: { role: "tooltip", "aria-hidden": "true" },
  });
  tooltip.style.display = "none";
  timelineWrap.appendChild(tooltip);

  // Initialize filter state late so initial signals seed time bounds.
  const initSignals = (store.get("signals.list") || []).slice();
  const filters = makeFilterState(initSignals);
  applySliderToFilters();

  // ---- hidden table fallback ---------------------------------------------

  const fallback = el("table", {
    cls: "sr-only",
    testid: "signals-table-fallback",
    attrs: { "aria-label": "Signals (text fallback)" },
  });
  const thead = el("thead");
  const trHead = el("tr");
  for (const col of ["UTC", "From", "To", "Kind", "Claim", "Evidence"]) {
    trHead.appendChild(el("th", { text: col, attrs: { scope: "col" } }));
  }
  thead.appendChild(trHead);
  fallback.appendChild(thead);
  const tbody = el("tbody");
  fallback.appendChild(tbody);
  page.appendChild(fallback);

  // Empty state placeholder (shown in place of timeline when no signals at all).
  const emptyState = el("div", {
    cls: "empty-state",
    testid: "signals-empty",
    text: "No inter-agent signals yet.",
  });
  emptyState.style.display = "none";
  timelineWrap.appendChild(emptyState);

  // ---- pan/zoom interaction ---------------------------------------------

  let isDragging = false;
  let dragStartX = 0;
  let panStartAtDrag = 0;

  function onWheel(e) {
    if (!e.ctrlKey && !e.shiftKey && !e.metaKey) {
      // Vertical wheel: treat as pan-x for trackpad-y; zoom on ctrl/meta.
      if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
        view.panX -= e.deltaX;
      } else {
        // Plain wheel = zoom around cursor x.
        zoomAt(e, e.deltaY < 0 ? 1.1 : 1 / 1.1);
      }
    } else {
      zoomAt(e, e.deltaY < 0 ? 1.1 : 1 / 1.1);
    }
    // Wheel left/scrolling left disables follow mode.
    if (e.deltaX < 0 || (Math.abs(e.deltaY) > 0 && view.zoom > 1)) {
      view.follow = false;
    }
    e.preventDefault();
    rerender();
  }
  function zoomAt(e, factor) {
    const rect = svg.getBoundingClientRect();
    const cursorX = e.clientX - rect.left;
    const before = (cursorX - view.panX) / view.zoom;
    view.zoom = clamp(view.zoom * factor, ZOOM_MIN, ZOOM_MAX);
    const after = (cursorX - view.panX) / view.zoom;
    view.panX += (after - before) * view.zoom;
  }
  function onMouseDown(e) {
    isDragging = true;
    dragStartX = e.clientX;
    panStartAtDrag = view.panX;
    svg.style.cursor = "grabbing";
  }
  function onMouseMove(e) {
    if (!isDragging) return;
    view.panX = panStartAtDrag + (e.clientX - dragStartX);
    if (e.clientX < dragStartX) view.follow = false; // panned left
    rerender();
  }
  function onMouseUp() {
    isDragging = false;
    svg.style.cursor = "";
  }
  svg.addEventListener("wheel", onWheel, { passive: false });
  svg.addEventListener("mousedown", onMouseDown);
  window.addEventListener("mousemove", onMouseMove);
  window.addEventListener("mouseup", onMouseUp);

  // ---- core rendering ---------------------------------------------------

  function rerender() {
    const all = (store.get("signals.list") || []).slice();
    // Refresh time bounds if the slice grew beyond previous bounds.
    const bounds = signalsTimeBounds(all);
    if (bounds.min < filters.boundsMin) filters.boundsMin = bounds.min;
    if (bounds.max > filters.boundsMax) {
      // Extend; if user was at full-right, keep them there.
      filters.boundsMax = bounds.max;
    }
    // If slider was at right-extreme, keep tMax glued to new boundsMax.
    if (Number(sliderMax.value) >= 1000) filters.tMax = filters.boundsMax;
    if (Number(sliderMin.value) <= 0) filters.tMin = filters.boundsMin;

    const filtered = all.filter((s) => passesFilters(s, filters));
    renderTimeline(all, filtered);
    renderFallback(all);
    emptyState.style.display = all.length === 0 ? "" : "none";
    timelineWrap.classList.toggle("is-empty", all.length === 0);
  }

  function renderTimeline(allSignals, signals) {
    clearChildren(svg);

    const plotLeft = LANE_LABEL_WIDTH;
    const plotWidth = Math.max(SVG_WIDTH - plotLeft - PAD_RIGHT, 200);
    const totalHeight = PAD_TOP + LANES.length * LANE_HEIGHT + PAD_BOTTOM;
    const intrinsicW = plotLeft + plotWidth + PAD_RIGHT;

    svg.setAttribute("viewBox", `0 0 ${intrinsicW} ${totalHeight}`);
    svg.setAttribute("width", String(intrinsicW));
    svg.setAttribute("height", String(totalHeight));

    // Title and desc for SR users who do read the SVG directly.
    const titleNode = svgEl("title", { text: "Inter-agent signals swim-lane timeline" });
    const descNode = svgEl("desc", {
      text: "Horizontal lanes by agent role. Each glyph represents one signal sent from one lane to another at a UTC timestamp. A hidden table below carries the same data.",
    });
    svg.appendChild(titleNode);
    svg.appendChild(descNode);

    // ---- lanes (gutters + labels) ----
    const lanesG = svgEl("g", { cls: "signals-lanes" });
    svg.appendChild(lanesG);
    for (let i = 0; i < LANES.length; i++) {
      const lane = LANES[i];
      const y = laneY(i);
      // Background stripe (alternating).
      if (i % 2 === 0) {
        lanesG.appendChild(svgEl("rect", {
          cls: "signals-lane-bg",
          attrs: {
            x: 0, y: y - LANE_HEIGHT / 2,
            width: intrinsicW, height: LANE_HEIGHT,
            fill: "rgba(255,255,255,0.012)",
          },
        }));
      }
      // Gutter line.
      lanesG.appendChild(svgEl("line", {
        cls: "signals-lane-gutter",
        attrs: {
          x1: plotLeft, y1: y, x2: intrinsicW - PAD_RIGHT, y2: y,
          stroke: "rgba(255,255,255,0.10)", "stroke-width": 1, "stroke-dasharray": "2 4",
        },
      }));
      // Label on the left.
      const labelG = svgEl("g", { cls: `signals-lane-label lane-chip ${laneClass(lane)}` });
      labelG.appendChild(svgEl("rect", {
        attrs: {
          x: 8, y: y - 12, width: LANE_LABEL_WIDTH - 16, height: 24,
          rx: 4, ry: 4,
          fill: "rgba(255,255,255,0.03)", stroke: "currentColor", "stroke-opacity": 0.4,
        },
      }));
      labelG.appendChild(svgEl("text", {
        attrs: {
          x: LANE_LABEL_WIDTH / 2, y: y + 4,
          "text-anchor": "middle",
          fill: "currentColor",
          "font-size": "12", "font-family": "var(--font-mono, monospace)", "font-weight": "600",
        },
        text: lane,
      }));
      lanesG.appendChild(labelG);
    }

    // ---- compute x-positions and live-edge autoscroll ----
    const t0 = filters.boundsMin;
    const t1 = filters.boundsMax;
    const tSpan = Math.max(t1 - t0, 1);

    function plotX(t) {
      const norm = (t - t0) / tSpan; // 0..1
      return plotLeft + view.panX + norm * plotWidth * view.zoom;
    }

    // Live-mode autoscroll: if user is at scroll-right edge, keep them there.
    if (view.follow) {
      const sp = scrollPort;
      const atEdge = (sp.scrollLeft + sp.clientWidth) >= (sp.scrollWidth - EDGE_AUTOSCROLL_THRESHOLD_PX);
      if (atEdge) {
        // Defer to next frame after svg size is committed.
        requestAnimationFrame(() => {
          try { sp.scrollLeft = sp.scrollWidth; } catch (_) { /* ignore */ }
        });
      }
    }

    // ---- glyphs and arrows ----
    if (signals.length === 0) {
      const empty = svgEl("text", {
        attrs: {
          x: plotLeft + plotWidth / 2, y: totalHeight / 2,
          "text-anchor": "middle", fill: "rgba(255,255,255,0.4)",
          "font-size": "13", "font-family": "var(--font-mono, monospace)",
        },
        text: allSignals.length === 0
          ? "No inter-agent signals yet."
          : "No signals match the current filters.",
      });
      svg.appendChild(empty);
      return;
    }

    const arrowsG = svgEl("g", { cls: "signals-arrows" });
    svg.appendChild(arrowsG);

    const glyphsG = svgEl("g", { cls: "signals-glyphs" });
    svg.appendChild(glyphsG);

    // Cluster: bucket by sender-lane+kind+from_to and floor(x/12px).
    // Glyphs whose buckets collide become a stack with a count badge.
    const buckets = new Map();
    for (const sig of signals) {
      const t = utcToMs(sig.utc);
      if (!Number.isFinite(t)) continue;
      const fromIdx = LANES.indexOf(sig.from_lane);
      const toIdx = LANES.indexOf(sig.to);
      if (fromIdx < 0) continue;
      const x = plotX(t);
      const bx = Math.floor((x - plotLeft) / CLUSTER_BUCKET_PX);
      const key = `${fromIdx}|${bx}`;
      let bucket = buckets.get(key);
      if (!bucket) {
        bucket = { x, fromIdx, toIdx, signals: [] };
        buckets.set(key, bucket);
      } else {
        bucket.x = (bucket.x * bucket.signals.length + x) / (bucket.signals.length + 1);
      }
      bucket.signals.push(sig);
    }

    // Draw one glyph (or cluster) per bucket. Draw the bucket's "primary" arrow
    // to its first signal's recipient; cluster glyph carries count.
    for (const [, bucket] of buckets) {
      const isCluster = bucket.signals.length > 1;
      const primary = bucket.signals[0];
      const x = bucket.x;
      const yFrom = laneY(bucket.fromIdx);

      // Arrow(s): always draw the primary arrow. For a cluster, skip per-signal
      // arrows (would render as spaghetti at this zoom).
      const recipients = isCluster
        ? arrayUniq(bucket.signals.map((s) => s.to)).filter((r) => LANES.indexOf(r) >= 0)
        : (LANES.indexOf(primary.to) >= 0 ? [primary.to] : []);
      for (const to of recipients) {
        const toIdx = LANES.indexOf(to);
        const yTo = laneY(toIdx);
        if (toIdx === bucket.fromIdx) continue; // self-loop skipped
        const arrow = makeArrow(x, yFrom, x + 18, yTo, primary.from_lane);
        arrowsG.appendChild(arrow);
      }

      // Glyph group.
      const ariaLabel = isCluster
        ? `${bucket.signals.length} signals from ${primary.from_lane || "?"} clustered at ${primary.utc || "?"}`
        : composeAriaLabel(primary);

      const testidParts = [
        "signal-glyph",
        safeSlug(primary.from_lane),
        safeSlug(primary.to),
        safeSlug(primary.kind),
        safeSlug(primary.utc),
      ];
      const glyphG = svgEl("g", {
        cls: `signals-glyph lane-chip ${laneClass(primary.from_lane)} kind-${safeSlug(primary.kind)}${isCluster ? " is-cluster" : ""}`,
        testid: testidParts.join("-"),
        attrs: {
          role: "img",
          tabindex: "0",
          transform: `translate(${x.toFixed(2)}, ${yFrom.toFixed(2)})`,
        },
      });
      // aria-label set via setAttribute with the safely-composed string.
      glyphG.setAttribute("aria-label", ariaLabel);

      // Shape per kind. Use currentColor (inherited from lane-chip class).
      glyphG.appendChild(makeGlyphShape(primary.kind));

      if (isCluster) {
        // Count badge: small circle above the glyph with the count text.
        const badge = svgEl("g", { cls: "signals-cluster-badge" });
        badge.appendChild(svgEl("circle", {
          attrs: {
            cx: GLYPH_R + 2, cy: -GLYPH_R - 2, r: 8,
            fill: "var(--surface-2, #1b1b1b)",
            stroke: "currentColor", "stroke-width": 1,
          },
        }));
        badge.appendChild(svgEl("text", {
          attrs: {
            x: GLYPH_R + 2, y: -GLYPH_R + 1,
            "text-anchor": "middle",
            fill: "currentColor",
            "font-size": "10", "font-weight": "700",
            "font-family": "var(--font-mono, monospace)",
          },
          text: String(bucket.signals.length),
        }));
        glyphG.appendChild(badge);
      }

      // Hover/focus: show tooltip.
      glyphG.addEventListener("mouseenter", () => showTooltipFor(primary, x, yFrom, isCluster ? bucket.signals.length : 1));
      glyphG.addEventListener("mouseleave", hideTooltip);
      glyphG.addEventListener("focus", () => showTooltipFor(primary, x, yFrom, isCluster ? bucket.signals.length : 1));
      glyphG.addEventListener("blur", hideTooltip);

      // Click: open evidence per spec.
      glyphG.addEventListener("click", () => openEvidence(primary));
      glyphG.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          openEvidence(primary);
        }
      });

      glyphsG.appendChild(glyphG);
    }
  }

  function laneY(idx) {
    return PAD_TOP + idx * LANE_HEIGHT + LANE_HEIGHT / 2;
  }

  function makeGlyphShape(kind) {
    // All shapes are centered at (0,0); fill/stroke uses currentColor.
    if (kind === "ACK-VERIFIED") {
      // Filled checkmark: polyline path inside a filled circle backdrop.
      const g = svgEl("g");
      g.appendChild(svgEl("circle", {
        attrs: { cx: 0, cy: 0, r: GLYPH_R, fill: "currentColor", "fill-opacity": 0.85 },
      }));
      g.appendChild(svgEl("path", {
        attrs: {
          d: `M ${-GLYPH_R / 2} 0 L ${-GLYPH_R / 6} ${GLYPH_R / 2} L ${GLYPH_R / 1.6} ${-GLYPH_R / 2}`,
          stroke: "var(--bg, #0d0d0d)", "stroke-width": 2, fill: "none",
          "stroke-linecap": "round", "stroke-linejoin": "round",
        },
      }));
      return g;
    }
    if (kind === "DISSENT") {
      // Filled X inside circle.
      const g = svgEl("g");
      g.appendChild(svgEl("circle", {
        attrs: { cx: 0, cy: 0, r: GLYPH_R, fill: "currentColor", "fill-opacity": 0.85 },
      }));
      const r = GLYPH_R / 1.8;
      g.appendChild(svgEl("path", {
        attrs: {
          d: `M ${-r} ${-r} L ${r} ${r} M ${-r} ${r} L ${r} ${-r}`,
          stroke: "var(--bg, #0d0d0d)", "stroke-width": 2,
          "stroke-linecap": "round",
        },
      }));
      return g;
    }
    if (kind === "DEFER") {
      // Filled clock face: filled circle with a clock-hand line.
      const g = svgEl("g");
      g.appendChild(svgEl("circle", {
        attrs: { cx: 0, cy: 0, r: GLYPH_R, fill: "currentColor", "fill-opacity": 0.85 },
      }));
      g.appendChild(svgEl("circle", {
        attrs: { cx: 0, cy: 0, r: 1.6, fill: "var(--bg, #0d0d0d)" },
      }));
      g.appendChild(svgEl("path", {
        attrs: {
          d: `M 0 0 L 0 ${-GLYPH_R + 2.5} M 0 0 L ${GLYPH_R - 3} 1`,
          stroke: "var(--bg, #0d0d0d)", "stroke-width": 1.6, "stroke-linecap": "round",
        },
      }));
      return g;
    }
    // SIGNAL (default): hollow circle.
    return svgEl("circle", {
      attrs: {
        cx: 0, cy: 0, r: GLYPH_R,
        fill: "none", stroke: "currentColor", "stroke-width": 2,
      },
    });
  }

  function makeArrow(x1, y1, x2, y2, fromLane) {
    // Quadratic curve; control point pulled toward x-midpoint with y leaning
    // toward the larger of |y1-y2| to make a gentle S-free arc.
    const g = svgEl("g", { cls: `signals-arrow lane-chip ${laneClass(fromLane)}` });
    const mx = (x1 + x2) / 2 + 24;
    const my = (y1 + y2) / 2;
    const d = `M ${x1.toFixed(2)} ${y1.toFixed(2)} Q ${mx.toFixed(2)} ${my.toFixed(2)} ${x2.toFixed(2)} ${y2.toFixed(2)}`;
    g.appendChild(svgEl("path", {
      attrs: {
        d,
        fill: "none",
        stroke: "currentColor", "stroke-opacity": 0.55,
        "stroke-width": 1.5,
      },
    }));
    // Arrowhead (small triangle pointing at recipient).
    const ang = Math.atan2(y2 - my, x2 - mx);
    const ah = 5;
    const hx = x2 - Math.cos(ang) * ah;
    const hy = y2 - Math.sin(ang) * ah;
    const px = Math.cos(ang + Math.PI / 2) * 3;
    const py = Math.sin(ang + Math.PI / 2) * 3;
    g.appendChild(svgEl("polygon", {
      attrs: {
        points: `${x2.toFixed(2)},${y2.toFixed(2)} ${(hx + px).toFixed(2)},${(hy + py).toFixed(2)} ${(hx - px).toFixed(2)},${(hy - py).toFixed(2)}`,
        fill: "currentColor", "fill-opacity": 0.65,
      },
    }));
    return g;
  }

  function showTooltipFor(sig, x, y, count) {
    clearChildren(tooltip);
    const head = el("div", { cls: "signals-tooltip-head mono" });
    head.appendChild(el("span", { cls: `lane-chip ${laneClass(sig.from_lane)}`, text: sig.from_lane || "?" }));
    head.appendChild(el("span", { cls: "signals-tooltip-arrow", text: " -> " }));
    head.appendChild(el("span", { cls: `lane-chip ${laneClass(sig.to)}`, text: sig.to || "?" }));
    head.appendChild(el("span", { cls: "badge", text: sig.kind || "SIGNAL" }));
    tooltip.appendChild(head);
    if (count > 1) {
      tooltip.appendChild(el("div", { cls: "signals-tooltip-cluster mono text-muted", text: `${count} signals at this position` }));
    }
    tooltip.appendChild(el("div", { cls: "signals-tooltip-utc mono text-muted", text: sig.utc || "" }));
    if (sig.claim) {
      tooltip.appendChild(el("div", { cls: "signals-tooltip-claim", text: sig.claim }));
    }
    const ev = sig.evidence || {};
    if (ev.path) {
      const evRow = el("div", { cls: "signals-tooltip-evidence mono text-muted" });
      evRow.appendChild(el("span", { text: "evidence: " }));
      evRow.appendChild(el("span", { text: `${ev.path}${ev.line ? ":" + ev.line : ""}${ev.section ? " §" + ev.section : ""}` }));
      tooltip.appendChild(evRow);
    }
    if (sig.finding_ref) {
      tooltip.appendChild(el("div", { cls: "signals-tooltip-ref mono text-muted", text: `finding: ${sig.finding_ref}` }));
    }
    tooltip.style.display = "";
    tooltip.setAttribute("aria-hidden", "false");
    // Position relative to timelineWrap.
    const wrapRect = timelineWrap.getBoundingClientRect();
    const svgRect = svg.getBoundingClientRect();
    const left = (svgRect.left - wrapRect.left) + x + 14;
    const top = (svgRect.top - wrapRect.top) + y - 8;
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;
  }
  function hideTooltip() {
    tooltip.style.display = "none";
    tooltip.setAttribute("aria-hidden", "true");
  }

  function openEvidence(sig) {
    if (!sig) return;
    if (sig.source_artifact === "finding") {
      const target = sig.finding_ref || (sig.evidence && sig.evidence.path) || "";
      if (!target) return;
      // Hash-routed findings page entry.
      const filename = target.replace(/^.*\/findings\//, "").replace(/^findings\//, "");
      try {
        location.hash = `#${filename}`;
        // If we're not on /findings, navigate there.
        if (!/\/findings\b/.test(location.pathname)) {
          location.assign(`/findings#${filename}`);
        }
      } catch (_) { /* ignore */ }
      return;
    }
    // status-notes or history: open in a new tab via a transient <a>.
    const path = sig.evidence && sig.evidence.path;
    if (!path) return;
    try {
      const a = document.createElement("a");
      a.href = `/${path.replace(/^\/+/, "")}`;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      // No need to attach to DOM — programmatic click works on detached anchors
      // in modern browsers, but for maximum compat we briefly append it.
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    } catch (_) { /* ignore */ }
  }

  // ---- fallback table render --------------------------------------------

  function renderFallback(allSignals) {
    clearChildren(tbody);
    const sorted = allSignals.slice().sort((a, b) => {
      const au = utcToMs(a.utc);
      const bu = utcToMs(b.utc);
      if (au < bu) return -1;
      if (au > bu) return 1;
      return 0;
    });
    for (const sig of sorted) {
      const tr = el("tr");
      tr.appendChild(el("td", { text: sig.utc || "" }));
      tr.appendChild(el("td", { text: sig.from_lane || "" }));
      tr.appendChild(el("td", { text: sig.to || "" }));
      tr.appendChild(el("td", { text: sig.kind || "" }));
      tr.appendChild(el("td", { text: sig.claim || "" }));
      const ev = sig.evidence || {};
      const evText = ev.path
        ? `${ev.path}${ev.line ? ":" + ev.line : ""}${ev.section ? " §" + ev.section : ""}`
        : "";
      tr.appendChild(el("td", { text: evText }));
      tbody.appendChild(tr);
    }
  }

  // ---- subscribe to store -----------------------------------------------

  const unsubSignals = store.subscribe("signals.list", () => rerender());

  // Initial paint.
  rerender();

  // ---- cleanup ----------------------------------------------------------

  return function cleanup() {
    try { unsubSignals(); } catch (_) { /* ignore */ }
    try { svg.removeEventListener("wheel", onWheel); } catch (_) { /* ignore */ }
    try { svg.removeEventListener("mousedown", onMouseDown); } catch (_) { /* ignore */ }
    try { window.removeEventListener("mousemove", onMouseMove); } catch (_) { /* ignore */ }
    try { window.removeEventListener("mouseup", onMouseUp); } catch (_) { /* ignore */ }
    clearChildren(root);
  };
}

export default { render };
