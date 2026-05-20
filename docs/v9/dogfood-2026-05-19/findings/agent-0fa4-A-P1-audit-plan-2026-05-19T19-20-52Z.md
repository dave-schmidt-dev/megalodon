# P1-A — AUDIT Plan (v9.2 codebase scan)

- **Lane:** LANE-A (AUDIT)
- **Agent:** `agent-0fa4`
- **Task:** `P1-A`
- **Phase:** PHASE 1 — PLAN
- **UTC:** 2026-05-19T19-20-52Z

## Summary

Static scan of `megalodon_ui/`, `ui/static/`, `scripts/tests/` at the v9.2-shipped state on branch `fleet/dogfood-2026-05-19`. Eleven candidate findings categorized as Protocol / Race / Security / Dead. Three elevated as top-risk for PHASE 2 BUILD.

The headline: v9.2's CSRF defense is half-built (token issued, never validated); the CV-9 stream-reader deferral leaves the backend tailing files with a dead `subscribers_lock` waiting for SR-3; and two state-mutating POST routes still write `TASKS.md`/`STATUS.md` inline without the queue/lock applied elsewhere in v9.2.

## Evidence — full candidate list

### Protocol violations

- `megalodon_ui/spawn.py:210` — TODO(P3.1) `pipe_pane()` call deferred; CV-9 stream-reader unimplemented; backend tails files instead of owning the byte stream (blocks CV-9 acceptance).
- `megalodon_ui/server.py:129` — `/api/v1/status` and `/api/v1/tasks` route decorators remain string literals, not in the canonical constants list (HISTORY.md:128 notes out-of-scope per spec D5).
- `megalodon_ui/mission_config/__init__.py:10`, `primitives.py:16` — v9.0 back-compat shape loader always synthesizes default config from legacy shape; no migration path documented for canonical `.mission-config.yaml` bootstrap.

### Race conditions

- `megalodon_ui/spawn.py:200-208` — Non-atomic session creation: `new-session` → `set-option remain-on-exit` → `set-environment MEGALODON_FLEET_OWNED=1`; concurrent `FleetSpawner.start_all` on the same socket could observe a session before the marker lands. Orphan-check treats unmarked sessions as unowned (conservative but undocumented).
- `megalodon_ui/server.py:522-535` — `POST /api/tasks` writes directly to `TASKS.md` without `fcntl` lock; race between read-modify-write and the concurrent applier queue (this route bypasses the queue used by signal/reclaim).
- `megalodon_ui/server.py:565-597` — `POST /api/lanes/{lane}/signal` modifies `STATUS.md` inline without a lock; appends SIG token via string search-replace; CAS-naive per its own comment.

### Security gaps

- `megalodon_ui/server.py` — **All POST routes lack CSRF validation**: `/api/tasks`, `/api/lanes/{lane}/reclaim`, `/api/lanes/{lane}/signal`, `/api/mission/flip`, `/api/v1/signal`, `/api/v1/reclaim`, `/api/v1/inject-task`. A `csrf_token` is generated and exposed to the FE, but never checked on inbound requests.
- `megalodon_ui/server.py:697` — Path traversal guard rejects only `/`, `\`, `..` literals; symlink attacks via `claims/` enumeration unaddressed.
- `megalodon_ui/server.py:434-442` and `:1001` — No CORS middleware enforced (`allowed_origins` computed at `make_app()` but never wired to a middleware). SPA fallback serves `index.html` for any path, opening HTML reflection via `spa_path` parameter.

### Dead code

- `megalodon_ui/legacy_history.py` — Entire module marked "SUNSET: when .archive/* HISTORY.md formats migrated"; parses four v9.0 format variants for read-only back-compat; no active callers in v9.2.
- `megalodon_ui/spawn.py:39-43` — `subscribers_lock: asyncio.Lock` allocated in `LaneSession` as "forward-hook for v9.2 P4 SSE fan-out (SR-3); not yet acquired in P1". Lock exists but is never acquired anywhere.
- `ui/static/pages/mission.js:33` — Comment references P2.5-D plan-v2 phase-gate (lane-drain + META capstone + HISTORY quiet >10min). Logic predicate may be superseded by current phase-flip control.

## Top-3 elevated to PHASE 2 BUILD

### 1. CSRF validation absent on every state-mutating POST route

**Files:** `megalodon_ui/server.py` (every `@app.post` decorator).
**Why it matters:** Any cross-origin page can forge requests that inject tasks, flip phases, send signals between lanes, or reclaim work. The token is *issued* and *exposed* to the FE, so external readers (including operators auditing the app) reasonably assume protection exists. It does not. An unattended dashboard tab is a CSRF surface for the entire mission control plane.
**P2 work:** Add an `X-CSRF-Token` header check (or double-submit cookie pattern) in a FastAPI dependency, wire it to every POST route, fail closed on missing/mismatched. Add regression test posting to `/api/v1/inject-task` without the token and asserting 403.

### 2. CV-9 stream-reader deferred; backend tails files instead of owning streams

**Files:** `megalodon_ui/spawn.py:210-212` (TODO marker), `megalodon_ui/spawn.py:39-43` (dead `subscribers_lock`).
**Why it matters:** The file-tail interim architecture suffers backpressure, loses bytes on quick lane exits, and cannot deliver per-pane output atomically to multiple SSE subscribers. The dead `subscribers_lock` is a half-built P4 marker that confuses readers about whether SR-3 is partially live.
**P2 work:** This is LANE-C's `P2-C`; AUDIT should track that the design eliminates the file-tail path entirely rather than keeping both code paths (a common half-migration trap). Verify the `subscribers_lock` becomes live (acquired) or is removed.

### 3. Race-prone direct file mutation on TASKS.md and STATUS.md

**Files:** `megalodon_ui/server.py:522-535` (`POST /api/tasks`), `megalodon_ui/server.py:565-597` (`POST /api/lanes/{lane}/signal`).
**Why it matters:** Both routes bypass the v9.2 queue/applier system used elsewhere. Two concurrent operator clicks (or one click overlapping an /loop agent's claim flow) can interleave read-modify-write on the same markdown table. The CAS-naive `STATUS.md` string-replace has undefined behavior when multiple SIG tokens arrive in the same tick.
**P2 work:** Move these two routes onto the existing applier queue OR wrap their I/O in `fcntl.flock` like the rest of v9.2. Add a regression test: 10 parallel `POST /api/tasks` requests should all land in `TASKS.md` with no lost rows.

## Recommendations

1. Elevate top-3 to PHASE 2 BUILD as `P2-A` deliverables, one finding per ADR-style document under `findings/`.
2. Coordinate with LANE-B (ARCHITECT) on the CSRF design — header check vs double-submit cookie is a v9.3 protocol decision, not a unilateral AUDIT pick.
3. Coordinate with LANE-C (BACKEND) on `P2-C` to confirm the stream-reader retires the file-tail path rather than coexisting with it.
4. Defer `legacy_history.py` removal until the `.archive/*` migration is scheduled — premature deletion would break the read-only back-compat contract.

## Next steps (for me)

- After PHASE-FLIP to BUILD, claim `P2-A` and produce three finding docs (one per top-risk above) with concrete file:line patches or design notes.
- Until then: pick up secondary tasks from the CROSS-LANE pool if any open under LANE-A criteria, otherwise idle-tick.
