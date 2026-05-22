# Agent Tool-Surface Policy — Design

**Date:** 2026-05-22
**Status:** design (awaiting operator review)
**Origin:** v94-ui-dogfood finding `operator-FM2-approval-friction-tool-surface-policy`
(`.archive/2026-05-22T19-50Z--v94-ui-dogfood/findings/`).

## Problem

Six autonomous agents hit a permission prompt on nearly every bash command, so the
operator can't keep up — clearing one prompt surfaces the next, and the dashboard's
"N agents awaiting approval" never empties. The v94-ui-dogfood was abandoned
(DEGRADED-CLOSE) because the visibility charter never started: the fleet was stuck
on bootstrap approvals the entire run.

Root cause: the launch protocol routes routine operations through **unbounded
interpreters** that cannot be safely allowlisted:

| Operation | Today | Why it can't be allowlisted |
|---|---|---|
| Agent ID | `python3 -c "from scripts._agent_id import …"` | bare `python3` = arbitrary code |
| Queue intent | `python -m megalodon_ui.queue.queue_client` | `python -m` = arbitrary code |
| Task claim | `mkdir claims/X && echo … > owner.txt` | compound (`&&`) → not allowlistable |
| Tick telemetry | `scripts._fleet_tick.record_tick(…)` (python import) | python |

Allowlisting `python` would defeat the permission model entirely (it is equivalent
to approving every possible command). The operator's constraint is explicit: **never
approve `python`.**

## Principle

**Never allowlist an unbounded interpreter. Give each agent operation a dedicated,
path-scoped tool, allowlist the path, and rewrite the protocol to direct agents to
those tools only.**

This is not a new idea in this codebase — `launch.md` already mandates
`scripts/poll.py` (not compound `cat|grep`), `scripts/atomic_close.py` (not python
heredocs), and `./scripts/run_e2e.sh`. The protocol simply left four holes routed
through raw `python`/compound. This design closes them.

## Policy (the allow/deny boundary)

**Allowed — bounded, by exact path or native tool:**
- Native agent tools: `Read`, `Edit`, `Write`, `Grep`, `Glob`, `Task*`,
  `ScheduleWakeup`. File reads go through `Read`/`Grep`, never shell `cat`/`ls`.
- `scripts/poll.py` — the **only** state-inspection path (no raw `cat`/`ls`/`grep`/`find`).
- `scripts/atomic_close.py` — RULE-10 atomic close.
- `scripts/claim.sh` *(new)* — the **only** `claims/` mutation path.
- `scripts/queue_submit.py` *(new)* — queue intent submission (wraps the existing
  `megalodon_ui.queue.queue_client` argparse CLI).
- `scripts/run_e2e.sh` — Playwright E2E.
- `pytest` — test running (TEST lane). NOT bare `python`.
- Read-only `git`: `git status`, `git diff`, `git log`, `git show`, `git rev-parse`,
  `git ls-files`. (NOT `git branch` — `git branch <name>` mutates; drop it.)

**Never allowed — unbounded (permanently off the allowlist):**
- `python`, `python3` (raw `-c` / `-m`)
- `bash -c`, `sh -c`, `eval`
- compound chains (`&&`, `||`, `|`, `;`)
- `curl`, `wget`
- `rm -rf` outside `claims/`, `sudo`, `chmod`, package installers (`pip`, `npm i`, …)

**Resolved decisions (operator, 2026-05-22):**
- Claim mechanism: dedicated `scripts/claim.sh` (not bare `mkdir claims/*`).
- Read-only inspection: route through `scripts/poll.py`; do NOT allowlist raw
  `cat`/`ls`/`grep`/`find` (ad-hoc reads use the native `Read`/`Grep` tools).
- Tests: allow `pytest:*`, not bare `python`.
- `fleet_tick`: **drop from the agent path** (optional telemetry; not worth a tool).
- Read-only `git`: **allowed** (bounded verbs).

## Architecture / change set (6 areas)

