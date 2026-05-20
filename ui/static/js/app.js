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

const PAGE_LOADERS = {
  "/": () => import("../pages/dashboard.js"),
  "/tasks": () => import("../pages/tasks.js"),
  "/findings": () => import("../pages/findings.js"),
  "/signals": () => import("../pages/signals.js"),
  "/mission": () => import("../pages/mission.js"),
};

let currentPageCleanup = null;
// Monotonic mount counter. Each mountPage() call bumps it. Stale in-flight
// renders compare their captured id against this counter and abort their
// final clearNode/appendChild writes if a newer navigation has started.
// Without this guard, dashboard.render's `await loadConfig()` would resolve
// LATE and paint dashboard over whatever tab the operator just clicked —
// the visible "snap back to Dashboard" bug.
let _mountSeq = 0;

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

async function mountPage(path) {
  const loader = PAGE_LOADERS[path] || PAGE_LOADERS["/"];
  const root = getRoot();
  if (!root) return;

  // Claim this mount. Any in-flight render older than `myId` must abort its
  // final paints; this is the only thing preventing dashboard's slow
  // `await loadConfig()` from overwriting a fresh tab the operator clicked
  // mid-render.
  const myId = ++_mountSeq;

  // Unmount the previous page first.
  if (currentPageCleanup) {
    try { currentPageCleanup(); } catch (err) { console.error("[app] cleanup error:", err); }
    currentPageCleanup = null;
  }

  // Loading placeholder.
  clearNode(root);
  root.appendChild(emptyState("Loading…"));

  // FIX(bug-2): update the active nav indicator BEFORE awaiting the lazy
  // page-module import. Otherwise the highlight only appears after the
  // dynamic import resolves (≈50-200ms), which the operator perceives as
  // "the indicator doesn't update". Also fired again after mount so a
  // late-stage failure doesn't leave the indicator stale.
  updateNavActive(path);

  try {
    const mod = await loader();
    if (myId !== _mountSeq) return;  // a newer navigation won the race
    clearNode(root);
    // FIX(bug-snap-back): `render` is async in every page module — so the
    // raw return value is a Promise, not the cleanup function. The previous
    // `typeof cleanup === "function"` guard always failed silently, leaking
    // every page's setIntervals + store subscriptions across navigations.
    // Now we await the render promise and adopt its resolved cleanup, and
    // gate the adoption on still being the current mount so a late-resolving
    // stale render can't (a) overwrite the visible page or (b) install its
    // cleanup as the page-cleanup-of-record.
    const cleanup = await mod.render(root);
    if (myId !== _mountSeq) {
      // A newer mountPage started while we were rendering. Discard our
      // cleanup by invoking it directly so its timers/subs don't leak.
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

  // Initial mount.
  mountPage(location.pathname);
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

export { mountPage };
