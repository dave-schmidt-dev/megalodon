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
 * Data: two channels, merged.
 *   1. `signals.list` snapshot from the store (Signal[]) — the durable on-disk
 *      scan hydrated at /state time.
 *   2. LIVE: an EventSource on `/api/v1/activity-wall` (the working SSE channel).
 *      Each `type:"signal"` event carries a payload we turn into a signal dict
 *      and merge into the rendered threads (deduped by filename, newest-first),
 *      so new signals appear in real time without a reload.
 *
 * Each signal dict (server source of truth — prefer these over parsed values):
 *   {filename, from_lane, to_lane, to, topic, utc, kind, body, source}
 *   source ∈ "file" | "status-note" | "finding"  → rendered as a channel chip.
 *
 * Contract: export render(root) -> cleanup().
 *
 * Security: zero innerHTML. All dynamic text uses textContent. No SVG.
 */

import { store } from "../js/store.js";
import { authedFetch, probeReauthOn401, onReauthSuccess } from "../js/auth.js";

// ---- filename parser --------------------------------------------------------

/**
 * Parse a signal filename into `{sender_lane, receiver_lane, topic, utc}`.
 *
 * Canonical grammar (frozen wire contract):
 *   `LANE-<FROM>-to-LANE-<TO>-<topic>-<UTC>.md`
 *   UTC := `\d{4}-\d{2}-\d{2}T\d{2}-\d{2}(-\d{2})?Z` (dash-form)
 * Example: `LANE-A-to-LANE-B-code-review-2026-05-25T18-49Z.md` →
 *   sender=LANE-A, receiver=LANE-B, topic=code-review, utc=2026-05-25T18-49Z
 *
 * The UTC stamp is anchored at the END so `topic` (which may itself contain
 * dashes) is everything between `to-LANE-Y-` and the trailing UTC.
 *
 * Legacy fallback (no trailing UTC): `LANE-X-to-LANE-Y-<topic>` → topic=rest,
 * utc="". Final fallback: `{sender_lane:"?", receiver_lane:"?", topic:base, utc:""}`.
 *
 * @param {string} filename
 * @returns {{ sender_lane: string, receiver_lane: string, topic: string, utc: string }}
 */
export function parseSignalFilename(filename) {
  const base = String(filename || "").replace(/\.md$/i, "");
  // Canonical: anchor the UTC at the end; topic is the greedy middle.
  const m = base.match(
    /^(LANE-[A-Z0-9]+)-to-(LANE-[A-Z0-9]+)-(.+)-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}(?:-\d{2})?Z)$/,
  );
  if (m) {
    return { sender_lane: m[1], receiver_lane: m[2], topic: m[3], utc: m[4] };
  }
  // Legacy: no trailing UTC stamp.
  const legacy = base.match(/^(LANE-[A-Z0-9]+)-to-(LANE-[A-Z0-9]+)-(.+)$/);
  if (legacy) {
    return { sender_lane: legacy[1], receiver_lane: legacy[2], topic: legacy[3], utc: "" };
  }
  return { sender_lane: "?", receiver_lane: "?", topic: base || filename || "?", utc: "" };
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
/**
 * Parse a UTC stamp (ISO `2026-05-20T10:00Z` OR filename dash-form
 * `2026-05-20T10-00Z`) to epoch-ms. Returns NaN for empty/invalid input.
 *
 * @param {string} utc
 * @returns {number}
 */
function parseUtcMs(utc) {
  if (!utc) return NaN;
  const s = String(utc);
  let iso = s;
  const m = s.match(/^(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})(?:-(\d{2}))?Z$/);
  if (m) {
    iso = `${m[1]}T${m[2]}:${m[3]}:${m[4] || "00"}Z`;
  }
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : NaN;
}

