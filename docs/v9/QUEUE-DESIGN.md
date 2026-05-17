---
status: DRAFT (orchestrator-authored, awaiting worker audit)
author: orchestrator-Claude (run-2 of 2026-05-16T17-30Z mission)
date: 2026-05-16T18:43Z
target-protocol-version: v9
parent-signal: SIG-ORCH-1 (orchestrator-SIGNAL-001-queue-required-v9.md)
audit-task: S-8 (TASKS.md)
---

# v9 Write-Queue Design

## 1. Problem statement

Under v8 CAS (Edit 4-bis), shared mutable files (`STATUS.md`, `TASKS.md`, `HISTORY.md`, `.mission-events`) experience high write contention when multiple workers tick on aligned `/loop 3m` boundaries. Empirical measurement (run-2 META):

- **CAS retry rate: ~79%** (11/14 writes required retry under load)
- **Observed during contention**: up to 4 lanes simultaneously edit STATUS.md
- **Operator-visible symptom**: repeated "File has been modified since read" Edit failures; manifests as agents "fighting"
- **Throughput cost**: each retry is a re-read + re-hash + re-edit cycle, plus operator attention if it goes interactive

CAS is optimistic concurrency; under sustained contention it degrades. A pessimistic mechanism is required.

## 2. Requirements (from SIG-ORCH-1 + run-2 empirical)

Operator-stated, non-negotiable:

1. **Serializes writes** to STATUS.md, TASKS.md, HISTORY.md, `.mission-events`. Workers do not edit these files directly.
2. **Workers write request files** (e.g., `queue/<utc>-<agent>-<file>-<intent>.json`).
3. **A single applier drains the queue** in strict timestamp order.
4. **Crash-recovery semantics**: in-flight queue items must be re-applied or marked rejected on restart.
5. **Free audit trail**: applied items archive into `queue/applied/`.
6. **Backwards-compatible read path**: workers READ shared files directly (no read serialization).

Run-2 empirical additions:

7. **Owner-id contract for every write request** (operator-injected and worker-injected). Required to prevent the anonymous-mkdir / silent-removal cascade observed in `claims/P2.5-C/` (v8.1-OBS-6).
8. **Idempotency**: same request applied twice is a no-op, not a duplicate (handles applier-restart between apply and archive).
9. **Per-intent schema validation**: applier rejects malformed requests rather than corrupting shared files.

## 3. Architecture

```
                   ┌──────────────────────────┐
                   │  Worker tick (any lane)   │
                   │                           │
                   │  1. Read STATUS.md        │  (read path unchanged)
                   │  2. Decide intent         │
                   │  3. Write request to      │
                   │     queue/pending/        │  ← single atomic file write
                   │                           │
                   │  Continue tick.           │
                   └────────────┬──────────────┘
                                │
                                ▼
                   ┌──────────────────────────┐
                   │  queue/pending/           │
                   │   <utc>-<agent>-          │
                   │   <file>-<intent>.json    │
                   └────────────┬──────────────┘
                                │
                  drain loop ▼  every 1-3s
                                │
                   ┌──────────────────────────┐
                   │  Applier (singleton)      │
                   │                           │
                   │  1. ls queue/pending/     │
                   │  2. Sort by timestamp     │
                   │  3. For each request:     │
                   │     a. Validate schema    │
                   │     b. Apply to target    │
                   │        file (atomic)      │
                   │     c. Move to applied/   │
                   │     d. Log to journal     │
                   └────────────┬──────────────┘
                                │
                                ▼
                   ┌──────────────────────────┐
                   │  queue/applied/           │  ← audit trail
                   │   <utc>-<agent>-          │
                   │   <file>-<intent>.json    │
                   └──────────────────────────┘

                   ┌──────────────────────────┐
                   │  queue/rejected/          │  ← schema or validation failures
                   │   <utc>-<agent>-          │
                   │   <file>-<intent>.json    │
                   │   + reason.txt            │
                   └──────────────────────────┘
```

## 4. Request schema

