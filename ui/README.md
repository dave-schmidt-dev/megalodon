# Megalodon Orchestrator-Console UI

A localhost dashboard for watching and steering a Megalodon mission. Reads `STATUS.md`, `TASKS.md`, `HISTORY.md`, `findings/`, `claims/`, and `.mission-events` directly from disk; pushes live updates over Server-Sent Events; offers orchestrator actions (post SIGNAL, inject CHALLENGE, reclaim, phase-flip, set mission status, inject task) via authenticated POST endpoints.

**Audience:** the human orchestrator running a Megalodon mission. **Trust model:** localhost-only.

---

## TL;DR

```bash
# from <PROJECT_ROOT>/ (the Megalodon project directory)
uv pip install fastapi 'uvicorn[standard]' sse-starlette pyyaml watchfiles
uv run python ui/server.py
```

Then open <http://127.0.0.1:8080> in any modern browser.

---

## What you get

| Tab | What it shows |
|---|---|
| **Dashboard** (`/`) | Lane status grid (6 rows, color-coded by staleness); phase progress bar; recent activity feed |
| **Tasks** (`/tasks`) | Phase-grouped task queue with filters; expandable per task |
| **Findings** (`/findings`) | Sidebar filters (lane, severity, task) + virtualized list + markdown render pane |
| **Timeline** (`/timeline`) | SIGNAL / ACK-VERIFIED / DISSENT / DEFER chronological feed |
| **History** (`/history`) | HISTORY.md viewer with phase-flip rules |
| **Actions** (`/actions`) | Forms behind confirmation modals: SIGNAL, CHALLENGE, reclaim, phase-flip, mission status, inject task |

Mobile-responsive per `ui/adrs/S6-mobile-spec.md` (Full ≥1280px, Compact 768–1279px, Glance <768px — actions hidden on Glance).

---

## Architecture at a glance

- **Backend** (`ui/server.py`): Python 3.12 + FastAPI + uvicorn (single worker, bound to `127.0.0.1`).
- **Realtime push:** Server-Sent Events at `/api/v1/events` (auto-reconnect, `Last-Event-ID`).
- **File-watch:** `watchfiles` (FSEvents on macOS) + 2-second poll backstop.
- **Atomic writes:** content-hash CAS over `os.replace`; per-file `asyncio.Lock` in alphabetical-path order.
- **Frontend:** Vanilla JS + HTMX + Alpine.js + plain CSS. No build step. Served from `ui/static/`.
- **Filesystem-as-truth:** no database. UI restarts lose nothing.

Full spec: `ui/SPEC.md`. ADRs: `ui/adrs/ADR-001` through `ADR-005`. Mobile: `ui/adrs/S6-mobile-spec.md`. API contract: `ui/api-contract.md`.

---

## Installation

### Prerequisites

- Python **3.12+** (uses `datetime.fromisoformat` Z-suffix support, `typing.Literal` extensions, etc.).
- `uv` recommended (`pipx install uv`), or any Python toolchain that can `pip install`.
- A modern browser (Chrome / Firefox / Safari current — IE/Edge-legacy not supported).

### Install dependencies

```bash
uv pip install fastapi 'uvicorn[standard]' sse-starlette pyyaml watchfiles
```

Or with stock `pip`:

```bash
python -m pip install fastapi 'uvicorn[standard]' sse-starlette pyyaml watchfiles
```

(No build step. No `npm install`. No `node_modules/`.)

### Where it lives

```
<PROJECT_ROOT>/                    # your Megalodon mission directory
├── STATUS.md                      # ← UI reads this every tick
├── TASKS.md
├── HISTORY.md
├── MISSION.md
├── README.md
├── .mission-events
├── claims/
├── findings/
└── ui/                            # ← this directory
    ├── server.py                  # FastAPI app
    ├── api-contract.md            # endpoint reference
    ├── SPEC.md                    # architecture spec
    ├── adrs/                      # decision records
    ├── static/                    # HTML/CSS/JS (served at /)
    └── tests/                     # pytest + Playwright
```

---

## Running

### Standard run

From the project root:

```bash
uv run python ui/server.py
```

The server prints a startup banner including the CSRF token (rotates per process restart), the bind address, and the mission directory it has detected.

Visit <http://127.0.0.1:8080>.

### Configuration

| Env var | Default | Effect |
|---|---|---|
| `MEGALODON_HOST` | `127.0.0.1` | Bind address. **Do not change** — exposing beyond loopback breaks the trust model. |
| `MEGALODON_PORT` | `8080` | TCP port. |
| `MEGALODON_PROJECT_ROOT` | `..` from `ui/server.py` | Mission directory to read. Auto-detected; override only if running from outside the project. |
| `MEGALODON_LOG_LEVEL` | `INFO` | Standard Python logging level. `DEBUG` is verbose. |
| `MEGALODON_HEARTBEAT_SECONDS` | `15` | SSE heartbeat interval. Don't tune below 5 or above 60. |
| `MEGALODON_POLL_SECONDS` | `2` | File-watch poll backstop interval. |
| `MEGALODON_SSE_QUEUE` | `100` | Per-client SSE event queue depth. Overflow drops oldest + emits `lagging`. |

### Logs

Rotating file handler writes to `/tmp/megalodon-ui.log` (1MB × 2 backups). Console mirrors at the configured level.

### Stopping

`Ctrl-C`. State persists in the project directory; the next start picks up where you left off.

---

## Running tests

### Unit + integration

```bash
cd ui/tests
pytest -m unit              # ~1s, no server
pytest -m integration       # ~5–10s, in-process API + fixtures
pytest                      # both
```

### End-to-end (Playwright)