function utcAgo(utc) {
  if (!utc) return "";
  const t = parseUtcMs(utc);
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
 * Resolve a signal's effective display fields. Server-provided fields
 * (`from_lane`/`to_lane`/`topic`/`utc`) are authoritative; we fall back to
 * parsing the filename for any that are missing (legacy / activity-wall rows).
 *
 * @param {object} sig
 * @returns {{ sender_lane: string, receiver_lane: string, topic: string, utc: string }}
 */
function signalFields(sig) {
  const parsed = parseSignalFilename(sig.filename || "");
  return {
    sender_lane: sig.from_lane || parsed.sender_lane,
    receiver_lane: sig.to_lane || sig.to || parsed.receiver_lane,
    topic: sig.topic || parsed.topic,
    utc: sig.utc || parsed.utc || "",
  };
}

/**
 * Best-available sort time for a signal, in epoch-ms (M2). Prefers the resolved
 * UTC (file signals); falls back to the event `ts` captured at ingest
 * (`_eventTs`) for status-note/finding signals whose `utc` is empty so they no
 * longer perpetually rank oldest. Returns 0 when nothing is parseable (those
 * sort last, which is the correct behaviour for a truly time-less signal).
 *
 * @param {object} sig
 * @returns {number}
 */
function _sortTimeMs(sig) {
  const utc = signalFields(sig).utc;
  let t = parseUtcMs(utc);
  if (Number.isFinite(t)) return t;
  t = parseUtcMs(sig && sig._eventTs);
  if (Number.isFinite(t)) return t;
  return 0;
}

/** Human label for a signal's source channel. */
function sourceLabel(source) {
  if (source === "status-note") return "status";
  if (source === "finding") return "finding";
  if (source === "file") return "file";
  return source || "file";
}

/**
 * Group signals by topic (exact string, no normalization). Prefers the
 * server-provided `topic` field, falling back to the parsed filename topic.
 *
 * @param {Array<object>} signals
 * @returns {Map<string, Array<object>>} topic → signals[]
 */
function groupByTopic(signals) {
  const map = new Map();
  for (const sig of signals) {
    const fields = signalFields(sig);
    const topic = fields.topic;
    if (!map.has(topic)) map.set(topic, []);
    map.get(topic).push({ ...sig, _parsed: fields });
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
    const parsed = sig._parsed || signalFields(sig);
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
    // Anti-spoof: mirror the row's unverified warning in the drawer header.
    if (sig.from_unverified) {
      const claimed = String(sig.claimed_from || parsed.sender_lane || "?");
      drawerTitle.appendChild(el("span", {
        cls: "signals-unverified-badge badge",
        testid: "signals-drawer-unverified-badge",
        text: "⚠ unverified",
        attrs: {
          "data-claimed-from": claimed,
          "aria-label": `Unverified sender; claimed: ${claimed}`,
          title: `Unverified sender — claimed: ${claimed}`,
        },
      }));
    }

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

  // Live signals received over the activity-wall SSE, keyed by filename so a
  // re-delivery (reconnect backfill) or a snapshot overlap dedupes cleanly.
  /** @type {Map<string, object>} */
  const liveByFilename = new Map();

  /**
   * Merge the store snapshot with live SSE signals. Dedupe by filename
   * (live wins — it is the freshest), newest-first by UTC.
   * @returns {Array<object>}
   */
  function mergedSignals() {
    const byFilename = new Map();
    for (const s of store.get("signals.list") || []) {
      if (s && s.filename) byFilename.set(s.filename, s);
    }
    for (const [fn, s] of liveByFilename) byFilename.set(fn, s);
    const all = [...byFilename.values()];
    // Newest-first by best-available time (M2). Status-note/finding signals
    // often have an empty `utc`; without a fallback they sort to the bottom and
    // never surface. Use the resolved signal UTC, falling back to the event ts
    // (`_eventTs`) captured at ingest, normalized to epoch-ms for a correct
    // numeric ordering across the dash-form (filename) and ISO (event) shapes.
    all.sort((a, b) => _sortTimeMs(b) - _sortTimeMs(a));
    return all;
  }

  function renderThreads() {
    const signals = mergedSignals();
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
        const parsed = sig._parsed || signalFields(sig);
        const read = isRead(filename);
        // Prefer the parsed/server UTC for the age display (no more mashed suffix).
        const utc = parsed.utc;
        const age = utcAgo(utc);

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
          text: age || utc || "",
          attrs: { title: utc ? `UTC: ${utc}` : "" },
        });
        // Source/channel chip (file / status / finding) so the operator knows
        // which channel carried the signal.
        const srcChip = el("span", {
          cls: `signals-source-chip signals-source--${sig.source || "file"}`,
          testid: "signal-source-chip",
          text: sourceLabel(sig.source),
          attrs: { title: `channel: ${sourceLabel(sig.source)}`, "data-source": sig.source || "file" },
        });

        row.appendChild(sender);
        // Anti-spoof (FE comms fix #2): a forged/unverifiable [SIG from=X] (the
        // token claimed a sender that doesn't own the row it was found in) is
        // flagged by the BE as `from_unverified`. Surface a clear warning badge
        // right next to the (claimed) sender so the operator can SEE that the
        // sender could not be verified. textContent only — zero innerHTML.
        if (sig.from_unverified) {
          const claimed = String(sig.claimed_from || parsed.sender_lane || "?");
          row.appendChild(el("span", {
            cls: "signals-unverified-badge badge",
            testid: "signal-unverified-badge",
            text: "⚠ unverified",
            attrs: {
              "data-claimed-from": claimed,
              "aria-label": `Unverified sender; claimed: ${claimed}`,
              title: `Unverified sender — claimed: ${claimed}`,
            },
          }));
        }
        row.appendChild(arrow);
        row.appendChild(receiver);
        row.appendChild(dot);
        row.appendChild(ageEl);
        row.appendChild(el("span", { cls: "text-muted", text: " · " }));
        row.appendChild(srcChip);

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

  // Subscribe to store updates (the legacy dead path; harmless if it never fires).
  const unsubSignals = store.subscribe("signals.list", () => renderThreads());

  // ---- LIVE: activity-wall SSE ----------------------------------------------
  // The working channel. We hydrate from the snapshot, then stream. Each
  // `type:"signal"` event becomes a signal dict merged into the thread list so
  // new signals appear in real time without a reload.

  let disposed = false;
  /** @type {EventSource|null} */
  let es = null;
  let reconnectDelay = 500;
  const RECONNECT_MAX_MS = 30_000;
  /** @type {ReturnType<typeof setTimeout>|null} */
  let reconnectTimer = null;

  /**
   * Turn an activity-wall signal event into a signal dict and merge it.
   *
   * R2-FE: ingest ALL three channels — file, finding, status-note. The BE emits
   * `type:"signal"` for every channel with `payload:{filename|id, from_lane,
   * to_lane, topic, utc, source, excerpt}`. Finding/status-note signals carry an
   * `id` (no on-disk filename), so we key on `filename` else `id`. The previous
   * `if (!filename) return;` silently dropped every non-file signal.
   *
   * M2: status-note/finding signals often have empty `utc`. We stash the event's
   * own `ts` as `_eventTs` so sorting can fall back to it (else they perpetually
   * rank oldest at the bottom).
   *
   * @param {{type?:string, ts?:string, payload?:object}} ev
   */
  function ingestEvent(ev) {
    if (!ev || ev.type !== "signal") return;
    const p = ev.payload || {};
    // Dedup key (FE comms fix #1): prefer the per-signal `id` when present, then
    // fall back to `filename`. status-note / finding signals carry a UNIQUE `id`
    // (the BE used to emit a CONSTANT `filename:"status-note"`, so two distinct
    // live status-notes collided and only one rendered). File signals have no
    // `id`, so they key on their (already-unique) filename. Skip only if BOTH
    // are absent.
    const key = p.id || p.filename || "";
    if (!key) return;
    const sig = {
      // Keep `filename` populated (UI rows / read-state use it). For id-bearing
      // signals (status-note/finding) the id is the stable per-row identity, so
      // prefer it so two distinct status-notes get distinct row keys.
      filename: p.id || p.filename || "",
      id: p.id || "",
      from_lane: p.from_lane || "",
      to_lane: p.to_lane || "",
      to: p.to_lane || "",
      topic: p.topic || "",
      utc: p.utc || "",
      // M2: best-available time fallback when utc is empty.
      _eventTs: ev.ts || "",
      kind: "SIGNAL",
      // The activity-wall payload carries an excerpt, not the full body. Use it
      // as the drawer body when no fuller body is known.
      body: p.excerpt || p.body || "",
      source: p.source || "file",
      // Anti-spoof (FE comms fix #2): surfaced as a warning badge on the row.
      from_unverified: p.from_unverified === true,
      claimed_from: p.claimed_from || "",
    };
    liveByFilename.set(key, sig);
  }

  async function hydrateSnapshot() {
    try {
      const resp = await authedFetch("/api/v1/activity-wall/snapshot?limit=200");
      if (!resp.ok) return;
      const json = await resp.json();
      const events = Array.isArray(json.events) ? json.events : [];
      for (const ev of events) ingestEvent(ev);
      if (!disposed) renderThreads();
    } catch (_) { /* tolerate; snapshot is best-effort */ }
  }

  function _clearReconnectTimer() {
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  }

  function _scheduleReconnect() {
    if (disposed) return;
    _clearReconnectTimer();
    const delay = Math.min(reconnectDelay, RECONNECT_MAX_MS);
    reconnectTimer = setTimeout(async () => {
      reconnectTimer = null;
      if (disposed) return;
      await hydrateSnapshot();
      if (disposed) return;
      startSSE();
    }, delay);
    reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
  }

  function startSSE() {
    if (disposed) return;
    es = new EventSource("/api/v1/activity-wall", { withCredentials: true });
    es.onopen = () => { reconnectDelay = 500; };
    es.onmessage = (ev) => {
      try {
        const event = JSON.parse(ev.data);
        if (event && event.type === "signal") {
          ingestEvent(event);
          if (!disposed) renderThreads();
        }
      } catch (_) { /* malformed event — ignore */ }
    };
    es.onerror = () => {
      if (!es || es.readyState !== EventSource.CLOSED) return;
      try { es.close(); } catch (_) { /* ignore */ }
      es = null;
      probeReauthOn401("/api/v1/activity-wall");
      _scheduleReconnect();
    };
  }

  // Bootstrap: snapshot then live stream.
  hydrateSnapshot().then(() => { if (!disposed) startSSE(); });

  // Force an immediate reconnect after a successful re-auth.
  const offReauth = onReauthSuccess(() => {
    if (disposed) return;
    _clearReconnectTimer();
    reconnectDelay = 500;
    if (es) { try { es.close(); } catch (_) { /* ignore */ } es = null; }
    hydrateSnapshot().then(() => { if (!disposed) startSSE(); });
  });

  // ---- cleanup --------------------------------------------------------------

  return function cleanup() {
    disposed = true;
    _clearReconnectTimer();
    try { offReauth(); } catch (_) { /* ignore */ }
    if (es) { try { es.close(); } catch (_) { /* ignore */ } es = null; }
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