1. **`megalodon_ui/harnesses/claude.py`** — replace the base `--allowedTools` string
   (lines ~66-95) with the narrowed allow-list above. Remove `Bash(ls/cat/grep/rg/
   head/tail/wc/echo/diff/stat/…)`, the `curl` patterns, and any `python` pattern.
   Add `Bash(scripts/claim.sh:*)` and `Bash(scripts/queue_submit.py:*)`. Keep
   `poll.py`, `atomic_close.py`, `run_e2e.sh`, `pytest:*`, read-only git, native tools.

2. **`scripts/claim.sh`** *(new)* — `claim.sh <task-id> <agent-id>`: atomically
   create `claims/<task-id>/` + write `owner.txt` with `<agent-id>` (the agent passes
   the ID it read from its launch header). Idempotent for the same agent; refuses
   path traversal (task-id must match `^[A-Za-z0-9._-]+$`); exits non-zero if already
   claimed by a different agent. Replaces `mkdir claims/X && echo … > owner.txt`.

3. **`scripts/queue_submit.py`** *(new, executable)* — thin shebang wrapper over
   `megalodon_ui.queue.queue_client`'s existing argparse `main()`. Allowlisted by
   path so agents run `scripts/queue_submit.py …` instead of `python -m …`.

4. **`scripts/gen_lane_launches.py`** — bake the deterministic agent ID into each
   `launch-<LANE>.md` header (`AGENT_ID:` line), computed from
   `mission-dir-basename | LANE | utc_started` (the documented A4 formula). Agents
   read it; no command needed.

5. **`launch.md`** — rewrite the protocol so every step uses a bounded tool:
   - Step 2 (agent ID): read `AGENT_ID` from the header; remove the `python3 -c`
     blocks (keep one as an explicit "legacy fallback only" note).
   - Step 4 (claim): `scripts/claim.sh P1-<X>` instead of `mkdir claims/…`.
   - RULE 15 (queue): `scripts/queue_submit.py` instead of `python -m …`.
   - §5.A fleet_tick: removed from the agent path.
   - Any remaining raw `cat`/compound inspection → `scripts/poll.py`.

6. **`scripts/new_run.sh` seed + templates/run** — update the seeded
   `.fleet/approval-rules.json` template to the new bounded set (drop the read-only
   shell patterns now covered by the policy).

## Testing

- **Allowlist enforcement test** *(new, the keystone)* — assert the base
  `--allowedTools` string from `claude.py` contains the bounded set AND contains no
  `python`/`bash -c`/`curl`/compound patterns. This is the regression guard: the
  deny-list cannot silently come back.
- `scripts/claim.sh` — claim creates dir+owner; double-claim by another agent fails;
  path-traversal task-id rejected; idempotent re-claim by same agent ok.
- `scripts/queue_submit.py` — forwards args to queue_client; `--help` works; bad
  args exit non-zero.
- `gen_lane_launches.py` — generated header contains a real `AGENT_ID: agent-XXXX`
  matching `deterministic_agent_id(basename, lane, utc)`; legacy `generate_all` path
  unchanged.
- `launch.md` lint — a test greps the rendered launch files for forbidden tokens
  (`python3 -c`, `python -m`, `&&` in fenced bash) and fails if present.

## Non-goals

- Changing the spawn mechanism, mission-config schema, or the queue applier.
- The v10 refactor (separate track).
- Reworking the permission_watcher (already fixed this session).
- Per-lane differentiated allowlists (all lanes get the same bounded set; TEST lane's
  `pytest` is already in the shared set).

## Done when

- The base allowlist contains zero unbounded patterns (enforced by test).
- A fresh `new_run.sh` + spawn produces a fleet that completes bootstrap (agent ID,
  claim, first queue submit) with **zero permission prompts**.
- `launch.md` contains no `python3 -c` / `python -m` / compound bash in agent steps.
- All new tools have tests; full suite green; CI green.
- Then: re-run the v9.4 UI dogfood on the hardened surface (the visibility charter
  finally gets to run).
