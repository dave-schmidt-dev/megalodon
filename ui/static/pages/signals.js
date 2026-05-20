// @ts-check
/**
 * /signals — Inter-agent signal thread view.
 *
 * Layout: list of topic-thread cards. Each card groups all signals sharing the
 * same topic (the suffix after the two lane names in the filename). Inside each
 * card, sub-rows show: [sender chip] → [receiver chip] · UTC age. Clicking a
 * row opens a side drawer with the signal's full body.
 *
 * Threading key: exact topic string extracted from the filename. No slugification
 * or normalization — two filenames with different whitespace/case are different
 * threads.
 *
 * Data: reads `signals.list` from the store (Signal[]). Snapshot on mount; no
 * polling or SSE within this page.
 *
 * Contract: export render(root) -> cleanup().
 *
 * Security: zero innerHTML. All dynamic text uses textContent. No SVG.
 */

import { store } from "../js/store.js";

// ---- filename parser --------------------------------------------------------

/**
 * Parse a signal filename into `{sender_lane, receiver_lane, topic}`.
 *
 * Grammar: `LANE-(X)-to-LANE-(Y)-<topic>.md`
 * Example: `LANE-A-to-LANE-B-code-review.md` → sender=LANE-A, receiver=LANE-B,
 *           topic=code-review
 *
 * Falls back to `{sender_lane: "?", receiver_lane: "?", topic: filename}` when
 * the pattern does not match.
 *
 * @param {string} filename
 * @returns {{ sender_lane: string, receiver_lane: string, topic: string }}
 */
export function parseSignalFilename(filename) {
  const base = String(filename || "").replace(/\.md$/i, "");
  // LANE-X-to-LANE-Y-<topic>
  const m = base.match(/^(LANE-[A-Z0-9]+)-to-(LANE-[A-Z0-9]+)-(.+)$/);
  if (m) {
    return { sender_lane: m[1], receiver_lane: m[2], topic: m[3] };
  }
  return { sender_lane: "?", receiver_lane: "?", topic: base || filename || "?" };
}

// ---- helpers ----------------------------------------------------------------

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

/**
 * "X time ago" from a UTC stamp. Accepts both ISO (`2026-05-20T10:00Z`) and
 * the filename dash-form (`2026-05-20T10-00Z`). Returns "" for invalid input.
 *
 * @param {string} utc
 * @returns {string}
 */
