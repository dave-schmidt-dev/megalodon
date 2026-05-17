# Megalodon UI — API Contract

**Version:** 0.2.0 (PHASE-BUILD tick 2 — live mutations)
**Server:** `ui/server.py` + `ui/mutations.py`
**Bind:** `127.0.0.1:8080` (PRIMARY defense). All POSTs additionally require `Origin: http://127.0.0.1:8080` and `X-CSRF-Token: <token>` (Δ8 — defense-in-depth).
**Plan basis:** `findings/agent-8318-C-P1-backend-plan-2026-05-16T15-33Z.md` (P1-C) + `findings/agent-8318-C-P2.5-backend-plan-v2-2026-05-16T15-46Z.md` (P2.5-C, deltas Δ1–Δ13).

## Status as of tick 2 (15:59Z)

- **Read endpoints**: fully functional. Each parses live filesystem under `<PROJECT_ROOT>/` on every request.
- **SSE stream** (`/api/v1/events`): emits `status-change`, `task-change`, `phase-flip`, `finding-new`, `history-append`, `claim-create`, `claim-done`, `signal-new`, `heartbeat` (15 s), `lagging` (on slow consumer), `sync` (on connect).
- **Mutation endpoints** (`POST /api/v1/*`): **LIVE** — real CAS-based filesystem writes via `ui/mutations.py`. Per-file `asyncio.Lock` acquired in alphabetical-absolute-path order (Δ5 / m6). Content-hash CAS replaces flock (C1) with 3-attempt retry on `STALE_READ`. Phase-flip endpoint acquires `mkdir`-as-lock per RULE 11.
- **File-watch**: 2 s polling. `watchfiles` upgrade deferred to tick 3 (the polling layer is the documented C5/Δ5 fallback regardless).

## Running

```bash
uv pip install fastapi 'uvicorn[standard]' sse-starlette pyyaml
uv run python ui/server.py
# Visit http://127.0.0.1:8080
```

The static FE mount serves files from `ui/static/`. CSRF token is logged on startup and exposed via `GET /api/v1/config`.

---

## Read endpoints

All `GET` endpoints. JSON responses, UTF-8. No auth on reads (localhost-only).

| Method | Path | Query params | Returns |
|---|---|---|---|
| GET | `/api/v1/state` | — | full `MissionState` snapshot (cold-render payload) |
| GET | `/api/v1/status` | — | `{lanes: LaneRow[]}` |
| GET | `/api/v1/tasks` | `phase?`, `lane?`, `state?` | `{tasks: Task[]}` filtered |
| GET | `/api/v1/phase` | — | `{current: string, last_event: PhaseEvent \| null}` |
| GET | `/api/v1/mission-events` | `since?` (UTC) | `{events: PhaseEvent[]}` (full log or `since` slice) |
| GET | `/api/v1/findings` | `lane?`, `severity?`, `task?` | `{findings: Finding[]}` (metadata only) |
| GET | `/api/v1/findings/{filename}` | — | full `Finding` with `body_md` |
| GET | `/api/v1/history` | `limit?` | `{history: HistoryEntry[]}` |
| GET | `/api/v1/claims` | — | `{claims: {[task_id]: Claim}}` |
| GET | `/api/v1/signals` | `since?` (UTC) | `{signals: Signal[]}` |
| GET | `/api/v1/lanes/{lane}` | — | `{row, findings, recent_history}` drilldown |
| GET | `/api/v1/config` | — | runtime config (heartbeat cadence, CSRF token, etc.) |
| GET | `/api/v1/events` | — | **SSE stream** (see SSE section) |

## Mutation endpoints

All `POST` endpoints. Body JSON. Require `Origin` allowlist + `X-CSRF-Token` header. Return `{ok: bool, ...}`. Errors return `{ok: false, error, code, recoverable, ...}` with HTTP 4xx/5xx.

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/v1/signal` | `{to_lane, claim, evidence}` | `{ok, new_status_row?, utc}` |
| POST | `/api/v1/reclaim` | `{lane, force?}` | `{ok, action: "stale-reclaim" \| "retroactive-recovery", utc}` |
| POST | `/api/v1/challenge` | `{finding_filename, description?}` | `{ok, task_id, utc}` |
| POST | `/api/v1/phase-flip` | `{from, to, reason, force?}` | `{ok, event_line, utc}` |
| POST | `/api/v1/mission-status` | `{status}` | `{ok, utc}` |
| POST | `/api/v1/inject-task` | `{task_text, section}` | `{ok, utc}` |

`task_text` MUST match: `^\[ \] \[[A-Z\-\d]+\] \`[A-Za-z0-9\-→\.]+\` — .+$` (C10/Δ10 validation).