Each request is a JSON file with the following envelope. All fields required.

```json
{
  "schema_version": 1,
  "request_id": "<utc>-<agent>-<file>-<intent>-<random4>",
  "submitted_utc": "2026-05-16T18:43:00Z",
  "agent": "agent-84f2",
  "lane": "C",
  "target_file": "STATUS.md",
  "intent": "STATUS_UPDATE",
  "preconditions": {
    "required_phase": "PHASE-CHALLENGE",
    "row_must_show_state": "working: P2.5-C"
  },
  "payload": { /* intent-specific, see §5 */ },
  "idempotency_key": "<sha256-of-payload>",
  "expected_hash_before": "<sha256-of-target-file-at-read-time>",
  "fallback": "REJECT" | "APPLY_ANYWAY"
}
```

### Field rationale

- **`request_id`**: globally unique; allows idempotent re-apply on applier restart.
- **`submitted_utc`**: applier orders by this; collisions break by `request_id` lex.
- **`agent` + `lane`**: owner-id contract (SIG-ORCH-1 req 7). Solves anonymous-mkdir problem.
- **`preconditions`**: applier checks these before applying. If false, applier moves to `rejected/` with reason. Solves the "lost update" problem (e.g., worker writes a STATUS update based on state that has since changed).
- **`expected_hash_before`**: optional optimistic-concurrency check. If set, applier verifies hash matches before applying; if not, moves to `rejected/` (or applies anyway if `fallback: APPLY_ANYWAY`).
- **`idempotency_key`**: applier hashes the payload; if same key already applied, skip.

## 5. Intent types

### 5.1 `STATUS_UPDATE`

```json
{
  "intent": "STATUS_UPDATE",
  "payload": {
    "lane": "C",
    "new_state": "idle",
    "new_utc": "2026-05-16T18:43:00Z",
    "new_notes": "P2.5-C done — full plan-v2 ..."
  }
}
```

Applier behavior: find the row matching `lane` and rewrite the State/Last UTC/Notes columns. Other rows untouched.

### 5.2 `TASKS_BRACKET`

```json
{
  "intent": "TASKS_BRACKET",
  "payload": {
    "task_id": "P2.5-C",
    "new_bracket": "[done: agent-84f2 @ 2026-05-16T18:43:00Z]"
  }
}
```

Applier behavior: find the line matching `[ ] [LANE-C] \`P2.5-C\`` (or `[claimed: ...]`) and replace the bracket prefix.

### 5.3 `HISTORY_APPEND`

```json
{
  "intent": "HISTORY_APPEND",
  "payload": {
    "line": "2026-05-16T18:43:00Z | agent-84f2 | C | P2.5-C | findings/<file>.md | MAJOR"
  }
}
```

Applier behavior: append `line` + `\n` to HISTORY.md. Schema validates line format (timestamp | agent | lane | task | file | severity).

### 5.4 `MISSION_EVENT_APPEND`

```json
{
  "intent": "MISSION_EVENT_APPEND",
  "payload": {
    "line": "2026-05-16T18:43:00Z PHASE-CHALLENGE->PHASE-BUILD by agent-fec0 -- ..."
  }
}
```

Applier behavior: append `line` + `\n` to `.mission-events`.

### 5.5 `CLAIM_DIR_CREATE` (NEW v9, solves OBS-6)

```json
{
  "intent": "CLAIM_DIR_CREATE",
  "payload": {
    "task_id": "P2.5-C",
    "owner_agent": "agent-84f2",
    "owner_lane": "C"
  }
}
```

Applier behavior: `mkdir claims/<task_id>` AND write `claims/<task_id>/owner.txt` with `<agent> <utc>`. **All claim mkdirs MUST go through this intent in v9 — direct `mkdir claims/<id>` is forbidden.** Solves anonymous-mkdir / silent-removal cascade.

### 5.6 `CLAIM_DIR_DONE` (NEW v9)

```json
{
  "intent": "CLAIM_DIR_DONE",
  "payload": {
    "task_id": "P2.5-C",
    "agent": "agent-84f2"
  }
}
```

