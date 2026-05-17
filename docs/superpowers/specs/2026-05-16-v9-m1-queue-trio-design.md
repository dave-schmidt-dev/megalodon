---
title: V9 M1 + M1.5 + M1.6 — Queue trio (design spec)
status: APPROVED-FOR-PLAN
version: 1.0
utc: 2026-05-16T22:35Z
roadmap-anchor: docs/v9/V9-ROADMAP.md §M1+M1.5+M1.6 + Migration plan §3d
spec-bundle: M1 (queue) + M1.5 (UI mutation migration) + M1.6 (backend unification)
prior-art:
  - docs/v9/QUEUE-DESIGN.md (full design, ACCEPT-WITH-FIXES per S-8)
  - docs/v9/queue/applier.py (skeleton, 397 LOC — needs B1/B2/B3/B4 fixes + Q1 additions)
  - docs/v9/queue/queue_client.py (skeleton, 270 LOC — needs B1 fix)
  - findings/agent-9bba-CROSS-S8-queue-design-audit-2026-05-16T19-12Z.md (S-8 audit)
codex-review: applied (CR-1, CR-2, CR-6 — UI migration + backend unification + legacy claim script)
---

# V9 M1 + M1.5 + M1.6 — Queue trio

## 1. Goal

Eliminate CAS contention (run-2: 79-83%) by serializing all writes to shared-mutable state through a singleton applier daemon. Ship M1.5 (UI mutation migration) in the same milestone — without it, two write paths persist and the queue's serialization guarantee is partial. Ship M1.6 (backend unification) — ui/server.py becomes a thin shim around `make_app()`; factory is canonical.

## 2. Locked decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **Promote skeletons from `docs/v9/queue/` to `megalodon_ui/queue/`** | Skeletons are 90% there. Promote into the codebase, apply S-8 fixes, add Q1 intents, add tests. Faster than rewriting. |
| D2 | **Singleton applier as standalone Python daemon** | Per QUEUE-DESIGN §6.5 + S-8 §A Q2 ACCEPT. `python3 -m megalodon_ui.queue.applier --mission-dir ...` invoked by operator at mission boot. |
| D3 | **S-8 BLOCKING fixes B1+B2+B3 land in v9.0** | B1 (UTC default) trivial; B2 (WAL crash-safety) discipline pattern; B3 (heartbeat file) ~10 LOC. B4 (legacy claim) handled by `scripts/migrate_claims_to_owner_txt.py` per CR-6, NOT runtime tolerance — strict mode preserved. |
| D4 | **Q1 add three intents in v9.0** | STATUS_ROW_INSERT, TASKS_INJECT, MISSION_EVENT_CORRECTION — all observed in run-2 traffic per S-8 §A Q1. |
| D5 | **M3 `_shared_state.py` swap at single line** | `scripts/_shared_state.py` imports `from ._backends import direct_fcntl as _backend`. M1 adds `scripts/_backends/queue_client.py` as a new backend; M1 swap is one-line change. The 92+ pytest tests covering M3 helper scripts continue passing without modification. |
| D6 | **M1.5 endpoints become 202-async** | Per Codex CR-1 open question: yes, async. POST returns `202 Accepted` + `Location: /api/v1/queue/<request_id>` header + JSON body `{request_id, intent, status: "pending"}`. FE polls or waits. Lower coupling, no synchronous block on applier latency. |
| D7 | **M1.6 hard-cuts ui/server.py to shim** | Per Codex CR-2 + open question: clean cut, no feature-flag fallback. ui/server.py becomes ~10 lines: `from megalodon_ui import make_app; uvicorn.run(make_app(mission_dir, ...))`. Preserves operator's `python ui/server.py` habit. All 1000+ lines of legacy logic delete. |
| D8 | **Legacy claim migration script lives at `scripts/migrate_claims_to_owner_txt.py`** | Per CR-6. Run once during v8→v9 cutover. Best-effort attribution from STATUS.md / HISTORY.md history; falls back to `legacy-pre-v9` if unknown. Idempotent — re-running on already-migrated tree is a no-op. |
| D9 | **Read path: unchanged direct reads + advisory LOCK_SH wrapper** | Per V9-ROADMAP M1 Option A (operator decision). `queue_client.read_consistent(path)` for correctness-bearing reads (rare). All other reads stay plain `Path.read_text()`. |
| D10 | **Test fixture: tiny mission tree under `scripts/tests/fixtures/queue_mission/`** | Self-contained mission state for queue/applier tests. Fresh per test via shutil.copytree (matches M3 fixture pattern). |