---

## SSE stream — `GET /api/v1/events`

Server sends. Client subscribes once. Each event has `event:`, `data:` (JSON), and `id:` (monotonic sequence number) per FRONTEND's tick-3 coordination item.

| Event type | Payload | When |
|---|---|---|
| `sync` | `{utc}` | on initial connect; FE should issue `GET /api/v1/state` immediately |
| `status-change` | `{fingerprint, utc}` | any STATUS.md/TASKS.md/claims change; FE re-fetches affected slice |
| `task-change` | `{task_id, old_state, new_state, agent, utc}` | (reserved — emitted in tick 2-3) |
| `phase-flip` | `{from, to, by, reason, utc}` | on `.mission-events` append |
| `finding-new` | `{filename, frontmatter, utc}` | new `findings/*.md` appears |
| `history-append` | `{entry, utc}` | new line in HISTORY.md |
| `claim-create` | `{task_id, utc}` | (reserved — emitted in tick 2-3) |
| `claim-done` | `{task_id, utc}` | (reserved — emitted in tick 2-3) |
| `signal-new` | `Signal` payload | (reserved — emitted in tick 2-3 with Δ2 strict-parsing) |
| `heartbeat` | `{utc}` | every 15 s |
| `lagging` | `{reason, resync_urls: string[], since_utc}` | client queue overflow; FE should refetch listed URLs |

**Connection recovery:** SSE auto-reconnects. Client may send `Last-Event-ID` header; server currently ignores (full-state resync). Cursor-based incremental fetch is DEFER-d per DELTA d13.

---

## Shapes (TypeScript-style)

```typescript
type LaneCode = "AUDIT" | "ARCHITECT" | "BACKEND" | "FRONTEND" | "TEST" | "META" | "ALL" | "ORCH";

type LaneState =
  | "unclaimed"
  | "initialized"
  | `working: ${string}`
  | "idle"
  | "BLOCKED"
  | "PEER-REVIEWER"
  | `LANE-${string}-PEER-REVIEWER`
  | "STALE-RECLAIMED";

interface LaneRow {
  lane: LaneCode;
  agent: string | null;          // null if unclaimed
  state: LaneState;
  last_utc: string | null;       // ISO-8601 UTC, trailing Z
  notes: string;                 // raw markdown cell content
  staleness_seconds: number | null;
  is_stale: boolean;             // staleness_seconds > 900 AND state not in (idle, PEER-REVIEWER)
  working_task_id: string | null;
}

interface Task {
  id: string;                    // e.g. "P1-C", "P2-C→B", "P2.5-C"
  phase: string;                 // e.g. "PHASE 1 — PLAN"
  lane_code: "A" | "B" | "C" | "D" | "E" | "F" | null;
  description: string;
  state: "open" | "claimed" | "done";
  claimer_agent: string | null;
  claim_utc: string | null;
  done_utc: string | null;
  has_lock_dir: boolean;         // claims/<id>/ exists (or normalized variant)
  has_done_marker: boolean;      // claims/<id>/done exists
}

interface Finding {
  filename: string;
  lane: string | null;
  agent: string | null;
  task: string | null;
  severity: string | null;       // BLOCKING | MAJOR | MINOR | NIT | DELTA | N/A
  utc: string | null;
  artifact: string | null;
  title: string | null;
  frontmatter: Record<string, any>;
  body_md: string | null;        // null in list endpoints; populated for /findings/{filename}
}

interface HistoryEntry {
  utc: string;
  agent: string;
  lane: string;
  task: string;
  finding_filename: string;
  severity: string;
}

interface PhaseEvent {
  utc: string;
  from_phase: string;
  to_phase: string;
  by_agent: string;
  reason: string;
}

interface Signal {
  utc: string;
  from_lane: LaneCode;
  from_agent: string;            // "agent-XXXX" or "orchestrator"
  to: LaneCode;
  kind: "SIGNAL" | "ACK-VERIFIED" | "DISSENT" | "DEFER";
  claim: string;
  evidence: { path: string; line?: number; section?: string };
  source_artifact: "status-notes" | "finding" | "history";
  source_ref: string;            // e.g. "STATUS.md#BACKEND"
  finding_ref?: string;
  confidence: "high" | "medium" | "low";
}

interface Claim {
  task_id: string;
  agent: string | null;          // resolved from TASKS.md if possible
  claimed_utc: string;           // mtime of claims/<id>/ dir
  done: boolean;                 // claims/<id>/done exists
  done_utc: string | null;       // mtime of done marker
}

interface MissionState {
  lanes: LaneRow[];
  tasks: Task[];
  findings: Finding[];           // metadata only
  history: HistoryEntry[];
  phase: string;
  phase_events: PhaseEvent[];
  claims: Record<string, Claim>;
  signals: Signal[];
  mission_status: string;        // ACTIVE | DRAINING | COMPLETE | IDLE | PHASE-*
  fingerprint: string;           // 16-char SHA-256 prefix; changes when any tracked field changes
  utc: string;
}
```

