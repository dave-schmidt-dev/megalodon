// @ts-check
/**
 * Megalodon orchestrator-console reactive store.
 * Vanilla ES module, no deps. Re-entrancy safe. Returns deep-frozen reads.
 *
 * Slices (see FRONTEND plan-v2 P2.5-D and BACKEND plan-v2 P2.5-C Δ1-Δ12):
 *   status   { lanes: LaneRow[], lastUtc: string }
 *   tasks    { phases: Record<string, Task[]>, cross: Task[] }
 *   findings { list: FindingMeta[], byFilename: Record<string, Finding> }
 *   signals  { list: Signal[] }
 *   mission  { phase: string, events: MissionEvent[], missionStatus: string }
 *   config   { heartbeatIntervalSeconds: number, ... }
 *   ui       { controlMode: boolean, lastEventId: string,
 *              connectionStatus: "connected"|"connecting"|"disconnected"|"lagging" }
 */

import {
  CONTROL_MODE_KEY,
  SSE_STATUS_CHANGE, SSE_TASK_CHANGE, SSE_PHASE_FLIP,
  SSE_FINDING_NEW, SSE_HISTORY_APPEND, SSE_CLAIM_CREATE,
  SSE_CLAIM_DONE, SSE_SIGNAL_NEW, SSE_LAGGING,
  SSE_HEARTBEAT, SSE_MISSION_STATUS,
} from './constants.js';

/** Deep-freeze any plain value. Mutates input but result is referentially equal. */
function deepFreeze(value) {
  if (value === null || typeof value !== "object" || Object.isFrozen(value)) {
    return value;
  }
  // Walk own keys (arrays included)
  for (const key of Object.keys(value)) {
    deepFreeze(value[key]);
  }
  return Object.freeze(value);
}

/** Structured clone with a fallback for older runtimes. */
function clone(value) {
  if (value === undefined || value === null) return value;
  if (typeof structuredClone === "function") {
    try { return structuredClone(value); } catch (_) { /* fall through */ }
  }
  return JSON.parse(JSON.stringify(value));
}

/** Split a dotted path into segments. Empty string -> []. */
function splitPath(path) {
  if (!path) return [];
  return String(path).split(".");
}

/** Walk an object by segments, returning undefined if any hop is missing. */
function walk(obj, segments) {
  let cur = obj;
  for (const seg of segments) {
    if (cur == null || typeof cur !== "object") return undefined;
    cur = cur[seg];
  }
  return cur;
}

function readControlModeFromStorage() {
  try {
    if (typeof localStorage === "undefined") return false;
    return localStorage.getItem(CONTROL_MODE_KEY) === "true";
  } catch (_) {
    return false;
  }
}

function persistControlMode(v) {
  try {
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(CONTROL_MODE_KEY, v ? "true" : "false");
    }
  } catch (_) { /* ignore */ }
  try {
    if (typeof document !== "undefined" && document.body) {
      document.body.dataset.controlMode = v ? "true" : "false";
    }
  } catch (_) { /* ignore */ }
}

function reflectLastEventId(id) {
  try {
    if (typeof document !== "undefined" && document.body) {
      document.body.dataset.lastEventId = String(id);
    }
  } catch (_) { /* ignore */ }
}

function initialState() {
  return {
    status: { lanes: [], lastUtc: "" },
    tasks: { phases: {}, cross: [] },
    findings: { list: [], byFilename: {} },
    signals: { list: [] },
    claims: { list: [] },
    activitySummaries: {},
    mission: { phase: "", events: [], missionStatus: "" },
    config: {
      heartbeatIntervalSeconds: 15,
      fileWatchDebounceMs: 100,
      pollIntervalSeconds: 2,
      maxFindingsPerPage: 100,
      sseQueueCapacity: 100,
    },
    ui: {
      controlMode: readControlModeFromStorage(),
      lastEventId: "",
      connectionStatus: "connecting",
    },
  };
}

export class Store {
  constructor() {
    /** @private */ this._state = initialState();
    /** @private */ this._subs = new Map(); // path -> Set<listener>
    /** @private */ this._anySubs = new Set(); // listener(path,new,old)
    /** @private */ this._appliedEvents = new Set(); // dedupe by `${type}|${utc}`
  }

  /**
   * Return a deep-frozen clone of the slice at the dotted path, or undefined.
   * @param {string} path
   */
  get(path) {
    const v = walk(this._state, splitPath(path));
    if (v === undefined) return undefined;
    return deepFreeze(clone(v));
  }