function utcAgo(utc) {
  if (!utc) return "";
  const s = String(utc);
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

/** localStorage key for a read signal. */
function readKey(filename) {
  return `signals.read.${filename}`;
}

function isRead(filename) {
  try {
    return localStorage.getItem(readKey(filename)) === "1";
  } catch (_) {
    return false;
  }
}

function markRead(filename) {
  try {
    localStorage.setItem(readKey(filename), "1");
  } catch (_) { /* ignore */ }
}

/**
 * Group signals by topic (exact string, no normalization).
 *
 * @param {Array<object>} signals
 * @returns {Map<string, Array<object>>} topic → signals[]
 */
function groupByTopic(signals) {
  const map = new Map();
  for (const sig of signals) {
    const parsed = parseSignalFilename(sig.filename || "");
    const topic = parsed.topic;
    if (!map.has(topic)) map.set(topic, []);
    map.get(topic).push({ ...sig, _parsed: parsed });
  }
  return map;
}

// ---- main render ------------------------------------------------------------

/**
 * @param {HTMLElement} root
 * @returns {Promise<() => void>} cleanup
 */
export async function render(root, _params) {
  clearChildren(root);

  const page = el("div", { cls: "signals-page", testid: "signals-page" });
  root.appendChild(page);

  // ---- drawer ---------------------------------------------------------------
  // Side drawer — shown when user clicks a signal row.

  const drawerOverlay = el("div", {
    cls: "signals-drawer-overlay",
    testid: "signals-drawer-overlay",
    attrs: { role: "presentation", "aria-hidden": "true" },
  });
  drawerOverlay.style.display = "none";
  root.appendChild(drawerOverlay);

  const drawer = el("div", {
    cls: "signals-drawer",
    testid: "signals-drawer",
    attrs: { role: "dialog", "aria-modal": "true", "aria-label": "Signal detail" },
  });
  drawer.style.display = "none";
  root.appendChild(drawer);

  const drawerHeader = el("div", { cls: "signals-drawer__header" });
  drawer.appendChild(drawerHeader);

  const drawerTitle = el("div", { cls: "signals-drawer__title mono", testid: "signals-drawer-title" });
  drawerHeader.appendChild(drawerTitle);

  const drawerClose = el("button", {
    cls: "signals-drawer__close button",
    testid: "signals-drawer-close",
    text: "×",
    attrs: { type: "button", title: "Close signal drawer", "aria-label": "Close drawer" },
  });
  drawerHeader.appendChild(drawerClose);

  const drawerBody = el("div", { cls: "signals-drawer__body", testid: "signals-drawer-body" });
  drawer.appendChild(drawerBody);

  let openFilename = null;

  function openDrawer(sig) {
    openFilename = sig.filename || null;

    // Mark read in localStorage; update DOM attribute immediately.
    if (openFilename) {
      markRead(openFilename);
      // Update the row's read attribute without a full re-render.
      const rowEl = root.querySelector(`[data-signal-filename="${CSS.escape(openFilename)}"]`);
      if (rowEl) {
        rowEl.setAttribute("data-signal-read", "true");
        rowEl.classList.add("is-read");
      }
    }

    // Populate drawer title.
    clearChildren(drawerTitle);
    const parsed = sig._parsed || parseSignalFilename(sig.filename || "");
    drawerTitle.appendChild(el("span", {
      cls: "lane-chip",
      text: parsed.sender_lane,
      attrs: { title: `sender: ${parsed.sender_lane}` },
    }));
    drawerTitle.appendChild(el("span", { cls: "signals-drawer__arrow", text: " → " }));
    drawerTitle.appendChild(el("span", {
      cls: "lane-chip",
      text: parsed.receiver_lane,
      attrs: { title: `receiver: ${parsed.receiver_lane}` },
    }));
    drawerTitle.appendChild(el("span", {
      cls: "mono text-muted signals-drawer__topic",
      text: ` · ${parsed.topic}`,
    }));

    // Populate body — textContent only; body is plain markdown text from server.
    clearChildren(drawerBody);
    const bodyText = String(sig.body || "");
    if (bodyText.trim()) {
      const pre = el("pre", { cls: "signals-drawer__content", text: bodyText });
      drawerBody.appendChild(pre);
    } else {
      drawerBody.appendChild(el("p", { cls: "text-muted", text: "(no body)" }));
    }

    drawer.style.display = "";
    drawerOverlay.style.display = "";
    drawerOverlay.setAttribute("aria-hidden", "false");
    drawer.removeAttribute("aria-hidden");
  }

  function closeDrawer() {
    openFilename = null;
    drawer.style.display = "none";
    drawerOverlay.style.display = "none";
    drawerOverlay.setAttribute("aria-hidden", "true");
    drawer.setAttribute("aria-hidden", "true");
  }

  drawerClose.addEventListener("click", closeDrawer);
  drawerOverlay.addEventListener("click", closeDrawer);

  function onKeydown(e) {
    if (e.key === "Escape" && drawer.style.display !== "none") {
      closeDrawer();
    }
  }
  window.addEventListener("keydown", onKeydown);

  // ---- thread list ----------------------------------------------------------

  const listEl = el("div", { cls: "signals-thread-list", testid: "signals-thread-list" });
  page.appendChild(listEl);

  function renderThreads() {
    const signals = (store.get("signals.list") || []).slice();
    clearChildren(listEl);

    if (signals.length === 0) {
      listEl.appendChild(el("div", {
        cls: "empty-state",
        testid: "signals-empty",
        text: "No signals yet.",
      }));
      return;
    }

    const grouped = groupByTopic(signals);

    for (const [topic, topicSignals] of grouped) {
      const card = el("div", {
        cls: "signals-thread-card card",
        testid: `signals-thread-${slugifyTopic(topic)}`,
        attrs: { "data-topic": topic },
      });

      const topicHeader = el("div", { cls: "signals-thread__topic-header" });
      topicHeader.appendChild(el("span", {
        cls: "mono signals-thread__topic-label",
        testid: "signals-thread-topic",
        text: `topic: ${topic}`,
        attrs: { title: `topic: ${topic}` },
      }));
      topicHeader.appendChild(el("span", {
        cls: "text-muted signals-thread__count",
        text: ` (${topicSignals.length})`,
        attrs: { title: `${topicSignals.length} signal${topicSignals.length === 1 ? "" : "s"} in thread` },
      }));
      card.appendChild(topicHeader);

      const rowsEl = el("div", { cls: "signals-thread__rows", testid: "signals-thread-rows" });

      for (const sig of topicSignals) {
        const filename = sig.filename || "";
        const parsed = sig._parsed || parseSignalFilename(filename);
        const read = isRead(filename);
        // Use sig.utc from server (the suffix), or fall back to parsed topic.
        const age = utcAgo(sig.utc || "");

        const row = el("div", {
          cls: `signals-thread__row${read ? " is-read" : ""}`,
          testid: `signal-row-${slugifyTopic(filename)}`,
          attrs: {
            role: "button",
            tabindex: "0",
            "data-signal-filename": filename,
            "data-signal-read": read ? "true" : "false",
            title: `Open signal: ${filename}`,
          },
        });

        const sender = el("span", {
          cls: "lane-chip signals-lane-chip",
          text: parsed.sender_lane,
          attrs: { title: `sender: ${parsed.sender_lane}` },
        });
        const arrow = el("span", { cls: "signals-row__arrow", text: " → " });
        const receiver = el("span", {
          cls: "lane-chip signals-lane-chip",
          text: parsed.receiver_lane,
          attrs: { title: `receiver: ${parsed.receiver_lane}` },
        });
        const dot = el("span", { cls: "text-muted", text: " · " });
        const ageEl = el("span", {
          cls: "mono text-muted signals-row__age",
          text: age || sig.utc || "",
          attrs: { title: sig.utc ? `UTC: ${sig.utc}` : "" },
        });

        row.appendChild(sender);
        row.appendChild(arrow);
        row.appendChild(receiver);
        row.appendChild(dot);
        row.appendChild(ageEl);

        row.addEventListener("click", () => openDrawer(sig));
        row.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            openDrawer(sig);
          }
        });

        rowsEl.appendChild(row);
      }

      card.appendChild(rowsEl);
      listEl.appendChild(card);
    }
  }

  // Initial paint.
  renderThreads();

  // Subscribe to store updates (SSE may push new signals).
  const unsubSignals = store.subscribe("signals.list", () => renderThreads());

  // ---- cleanup --------------------------------------------------------------

  return function cleanup() {
    try { unsubSignals(); } catch (_) { /* ignore */ }
    window.removeEventListener("keydown", onKeydown);
    clearChildren(root);
  };
}

// ---- internal util ----------------------------------------------------------

function slugifyTopic(s) {
  return String(s || "").replace(/[^A-Za-z0-9_-]/g, "-").slice(0, 64);
}

export default { render };
