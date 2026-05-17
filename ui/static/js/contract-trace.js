// V9 M2 — runtime fetch wrapper for contract scan instrumentation.
// Active only when window.__M9_CONTRACT_TRACE__ === true.
// In production (flag unset), this script is a no-op.
//
// Spec: docs/superpowers/specs/2026-05-16-v9-m2-contract-scan-design.md §7.

(function () {
  if (typeof window === "undefined" || !window.__M9_CONTRACT_TRACE__) return;

  const calls = [];
  const originalFetch = window.fetch.bind(window);
  const OriginalEventSource = window.EventSource;

  window.fetch = async function (input, init) {
    const url = typeof input === "string" ? input : input.url;
    const method =
      (init && init.method) ||
      (typeof input === "object" && input.method) ||
      "GET";
    calls.push({ kind: "fetch", method, url, ts: Date.now() });
    return originalFetch(input, init);
  };

  window.EventSource = function (url, options) {
    calls.push({ kind: "eventsource", method: "GET", url, ts: Date.now() });
    return new OriginalEventSource(url, options);
  };

  window.__M9_CONTRACT_CALLS__ = calls;
  console.info("[M9] contract-trace active");
})();
