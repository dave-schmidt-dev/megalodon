// sse.js — Server-Sent Events client for the Megalodon orchestrator console.
//
// Bridges BACKEND's /api/v1/events stream to the in-memory store. Handles:
// - Initial state hydration via GET /api/v1/state
// - Auto-reconnect with exponential backoff (capped at 30s)
// - Heartbeat watchdog: if no event within 2.5 * heartbeatIntervalSeconds, force reconnect
// - Lagging events: refetch per-slice URLs the server identifies
// - Idempotent event delivery (store.applyEvent dedupes on utc)
//
// BACKEND event-stream contract:
//   /api/v1/events emits: status-change, task-change, phase-flip, finding-new,
//   history-append, claim-create, claim-done, signal-new, lagging, heartbeat,
//   mission-status.
// See findings/agent-8318-C-P1-backend-plan-2026-05-16T15-33Z.md §3 + P2.5-C Δ1-Δ7.

import { store } from "./store.js";
import { API_STATE, API_CONFIG, API_EVENTS, SSE_EVENT_TYPES } from "./constants.js";

const RECONNECT_INITIAL_MS = 500;
const RECONNECT_MAX_MS = 30_000;
const HEARTBEAT_GRACE_MULTIPLIER = 2.5;

const EVENT_TYPES = SSE_EVENT_TYPES;

let es = null;
let reconnectDelay = RECONNECT_INITIAL_MS;
let heartbeatTimer = null;
let connecting = false;
// BUG 3: single in-flight reconnect handle. Tracking the pending setTimeout
// lets reconnect()/connect() cancel a scheduled reconnect so a visibilitychange
// (or watchdog) and a queued backoff can't both fire connect() concurrently.
let reconnectTimer = null;

function clearReconnectTimer() {
  if (reconnectTimer !== null) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
}

function setConnectionStatus(status) {
  store.set("ui.connectionStatus", status);
}

function clearHeartbeatTimer() {
  if (heartbeatTimer !== null) {
    clearTimeout(heartbeatTimer);
    heartbeatTimer = null;
  }
}

function armHeartbeatWatchdog() {
  clearHeartbeatTimer();
  const cfg = store.get("config") || {};
  const interval = cfg.heartbeatIntervalSeconds ?? 15;
  const graceMs = Math.round(interval * HEARTBEAT_GRACE_MULTIPLIER * 1000);
  heartbeatTimer = setTimeout(() => {
    // No event for too long — likely a silently-dead connection. Force reconnect.
    console.warn("[sse] heartbeat watchdog tripped; forcing reconnect");
    reconnect();
  }, graceMs);
}

async function hydrateInitialState() {
  try {
    const [stateRes, configRes] = await Promise.all([
      fetch(API_STATE, { credentials: "same-origin" }),
      fetch(API_CONFIG, { credentials: "same-origin" }),
    ]);
    if (!stateRes.ok) throw new Error(`state: HTTP ${stateRes.status}`);
    if (!configRes.ok) throw new Error(`config: HTTP ${configRes.status}`);
    const state = await stateRes.json();
    const config = await configRes.json();
    // Fix per BACKEND P4-C→D V1 (BLOCKING): write CSRF token into the meta tag
    // so mission.js / dashboard.js POST helpers find a non-empty token.
    if (config.csrf_token && typeof document !== "undefined") {
      const meta = document.querySelector('meta[name="csrf-token"]');
      if (meta) meta.setAttribute("content", config.csrf_token);
    }
    store.set("config", normalizeConfig(config));
    store.hydrate(state);
  } catch (err) {
    console.error("[sse] initial hydrate failed:", err);
    setConnectionStatus("disconnected");
    throw err;
  }
}

function normalizeConfig(raw) {
  // BACKEND Δ7 emits snake_case; FE consumes camelCase.
  return {
    heartbeatIntervalSeconds: raw.heartbeat_interval_seconds ?? raw.heartbeatIntervalSeconds ?? 15,
    fileWatchDebounceMs: raw.file_watch_debounce_ms ?? raw.fileWatchDebounceMs ?? 100,
    pollIntervalSeconds: raw.poll_interval_seconds ?? raw.pollIntervalSeconds ?? 2,
    maxFindingsPerPage: raw.max_findings_per_page ?? raw.maxFindingsPerPage ?? 100,
    sseQueueCapacity: raw.sse_queue_capacity ?? raw.sseQueueCapacity ?? 100,
  };
}

