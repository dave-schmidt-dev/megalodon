// app.js — Bootstrap and router for the Megalodon orchestrator console.
//
// Responsibilities:
// - History-API based routing for 5 pages (/, /tasks, /findings, /signals, /mission)
// - Mount/unmount page modules into #app-root
// - Wire up the control-mode toggle (localStorage + body[data-control-mode])
// - Reflect store.mission.phase onto phase-segment aria-current="step"
// - Reflect store.ui.connectionStatus into a chrome indicator
//
// Pages are loaded lazily (import()), so the build stays small even if a page is huge.
// All page mounts use safe DOM APIs only — no innerHTML with user-influenced content.

import { store } from "./store.js";
import { whenAuthReady } from "./auth.js";

const ROUTES = [
  { pattern: /^\/$/, loader: () => import("../pages/board.js"), params: () => ({}) },
  { pattern: /^\/lane\/([A-Za-z0-9_-]+)$/, loader: () => import("../pages/lane_detail.js"), params: (m) => ({ short: m[1] }) },
  { pattern: /^\/tasks$/, loader: () => import("../pages/tasks.js"), params: () => ({}) },
  { pattern: /^\/findings$/, loader: () => import("../pages/findings.js"), params: () => ({}) },
  { pattern: /^\/signals$/, loader: () => import("../pages/signals.js"), params: () => ({}) },
  { pattern: /^\/mission$/, loader: () => import("../pages/mission.js"), params: () => ({}) },
  { pattern: /^\/approval-rules$/, loader: () => import("../pages/approval_rules.js"), params: () => ({}) },
];

function matchRoute(path) {
  for (const route of ROUTES) {
    const m = path.match(route.pattern);
    if (m) return { loader: route.loader, params: route.params(m) };
  }
  return { loader: ROUTES[0].loader, params: {} };
}

let currentPageCleanup = null;
// Monotonic mount counter. Each mountPage() call bumps it; a render whose
// captured id is no longer the latest is stale and must not commit.
let _mountSeq = 0;
// Render chain: mounts are SERIALIZED through this promise so two overlapping
// navigations (e.g. a slow page render still in flight when the operator hits
// Back → popstate) never interleave their clearNode/appendChild on the shared
// #app-root. Without serialization a stale render's DOM writes can land AFTER
// the winning page painted, leaving the wrong page (or a blank root) at the
// current URL — the WebKit back-navigation blank/wrong-board bug. The _mountSeq
// guard alone is insufficient: it only fires AFTER `await mod.render`, by which
// point the stale render has already mutated the DOM.
let _renderChain = Promise.resolve();

function getRoot() {
  return document.getElementById("app-root");
}