  /**
   * Set the slice at the dotted path. Notifies listeners on that path and ancestors.
   * @param {string} path
   * @param {*} value
   */
  set(path, value) {
    const segs = splitPath(path);
    if (segs.length === 0) {
      const oldRoot = this._state;
      this._state = clone(value) ?? initialState();
      this._notifyAll(oldRoot, this._state);
      return;
    }
    const oldVal = walk(this._state, segs);
    // Walk/create parents on a structural-share basis (top-level slices are mutable refs).
    let cur = this._state;
    for (let i = 0; i < segs.length - 1; i++) {
      const k = segs[i];
      if (cur[k] == null || typeof cur[k] !== "object") cur[k] = {};
      cur = cur[k];
    }
    cur[segs[segs.length - 1]] = clone(value);

    // Side-effects for ui.* keys.
    if (path === "ui.controlMode") persistControlMode(!!value);
    if (path === "ui.lastEventId") reflectLastEventId(value);

    this._emitPath(segs, oldVal, cur[segs[segs.length - 1]]);
  }

  /**
   * Functional update: updaterFn(prev) -> next. Same notification semantics as set().
   * @param {string} path
   * @param {(prev:any)=>any} updaterFn
   */
  update(path, updaterFn) {
    const prev = this.get(path); // frozen snapshot for caller
    const next = updaterFn(prev);
    this.set(path, next);
  }

  /**
   * Subscribe to changes at a path. listener(newValue, oldValue). Returns unsubscribe.
   * @param {string} path
   * @param {(next:any, prev:any)=>void} listener
   */
  subscribe(path, listener) {
    let set = this._subs.get(path);
    if (!set) { set = new Set(); this._subs.set(path, set); }
    set.add(listener);
    return () => set.delete(listener);
  }

  /**
   * Subscribe to every change. listener(path, newValue, oldValue). Returns unsubscribe.
   * @param {(path:string, next:any, prev:any)=>void} listener
   */
  subscribeAll(listener) {
    this._anySubs.add(listener);
    return () => this._anySubs.delete(listener);
  }

  /**
   * Hydrate from /api/v1/state response shape. Populates every slice in one pass.
   * Accepts snake_case (server) and camelCase (test) keys.
   * @param {object} payload
   */
  hydrate(payload) {
    if (!payload || typeof payload !== "object") return;
    if (payload.status) this.set("status", payload.status);
    if (payload.tasks) this.set("tasks", payload.tasks);
    if (payload.findings) this.set("findings", payload.findings);
    if (payload.signals) this.set("signals", payload.signals);
    if (payload.claims) this.set("claims", payload.claims);
    if (payload.mission) this.set("mission", payload.mission);
    if (payload.config) {
      const c = payload.config;
      const normalized = {
        heartbeatIntervalSeconds:
          c.heartbeatIntervalSeconds ?? c.heartbeat_interval_seconds ?? 15,
        fileWatchDebounceMs:
          c.fileWatchDebounceMs ?? c.file_watch_debounce_ms ?? 100,
        pollIntervalSeconds:
          c.pollIntervalSeconds ?? c.poll_interval_seconds ?? 2,
        maxFindingsPerPage:
          c.maxFindingsPerPage ?? c.max_findings_per_page ?? 100,
        sseQueueCapacity:
          c.sseQueueCapacity ?? c.sse_queue_capacity ?? 100,
        ...c,
      };
      this.set("config", normalized);
    }
  }

