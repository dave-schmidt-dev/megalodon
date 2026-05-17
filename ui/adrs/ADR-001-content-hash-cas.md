# ADR-001 — Content-hash CAS over `fcntl.flock` for cross-process state mutation

- **Status:** Accepted
- **UTC:** 2026-05-16T15:53Z
- **Authored by:** agent-aa79 (ARCHITECT, P3-B)
- **Replaces:** P1-B §6.1 (mitigation candidate "a" — flock)
- **Cited by:** SPEC §7, BACKEND §Δ5

## Context

The orchestrator-console UI mutates `STATUS.md`, `TASKS.md`, README.md "Mission status" section, and `.mission-events` in response to operator actions (post SIGNAL, reclaim stale row, flip phase, set mission status, inject task). Simultaneously, **6 worker sessions** mutate the same files via the Claude harness's `Edit` tool every 3-minute tick — heartbeats, claim/done bracket flips, history appends.

The UI and the workers run in **different processes** (UI is a `uvicorn` process; each worker is its own Claude session). They share no in-process state and have no IPC primitive coordinating them.

Empirical evidence (this run): orchestrator hit ≥2 "file modified since read" errors during PHASE-PLAN attempting to write SIGNAL notes (HISTORY.md @ 15:38Z SIG-ORCH#2). Workers presumably hit similar races silently.

## Considered options

1. **`fcntl.flock` advisory locks** — UI acquires `flock(LOCK_EX)` before read-modify-write.
2. **Content-hash compare-and-swap (CAS)** — UI reads, hashes, writes via atomic rename, then re-reads and verifies the hash matches what it wrote; retry on mismatch.
3. **Append-only event log + materialized views** — Replace in-place mutation with append-only logs; STATUS.md and TASKS.md become read-only derived views.
4. **mkdir-based write locks** (`.file-locks/<file>.lock`) — UI cooperates with a file-level lock convention.

## Decision

**Option 2 — Content-hash CAS.**

```python
def cas_write(path: Path, mutate_fn, *, max_attempts: int = 3) -> None:
    for attempt in range(max_attempts):
        original = path.read_bytes()
        new_content = mutate_fn(original)
        h_intended = hashlib.sha256(new_content).hexdigest()
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(new_content)
        os.replace(tmp, path)  # POSIX-atomic
        after = path.read_bytes()
        if hashlib.sha256(after).hexdigest() == h_intended:
            return  # success
        # someone else wrote between our rename and verify — retry
        time.sleep(0.05 * (attempt + 1))
    raise CasContentionError(path)
```

## Why not Option 1 (flock)

**Decisive:** `fcntl.flock` is **advisory** on POSIX. Workers don't call `flock` — they use the Claude harness's `Edit` tool, which is a high-level RMW operation that does not wrap in flock. An advisory lock acquired only by the UI has zero effect on worker writes.

This is BACKEND's C1 finding (P2-C→B `findings/agent-8318-C-P2-challenge-of-architect-2026-05-16T15-38Z.md:19-37`). The plan-v1 mitigation was wrong; CAS is the correct primitive.

## Why not Option 3 (append-only logs)

Conceptually clean (matches SIG-ORCH#2 candidate 2 and Raft-style logs). But would require **changing the v7 protocol** — workers would need to write to per-lane log files instead of mutating STATUS.md rows. That's AUDIT's `docs/v8-changeset.md` territory, not the UI's. Adopt CAS now; consider Option 3 for v8.

## Why not Option 4 (mkdir write-locks)

Workers would also need to cooperate (mkdir lock, then write, then rmdir). Same protocol-change requirement as Option 3. CAS needs no worker cooperation.

## Consequences

**Positive:**
- Works for the existing v7 protocol with no worker-side changes.
- Application-level OCC; semantically equivalent to what `mkdir` does for the claims dimension.
- Bounded retry (3 attempts × ~150ms jittered) — operator-visible latency under contention is ≤500ms.
- Read after rename catches both: same-process concurrent writers and other-process writers.

**Negative:**
- Two reads per write (initial + verify). At 6 workers × 1 STATUS write per 3 min, contention is rare; the extra read cost is negligible.
- `CasContentionError` is unrecoverable from the operator's perspective — they retry the action. Mitigation: UI surfaces the error with a "Try again" button rather than a hard failure.
- No fairness guarantee — under sustained contention, an unlucky retry chain could exhaust attempts. Empirically unlikely at 6 workers + 1 operator.

**Mitigation for sustained contention (if observed in PHASE-VERIFY):**
- Append-only operations on STATUS.md notes column (orchestrator never touches worker rows) reduce contention surface. Already SPEC'd in §A of P2.5-B.
- If `CasContentionError` rate exceeds 1% of actions during PHASE-VERIFY, AUDIT should escalate to v8 Option-3 (append-only logs).

## References

- BACKEND P2-C→B C1: `findings/agent-8318-C-P2-challenge-of-architect-2026-05-16T15-38Z.md:19-37`
- ARCHITECT P2.5-B §A: `findings/agent-aa79-B-P2.5-arch-plan-v2-2026-05-16T15-46Z.md`
- HISTORY @ 15:38Z SIG-ORCH#2: file-collision concurrency-architecture v8-candidate
