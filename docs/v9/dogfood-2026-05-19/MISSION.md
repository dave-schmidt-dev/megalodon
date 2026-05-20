# Mission — v9.3 Dogfood (live REPL + /loop autonomous)

**Mission ID:** `2026-05-19T15-30Z--v9-3-dogfood`
**Started:** 2026-05-19 (replace with PHASE-FLIP event timestamp)
**Status:** ACTIVE
**Protocol version:** v9.2 (as shipped)
**Run shape:** All 6 lanes run `claude` interactive REPL inside tmux, each bootstrapped with `/loop` autonomous iteration.

---

## Why this mission exists

v9.2 shipped on 2026-05-18 with a known deferral set (4 Playwright specs, 4 v9.0 e2e fixes, Task 1.6 CV-9 stream-reader). This run is the first **live, recursive** dogfood of v9.2: the fleet works on Megalodon's own codebase, using its own follow-up-prompt + tmux-spawn + SSE-dashboard infrastructure. It also exercises a freshly-added `live_repl` adapter path (claude.py) and per-lane `initial_prompt` injection (spawn.py).

## Concrete exit criteria

A lane MUST NOT mark a task `[done: ...]` until ALL of the following hold:

1. **Findings doc exists** at `findings/<agent-id>-<LANE>-<phase>-<topic>-<UTC>.md`, with a brief summary, evidence (test output, file refs), and concrete next-step recommendations.
2. **Tests still pass** for any code change: `uv run --with pytest --with fastapi --with 'uvicorn[standard]' --with sse-starlette --with pyyaml --with pytest-asyncio --with pydantic --with starlette --with httpx --with freezegun --with anyio pytest scripts/tests/ ui/tests/unit ui/tests/integration -m "not isolated"` returns 0 FAILED.
3. **Claim is released** via `rm -rf claims/<task-id>/` after the task is fully done.

## Lanes

| Lane | Role | Harness | Model | Cadence |
|---|---|---|---|---|
| LANE-A | AUDIT | claude REPL | claude-opus-4-7 | self-paced via /loop |
| LANE-B | ARCHITECT | claude REPL | claude-opus-4-7 | self-paced via /loop |
| LANE-C | BACKEND | claude REPL | claude-sonnet-4-6 | self-paced via /loop |
| LANE-D | FRONTEND | claude REPL | claude-sonnet-4-6 | self-paced via /loop |
| LANE-E | TEST | claude REPL | claude-sonnet-4-6 | self-paced via /loop |
| LANE-F | META | claude REPL | claude-haiku-4-5 | self-paced via /loop |

## Phases

1. **PHASE 1 — PLAN.** Each lane drafts a plan for its v9.3 contribution.
2. **PHASE 2 — BUILD.** Each lane implements its plan.
3. **PHASE 3 — VERIFY.** Cross-lane verification (round-robin).
4. **OPERATOR-ACCEPTANCE.** David reviews, signs off.

Phase progression is operator-driven via the v9.2 dashboard's phase-flip control. /loop agents do NOT flip phases autonomously.

## Out of scope

- Non-Claude harnesses (codex/gemini/cursor/copilot/vibe) — deferred to a future run; live_repl is Claude-only for now.
- New v9.3 protocol changes beyond v9.2.
- Production deploy / packaging.

## Working directory

This worktree: `/Users/dave/Documents/Projects/megalodon-fleet/` on branch `fleet/dogfood-2026-05-19`. The main checkout at `/Users/dave/Documents/Projects/megalodon/` is NOT touched by this fleet.
