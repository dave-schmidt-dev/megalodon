# ADR-004 — Filesystem as the single source of truth (no database)

- **Status:** Accepted
- **UTC:** 2026-05-16T15:54Z
- **Authored by:** agent-aa79 (ARCHITECT, P3-B)
- **Codifies:** MISSION.md §"Out of scope: DB persistence (filesystem is source of truth; UI is a renderer/controller)"

## Context

The v7 protocol places all mission state on the filesystem:

- `STATUS.md`, `TASKS.md`, `HISTORY.md`, `MISSION.md`, `README.md` — markdown files.
- `.mission-events` — append-only log.
- `claims/<task-id>/` — directories; presence is a lock; `done` file is a completion marker.
- `findings/*.md` — worker outputs with YAML frontmatter.
- `.phase-flip-locks/<from>-to-<to>` — phase-flip mkdir locks.

The orchestrator-console UI could mirror this state into a database (SQLite/DuckDB) for query convenience. MISSION.md §"Out of scope: DB persistence" explicitly forbids this. This ADR records *why* and what the implications are.

## Decision

**No persistent database.** All UI state is derived per-request from the filesystem; the server holds zero authoritative state across restarts. In-memory caches are mtime-keyed and disposable.

```
Request → read relevant file(s) → parse → respond
SSE   → file-watch event → parse → emit
Action → CAS write to file → respond
```

If you `rm -rf` the UI process state, nothing is lost. If you `git stash` the project, the UI returns to a consistent prior state.

## Why

1. **The protocol already has authoritative state.** Adding a DB introduces a second source of truth and a synchronization problem. v7 workers don't know about the DB; the DB would be a UI artifact that could drift from the canonical files.
2. **`git diff` is the audit trail.** A consultant should be able to inspect the mission's progress via `git log` after the fact. Markdown files + plain directories are the natural medium.
3. **Restart simplicity.** The operator can `Ctrl-C` the UI and restart with no data migration. State persists in the project directory.
4. **No schema evolution.** No `alembic`, no migrations, no "v3 mission with v4 UI" conflicts.
5. **Workers can't write to a DB.** Workers run as Claude sessions and mutate via the `Edit` tool. They can't open a database connection. A DB would only contain UI-written state, doubling the divergence surface.

## What this requires

- **Parse on every read.** Markdown parsing of STATUS.md, TASKS.md must be fast enough at request time. Empirically: STATUS.md is ~25 lines, TASKS.md is ~100 lines, HISTORY.md grows to ~50 lines per mission. Sub-millisecond parses.
- **Mtime-based caching.** The server can cache parsed structures keyed by `(path, mtime, size)`. Invalidation is automatic when the file changes.
- **Atomic-rename writes.** Action endpoints write via `os.replace` (CAS — ADR-001) to keep readers consistent.
- **File-watch for push.** `watchfiles` + 2s poll backstop (ADR-002).

## What we give up

- **Complex queries.** "Findings by lane × severity, joined with HISTORY entries authored by the same agent" is harder against markdown than against SQL. Mitigation: in v1 the UI's queries are flat (filter by lane, by severity, by phase) — no join needed. If we ever need joins, we can build a query layer in Python without persisting to disk.
- **Concurrent-write coordination.** A DB has transactions; the filesystem has `mkdir` (atomic) and `os.replace` (atomic). For the operations we perform (single-file mutation + append), this is sufficient. CAS (ADR-001) handles cross-process safety.
- **Pagination / indexing.** A long-running mission with 1000 findings would be slow to filter by hand-scanning all files. Mitigation: at MVP scale (≤50 findings per mission), brute-force is fast. v2 could add an in-process inverted index if needed.

## What this enables

- **Trivial fixture missions for testing.** TEST P3-E creates `ui/tests/fixtures/<small|medium|large>/` directories. The server reads them like any real mission. No fixture loader, no factory pattern.
- **`docs/v8-changeset.md` reflects the canonical files.** AUDIT's changeset is reviewed against the same files the UI reads — no DB schema to translate.
- **Archive-friendly.** End-of-run archive (per README §"End-of-run process") copies the project directory. UI state archives for free.

## Boundaries

- **Logs to disk (write-only).** The UI emits structured logs to `/tmp/megalodon-ui.log` (rotating, 1MB × 2 backups). This is observability, not state.
- **Process-local SSE queues.** Per-client SSE event queues are in-memory only (ADR-002). They are not state — they are transient buffers that get refilled from filesystem on reconnect via `lagging` event + `resync_urls`.
- **`/tmp/megalodon-ui-csrf-token` (optional).** Could persist the CSRF token across restarts to avoid invalidating open browser tabs. Decision: **no**, regenerate on each start; CSRF rotation per restart is a feature.

## Consequences

**Positive:**
- Aligns with v7 protocol invariant; no synchronization problem.
- Zero migration risk.
- `git log` is the audit trail.
- Easy to debug: `cat STATUS.md` shows what the server sees.

**Negative:**
- No cross-mission queries (out of scope for v1 anyway).
- O(n) scans where O(log n) DB queries would suffice. Acceptable at MVP scale.
- Locking semantics are protocol-level, not DB-level. We don't have ACID; we have CAS + atomic-rename.

## References

- MISSION.md §"Out of scope: DB persistence"
- ADR-001 (CAS) — the concurrency primitive that replaces DB transactions for our cases.
- ARCHITECT P1-B §1 (data model derived from filesystem)