function clearNode(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function emptyState(text) {
  const p = document.createElement("p");
  p.className = "empty-state";
  p.textContent = text;
  return p;
}

function mountPage(path) {
  // Claim the mount synchronously so a later call immediately makes us stale.
  const myId = ++_mountSeq;
  // Queue behind any in-flight render; never run two page renders concurrently.
  _renderChain = _renderChain
    .then(() => _runMount(path, myId))
    .catch((err) => console.error("[app] mount error:", err));
  return _renderChain;
}

async function _runMount(path, myId) {
  // Superseded before our turn came up? A newer mount will paint; skip.
  if (myId !== _mountSeq) return;

  const { loader, params } = matchRoute(path);
  const root = getRoot();
  if (!root) return;

  // Unmount the previous page first.
  if (currentPageCleanup) {
    try { currentPageCleanup(); } catch (err) { console.error("[app] cleanup error:", err); }
    currentPageCleanup = null;
  }

  // Loading placeholder.
  clearNode(root);
  root.appendChild(emptyState("Loading…"));

  // FIX(bug-2): update the active nav indicator BEFORE awaiting the lazy
  // page-module import so the highlight updates immediately, not only after the
  // dynamic import resolves. Fired again after mount to recover from a
  // late-stage failure.
  updateNavActive(path);

  try {
    const mod = await loader();
    if (myId !== _mountSeq) return;  // a newer navigation won the race
    clearNode(root);
    // `render` is async in every page module, so await the promise and adopt
    // its resolved cleanup — gated on still being the current mount so a
    // late-resolving stale render can't install its cleanup as the
    // page-cleanup-of-record. Serialization guarantees no other render mutates
    // the DOM concurrently with this one.
    const cleanup = await mod.render(root, params);
    if (myId !== _mountSeq) {
      // A newer mountPage started while we were rendering. Discard our cleanup
      // by invoking it directly so its timers/subs don't leak. (Page cleanups
      // must NOT clearNode(root); the next queued mount owns repainting.)
      if (typeof cleanup === "function") {
        try { cleanup(); } catch (err) { console.error("[app] stale-cleanup error:", err); }
      }
      return;
    }
    currentPageCleanup = typeof cleanup === "function" ? cleanup : null;
  } catch (err) {
    if (myId !== _mountSeq) return;
    console.error(`[app] failed to render ${path}:`, err);
    clearNode(root);
    root.appendChild(emptyState(`Page failed to load: ${String(err)}`));
  }
  if (myId !== _mountSeq) return;
  updateNavActive(path);
}

function updateNavActive(path) {
  // Normalize both sides: strip trailing slashes; treat "" same as "/".
  const norm = (path || "/").replace(/\/+$/, "") || "/";
  const links = document.querySelectorAll(".app-nav a");
  for (const a of links) {
    const href = (a.getAttribute("href") || "").replace(/\/+$/, "") || "/";
    if (href === norm) {
      a.setAttribute("aria-current", "page");
    } else {
      a.removeAttribute("aria-current");
    }
  }
}

function attachRouter() {
  // Intercept nav clicks to use history.pushState rather than full page loads.
  document.addEventListener("click", (ev) => {
    const a = ev.target.closest("a[href]");
    if (!a) return;
    const href = a.getAttribute("href");
    if (!href || !href.startsWith("/")) return;
    if (a.hasAttribute("data-external")) return;
    if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
    ev.preventDefault();
    if (location.pathname !== href) {
      history.pushState({}, "", href);
    }
    mountPage(href);
  });

  window.addEventListener("popstate", () => mountPage(location.pathname));

  // Initial mount — gated behind the first-load auth exchange so no page
  // module issues a gated request (narrative, lanes/stale, narrative-stream,
  // activity-wall, terminal pane-streams, approval-rules) BEFORE the
  // token→cookie exchange has set the session cookie. Without this gate the
  // first paint races the exchange → 401 → permanently empty board (audit
  // bug #1). whenAuthReady() is idempotent and never rejects. Show a loading
  // placeholder immediately so the chrome isn't blank during the (sub-100ms)
  // exchange.
  const root = getRoot();
  if (root) {
    clearNode(root);
    root.appendChild(emptyState("Authenticating…"));
  }
  whenAuthReady().then(() => mountPage(location.pathname));
}

function attachControlToggle() {
  const toggle = document.querySelector('[data-testid="action-toggle-control-mode"]');
  if (!toggle) return;
  const reflectFromStore = () => {
    const on = !!store.get("ui.controlMode");
    toggle.setAttribute("aria-checked", on ? "true" : "false");
    toggle.setAttribute("aria-pressed", on ? "true" : "false");
    document.body.dataset.controlMode = on ? "true" : "false";
  };
  toggle.addEventListener("click", () => {
    const next = !store.get("ui.controlMode");
    store.set("ui.controlMode", next);
  });
  store.subscribe("ui.controlMode", reflectFromStore);
  reflectFromStore();
}

function attachPhaseIndicator() {
  const reflect = () => {
    const current = store.get("mission.phase") || "INIT";
    document.querySelectorAll(".phase-segment").forEach((el) => {
      const segment = el.dataset.testid?.replace(/^phase-segment-/, "");
      if (segment === current) {
        el.setAttribute("aria-current", "step");
      } else {
        el.removeAttribute("aria-current");
      }
    });
    const statusEl = document.querySelector('[data-testid="mission-status"]');
    if (statusEl) statusEl.textContent = current ? `· ${current}` : "";
  };
  store.subscribe("mission.phase", reflect);
  reflect();
}

function attachConnectionIndicator() {
  // Repurpose the toast region for connection-status announcements.
  const toast = document.getElementById("toast-region");
  let lastStatus = null;
  store.subscribe("ui.connectionStatus", (status) => {
    if (status === lastStatus) return;
    lastStatus = status;
    if (!toast) return;
    if (status === "connected") {
      toast.textContent = "";
    } else if (status === "connecting") {
      toast.textContent = "Connecting…";
    } else if (status === "lagging") {
      toast.textContent = "Catching up…";
    } else {
      toast.textContent = "Disconnected — retrying";
    }
  });
}

function bootstrap() {
  attachControlToggle();
  attachPhaseIndicator();
  attachConnectionIndicator();
  attachRouter();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", bootstrap);
} else {
  bootstrap();
}

export { mountPage, matchRoute, ROUTES };