Applier behavior: validates `claims/<task_id>/owner.txt` matches `agent`, then `touch claims/<task_id>/done`. **Only the owner can mark done.**

## 6. Applier specification

### 6.1 Singleton requirement

Only ONE applier process may run at a time. Acquired via filesystem lock: `queue/.applier.lock` (mkdir-atomic). Lock holder writes its PID + start UTC inside.

### 6.2 Drain loop

```python
while True:
    pending = sorted(glob("queue/pending/*.json"), key=lambda p: read_field(p, "submitted_utc"))
    for req_path in pending:
        req = load_json(req_path)

        # Idempotency
        if applied_already(req["request_id"]):
            move(req_path, f"queue/applied/{req['request_id']}.json")
            continue

        # Schema validation
        if not validate_schema(req):
            move(req_path, f"queue/rejected/{req['request_id']}.json")
            write(f"queue/rejected/{req['request_id']}-reason.txt", "schema invalid")
            continue

        # Preconditions
        if not check_preconditions(req):
            move(req_path, f"queue/rejected/{req['request_id']}.json")
            write(f"queue/rejected/{req['request_id']}-reason.txt", "precondition failed")
            continue

        # Hash check (optional)
        if "expected_hash_before" in req and req["fallback"] == "REJECT":
            if hash_file(req["target_file"]) != req["expected_hash_before"]:
                move(req_path, f"queue/rejected/{req['request_id']}.json")
                continue

        # Apply atomically (tmpfile + rename)
        apply_intent(req)
        move(req_path, f"queue/applied/{req['request_id']}.json")

    sleep(POLL_INTERVAL_SECONDS)  # 1-3s
```

### 6.3 Atomic apply (per file)

Each target file gets a per-file `flock` (or `fcntl.lockf` on Linux, `fnctl` on Darwin) during apply. Read, modify in memory, write to tmpfile, fsync, rename. Guarantees no torn writes.

### 6.4 Crash recovery

On applier startup:
1. Check `queue/.applier.lock` — if exists, read PID. If PID is alive, exit (singleton enforcement). If stale, take over.
2. Read `queue/journal.log` to determine last-applied request_id.
3. Resume from there.

The journal is append-only, written before each apply, ensuring no-double-apply on crash mid-apply.

### 6.5 Applier ownership

Three options for who runs the applier:

| Option | Pros | Cons |
|---|---|---|
| **Orchestrator-Claude** | Already polling; reuses existing process | Coupling: applier dies if orchestrator session dies |
| **Dedicated tiny daemon** | Process isolation; survives orchestrator restarts | New process to manage |
| **Lane-elected** (worker takes role + heartbeat) | No new process; distributed-ish | Election protocol complexity; if winner dies, mission stalls |

**Recommendation: Dedicated tiny daemon.** `~/Documents/Projects/megalodon/queue/applier.py`, started by operator at mission boot. ~200 LOC. Lives as long as the mission directory has `.mission-events` without a `COMPLETE` line.

## 7. Migration path (v8 → v9)

Workers stop calling `Edit STATUS.md` directly. Instead they call a tiny helper:

```python
# megalodon_ui/queue_client.py
def submit_status_update(lane, new_state, new_utc, new_notes):
    req = {...}
    write_atomic(f"queue/pending/{req['request_id']}.json", json.dumps(req))
```

The helper does one atomic file write to `queue/pending/`. No CAS needed. Worker proceeds.

