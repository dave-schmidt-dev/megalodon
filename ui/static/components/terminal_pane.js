// @ts-check
// components/terminal_pane.js — reusable xterm.js + pane-stream SSE component.
//
// Lifted from ui/static/pages/dashboard-v92.js:245-291 (openEventSource) and
// the Terminal instantiation at dashboard-v92.js:184-195.
//
// Used by:
//   pages/grid.js       — scrollback: 500 (memory-bound grid panes)
//   pages/lane_detail.js — scrollback: 5000 (full-screen lane view)
//
// Prerequisites:
//   /static/xterm/xterm.js  must be loaded before this module runs
//   /static/xterm/xterm.css must be linked in <head>
//
// window.Terminal and window.FitAddon (optional) are the UMD exports of the
// vendored xterm.js and addon-fit.js bundles.

/**
 * Create a self-contained terminal pane that subscribes to a lane's pane-stream
 * SSE endpoint, decodes base64 byte chunks, and writes them to an xterm.js
 * Terminal instance.
 *
 * @param {{ lane: string, scrollback?: number }} opts
 *   lane      - lane short code used to build the SSE URL
 *   scrollback - number of lines to keep in xterm scrollback buffer (default 500)
 *
 * @returns {{ element: HTMLElement, cleanup: () => void }}
 *   element  - the container <div> to append into the DOM; xterm opens inside it
 *   cleanup  - call this to close the SSE connection and dispose the Terminal
 */
export function createTerminalPane({ lane, scrollback = 500 }) {
  // --- DOM container -------------------------------------------------------

  const container = document.createElement('div');
  container.className = 'term-pane';
  // Flex child: let the parent control sizing. xterm's own canvas will fill it.
  container.style.cssText = 'flex:1; min-height:0; overflow:hidden;';

  // --- xterm instance -------------------------------------------------------

  const term = new window.Terminal({
    allowProposedApi: true,
    convertEol: true,
    cursorBlink: false,
    scrollback,
    theme: { background: '#0b0d10' },
  });

  // Open the terminal inside the container once it exists (caller must append
  // container to the document, but xterm.open() just needs a DOM node — it
  // doesn't require the element to be attached to document).
  term.open(container);

  // --- SSE connection -------------------------------------------------------

  const url = `/api/v1/lane/${encodeURIComponent(lane)}/pane-stream`;
  const es = new EventSource(url, { withCredentials: true });

  es.onmessage = (ev) => {
    try {
      const bytes = _base64ToUint8(ev.data);
      term.write(bytes);
    } catch (e) {
      // malformed event — ignore
    }
  };

  es.onerror = async () => {
    // EventSource fires onerror on any disruption. Only act when CLOSED (not
    // just a transient blip). Probe the same URL via a plain fetch so we can
    // distinguish a 401 (session expired) from a temporary outage.
    if (es.readyState !== EventSource.CLOSED) return;
    try {
      const probe = await fetch(url, {
        method: 'GET',
        credentials: 'same-origin',
        headers: { Accept: 'text/event-stream' },
      });
      if (probe.status === 401) {
        // Surface the auth-failure through the global v9.2 modal if present.
        if (typeof window.__v92_showPasteTokenModal === 'function') {
          window.__v92_showPasteTokenModal();
        }
      }
      // Best-effort: cancel the response body to free the connection.
      try { probe.body && probe.body.cancel(); } catch {}
    } catch {
      // network error — leave silently
    }
  };

  // --- cleanup --------------------------------------------------------------

  function cleanup() {
    try { es.close(); } catch {}
    try { term.dispose(); } catch {}
  }

  return { element: container, cleanup };
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/**
 * Decode a base64 string to a Uint8Array.
 * xterm.js `.write()` accepts Uint8Array directly, which preserves every raw
 * byte including ANSI escape sequences without any charset re-encoding.
 *
 * @param {string} b64
 * @returns {Uint8Array}
 */
function _base64ToUint8(b64) {
  const bin = atob(b64);
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  return arr;
}

// ---------------------------------------------------------------------------
// Global registration — for non-module scripts (dashboard-v92.js IIFE) that
// cannot use `import`. ES-module consumers should import createTerminalPane
// directly; this is a convenience shim only.
// ---------------------------------------------------------------------------
window.createTerminalPane = createTerminalPane;