## 3. File manifest

### 3.1 Created

| Path | Purpose | Est. LOC |
|------|---------|---------|
| `megalodon_ui/queue/__init__.py` | package init | 5 |
| `megalodon_ui/queue/applier.py` | promoted from `docs/v9/queue/` + S-8 fixes B1-B4 + Q1 intents | ~500 |
| `megalodon_ui/queue/queue_client.py` | promoted + B1 fix + Q1 helpers | ~350 |
| `megalodon_ui/queue/schemas.py` | per-intent payload schemas (Pydantic) | ~200 |
| `megalodon_ui/queue/journal.py` | WAL journal (PENDING/APPLIED entries per B2) | ~100 |
| `scripts/_backends/queue_client.py` | M3 backend adapter that calls `megalodon_ui.queue.queue_client.submit()` | ~100 |
| `scripts/migrate_claims_to_owner_txt.py` | one-shot v8→v9 claim migration (CR-6) | ~120 |
| `scripts/start_applier.sh` | operator-friendly applier launcher | ~15 |
| `scripts/tests/fixtures/queue_mission/` | tiny fixture mission | (template files) |
| `scripts/tests/test_queue_applier.py` | T1-T4 from S-8 + applier core | ~400 (20 tests) |
| `scripts/tests/test_queue_client.py` | B1 regression + intent helpers | ~200 (15 tests) |
| `scripts/tests/test_queue_journal.py` | WAL + crash recovery | ~150 (8 tests) |
| `scripts/tests/test_queue_migrate_claims.py` | migration script | ~120 (6 tests) |
| `scripts/tests/test_shared_state_via_queue.py` | M3→M1 backend swap integration | ~100 (5 tests) |

### 3.2 Modified

| Path | Change | Est. LOC delta |
|------|--------|-----|
| `scripts/_shared_state.py` | swap `from ._backends import direct_fcntl as _backend` → `queue_client as _backend` | 1 |
| `megalodon_ui/server.py` | 4 mutation endpoints (lines ~324-337, ~367-400, ~537-565, +1) → 202-async via queue_client | +80, -60 |
| `ui/server.py` | reduce to thin shim (~10 lines) | +10, -1200 |
| `launch.md` | RULE 12-14 update: workers MUST use queue_client (already covered by M3 helper-scripts); add operator-startup instruction for applier | +20 |
| `README.md` | v9 operator runbook section: start applier first, then UI server | +30 |
| `HISTORY.md` | M1-COMPLETE entry | +1 entry |
| `.gitignore` | add `queue/pending/`, `queue/applied/`, `queue/rejected/`, `queue/.applier.lock/` (mission-state, not fixtures) | +6 |

## 4. M1 — Queue applier + queue_client (core)

### 4.1 Apply S-8 fixes

**B1 (BLOCKING)** — `queue_client.py:95` UTC default. Replace:
```python
utc = new_utc or utc_now().replace(":", "Z", 1)[:17] + "Z"  # broken
```
with:
```python
utc = new_utc or utc_now()  # full-precision ISO-8601 with seconds
```

**B2 (MAJOR)** — WAL pattern for append-intent crash safety. New `megalodon_ui/queue/journal.py`:
```python
class Journal:
    """Append-only WAL — entries PENDING then APPLIED.
    On replay: PENDING-without-APPLIED → check target file before re-applying.
    """
    def write_pending(self, rid: str, intent: str, target: str, payload: dict) -> None: ...
    def write_applied(self, rid: str, summary: str) -> None: ...
    def write_rejected(self, rid: str, reason: str) -> None: ...
    def replay(self) -> dict[str, str]:
        """Returns {rid: terminal_status} where status ∈ {APPLIED, REJECTED, PENDING_INDOUBT}."""
```

Applier integration: `_apply()` becomes:
```python
def _apply(self, req: dict) -> None:
    self.journal.write_pending(req["request_id"], req["intent"], req["target_file"], req["payload"])
    try:
        self._apply_inner(req)  # mutates target file
    except Exception as e:
        self.journal.write_rejected(req["request_id"], str(e))
        raise
    self.journal.write_applied(req["request_id"], "ok")
```

