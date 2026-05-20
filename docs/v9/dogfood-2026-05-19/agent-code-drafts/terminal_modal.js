// terminal_modal.js — S-HYBRID-DASHBOARD live terminal modal
// Opens a read-only view of a lane's tmux pipe-pane stream in a centered dialog.
// SSE source: GET /api/v1/lane/{short}/terminal_stream
// Falls back gracefully when BE endpoint is not yet available.

// Strip common ANSI/VT100 escape sequences for plain-text <pre> display.
function stripAnsi(raw) {
  return raw
    .replace(/\x1b\[[0-9;?]*[A-Za-z]/g, "")
    .replace(/\x1b\][^\x07\x1b]*((\x07)|(\x1b\\))/g, "")
    .replace(/\x1b[^[\]]/g, "")
    .replace(/\r(?!\n)/g, "\n");
}

// Decode base64 bytes to UTF-8 string.
function b64ToStr(b64) {
  try {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new TextDecoder("utf-8", { fatal: false }).decode(bytes);
  } catch (_) {
    return "";
  }
}

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") node.className = v;
      else if (k.startsWith("on") && typeof v === "function") {
        node.addEventListener(k.slice(2).toLowerCase(), v);
      } else if (v === true) {
        node.setAttribute(k, "");
      } else {
        node.setAttribute(k, String(v));
      }
    }
  }
  for (const c of children) {
    if (c == null || c === false) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

// Maximum accumulated text buffer size (chars). Oldest chars trimmed when exceeded.
const MAX_BUFFER = 200_000;

export function openTerminalModal({ lane, short }) {
  let textBuffer = "";
  let es = null;
  let reconnectTimer = null;

  // ── Status badge ────────────────────────────────────────────────────────
  const statusBadge = el("span", {
    class: "badge state-idle",
    "data-testid": "terminal-modal-status",
  }, "Connecting");

  // ── Output display ──────────────────────────────────────────────────────
  const pre = el("pre", {
    style: [
      "flex:1; min-height:0; margin:0; padding:var(--sp-3);",
      "overflow:auto; font-family:var(--font-mono,ui-monospace,monospace); font-size:12px;",
      "background:var(--bg,#0e0f12); color:var(--text,#e7e9ec);",
      "white-space:pre-wrap; word-break:break-all; tab-size:8;",
    ].join(" "),
    "aria-label": `Terminal output for ${lane}`,
    "aria-live": "polite",
    "aria-atomic": "false",
    "data-testid": "terminal-modal-pre",
  });

  // ── Header ──────────────────────────────────────────────────────────────
  const header = el("div", {
    style: [
      "display:flex; align-items:center; gap:var(--sp-3);",
      "padding:var(--sp-3) var(--sp-4); flex-shrink:0;",
      "border-bottom:1px solid var(--border,#2a2e35);",
    ].join(" "),
  },
    el("span", { style: "font-weight:600; font-size:13px; letter-spacing:0.02em;" },
      `${lane} · live terminal`),
    statusBadge,
    el("span", { style: "flex:1;" }),
  );

  // ── Footer ──────────────────────────────────────────────────────────────
  const copyBtn = el("button", {
    type: "button",
    class: "button",
    title:
      "Copy the entire terminal buffer to clipboard. " +
      "Buffer may contain agent prompts, model output, and tool call args.",
    "data-testid": "terminal-modal-copy",
    onclick: () => {
      navigator.clipboard.writeText(textBuffer).catch(() => {});
    },
  }, "Copy buffer");

  const closeBtn = el("button", {
    type: "button",
    class: "button button--primary",
    title: "Close terminal modal (Esc)",
    "data-testid": "terminal-modal-close",
    onclick: () => closeModal(),
  }, "Close (Esc)");

  const footer = el("div", {
    style: [
      "display:flex; justify-content:space-between; align-items:center;",
      "padding:var(--sp-3) var(--sp-4); flex-shrink:0;",
      "border-top:1px solid var(--border,#2a2e35);",
    ].join(" "),
  }, copyBtn, closeBtn);

  // ── Modal ────────────────────────────────────────────────────────────────
  const modal = el("div", {
    role: "dialog",
    "aria-modal": "true",
    "aria-label": `${lane} live terminal`,
    "data-testid": "terminal-modal",
    style: [
      "position:fixed; top:50%; left:50%; transform:translate(-50%,-50%);",
      "width:min(960px,92vw); height:min(620px,82vh);",
      "background:var(--surface,#15171b);",
      "border:1px solid var(--border,#2a2e35);",
      "border-radius:var(--r-2,8px);",
      "display:flex; flex-direction:column;",
      "z-index:10001; box-shadow:0 24px 64px rgba(0,0,0,0.75);",
    ].join(" "),
  }, header, pre, footer);

  // ── Backdrop ─────────────────────────────────────────────────────────────
  const backdrop = el("div", {
    "data-testid": "terminal-modal-backdrop",
    style: [
      "position:fixed; inset:0; background:rgba(0,0,0,0.55);",
      "z-index:10000; cursor:pointer;",
    ].join(" "),
    onclick: () => closeModal(),
  });

  // ── Lifecycle ─────────────────────────────────────────────────────────────
  function setStatus(text, stateClass) {
    statusBadge.textContent = text;
    statusBadge.className = `badge state-${stateClass}`;
  }

  function appendText(text) {
    pre.textContent += text;
    textBuffer += text;
    // Trim oldest chars if buffer is too large.
    if (textBuffer.length > MAX_BUFFER) {
      const trim = textBuffer.length - MAX_BUFFER;
      textBuffer = textBuffer.slice(trim);
      pre.textContent = textBuffer;
    }
    pre.scrollTop = pre.scrollHeight;
  }

  function closeModal() {
    if (reconnectTimer != null) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    if (es) { es.close(); es = null; }
    backdrop.remove();
    modal.remove();
    document.removeEventListener("keydown", onKeyDown);
  }

  function onKeyDown(ev) {
    if (ev.key === "Escape") {
      ev.preventDefault();
      closeModal();
    }
  }

  // ── SSE stream ───────────────────────────────────────────────────────────
  function attach() {
    if (!modal.isConnected) return;
    const url = `/api/v1/lane/${encodeURIComponent(short)}/terminal_stream`;
    es = new EventSource(url, { withCredentials: true });

    es.addEventListener("snapshot", (evt) => {
      try {
        const { bytes_b64 } = JSON.parse(evt.data);
        const text = stripAnsi(b64ToStr(bytes_b64));
        pre.textContent = text;
        textBuffer = text;
        pre.scrollTop = pre.scrollHeight;
        setStatus("Attached", "working");
      } catch (_) {}
    });

    es.addEventListener("append", (evt) => {
      try {
        const { bytes_b64 } = JSON.parse(evt.data);
        appendText(stripAnsi(b64ToStr(bytes_b64)));
      } catch (_) {}
    });

    es.onerror = () => {
      setStatus("Error", "blocked");
      es.close();
      es = null;
      // Exponential backoff capped at 5s.
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        if (modal.isConnected) {
          setStatus("Reconnecting…", "idle");
          attach();
        }
      }, 2000);
    };
  }

  // ── Mount ─────────────────────────────────────────────────────────────────
  document.body.appendChild(backdrop);
  document.body.appendChild(modal);
  document.addEventListener("keydown", onKeyDown);
  closeBtn.focus();
  attach();
}