async function handleLagging(payload) {
  // BACKEND Δ6: payload has { reason, resync_urls: string[], since_utc }
  const urls = payload.resync_urls || [];
  setConnectionStatus("lagging");
  // BUG 2: track whether ANY resync fetch actually succeeded. If every resync
  // failed (network error / non-2xx), the local state is still stale, so we
  // must NOT flip back to "connected" — that would mask the problem and stop
  // the watchdog/backoff from recovering. Leave the status at "lagging".
  let anySucceeded = false;
  for (const url of urls) {
    try {
      const res = await fetch(url, { credentials: "same-origin" });
      if (!res.ok) continue;
      const slice = await res.json();
      const sliceName = url.split("/").pop();
      // Heuristic mapping by URL tail; BACKEND endpoint paths match slice names.
      if (sliceName === "status") store.set("status.lanes", slice.lanes ?? slice);
      else if (sliceName === "tasks") store.set("tasks", slice);
      else if (sliceName === "signals") store.set("signals.list", slice.list ?? slice);
      else if (sliceName === "findings") store.set("findings.list", slice.list ?? slice);
      else if (sliceName === "history") store.set("mission.events", slice.list ?? slice);
      else if (sliceName === "mission-events") store.set("mission.events", slice.list ?? slice);
      else if (sliceName === "phase") {
        store.set("mission.phase", slice.current);
      }
      anySucceeded = true;
    } catch (err) {
      console.error(`[sse] resync ${url} failed:`, err);
    }
  }
  // Only declare recovery if at least one slice resynced (or there was nothing
  // to resync). If we had URLs and every one failed, stay "lagging".
  if (anySucceeded || urls.length === 0) {
    setConnectionStatus("connected");
  }
}

function attachEventHandlers(source) {
  for (const type of EVENT_TYPES) {
    source.addEventListener(type, (ev) => {
      let payload;
      try {
        payload = JSON.parse(ev.data);
      } catch {
        console.warn(`[sse] non-JSON ${type} payload dropped`);
        return;
      }
      armHeartbeatWatchdog();
      if (type === "lagging") {
        handleLagging(payload);
        return;
      }
      // store.applyEvent is idempotent (keyed on type|utc) and updates body[data-last-event-id]
      requestAnimationFrame(() => store.applyEvent(type, payload));
    });
  }
}

function reconnect() {
  // Cancel any pending scheduled reconnect so we don't end up with two
  // concurrent connect() runs (BUG 3).
  clearReconnectTimer();
  if (es) {
    try { es.close(); } catch { /* ignore */ }
    es = null;
  }
  clearHeartbeatTimer();
  if (!connecting) connect();
}

async function connect() {
  // BUG 3: a single guard for the whole connect lifecycle. `connecting` is now
  // held until the EventSource resolves (onopen) or fails (onerror/catch) —
  // NOT released synchronously while `es` is still in CONNECTING state. That
  // close the window where onerror→scheduleReconnect and
  // visibilitychange→reconnect could both spawn a second EventSource.
  if (connecting) return;
  connecting = true;
  // Any pending backoff is now superseded by this attempt.
  clearReconnectTimer();
  setConnectionStatus("connecting");
  try {
    await hydrateInitialState();
    es = new EventSource(API_EVENTS);
    attachEventHandlers(es);
    es.onopen = () => {
      connecting = false;
      reconnectDelay = RECONNECT_INITIAL_MS;
      setConnectionStatus("connected");
      armHeartbeatWatchdog();
    };
    es.onerror = () => {
      // EventSource auto-reconnects, but only if the server didn't return 4xx.
      // To be safe (proxy weirdness), close and back-off ourselves.
      connecting = false;
      setConnectionStatus("disconnected");
      clearHeartbeatTimer();
      try { es.close(); } catch { /* ignore */ }
      es = null;
      scheduleReconnect();
    };
  } catch (err) {
    // hydrate / EventSource construction failed before handlers were wired:
    // release the guard here and back off.
    console.error("[sse] connect failed:", err);
    connecting = false;
    scheduleReconnect();
  }
}

function scheduleReconnect() {
  // Single in-flight timer: clear any prior one so overlapping triggers
  // (onerror + watchdog) collapse to one pending reconnect (BUG 3).
  clearReconnectTimer();
  const delay = Math.min(reconnectDelay, RECONNECT_MAX_MS);
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, delay);
  reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
}

// Reconnect when the tab becomes visible again — proxies often kill SSE on backgrounded tabs.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && store.get("ui.connectionStatus") !== "connected") {
    reconnect();
  }
});

// Auto-start on module load.
connect();

export { connect, reconnect };