Crash-recovery in `Applier.__init__`:
```python
def __init__(self, ...):
    ...
    self.journal = Journal(self.queue_dir / "journal.log")
    terminal = self.journal.replay()
    for rid, status in terminal.items():
        if status == "PENDING_INDOUBT":
            # Check if target file already shows the payload.
            # For append intents, scan target for payload["line"]; if found, mark APPLIED.
            # Otherwise re-apply via standard drain.
            self._reconcile_indoubt(rid)
```

**B3 (MEDIUM)** — Heartbeat file. Applier drain loop:
```python
while True:
    (self.lock_dir / "heartbeat.txt").write_text(utc_now())
    self._drain_once()
    time.sleep(self.poll_seconds)
```

Workers check `queue/.applier.lock/heartbeat.txt` mtime; >30s = `BLOCKED-APPLIER-DOWN`.

**B4 (MINOR)** — Strict mode preserved per D8. Pre-v9 claim dirs without `owner.txt` rejected at applier; legacy claims migrated by `scripts/migrate_claims_to_owner_txt.py` during cutover.

### 4.2 Q1 — Add three new intents

#### STATUS_ROW_INSERT
```python
{
  "intent": "STATUS_ROW_INSERT",
  "payload": {
    "lane": "OBSERVER-7",  # any lane label; appender for surplus rows
    "agent": "agent-xxxx",
    "initial_state": "idle",
    "initial_utc": "2026-05-16T...",
    "initial_notes": ""
  }
}
```
Applier behavior: find STATUS.md table; insert new row at end (before closing line, if any). Preconditions: lane must NOT already exist in table.

#### TASKS_INJECT
```python
{
  "intent": "TASKS_INJECT",
  "payload": {
    "task_id": "CHALLENGE-42",
    "lane": "BE",
    "bracket": "[ ]",
    "description": "Fix XYZ bug",
    "after_task_id": "P5-RUN-MUTATIONS-E2E"  # insert position; or null = append
  }
}
```
Applier behavior: insert new task line into TASKS.md. Preconditions: task_id must NOT already exist.

#### MISSION_EVENT_CORRECTION
```python
{
  "intent": "MISSION_EVENT_CORRECTION",
  "payload": {
    "line": "<UTC> CORRECTION by <agent> -- <details referencing earlier line>"
  }
}
```
Applier behavior: append `line` to `.mission-events`. Schema validates "CORRECTION by " prefix. Identical to MISSION_EVENT_APPEND but with required prefix.

### 4.3 Singleton enforcement + lockfile

`queue/.applier.lock/` is a directory (mkdir-atomic). Contains:
- `pid.txt` — process PID at lock acquisition
- `start_utc.txt` — when applier started
- `heartbeat.txt` — refreshed every poll (B3)

On startup:
```python
def acquire_singleton(self) -> None:
    try:
        self.lock_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        pid_file = self.lock_dir / "pid.txt"
        if pid_file.exists():
            pid = int(pid_file.read_text().strip())
            if _process_alive(pid):
                raise RuntimeError(f"applier already running, pid={pid}")
        # Stale lock — take over.
        shutil.rmtree(self.lock_dir)
        self.lock_dir.mkdir(parents=True)
    (self.lock_dir / "pid.txt").write_text(str(os.getpid()))
    (self.lock_dir / "start_utc.txt").write_text(utc_now())
```

### 4.4 Applier CLI

```
python3 -m megalodon_ui.queue.applier --mission-dir PATH [--poll-seconds 2] [--debug]
```

Foreground process. Signal handlers: SIGTERM/SIGINT → graceful shutdown (finish current drain, release lock, exit 0).

### 4.5 Operator launcher script

`scripts/start_applier.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
MISSION_DIR="${1:-$PWD}"
echo "Starting applier for mission: $MISSION_DIR"
exec uv run --directory "$(git rev-parse --show-toplevel)" \
    --with pyyaml --with pydantic \
    python -m megalodon_ui.queue.applier \
    --mission-dir "$MISSION_DIR" "${@:2}"
```

## 5. M1.5 — UI mutation endpoint migration

### 5.1 Endpoints to migrate (factory only — `megalodon_ui/server.py`)