  /**
   * Dispatch an SSE event by name. Idempotent on (eventType, payload.utc).
   * @param {string} eventType
   * @param {*} payload
   */
  applyEvent(eventType, payload) {
    if (!eventType || !payload) return;
    const utc = payload.utc ?? payload.id ?? "";
    const dedupeKey = `${eventType}|${utc}`;
    if (utc && this._appliedEvents.has(dedupeKey)) return;

    switch (eventType) {
      case SSE_STATUS_CHANGE: {
        const row = payload.row;
        // BUG 1 guard: a status-change with no usable row (or no lane key on
        // the row) must NOT push `undefined`/garbage into status.lanes. Doing
        // so makes the NEXT event's findIndex dereference an undefined element
        // and throw (TypeError: Cannot read properties of undefined). Skip the
        // mutation entirely for malformed payloads.
        const laneKey = (row && row.lane) ?? payload.lane;
        if (!row || !laneKey) break;
        const lanes = (this.get("status.lanes") || []).slice();
        // Harden findIndex against any pre-existing undefined/null elements.
        const idx = lanes.findIndex((l) => l && l.lane === laneKey);
        if (idx >= 0) lanes[idx] = row; else lanes.push(row);
        this.set("status", { lanes, lastUtc: utc || this.get("status.lastUtc") || "" });
        break;
      }
      case SSE_TASK_CHANGE: {
        const t = payload.task || payload;
        const phase = t.phase || "_cross";
        if (phase === "_cross") {
          const cross = (this.get("tasks.cross") || []).slice();
          const idx = cross.findIndex((x) => x.id === t.id);
          if (idx >= 0) cross[idx] = t; else cross.push(t);
          this.set("tasks.cross", cross);
        } else {
          const phases = clone(this.get("tasks.phases") || {});
          const arr = (phases[phase] || []).slice();
          const idx = arr.findIndex((x) => x.id === t.id);
          if (idx >= 0) arr[idx] = t; else arr.push(t);
          phases[phase] = arr;
          this.set("tasks.phases", phases);
        }
        break;
      }
      case SSE_PHASE_FLIP: {
        // Fix per BACKEND P4-C→D V3 (MAJOR): SSE envelope uses `to` per
        // ui/api-contract.md; `to_phase` only appears on /api/v1/mission-events.
        const events = (this.get("mission.events") || []).slice();
        events.push(payload);
        this.set("mission", {
          phase: payload.to || payload.to_phase || payload.toPhase || this.get("mission.phase") || "",
          events,
          missionStatus: this.get("mission.missionStatus") || "",
        });
        break;
      }
      case SSE_FINDING_NEW: {
        const meta = payload.meta || payload;
        const list = (this.get("findings.list") || []).slice();
        list.unshift(meta);
        this.set("findings.list", list);
        if (payload.finding && (meta.filename || meta.path)) {
          const byFilename = clone(this.get("findings.byFilename") || {});
          byFilename[meta.filename || meta.path] = payload.finding;
          this.set("findings.byFilename", byFilename);
        }
        break;
      }
      case SSE_HISTORY_APPEND: {
        // History entries are surfaced through mission timeline.
        const events = (this.get("mission.events") || []).slice();
        events.push({ kind: "history", ...payload });
        this.update("mission", (m) => ({ ...(m || {}), events }));
        break;
      }
      case SSE_CLAIM_CREATE:
      case SSE_CLAIM_DONE: {
        // Repurpose task-change path: payload carries {task_id, lane, ...}
        const taskId = payload.task_id || payload.taskId;
        if (!taskId) break;
        const phases = clone(this.get("tasks.phases") || {});
        for (const ph of Object.keys(phases)) {
          const arr = phases[ph];
          const idx = arr.findIndex((x) => x.id === taskId);
          if (idx >= 0) {
            arr[idx] = { ...arr[idx], claim_state: eventType === SSE_CLAIM_DONE ? "done" : "claimed" };
            this.set("tasks.phases", phases);
            return;
          }
        }
        // Fall through to cross
        const cross = (this.get("tasks.cross") || []).slice();
        const idx = cross.findIndex((x) => x.id === taskId);
        if (idx >= 0) {
          cross[idx] = { ...cross[idx], claim_state: eventType === SSE_CLAIM_DONE ? "done" : "claimed" };
          this.set("tasks.cross", cross);
        }
        break;
      }
      case SSE_SIGNAL_NEW: {
        const list = (this.get("signals.list") || []).slice();
        list.push(payload);
        this.set("signals.list", list);
        break;
      }
      case SSE_LAGGING: {
        this.set("ui.connectionStatus", "lagging");
        break;
      }
      case SSE_HEARTBEAT: {
        this.set("ui.connectionStatus", "connected");
        break;
      }
      case SSE_MISSION_STATUS: {
        this.set("mission.missionStatus", payload.status || payload.mission_status || "");
        break;
      }
      default:
        // Unknown event: no-op (forward-compatible).
        break;
    }

    // BUG 1: mark the event applied only AFTER the switch completes without
    // throwing. If a handler threw, we never reach here, so a retry/replay of
    // the same (type, utc) can still be processed once the payload is sane.
    if (utc) this._appliedEvents.add(dedupeKey);

    if (utc) this.set("ui.lastEventId", String(utc));
  }

  // --- private: notification machinery ---

  /** Emit change events for a path, all ancestors (including root ""),
   *  AND any subscribed descendant paths whose value changed.
   *
   *  Descendant fan-out fixes bug-3 (phase strip stuck on INIT): when
   *  `hydrate()` does `set("mission", {phase: "PHASE-PLAN", ...})` the
   *  parent slice is replaced wholesale, so subscribers on the nested
   *  key `"mission.phase"` would never fire without this walk.
   *  Only paths that already have at least one subscriber are visited,
   *  so this is O(subscribed descendants) — not a full deep traversal.
   */
  _emitPath(segs, oldVal, newVal) {
    const exactPath = segs.join(".");
    // exact path
    this._notify(exactPath, newVal, oldVal);
    // descendants — only subscribed paths under exactPath that actually changed.
    this._emitChangedDescendants(exactPath, oldVal, newVal);
    // ancestors
    for (let i = segs.length - 1; i >= 0; i--) {
      const ancestorSegs = segs.slice(0, i);
      const ancestorPath = ancestorSegs.join(".");
      const ancestorVal = i === 0 ? this._state : walk(this._state, ancestorSegs);
      this._notify(ancestorPath, ancestorVal, ancestorVal /* old≈new for ancestor */);
    }
  }