CAS pattern (Edit 4-bis) **remains in spec** as a fallback for files not yet queued (e.g., findings/ writes are still direct, since they don't have multi-writer contention). The queue covers only the 4 high-contention shared-mutable files.

## 8. Open questions for worker audit

The audit task S-8 should evaluate:

1. **Q1**: Is the per-intent schema list complete? Are there other write intents we're missing? (Possible: `SCRATCH_NOTE`, `FINDINGS_LINK`, `SIGNAL_DELIVERY`.)
2. **Q2**: Is the singleton-applier the right choice, or should we explore multi-applier with per-file partitioning? (Trade-off: throughput vs complexity.)
3. **Q3**: Should rejections trigger a SIGNAL back to the submitter? Currently they sit in `queue/rejected/` silently.
4. **Q4**: Is the 1-3s drain interval right? Lower = lower latency but more poll waste. Higher = batching efficiency but laggy STATUS.
5. **Q5**: What happens to the queue during PHASE-OPERATOR-ACCEPTANCE? Does applier pause, or continue draining? (Probably continue — workers still heartbeat.)
6. **Q6**: Does this design adequately solve v8.1-OBS-6 (unauthorized claim-dir removal)? Specifically: `CLAIM_DIR_REMOVE` is NOT in the intent list — should it be? If so, who can remove (only owner? operator-injected?)?
7. **Q7**: Crash-recovery: is the journal mechanism sufficient, or do we need WAL?
8. **Q8**: Backwards compatibility: should the design support a v8-fallback mode where workers can still CAS-write if the applier is down? Or hard-fail and force operator intervention?
9. **Q9**: What's the failure mode when the queue itself becomes contended (operator writes manually, multiple intents racing on same target)? Test plan needed.
10. **Q10**: Should the journal store hashes of pre/post-apply state for forensic replay?

## 9. Out of scope for v9.0

These are v9.x or v10 candidates:

- Cross-provider queue (multi-applier across providers)
- Distributed-applier consensus (Raft-style)
- Read serialization (currently unbounded; should be fine)
- Worker-to-worker message routing through queue (just for STATUS / TASKS / HISTORY / events for now)

## 10. Implementation plan (post-run-2)

**Pre-implementation gate (MANDATORY)**: a **contrarian review by Codex (different provider, different training distribution)** must complete before any code lands. This applies to the entire v9 plan, not just the queue. Operator (David) requirement — captured 2026-05-16T19:02Z. Cross-provider review is empirically stronger than intra-Claude convergence (see run-2 META observations on transitive-trust ACK failure, OBS-8). The contrarian review must produce `docs/v9/CONTRARIAN-REVIEW-CODEX.md` with explicit positions (accept / reject / modify) on each major v9 item and any additional gaps surfaced. Worker-audit (S-8, optional, Claude-internal) does NOT substitute for Codex contrarian review.

After Codex review lands and orchestrator addresses blockers:

1. **Implement `queue/applier.py`** (~200 LOC Python, no external deps beyond `json`, `pathlib`, `time`, `hashlib`, `fcntl`).
2. **Implement `megalodon_ui/queue_client.py`** (~50 LOC helper for workers).
3. **Update `launch.md`** to start applier before workers.
4. **Write `ui/tests/integration/test_queue_applier.py`** — schema validation, idempotency, ordering, crash recovery.
5. **Update README.md to v9** with new rule replacing Edit 4-bis CAS for shared-mutable files.
6. **Migration test**: run a synthetic mission with the queue + verify CAS retries drop to 0.

Estimated wall-clock: 2-4 hours focused work (queue only). Plus ~30-60 min Codex contrarian review walltime + Claude-side blocker-address.

## 11. Acknowledgements

This design responds to:

- SIG-ORCH-1 (operator-injected v9 queue requirement)
- v8.1-OBS-6 (unauthorized claim-dir removal; META P3-F draft)
- v8.1-OBS-1 (Edit-14 owner-id; ARCHITECT P2.5-B addendum)
- META P3-F CAS retry baseline (~79% under load)
- AUDIT P3-A v8.1-candidate.md item 1 (Edit-14 BLOCKING)

---

# Audit instructions

A worker (likely AUDIT or META, when idle) should claim CROSS task `S-8` and evaluate this design against the 10 open questions in §8. Output: `findings/<agent>-CROSS-S8-queue-design-audit-<UTC>.md` with explicit answers to each Q + any additional gaps surfaced. RULE-10 atomic completion on `S-8`.

If audit surfaces blocking issues, orchestrator authors v2 of this doc post-run-2.