Per V9-ROADMAP M1.5:
1. **Lines ~324-337** — TASKS.md direct write → `queue_client.tasks_bracket()`
2. **Lines ~367-400** — STATUS.md direct write → `queue_client.status_update()`
3. **Lines ~537-565** — README.md + TASKS.md direct write → `queue_client.tasks_bracket()` (README write retained as direct since README isn't queue-target)
4. The 4th from V9-ROADMAP refers to `ui/server.py:1178-1215` — handled by M1.6 deprecation (legacy file deleted).

### 5.2 Pattern — synchronous → 202-async

**Before** (direct write):
```python
@app.post("/api/v1/reclaim")
async def reclaim(req: Request):
    body = await req.json()
    # ... validation ...
    write_atomic_to_status_md(body["lane"], body["new_state"], ...)
    return {"ok": True, "message": "reclaimed"}
```

**After** (queue submission):
```python
@app.post(API_RECLAIM)
async def reclaim(req: Request):
    body = await req.json()
    # ... validation ...
    rid = queue_client.status_update(
        mission_dir=ctx.mission_dir,
        agent=body["agent"],
        lane=body["lane"],
        new_state=body["new_state"],
        new_notes=body.get("notes", ""),
    )
    return JSONResponse(
        status_code=202,
        content={"request_id": rid, "intent": "STATUS_UPDATE", "status": "pending"},
        headers={"Location": f"/api/v1/queue/{rid}"},
    )
```

### 5.3 New introspection endpoint — `GET /api/v1/queue/{request_id}`

Returns current state of a submitted request:
```json
{"request_id": "...", "status": "pending|applied|rejected", "rejection_reason": "..."|null}
```

FE may poll this to confirm landing. Optional — FE may also just "fire and forget" with optimistic UI update.

### 5.4 FE adaptation (light)

`ui/static/pages/mission.js postAction(...)` already handles 200/202 + JSON. Confirm via smoke. If FE assumes 200 anywhere, adjust to accept 202.

## 6. M1.6 — Backend unification (factory canonical)

### 6.1 Rewrite `ui/server.py` as thin shim

Before: ~1200 LOC duplicating factory functionality.

After (~30 LOC):
```python
"""Legacy entry point — preserved for operator habit `python ui/server.py`.

All logic lives in `megalodon_ui.server.make_app()` per V9 M1.6.
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path

from megalodon_ui import make_app
from megalodon_ui.config import AppConfig
from megalodon_ui.constants import DEFAULT_PORT


def main() -> int:
    parser = argparse.ArgumentParser(prog="ui/server.py [legacy shim]")
    parser.add_argument("--mission-dir", default=os.environ.get("MEGALODON_MISSION_DIR", "."))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MEGALODON_PORT", DEFAULT_PORT)))
    args = parser.parse_args()

    import uvicorn
    app = make_app(mission_dir=Path(args.mission_dir).resolve(), config=AppConfig.load(), port=args.port)
    uvicorn.run(app, host="0.0.0.0", port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

### 6.2 Update MISSION.md run-3 to validate against factory

MISSION.md:20 currently validates against legacy shapes. Per CR-2: M1.6 makes shim invoke factory, so both `python ui/server.py` and `python -m megalodon_ui` produce identical responses. MISSION.md updated to either invocation — both exercise factory code.

### 6.3 api-contract.md is factory-only (M2 deliverable)

Already true per M2 spec D4. No additional change.

## 7. M3 backend swap integration

`scripts/_shared_state.py` currently:
```python
from ._backends import direct_fcntl as _backend
```

After M1 lands:
```python
from ._backends import queue_client as _backend
```

New `scripts/_backends/queue_client.py` adapter — same interface as `direct_fcntl.py` (`claim_dir_done`, `tasks_bracket`, `history_append`, `status_update`) but routes through `megalodon_ui.queue.queue_client.submit()` + `wait_until_applied()`. Returns when applied (or raises on rejection).

**Test:** `test_shared_state_via_queue.py` runs the M3 atomic_close test suite against the queue backend. If 92 M3 tests still pass, swap is verified.

## 8. Test plan T1-T4 (per S-8 §A Q9)

### T1 — Concurrent writes to same target_file from different agents
```python
def test_5_concurrent_status_updates_different_lanes():
    """5 STATUS_UPDATE requests submitted within 100ms to 5 different lanes.
    Verify applier applies in submitted_utc order; all 5 land cleanly."""
```

### T2 — Operator-direct-edit race
```python
def test_operator_direct_edit_rejected_by_hash_mismatch():
    """Submit STATUS_UPDATE with expected_hash_before; operator edits STATUS.md
    out-of-band; applier sees hash mismatch → reject."""
```

### T3 — Pending queue overflow
```python
def test_1000_pending_drains_fifo_without_oom():
    """Drop 1000 requests in queue/pending/; verify applier drains FIFO
    without crashing; verify rejected queue independent."""
```

### T4 — Disk-full during apply (simulated via mock)
```python
def test_disk_full_during_apply_rejects_no_corruption():
    """Mock fsync to raise OSError(ENOSPC). Verify rejection + journal log;
    verify target file unchanged."""
```

## 9. Migration script (CR-6)

`scripts/migrate_claims_to_owner_txt.py`:

```python
"""V9 M1 — backfill owner.txt for pre-v9 claim directories.

Walks claims/*/ directories. For each that lacks owner.txt:
  1. Attempt to infer owner from STATUS.md or HISTORY.md history.
  2. Fall back to `legacy-pre-v9 <unknown> <pre-cutover-utc>` if unknown.
  3. Write owner.txt atomically.

Idempotent: skip claims that already have owner.txt.

Usage:
    python3 scripts/migrate_claims_to_owner_txt.py --mission-dir PATH [--dry-run]
"""
```

CLI options:
- `--mission-dir PATH` (required)
- `--dry-run` (print what would be written, don't write)
- `--default-owner STR` (override the fallback string)

## 10. Operator runbook (README update)

```markdown
## V9 startup sequence

1. **Start the applier** (background process, one per mission):
   ```bash
   ./scripts/start_applier.sh /path/to/mission &
   ```

2. **Start the UI server**:
   ```bash
   uv run --with fastapi --with "uvicorn[standard]" --with sse-starlette --with pyyaml \
       python -m megalodon_ui --mission-dir /path/to/mission --port 8080
   ```

3. **Verify applier healthy**:
   ```bash
   cat /path/to/mission/queue/.applier.lock/heartbeat.txt
   # Should be a UTC stamp within the last 5 seconds.
   ```

4. **(One-time, v8→v9 cutover only)** migrate legacy claims:
   ```bash
   python3 scripts/migrate_claims_to_owner_txt.py --mission-dir /path/to/mission
   ```

5. **Workers** (per launch.md §5 RULES 12-14): all shared-state writes go through `scripts/atomic_close.py` or `python -m megalodon_ui.queue.queue_client`.
```

## 11. Definition of done

- [ ] `megalodon_ui/queue/applier.py` + `queue_client.py` + `schemas.py` + `journal.py` + `__init__.py` shipped.
- [ ] S-8 fixes B1 (UTC), B2 (WAL), B3 (heartbeat) applied.
- [ ] Q1 additions: STATUS_ROW_INSERT, TASKS_INJECT, MISSION_EVENT_CORRECTION implemented + tested.
- [ ] All 6 original intents + 3 new intents have payload schema validation (Pydantic).
- [ ] T1-T4 tests pass.
- [ ] `scripts/migrate_claims_to_owner_txt.py` with 6 unit tests + idempotency test.
- [ ] `scripts/start_applier.sh` operator launcher.
- [ ] M1.5: 3 megalodon_ui/server.py endpoints converted to 202-async via queue_client.
- [ ] M1.5: new `GET /api/v1/queue/{request_id}` introspection endpoint.
- [ ] M1.6: `ui/server.py` reduced to ~30-line shim wrapping `make_app()`.
- [ ] `scripts/_backends/queue_client.py` adapter — same interface as direct_fcntl, routes through queue.
- [ ] `scripts/_shared_state.py` swapped one line: `from ._backends import queue_client as _backend`.
- [ ] **M3 regression**: 92 existing M3 tests still pass via queue backend (test_shared_state_via_queue.py wraps them).
- [ ] M2 regression: 14 M2 tests still pass.
- [ ] M4 regression: 10 M4 tests still pass.
- [ ] All 54+ new M1 tests pass (54 = 20+15+8+6+5).
- [ ] End-to-end smoke: start applier + start factory + curl reclaim → applies via queue + introspect shows applied.
- [ ] launch.md RULES 12-14 updated (or new rule 15 for applier startup).
- [ ] README.md v9 startup sequence section added.
- [ ] HISTORY.md M1-COMPLETE entry.

## 12. Implementation order (TDD)

This is the largest milestone — break into phases:

### Phase A — Queue core (M1)
1. Create `megalodon_ui/queue/__init__.py`, `journal.py` (WAL impl + tests).
2. Promote `applier.py` from `docs/v9/queue/`; apply B1+B2+B3+B4 fixes.
3. Promote `queue_client.py`; apply B1 fix.
4. Add `schemas.py` with Pydantic models for all 9 intents.
5. Add Q1 new intents (STATUS_ROW_INSERT, TASKS_INJECT, MISSION_EVENT_CORRECTION).
6. Write `test_queue_applier.py` (T1-T4 + core).
7. Write `test_queue_client.py` (B1 regression + helpers).
8. Write `test_queue_journal.py` (WAL + crash recovery).
9. Write `scripts/start_applier.sh`.

### Phase B — Migration script (CR-6)
10. Write `scripts/migrate_claims_to_owner_txt.py` + tests.

### Phase C — M3 backend swap
11. Create `scripts/_backends/queue_client.py` adapter.
12. Write `test_shared_state_via_queue.py` (verifies M3 tests pass via queue).
13. Swap line in `scripts/_shared_state.py`.
14. Run full M3 test suite — confirm 92 still pass.

### Phase D — M1.5 (UI mutation migration)
15. Migrate 3 endpoints in `megalodon_ui/server.py` to 202-async via queue_client.
16. Add `GET /api/v1/queue/{request_id}` introspection endpoint.
17. Update `docs/v9/api-contract.md` with the 3 changed endpoints (now return 202) + new endpoint.
18. Run M2 contract scan; confirm pass.

### Phase E — M1.6 (backend unification)
19. Replace `ui/server.py` with thin shim (~30 lines).
20. Run all integration + e2e tests; confirm pass via shim.
21. Update launch.md + README.md.
22. Run end-to-end smoke (applier + factory + reclaim via FE).

### Phase F — Final
23. Append HISTORY.md M1-COMPLETE entry.
24. Confirm full pytest suite passes (target: 102 + 54 = ~156 tests).

## 13. Risks

| Risk | Mitigation |
|------|------------|
| Applier crashes mid-drain, corrupts target file | WAL pattern (B2 fix) — journal-before-apply + reconcile on restart. T4 test covers. |
| Queue grows unbounded if applier dies | Heartbeat (B3) → workers detect within 30s → switch to BLOCKED-APPLIER-DOWN state. Operator restart resumes. |
| M3 test regression on backend swap | Phase C explicitly runs M3 tests against queue backend BEFORE any other phase depends on it. |
| Legacy ui/server.py shim breaks operator habit | Shim accepts same args + env vars as old file. Smoke includes `python ui/server.py` invocation. |
| 202-async breaks FE expecting 200 | FE adaptation in §5.4: `postAction` already returns 200/202 transparently. Smoke confirms. |
| Q1 new intents introduce schema bugs | Pydantic schemas enforce + 8 tests cover insertion, ordering, idempotency. |
| Migration script attributes claims wrong | --dry-run + manual operator review before live run during cutover. Idempotent re-run if mistakes found. |
| Singleton lock TOCTOU race | Documented in S-8 §B B3 note as low-probability. Acceptable for v9.0. |

## 14. Out-of-scope (per V9-ROADMAP M1)

- Multi-applier with per-file partitioning (deferred to v9.x per S-8 §A Q2)
- Read serialization beyond LOCK_SH (V9-ROADMAP Option A locked)
- DELETE-class intents (STATUS_ROW_RESET, TASKS_TASK_REMOVE, CLAIM_DIR_ABANDON) — v9.1 per S-8 G3
- Per-rejection notifications to submitter (S-8 §A Q3 ACCEPT-WITH-MODIFICATION) — v9.1
- Schema version migration story (S-8 §B B5) — v9.1
- Cross-mission queue isolation enforcement (S-8 §C G4) — process discipline note in launch.md
- Pre/post-state hashes in journal (S-8 §A Q10) — v9.1

## 15. Document control

- Author: orchestrator (Claude)
- Date: 2026-05-16T22:35Z
- Status: APPROVED-FOR-PLAN (delegated brainstorming per operator 2026-05-16T21:12Z)
- Predecessors: V9-ROADMAP §M1+M1.5+M1.6, QUEUE-DESIGN.md, S-8 audit findings, Codex CR-1/CR-2/CR-6
- Successor: `docs/superpowers/plans/2026-05-16-v9-m1-queue-trio.md`