  /** Notify subscribers on any descendant of `parentPath` whose value
   *  differs between oldParent and newParent. Walks only paths that
   *  actually have subscribers (looked up in this._subs).
   *  Skips the parent path itself and any top-level paths when
   *  parentPath === "" (root callers fire those separately).
   */
  _emitChangedDescendants(parentPath, oldParent, newParent) {
    const prefix = parentPath === "" ? "" : parentPath + ".";
    for (const subPath of this._subs.keys()) {
      if (subPath === parentPath) continue;
      if (subPath === "") continue;
      if (parentPath !== "" && !subPath.startsWith(prefix)) continue;
      const relSegs = parentPath === ""
        ? splitPath(subPath)
        : splitPath(subPath.slice(prefix.length));
      // Skip immediate top-level keys when fanning out from root — _notifyAll
      // already emits those.
      if (parentPath === "" && relSegs.length === 1) continue;
      const newDesc = walk(newParent, relSegs);
      const oldDesc = walk(oldParent, relSegs);
      if (newDesc !== oldDesc) {
        this._notify(subPath, newDesc, oldDesc);
      }
    }
  }

  _notify(path, next, prev) {
    const set = this._subs.get(path);
    if (set && set.size) {
      // Snapshot to keep re-entrant set() calls safe.
      for (const fn of Array.from(set)) {
        try { fn(next, prev); } catch (e) { /* listener errors swallowed */ }
      }
    }
    if (this._anySubs.size) {
      for (const fn of Array.from(this._anySubs)) {
        try { fn(path, next, prev); } catch (e) { /* swallow */ }
      }
    }
  }

  _notifyAll(oldRoot, newRoot) {
    this._notify("", newRoot, oldRoot);
    for (const k of Object.keys(newRoot)) {
      this._notify(k, newRoot[k], oldRoot ? oldRoot[k] : undefined);
    }
    // Fan out to any subscribed nested paths whose value changed under the
    // wholesale root swap (same fix as _emitPath but for the root-set path).
    this._emitChangedDescendants("", oldRoot || {}, newRoot);
  }
}

export const store = new Store();

/**
 * Whether the operator is in CONTROL mode (state-changing actions allowed).
 * READ-ONLY is the safe default. This is the single read every action affordance
 * (inject, restart-loop, kill-switch) must consult before enabling itself, and
 * should be re-read at action time (not cached in a closure) so there is one
 * source of truth and no stale-toggle risk.
 *
 * SCOPE — control mode is a CLIENT-SIDE UX SAFETY AFFORDANCE, NOT A SERVER-SIDE
 * AUTHORIZATION BOUNDARY. Its sole purpose is to prevent ACCIDENTAL destructive
 * actions (a stray click injecting into a lane, killing the fleet, restarting a
 * loop) by defaulting the UI to read-only and requiring an explicit toggle. It
 * is NOT a permission/security control:
 *   - The state lives in localStorage and on document.body — fully attacker- and
 *     operator-mutable from the client; it never reaches the server.
 *   - The mutating endpoints (inject / restart-loop / kill / phase-flip, etc.)
 *     are protected SERVER-SIDE by the session cookie + CSRF token. THAT is the
 *     real authorization boundary.
 *   - An authenticated operator can perform any of these actions regardless of
 *     the toggle's state (e.g. via curl, or by flipping the toggle on). Flipping
 *     it off does NOT revoke anyone's ability to act — it only re-arms the
 *     accidental-click guard in this UI.
 * Do not treat this flag as a substitute for backend auth.
 * @returns {boolean}
 */
export function controlEnabled() {
  return !!store.get("ui.controlMode");
}

/**
 * Subscribe to control-mode flips. Fires immediately with the current value so
 * callers can set their initial enabled/disabled state in one place, then again
 * on every change. Returns an unsubscribe function.
 * @param {(on: boolean) => void} fn
 * @returns {() => void} unsubscribe
 */
export function onControlMode(fn) {
  const unsub = store.subscribe("ui.controlMode", (next) => fn(!!next));
  try { fn(controlEnabled()); } catch (_) { /* ignore */ }
  return unsub;
}

// Reflect initial control-mode onto <body> so CSS can hook it on first paint.
try {
  if (typeof document !== "undefined" && document.body) {
    document.body.dataset.controlMode = store.get("ui.controlMode") ? "true" : "false";
  }
} catch (_) { /* ignore */ }

export default store;
