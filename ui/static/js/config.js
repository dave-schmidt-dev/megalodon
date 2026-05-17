// config.js — Single-flight FE config loader.
//
// Fetches /api/v1/config once, caches the result, and returns it to all
// concurrent and subsequent callers. Concurrent callers before resolution
// receive the same in-flight Promise. Subsequent callers after resolution
// receive a resolved Promise (no additional network requests).
//
// Usage:
//   import { loadConfig } from "./config.js";
//   const config = await loadConfig();
//
// Errors: network/parse failures reject the Promise. The caller decides how
// to handle (page-level skeleton dismissal, retry prompt, etc.). No automatic
// retry — that is policy that belongs in the caller.

import { API_CONFIG } from "./constants.js";

/**
 * @typedef {Object} Config
 * @property {string}   csrf_token
 * @property {number}   heartbeat_interval_seconds
 * @property {number}   poll_interval_seconds
 * @property {number}   stale_threshold_seconds
 * @property {string[]} allowed_origins
 * @property {Object[]} lanes
 * @property {Object[]} phases
 * @property {string[]} task_id_patterns
 * @property {string[]} harnesses
 * @property {Object[]} task_sections
 */

/** @type {Promise<Config> | null} */
let _pending = null;

/**
 * Load the application config from /api/v1/config.
 *
 * Single-flight: multiple concurrent callers share one in-flight fetch.
 * After first successful resolution the same Promise is returned on every
 * subsequent call (no further network activity).
 *
 * @returns {Promise<Config>}
 */
export async function loadConfig() {
  if (_pending !== null) return _pending;

  _pending = fetch(API_CONFIG)
    .then((res) => {
      if (!res.ok) {
        throw new Error(`[config] HTTP ${res.status} from ${API_CONFIG}`);
      }
      return res.json();
    })
    .then((/** @type {Config} */ data) => {
      // Breadcrumb for manual-smoke verification.
      const laneCount = Array.isArray(data.lanes) ? data.lanes.length : 0;
      const phaseCount = Array.isArray(data.phases) ? data.phases.length : 0;
      console.log("[config] loaded", laneCount, "lanes,", phaseCount, "phases");
      // Replace _pending with a stable resolved Promise so future callers
      // skip the fetch entirely.
      _pending = Promise.resolve(data);
      return data;
    })
    .catch((err) => {
      // Clear cache so a future call can retry (policy is caller's, but at
      // least don't permanently cache an error).
      _pending = null;
      throw err;
    });

  return _pending;
}

/**
 * Reset the internal cache. FOR TESTS ONLY — do not call in production code.
 *
 * Clears the module-level `_pending` slot so the next `loadConfig()` call
 * triggers a fresh fetch. Required between unit test cases to isolate state.
 *
 * @returns {void}
 */
export function _resetForTests() {
  _pending = null;
}