---

## Error model

All error responses (HTTP 4xx/5xx):

```json
{
  "ok": false,
  "error": "human-readable description",
  "code": "STALE_READ" | "LANE_NOT_FOUND" | "LANE_FRESH" | "PHASE_MISMATCH" | "CONCURRENT_FLIP" | "VALIDATION_FAILED" | "FILESYSTEM_ERROR" | "ORIGIN_REJECTED" | "CSRF_FAILED" | "FILE_NOT_FOUND" | "INVALID_STATUS" | "FINDING_NOT_FOUND",
  "recoverable": true,
  "retry_after_utc": "..."        // optional
}
```

`recoverable: true` invites the FE to refresh state and retry (up to 3 attempts with 100 ms backoff). `false` means operator must hand-fix.

### Error code → endpoint matrix

| Endpoint | Possible error codes |
|---|---|
| `GET /api/v1/state` | `FILESYSTEM_ERROR` |
| `GET /api/v1/findings/{filename}` | `FILE_NOT_FOUND`, `FILESYSTEM_ERROR`, `400 invalid filename` |
| `GET /api/v1/lanes/{lane}` | `LANE_NOT_FOUND` |
| `POST /api/v1/signal` | `VALIDATION_FAILED`, `STALE_READ`, `ORIGIN_REJECTED`, `CSRF_FAILED` |
| `POST /api/v1/reclaim` | `LANE_NOT_FOUND`, `LANE_FRESH` (recoverable=false; pass `force=true`), `FILESYSTEM_ERROR` |
| `POST /api/v1/challenge` | `FINDING_NOT_FOUND`, `STALE_READ` |
| `POST /api/v1/phase-flip` | `PHASE_MISMATCH` (recoverable=true), `CONCURRENT_FLIP`, `STALE_READ`, `VALIDATION_FAILED` |
| `POST /api/v1/mission-status` | `INVALID_STATUS`, `STALE_READ` |
| `POST /api/v1/inject-task` | `VALIDATION_FAILED`, `STALE_READ` |

`STALE_READ` is recoverable: FE retries (3× / 100 ms backoff).

---

## Authentication (Δ8)

Three layers, none alone sufficient:

1. **Bind to `127.0.0.1`** (PRIMARY). Cannot be reached from network.
2. **Origin header check** on POSTs. Reject if `Origin` is not `http://127.0.0.1:8080` or `http://localhost:8080`. Returns 403 with `ORIGIN_REJECTED`.
3. **CSRF token**. Server generates per-process random token at startup. FE includes it in `X-CSRF-Token` header on POSTs. Token exposed via `GET /api/v1/config`. Mismatch → 403 `CSRF_FAILED`.

Token rotates per process restart — no session store.

---

## Concurrency model (current — stub)

- **Single uvicorn worker.** AsyncIO event loop.
- **Per-file `asyncio.Lock`** for mutations (alphabetical-absolute-path acquisition order, Δ5) — implemented but currently unused by stubs.
- **File reads** are blocking I/O wrapped via `run_in_threadpool` (FastAPI default).
- **Polling watcher** runs as a background `asyncio.Task` (2 s interval). Computes a fingerprint hash; broadcasts `status-change` on diff.
- **SSE clients** each get a bounded `asyncio.Queue(maxsize=100)`. Overflow drops queue contents and emits `lagging` event for explicit resync.

## Concurrency model (tick 2-3 target)

- Replace polling with `watchfiles.Observer` + retain 2 s poll as fallback (Δ5 / C5 cross-platform safety on macOS).
- Add **content-hash CAS** for all multi-file mutations (replaces flock from P1-C; addresses C1 advisory-lock semantic gap).
- POST endpoints actually mutate filesystem with temp-file + atomic-rename (`os.replace`).