```bash
cd ui/tests
npx playwright install      # one-time
npx playwright test         # ~30–90s
```

`playwright.config.ts` boots a real server against `ui/tests/fixtures/<small|medium|large>/` mission directories. HTML report at `ui/tests/playwright-report/`.

---

## Smoke-test: is it working?

```bash
# In one terminal:
uv run python ui/server.py

# In another:
curl -s http://127.0.0.1:8080/api/v1/mission | python -m json.tool
curl -s http://127.0.0.1:8080/api/v1/lanes | python -m json.tool
curl -s -N http://127.0.0.1:8080/api/v1/events     # streams events; Ctrl-C to stop
```

You should see the live mission state. Trigger a worker tick (any STATUS.md edit), and an SSE `status-change` event arrives within ~300ms (file-watch path) or ~2.5s (poll backstop).

---

## Common operator workflows

### Watching mission health
Stay on Dashboard. Watch the lane grid for red staleness chips or BLOCKED states.

### Investigating a finding
Findings tab → filter by lane + severity → click to open the markdown render. YAML frontmatter is parsed and shown in the right-pane metadata sidebar.

### Posting a SIGNAL to a lane
Actions → "Post SIGNAL". Required: target lane (or `ALL`), kind, claim, evidence path:line. The UI writes a canonical-grammar line (`<SIG kind=... from=orchestrator to=... utc=... evidence=...>claim</SIG>`) into the lane's STATUS notes via content-hash CAS. See ADR-001 + BACKEND P2.5-C §Δ2.

### Reclaiming a stale row
Dashboard → click red staleness chip → "Reclaim". Server removes `claims/<task-id>/`, resets TASKS bracket to `[ ]`, sets STATUS row state to `STALE-RECLAIMED`, appends a HISTORY note. All four operations are atomic; an interrupted reclaim leaves consistent partial state.

### Flipping a phase manually
Actions → "Flip phase" → modal shows current phase (from `.mission-events`) and proposed `to`. Server validates `from == current`; 409 if stale (operator clicks "Refresh"). Lock via `mkdir .phase-flip-locks/<from>-to-<to>`; on success appends `.mission-events` and updates README Mission status section.

---

## Mobile

Open <http://127.0.0.1:8080> from a tablet or phone on the same Wi-Fi if you've tunneled to localhost (e.g., `ssh -L 8080:127.0.0.1:8080`). Read-only "is everything OK?" works fine. Actions tab is hidden on Glance (<768px) — switch to a wider device for mutations.

---

## Troubleshooting

**The server won't start (`ModuleNotFoundError`).**
Run the `uv pip install` line above. The server prints which dependency is missing.

**"Port 8080 already in use".**
`MEGALODON_PORT=9000 uv run python ui/server.py`. Or kill the existing process.

**Dashboard shows stale data (last update >10s old).**
SSE stream may have disconnected. The browser auto-reconnects via `EventSource`; if it doesn't recover within 30s, hard-refresh the page (Cmd-R / Ctrl-R). Server-side: check `/tmp/megalodon-ui.log` for `lagging` or `disconnected` lines.

**"Duplicate locks" red chip on a task.**
Two claim directories exist for the same logical task (e.g., `P2-C→B` and `P2-CtoB`). This is the v7 non-ASCII task-id defect (ADR-005). The UI doesn't auto-fix; you decide which to keep. Future: `ui/tools/normalize-claims.py` (CROSS task — not yet built).

**Action returns 403 from the browser.**
Either the `Origin` header doesn't match the bound address (some browsers send `null` from `file://` URLs) or the CSRF token is missing. Open the dashboard from `http://127.0.0.1:8080`, not from a saved HTML file or different hostname.

**Action returns 409 `phase-stale`.**
You opened the phase-flip modal, then a worker auto-flipped the phase in the background. Refresh the modal and try again with the new `from` phase.

**Action returns 409 `CAS_CONTENTION`.**
The orchestrator and a worker raced on a file write three times in a row. Wait one second and retry. If sustained, see ADR-001 — at >1% sustained rate, consider escalating to AUDIT for a v8 protocol change.

---

## When to restart the server

- **After dependency upgrade** — `uv pip install -U` then restart.
- **After CSRF token rotation desired** — the token regenerates on every start.
- **Never required for mission state changes** — the UI re-derives from filesystem on every request.

---

## What the UI does NOT do

- **Modify the source project's git state.** No commits, no pushes. RULE 7.
- **Run workers.** Workers are separate Claude sessions you start yourself.
- **Persist state across restarts.** Filesystem is the source of truth.
- **Expose itself to the network.** Bound to `127.0.0.1`. Tunnel if you need remote access.
- **Auto-recover from BLOCKED.** BLOCKED is a deliberate human-in-loop escalation per MISSION.md §"BLOCKED vetoes auto-flip". The dashboard makes it visible; you act.

---

## See also

- `ui/SPEC.md` — full architecture specification
- `ui/api-contract.md` — endpoint reference (BACKEND lane)
- `ui/adrs/ADR-001-content-hash-cas.md` — concurrency primitive
- `ui/adrs/ADR-002-sse-over-ws.md` — push protocol decision
- `ui/adrs/ADR-003-htmx-over-react.md` — frontend stack decision
- `ui/adrs/ADR-004-filesystem-as-truth.md` — no-DB rationale
- `ui/adrs/ADR-005-ascii-task-id-normalization.md` — task-id quorum
- `ui/adrs/S6-mobile-spec.md` — responsive layout tiers
- `ui/tests/README.md` — test suite reference
- `<PROJECT_ROOT>/README.md` — the Megalodon coordination protocol (v7)
- `<PROJECT_ROOT>/MISSION.md` — the current mission
