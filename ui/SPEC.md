# Megalodon Orchestrator-Console UI — Specification

- **Version:** 1.0 (post-PHASE-CHALLENGE)
- **Authored by:** agent-aa79 (ARCHITECT, LANE-B)
- **Task:** `P3-B`
- **UTC:** 2026-05-16T15:53Z
- **Status:** Final for PHASE-VERIFY (`P4-A→B`)
- **Audience:** Operator (single user, localhost), BACKEND lane (`P3-C` implementor), FRONTEND lane (`P3-D` implementor), TEST lane (`P3-E` verifier), AUDIT lane (`P4-A→B` verifier).

This SPEC is the canonical architecture document for the Tier-3 orchestrator console UI. It is the synthesis of:

- `findings/agent-aa79-B-P1-arch-plan-2026-05-16T15-33Z.md` (P1-B — base plan)
- `findings/agent-aa79-B-P2.5-arch-plan-v2-2026-05-16T15-46Z.md` (P2.5-B — incorporates BACKEND C1–C10)
- `findings/agent-8318-C-P1-backend-plan-2026-05-16T15-33Z.md` + `findings/agent-8318-C-P2.5-backend-plan-v2-2026-05-16T15-46Z.md` (BACKEND base + reconciled plan)
- `findings/agent-1371-D-P1-frontend-plan-...` + `findings/agent-1371-D-P2.5-frontend-plan-v2-2026-05-16T15-45Z.md` (FRONTEND base + reconciled plan)
- `findings/agent-9265-E-P2.5-test-plan-v2-2026-05-16T15-44Z.md` (TEST plan-v2)
- `findings/agent-34fc-A-P2.5-audit-plan-v2-2026-05-16T15-43Z.md` (AUDIT plan-v2 — v8 changeset basis)
- HISTORY.md @ 15:36Z (SIG-ORCH#1 bootstrap), @ 15:38Z (SIG-ORCH#2 file-collision), @ 15:40Z (SIG-ORCH#3 output-format)

If anything in this SPEC contradicts the plan-v2 docs, **the plan-v2 docs win** for their own lanes' contracts (BACKEND owns API contract; FRONTEND owns interaction; TEST owns acceptance). This SPEC is the meeting point and resolves cross-lane contracts only.

---

## 1. Scope and constraints

### In scope (MVP)
- Single-mission localhost dashboard rendering one `<PROJECT_ROOT>/` directory.
- Read views: dashboard (lanes + phase + activity feed), tasks, findings, timeline, history.
- Orchestrator actions: post SIGNAL, inject CHALLENGE, reclaim stale lane, phase-flip, set mission status, inject task.
- Real-time updates via Server-Sent Events with poll fallback.
- Mobile-responsive degradation (three tiers; see `ui/adrs/S6-mobile-spec.md`).

### Out of scope (deferred or explicit non-goals)
- Multi-mission selector (S-9 ADR exists as a foundation; not implemented in v1).
- Multi-user / auth / network-reachable deploy. Trust model: whoever can reach `127.0.0.1:<port>`.
- DB persistence. Filesystem is the source of truth; UI is renderer + controller.
- Cursor-based event-log resync (BACKEND plan-v2 §Δ13 — deferred, full-state for v1).
- Native mobile app, push notifications, service worker.

### Hard constraints (load-bearing)
- **Filesystem is canonical** (ADR-004). No DB; reads go through `os.stat`/`open`. Writes use `os.replace` atomic rename + content-hash CAS (ADR-001).
- **127.0.0.1 bind primary**, Origin-header check + CSRF-token-meta defense-in-depth (BACKEND plan-v2 §Δ8).
- **Phase source of truth = `.mission-events`**, not README.md (RULE 11).
- **Worker writes are not coordinated with UI writes** (ADR-001 — CAS not flock).

---

## 2. Data model (canonical)

Derived per-request from the filesystem. No persistence in the UI process.

```typescript
type LaneCode = "AUDIT" | "ARCHITECT" | "BACKEND" | "FRONTEND" | "TEST" | "META" | "ALL" | "ORCH";

type Phase = "INIT" | "PHASE-PLAN" | "PHASE-CHALLENGE" | "PHASE-BUILD" | "PHASE-VERIFY"
           | "DRAINING" | "COMPLETE";

type Severity = "BLOCKING" | "MAJOR" | "MINOR" | "NIT" | "DELTA";

type SignalKind = "SIGNAL" | "ACK-VERIFIED" | "DISSENT" | "DEFER";

interface Mission {
  id: string;                  // from MISSION.md
  status: "ACTIVE" | "DRAINING" | "COMPLETE" | "IDLE";
  current_phase: Phase;        // last .mission-events line's `to`
  cadence_seconds: number;
  started_utc: string | null;
  deliverable_date: string | null;
  scope: { in_scope: string[]; out_of_scope: string[] };
  lanes: LaneDef[];
  task_matrix: { [phase: string]: { [lane: string]: string } };
}

interface LaneRow {                  // BACKEND plan-v2 §Δ4
  lane: LaneCode;
  agent: string | null;
  state: "unclaimed" | "initialized" | `working: ${string}` | "idle" | "BLOCKED"
       | "PEER-REVIEWER" | `LANE-${string}-PEER-REVIEWER` | "STALE-RECLAIMED";
  last_utc: string | null;
  notes: string;
  staleness_seconds: number;
  is_stale: boolean;                 // staleness_seconds > 900
  working_task_id: string | null;
}

interface Task {
  id: string;                        // canonical ASCII per ADR-005
  id_aliases: string[];              // any Unicode-arrow forms observed
  phase: Phase;
  lane_code: LaneCode | "CROSS" | "CHALLENGE";
  description: string;
  expected_output_glob: string;
  state: "open" | "claimed" | "done";
  claimer_agent: string | null;
  claim_utc: string | null;
  done_utc: string | null;
  has_lock_dir: boolean;
  has_done_marker: boolean;
  dup_claim_dirs: string[];          // ADR-005 — duplicate detection
}

interface Finding {
  filename: string;
  frontmatter: {
    lane: LaneCode;
    agent: string;
    task: string;
    severity?: Severity;
    utc: string;
    artifact?: string;
    [k: string]: unknown;
  };
  body_md: string;                   // YAML stripped; renderable
}

interface HistoryEntry {
  utc: string;
  agent: string;
  lane: string;
  task_id: string;
  finding_filename: string | null;
  severity: Severity | "INFO";
}

interface PhaseEvent {
  utc: string;
  from_phase: Phase;
  to_phase: Phase;
  by_agent: string;
  reason: string;
}

interface Signal {                   // BACKEND plan-v2 §Δ1
  utc: string;
  from_lane: LaneCode;
  from_agent: string;
  to: LaneCode;
  kind: SignalKind;
  claim: string;
  evidence: { path: string; line?: number; section?: string };
  source_artifact: "status-notes" | "finding" | "history";
  source_ref: string;
  finding_ref?: string;
  confidence?: "high" | "low";       // low if parsed via free-form fallback
}
```

**Derived invariants the UI surfaces visually:**

- Task with `has_lock_dir = true` but state `open` ⇒ **amber chip** ("inconsistent: lock without claim").
- Task with `has_done_marker = true` but state ≠ `done` ⇒ **retroactive-recovery candidate** ("worker died mid-completion").
- Lane with `is_stale = true` and `state` ≠ `idle/PEER-REVIEWER` ⇒ **red chip + reclaim affordance**.
- Task with `dup_claim_dirs.length > 1` ⇒ **red chip** ("duplicate claim dirs: [list]"); ADR-005 mitigation.

---

## 3. API surface (canonical)

Authoritative: BACKEND's `ui/api-contract.md` (P3-C deliverable). This section names the contracts and is normative; full payload schemas live in BACKEND's doc.

### Read endpoints (idempotent GET)

| Path | Returns |
|---|---|
| `GET /api/v1/snapshot` | full initial-render state (mission + lanes + tasks + recent findings + recent history) |
| `GET /api/v1/mission` | `Mission` |
| `GET /api/v1/lanes` | `LaneRow[]` |
| `GET /api/v1/lanes/{lane}` | drilldown — `{row, working_finding?, recent_history[]}` (BACKEND §Δ9) |
| `GET /api/v1/tasks?phase=&lane=&state=` | filtered `Task[]` |
| `GET /api/v1/findings?lane=&severity=&task=` | `Finding[]` (frontmatter + filename only) |
| `GET /api/v1/findings/{filename}` | `{frontmatter, body_md}` (BACKEND §Δ — frontmatter-aware) |
| `GET /api/v1/history?limit=` | `HistoryEntry[]` (newest first) |
| `GET /api/v1/mission-events` | `PhaseEvent[]` (BACKEND §Δ3) |
| `GET /api/v1/claims` | `{[task_id]: {present: bool, done: bool, paths: string[]}}` |
| `GET /api/v1/signals?since=<utc>` | `Signal[]` (BACKEND §Δ1) |
| `GET /api/v1/config` | `{heartbeat_interval_seconds, file_watch_debounce_ms, poll_interval_seconds, ...}` (BACKEND §Δ7) |
| `GET /api/v1/stream` | **SSE** — see §4 below |

All task-id-bearing endpoints accept both `P2-C→B` and `P2-C-to-B` forms; server canonicalizes to ASCII in responses (ADR-005).

### Action endpoints (POST; require CSRF token + Origin header)

| Path | Body (sketch) | Effects |
|---|---|---|
| `POST /api/v1/signal` | `{target_lane?, target_agent?, kind, claim, evidence}` | CAS-append into STATUS.md notes column |
| `POST /api/v1/inject-challenge` | `{converged_finding_id, severity?, target_lane?}` | Append to TASKS.md CHALLENGE section |
| `POST /api/v1/reclaim` | `{lane_code, force?}` | `rm -rf claims/<id>`; reset TASKS bracket; STATUS → STALE-RECLAIMED (alphabetical lock order per BACKEND §Δ5) |
| `POST /api/v1/phase-flip` | `{from, to, reason, force?}` (BACKEND §Δ; ARCHITECT §B) | mkdir `.phase-flip-locks/<from>-to-<to>`; append `.mission-events`; update README header. 409 on `from != current` (no force). |
| `POST /api/v1/mission-status` | `{status, note?}` | Append `.mission-events`; update README Mission status section |
| `POST /api/v1/inject-task` | `{task_text, section}` (validated by regex per BACKEND §Δ; ARCHITECT P2.5-B §C10) | Append into TASKS.md targeted section |

All actions: write-tmp + atomic rename + append HISTORY note `<utc> | orchestrator-ui | ACTION | <action> | — | INFO`.

**Error model** (BACKEND §Δ10): error-by-endpoint matrix authoritative in BACKEND's `ui/api-contract.md`. All `STALE_READ` errors are retryable; FE retries up to 3 times with 100ms backoff.

---

## 4. Real-time event stream

`GET /api/v1/stream` (SSE) emits events as filesystem changes are observed by the BACKEND's `watchfiles` watcher + 2-second poll backstop (ADR-002).

| Event name | Payload |
|---|---|
| `status-change` | `{lane, row: LaneRow, utc}` (BACKEND §Δ4) |
| `task-changed` | `{task_id, new_state, claimer_agent?, done_utc?, utc}` |
| `finding-added` | `{filename, lane, severity, task, utc}` |
| `history-appended` | `{entry: HistoryEntry}` |
| `phase-flip` | `{from, to, by, reason, utc}` |
| `claim-added` | `{task_id, agent, utc}` |
| `claim-done` | `{task_id, agent, utc}` |
| `signal-new` | `Signal` (BACKEND §Δ1; deduped server-side by `(from_agent, utc, to, claim_hash)`) |
| `lagging` | `{reason, resync_urls[], since_utc}` (BACKEND §Δ6) |

**Backpressure (ADR-002 / P2.5-B §C):** per-client bounded queue (default 100 events). Overflow → drop oldest + emit `lagging`. Server-side coalescing of `status-change` events at 100ms granularity (30ms during 30s post-phase-flip burst window).

**Heartbeat (BACKEND §Δ7):** server emits `event: heartbeat\ndata: {}` every 15 seconds; FE timeout = `2.5 × heartbeat_interval = 37.5s` → force reconnect.

---

## 5. Page structure

Six tabs in a single-page app. Routes are `/`, `/tasks`, `/findings`, `/timeline`, `/history`, `/actions`. Mobile degradation per `ui/adrs/S6-mobile-spec.md`.

1. **Dashboard** (`/`): lane status grid, phase progress bar, recent activity feed (last 20).
2. **Tasks** (`/tasks`): phase-grouped task queue. Filters: lane (multi), state. Each task expandable: claim history + finding link.
3. **Findings** (`/findings`): filter sidebar (lane multi, severity multi, task) + virtualized finding list + markdown render pane (right). Glance tier opens finding as full-screen modal.
4. **Timeline** (`/timeline`): chronological SIGNAL/ACK/DISSENT/DEFER feed. Two-axis swim lanes (lanes vertical, time horizontal) on Full tier; linear list on Compact/Glance.
5. **History** (`/history`): tabular HISTORY.md viewer + phase-flip horizontal rules.
6. **Actions** (`/actions`): orchestrator action forms behind confirmation modals. Hidden on Glance tier per ADR-S6.

Wireframes: see `findings/agent-aa79-B-P1-arch-plan-2026-05-16T15-33Z.md` §3-4. FRONTEND plan-v2 reaffirms 1280–1920px primary viewport.

---

## 6. Tech stack (locked)

| Layer | Choice | ADR |
|---|---|---|
| Runtime | Python 3.12 | — |
| Web framework | FastAPI + uvicorn (single-worker) | — |
| File-watch | `watchfiles` + 2s backup poll | ADR-002 |
| Templating | Jinja2 (SSR for initial paint) | — |
| Markdown | `markdown-it-py`; YAML frontmatter via `python-frontmatter` | — |
| YAML | `pyyaml.safe_load` | — |
| Realtime | Server-Sent Events (`sse-starlette`) — not WS | ADR-002 |
| Frontend | Vanilla JS + HTMX + Alpine.js (no build step) | ADR-003 |
| Styling | Plain CSS + custom properties; dark default | — |
| Atomic writes | `os.replace` + content-hash CAS | ADR-001 |
| Static serving | FastAPI `StaticFiles` mount at `/static/` (BACKEND §Δ11) | — |
| Tests | `pytest` + `httpx.AsyncClient` + `@playwright/test` | TEST §P3-E |

---

## 7. Concurrency contract

Authoritative concurrency rules (synthesizes ARCHITECT §A, BACKEND §Δ5, §Δ8):

1. **Content-hash CAS** for STATUS.md / TASKS.md / README.md / `.mission-events` writes by the UI (ADR-001). Max 3 attempts; CasContentionError on exhaustion.
2. **Per-file `asyncio.Lock` in strict alphabetical absolute-path order**; release in reverse (BACKEND §Δ5). Eliminates UI-internal deadlock.
3. **Phase-flip lock by mkdir** `.phase-flip-locks/<from>-to-<to>` (RULE 11). UI must validate `from == current` before mkdir; 409 otherwise.
4. **HISTORY.md uses O_APPEND**; no lock needed.
5. **`os.replace` is atomic** on POSIX; the rename is the commit point.

---

## 8. ADR index

Architecture Decision Records in `ui/adrs/` (this run):

| ID | Title | Status |
|---|---|---|
| ADR-001 | Content-hash CAS over `fcntl.flock` for cross-process state | Accepted |
| ADR-002 | SSE over WebSockets for server-to-client push | Accepted |
| ADR-003 | HTMX + Alpine.js over React/Vue for the frontend | Accepted |
| ADR-004 | Filesystem-as-truth (no DB) | Accepted |
| ADR-005 | ASCII task-id normalization | Accepted |
| S-6 | Mobile-responsive layout spec (CROSS) | Proposed |
| S-9 | Multi-mission selector (CROSS) | Future |

---

## 9. Acceptance criteria (handoff to TEST P3-E and AUDIT P4-A→B)

The build (P3-C + P3-D) satisfies this SPEC iff:

1. **Read path:** all 12 `GET /api/v1/...` endpoints return 200 with the schemas in §2/§3 for the live mission directory.
2. **Action path:** all 6 `POST /api/v1/...` endpoints succeed against a clean fixture mission and produce the documented filesystem effects, atomic-rename-verified.
3. **SSE:** consumers receive events in §4's grammar within 300ms of file change (file-watch path); within 2.5s under FSEvents drop (poll backstop). `lagging` event fires when queue would overflow.
4. **Concurrency:** running 2 simultaneous `POST /api/v1/reclaim` against the same lane produces exactly one success + one 409 (`STALE_READ` or `LANE_FRESH`); no partial state.
5. **Task-id normalization (ADR-005):** the same task referenced as `P2-C→B`, `P2-C-to-B`, and URL-encoded `P2-C%E2%86%92B` all resolve to the same `Task`; duplicate claim dirs surface in `dup_claim_dirs`.
6. **Phase-flip safety (BACKEND §Δ; ARCHITECT §B):** with stale `from`, server returns 409 `phase-stale`; UI prefills `from` from snapshot.
7. **Phase source of truth:** UI displays current phase from `GET /api/v1/mission-events` last line, never from README.md (RULE 11).
8. **Mobile tiers (S-6):** load page at 1440×900 = Full; 1024×768 = Compact (action tab still visible); 390×844 = Glance (action tab hidden + explanatory placeholder).
9. **Accessibility:** axe-core no violations on Findings tab at all three viewports.
10. **Localhost-only:** server bound to `127.0.0.1`; cross-origin POST without `Origin: http://localhost:*` returns 403; missing CSRF token returns 403.

TEST's plan-v2 enumerates Playwright test IDs (T-R11-c, T-R2-b, T-V-HIST-validator/e2e, T-FX-FAILMODE-a/b/c). The above acceptance criteria are necessary; TEST's matrix is the sufficient implementation.

---

## 10. Forward dependencies and PHASE-VERIFY readiness

| Item | Owner | Blocking PHASE-VERIFY |
|---|---|---|
| `ui/server.py` + stub endpoints | BACKEND (P3-C) | Yes |
| `ui/api-contract.md` | BACKEND (P3-C) | Yes (schema source for AUDIT verify) |
| `ui/static/*` + integration | FRONTEND (P3-D) | Yes |
| `ui/tests/*` Playwright + integration | TEST (P3-E) | Yes |
| `docs/v8-changeset.md` (canonical SIGNAL grammar, ASCII task-ids, etc.) | AUDIT (P3-A) | No (informs UI but doesn't gate build) |
| Mid-mission META report | META (P3-F) | No |

This SPEC is final for the run. RECONSIDERED notes welcome; rewrites are not.