---

## Non-ASCII task IDs (C3 / META CH-2 / 5-source BLOCKING quorum)

Workers have used both `P2-C→B` (Unicode `→`, U+2192) and `P2-C-to-B` (ASCII). The server's `_normalize_task_id` helper accepts both forms and additionally checks the truncated source-lane form (e.g., `P2-C`) when resolving filesystem state. UI surfaces inconsistency: a `Task` with `has_lock_dir` true on multiple variants is flagged amber/red.

**v8 expected to mandate** ASCII task IDs (AUDIT's `docs/v8-changeset.md` P3-A will codify). This server's lenient resolution remains as a compat shim for v7-style task IDs.

---

## SIGNAL grammar (Δ2 / 3-lane BLOCKING quorum)

Canonical form (single-line, deterministic-parse):

```
<SIG kind="SIGNAL" from="agent-XXXX" to="LANE-Y" utc="2026-05-16T15:46Z" evidence="path:line"> claim text </SIG>
```

Server-side parser is **lenient** — also accepts RULE-5 prose-style:

```
ACK-VERIFIED ARCHITECT: I read findings/agent-aa79-... at 2026-05-16T15:46Z and confirm ...
DISSENT TEST: I read STATUS.md:13 at ... and disagree because ...
DEFER FRONTEND: will address in tick N when I work on P2.5-D.
SIGNAL-ORCH: ...
```

Confidence field reflects parse quality:
- `high` — canonical `<SIG>...</SIG>` token
- `medium` — recognized prose pattern (ACK-VERIFIED, DISSENT, DEFER, SIGNAL-prefix)
- `low` — fallback / could not extract structure

The orchestrator-UI's `POST /api/v1/signal` endpoint writes only canonical form when persisting (tick 2-3). v8 changeset (AUDIT P3-A) may mandate canonical form for workers.

---

## What's NOT in this build (deferred to tick 3+)

1. ~~Real filesystem mutation in POST endpoints~~ — **LANDED tick 2** (CAS + temp-rename via `ui/mutations.py`).
2. `watchfiles`-driven file events — still polling at 2 s. Polling is the documented C5 fallback; an upgrade adds latency improvement, not correctness.
3. ~~`task-change`, `claim-create`, `claim-done`, `signal-new` SSE events~~ — **LANDED tick 2** via PollingWatcher diff detection.
4. ~~Phase-flip-lock acquisition by orchestrator-UI~~ — **LANDED tick 2** (`try_acquire_phase_flip_lock` mkdir-based).
5. Retroactive-recovery path in `/reclaim` — **detected** but currently only writes a HISTORY note; doesn't yet retro-mark TASKS as `[done]`. Tick 3.
6. Cursor-based delta resync (DEFER d13; ring-buffer optional add-on).
7. Mobile-responsive viewport adjustments (ARCHITECT's S-6 ADR — FE-side; server is transport-neutral).
8. `--project-root` CLI argument (TEST coordination item).

---

## Coordination with peer lanes

- **FRONTEND (`agent-1371`):** integrate against this stub. Live-file reads mean your FE will see real mission state immediately. POSTs return `{ok:true, stub:true}` so optimistic-update flows can be wired without state divergence. Coordinate `task-change` event shape with me before tick 3.
- **TEST (`agent-9265`):** `ui/tests/fixtures/small/` is a future fixture mission dir — server respects `MEGALODON_UI_PORT` env var for parallel test sessions. Will provide a `--project-root` CLI arg in tick 2.
- **ARCHITECT (`agent-aa79`):** ADR-001 (SSE vs WS) is encoded here per Δ12. ADR-002+ (your call) — defer to your `ui/SPEC.md`.
- **AUDIT (`agent-34fc`):** `docs/v8-changeset.md` items that affect this server: canonical SIGNAL grammar (Δ2 / M2 / C8), ASCII task IDs (C3), STATUS.md per-lane file split (SIG-ORCH#2). Will read your P3-A output and adjust in tick 2-3 (or P3.5 if it exists).
- **META (`agent-5f87`):** mid-mission report `findings/agent-5f87-F-P3-mid-mission-meta-2026-05-16T15-51Z.md` notes 3 BLOCKING-quorums emerged. This server's parser already handles 2 of the 3 (signal grammar leniency, claim-canon normalization).
