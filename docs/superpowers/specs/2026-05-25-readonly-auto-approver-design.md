# Read-only auto-approver — design

**Date:** 2026-05-25
**Plan reference:** `docs/v10-readiness-plan.md` §1b option (A)
**Status:** approved (brainstorm), pending implementation plan

## Problem

Within ~5 minutes of launch, lanes run read-only exploration commands
(`find … *.py`, `find ui/static/`) and **stall on a Claude Code permission
prompt**. Prose constraints (`launch.md`, the `ab2494b` Glob/Grep guidance) lower
the stall rate but do not bind tool choice — an agent can always reach for `find`.
Until a *structural* fix ships, an unattended run keeps stalling in its first
minutes, which directly blocks the "barely-workable autonomous fleet" goal.

## Goal

Make benign read-only exploration **un-stallable** regardless of which tool the
agent reaches for, while writes / network / destructive operations still gate to
the operator. The change must be security-reviewable in one place, on by default,
and disableable per mission.

## Non-goals

- Auto-approving anything that writes, mutates, fetches network, or executes
  arbitrary code. (Out of scope, on purpose.)
- Consuming the operator-curated `approval-rules.json` store for auto-approval.
  That store stays for the dynamic, operator-driven case; this feature is a
  static, built-in allowlist only.
- `awk`, `sed`, `xargs` auto-approval (can execute/mutate). Explicitly deferred.
- **Bounding command runtime/output.** Auto-approving `find /` or `du /` trades a
  *permission* stall for a possible *runtime* stall, but that is strictly no worse
  than the stall it replaces, and the existing stale-lane detector
  (`GET /api/v1/lanes/stale`, `server.py:2599`) already surfaces a lane hung on a
  slow command. Runtime guards are out of scope. (Self-pass WR-2.)
- A runtime kill-switch endpoint. Disable is via mission config + restart.

## Existing infrastructure (what we build on)

- **`PermissionWatcher`** (`permission_watcher.py`): pure *detection*. Polls each
  lane's tmux pane, matches `"Do you want to proceed?"`, extracts a
  `command_preview` (e.g. `[Bash command] find . -name x`), and surfaces a
  `PromptInfo`. On a new-prompt transition it fires
  `on_change(lane_short, info, None)`. It performs **no** send-keys action.
- **`respond` endpoint** (`server.py:2537`): the *action* path. Maps
  approve/approve_remember/deny → tmux send-keys `1`/`2`/`3` → `clear_lane`.
- **`approval_rules.py`**: `extract_pattern()` + `_has_compound_structure()` —
  conservative refusal of compound/redirect/pipe/control-flow commands. Reused
  here as the single audited definition of "too ambiguous to trust."
- **`MissionConfig`** (`mission_config/schema.py`): pydantic model; new boolean
  flags with defaults are backward-compatible.
- **Audit log**: inject/restart endpoints append JSON lines to
  `.fleet/inject-log-YYYY-MM-DD.jsonl`. We follow the same daily-rotated JSONL
  convention but in a dedicated file (see Component 5).

## Architecture

Chosen integration: **server `on_change` wiring** (vs. a watcher-internal send
path). The watcher stays pure detection; the server owns tmux I/O and reuses the
verified send-keys path with the correct session name. This also closes the
"missing `on_change` wiring" gap tracked in v10-readiness-plan §3 (minor).

```
PermissionWatcher._scan_once
  detects NEW Bash prompt
        │ fires on_change(lane, info, None)
        ▼
server on_change handler (NEW)
        │ auto_approve.decide(info.command)
        ├── abstain → leave pending (operator sees it, as today)
        └── approve → asyncio.create_task(
                        send_keys("1") → clear_lane(action="auto_approve")
                        + append audit line)
```

### Component 1 — `megalodon_ui/auto_approve.py` (pure, no I/O)

```python
@dataclass(frozen=True)
class AutoApproveDecision:
    approve: bool
    reason: str          # human-readable, written to the audit log

def decide(command_preview: str, *, truncated: bool) -> AutoApproveDecision: ...
```

`decide` takes the preview text **and** a `truncated: bool` flag supplied by the
caller (see Component 2 — the extractor reports truncation; the policy does not
re-derive it from a copied length constant). Decision logic, in order (first match
wins; default is **abstain**):

1. **Eligibility** — only `[Bash command]` previews are eligible. Any other tool
   header (`[Edit file]`, `[Write file]`, `[Read file]`, `[WebFetch]`, …) or the
   `[unknown tool]` / `<no context available>` fallbacks → **abstain**.
2. **Fail-safe on ambiguity** — if `truncated` is true (the extractor's excerpt hit
   its cap, so the command cannot be proven complete) or the post-prefix text fails
   `shlex.split` → **abstain**. Never auto-approve a command we cannot fully see.
   (Self-pass PW-2: truncation is a fact owned by the extractor, passed in — not a
   `>= 270` guess coupled to a constant the policy doesn't own.)
3. **Compound/redirect** — reuse `approval_rules._has_compound_structure`. Any
   pipe / redirect / `;` / `&&` / `||` / `$(` / backtick / control-flow keyword
   → **abstain**.
4. **Allowlist** — `program = shlex.split(cmd)[0]`. If `program` not in the
   ALLOWLIST and not `git` → **abstain**.
5. **`find` denylist** — if `program == "find"` and any denylisted flag token is
   present → **abstain**.
6. **`git` read-only-and-pager-safe** (Self-pass PW-1 / WR-2; Re-pass OW-2) — if
   `program == "git"`:
   - **Global-option safety first.** Git accepts global options *before* the
     subcommand, some of which execute arbitrary code through an otherwise
     read-only subcommand (`git -c core.pager='!sh -c …' log`, `git -c
     alias.x='!cmd' x`). Walk the tokens after `git`: the only permitted
     pre-subcommand globals are `--no-pager`, `--paginate`/`-p`. **Any** other
     global option — notably `-c`, `--config-env`, `-C`, `--git-dir`,
     `--work-tree`, `--exec-path`, `-c`-style `key=val` — → **abstain**.
   - **Subcommand resolution.** The subcommand is the first token after `git` that
     is not one of the permitted globals above (so `git --no-pager log` resolves to
     `log`, not `--no-pager`). It must be in GIT_READONLY_SUBCOMMANDS.
   - **Pager safety.** A never-paginate subcommand (`status, rev-parse, branch,
     ls-files, show-ref, describe`) is approved directly. A paginating read
     subcommand (`log, diff, show, blame, shortlog`) is approved **only** when
     `--no-pager` is present (else it blocks the tmux pane on `less`) → otherwise
     **abstain**.
   - Any non-allowlisted subcommand → **abstain**.
7. Otherwise → **approve** with a reason naming the matched program/subcommand.

**ALLOWLIST** (read-only heads):
`find, ls, cat, head, tail, wc, grep, rg, fd, tree, stat, file, du, pwd,
basename, dirname, realpath, readlink`

**GIT_READONLY_SUBCOMMANDS** (pager-safe set):
- never-paginate (approve directly): `status, rev-parse, branch, ls-files,
  show-ref, describe`
- paginate (approve only with `--no-pager`): `log, diff, show, blame, shortlog`

**DENYLIST** (`find` flags that mutate or execute):
`-exec, -execdir, -ok, -okdir, -delete, -fprint, -fprintf, -fls, -fprint0`

### Component 2 — `PermissionWatcher` / extractor (truncation reporting)

`_extract_command_preview` currently excerpts `before[best_idx:best_idx+280]` and
silently truncates. Change it to also report whether it truncated, so the policy
receives truncation as a fact rather than guessing from a length constant it does
not own (Self-pass PW-2). Concretely: add a `truncated` field to `PromptInfo`
(set when the `[Bash command]` excerpt was clipped at the cap), threaded from the
extractor → `_scan_once` → `PromptInfo`. The cap constant stays the single source
of truth inside the extractor. Detection behavior is otherwise unchanged; the
existing `on_change(lane, info, None)` new-prompt transition remains the hook.

### Component 3 — server wiring (`server.py` ~1252)

When constructing the **live** `PermissionWatcher`, pass an `on_change` handler
**only if** `ctx.mission_config.auto_approve_readonly` is true. Handler:

- Fires only on a new-prompt transition (`info is not None and action is None`).
- Calls `auto_approve.decide(info.command_preview, truncated=info.truncated)`.
- On `approve`: schedule the existing async send-keys(`"1"`)→`clear_lane(lane,
  action="auto_approve")` path + audit-line append. The scheduled task is
  **tracked, not fire-and-forget** (Self-pass OW-1): keep a reference in a
  module/handler-owned `set[asyncio.Task]`, attach a `add_done_callback` that
  discards it and logs any exception, and cancel/drain the set on watcher
  `stop()`. This avoids reintroducing the unawaited-detached-task anti-pattern the
  v10 plan already tracks as debt (`v10-readiness-plan.md:114`). Fake-spawner path
  skips send-keys (mirrors the manual `respond`/inject handlers, detected via the
  `fake_emit` attribute).
- On `abstain`: do nothing — the prompt stays pending and the operator sees it
  exactly as today.

When the flag is false, **no** `on_change` handler is installed and the watcher
behaves exactly as it does today (operator gates everything).

### Component 4 — `MissionConfig.auto_approve_readonly`

Add `auto_approve_readonly: bool = True` to `MissionConfig`
(`mission_config/schema.py`). Default `True` satisfies the on-by-default posture;
the default keeps every existing mission config valid.

### Component 5 — audit + visibility

- Each auto-approval appends a JSON line to
  `.fleet/auto-approve-log-YYYY-MM-DD.jsonl`:
  `{utc, lane, command, decision: "approve", reason}`. A dedicated file (not the
  inject log) keeps the unattended-action trail easy to review in isolation.
- The auto-approval flows through `clear_lane(action="auto_approve")`, so the
  existing `on_change`/SSE machinery can surface a transient "auto-approved"
  signal in the dashboard rather than the prompt silently vanishing.

## Error handling

- A misbehaving `on_change` callback must never crash the watcher — the watcher
  already wraps `on_change` in try/except (`_fire_change`). The handler itself
  also guards its own body.
- `decide` is pure and total: any unexpected input → **abstain** (fail safe).
- send-keys failure in the auto-approve task is logged; the prompt remains
  pending so the operator can still act. (No retry storm: the watcher only fires
  `on_change` once per new fingerprint.)

## Testing (TDD)

**Pure policy (`test_auto_approve.py`) — the security core:**
- Approve: `find . -name '*.py'`, `ls -la ui/static`, `grep -rn foo src`,
  `rg pattern`, `cat README.md`, `head -50 file`, `wc -l x`, `tree -L 2`.
- Approve — git pager-safe: `git status`, `git rev-parse HEAD`, `git ls-files`,
  `git branch`, `git --no-pager log -5`, `git --no-pager diff`.
- Abstain — git paginating without `--no-pager`: `git log`, `git diff`,
  `git show`, `git blame x`.
- Abstain — git mutating subcommand: `git commit …`, `git push`, `git checkout …`,
  `git reset …`, `git clean -fd`.
- Abstain — git global-option code injection (Re-pass OW-2): `git -c
  core.pager='!sh -c "rm x"' log`, `git -c alias.s='!cmd' s`, `git -C /other log`,
  `git --exec-path=/tmp log`.
- Abstain — denylist: `find . -delete`, `find . -exec rm {} \;`,
  `find . -execdir …`, `find . -ok …`.
- Abstain — structure: `ls | wc -l`, `cat a > b`, `find . ; rm x`,
  `cat $(echo x)`, backtick, `grep x && rm y`.
- Abstain — not allowlisted: `python3 x`, `sed -i …`, `awk …`, `xargs rm`,
  `rm -rf`.
- Abstain — eligibility: `[Edit file] …`, `[Write file] …`, `[unknown tool] …`,
  `<no context available>`.
- Abstain — truncation: `decide(preview, truncated=True)` returns abstain even for
  an otherwise-allowlisted preview.

**Golden-fixture validation (Self-pass PW-3):** the policy's correctness rests on
the `command_preview` shape, currently asserted only against a *hand-authored*
fixture (`SAMPLE_PROMPT_BLOCK`). Before relying on auto-approval in a live run,
capture a **real** `claude`-REPL Bash permission prompt (e.g. `tmux capture-pane`
during a run, or the watcher's stream log) for `find`/`ls`/`git status`, commit it
as a golden fixture, and assert `_extract_command_preview` + `decide` produce the
expected approve decision on it. This is an explicit implementation-plan task, not
optional — the fail-safe direction limits blast radius, but unverified parsing of
real output is the residual risk this feature carries.

**Integration (fake spawner):**
- A policy-approve prompt drives send-keys `"1"` + `clear_lane(action=
  "auto_approve")` + an audit line.
- A policy-abstain prompt leaves the prompt pending and writes no audit line.
- `auto_approve_readonly: false` → no `on_change` handler installed; an
  allowlisted command stays pending (operator-gated).
- Scheduled auto-approve tasks are tracked and drained on watcher `stop()` (no
  pending-task warnings; OW-1 regression guard).

## Rollout / relationship to §9 MCP

This is the smallest structural fix that makes runs autonomous now. The §9 custom
MCP-server direction would later subsume the *protocol* surface, but §9 itself
notes raw exploration would still need either an MCP `survey_files` tool or this
auto-approver — so this work is not wasted by a later MCP bet.

**Self-pass WR-1 (deferred to external review):** the static ALLOWLIST is a
maintenance treadmill — every future read-tool an agent reaches for (`jq`, `diff`,
`comm`, `column`, `xxd`, `od`, …) is a fresh stall until the list is edited. Kept
as a conscious trade-off for the minimal first cut; whether the allowlist is the
right long-term shape (vs. the §9 MCP `survey_files` tool) is left open for the
external contrarian reviewer to weigh.

## Revision log

- **2026-05-25 — In-session contrarian self-pass applied.** 1 OW + 3 PW + 2 WR
  findings; 5 fixed inline, 1 deferred to external review:
  - OW-1 (fixed): tracked auto-approve task instead of fire-and-forget
    `create_task` (avoids the unawaited-detached-task debt at
    `v10-readiness-plan.md:114`).
  - PW-1 + WR-2 (fixed): added pager-safe read-only `git` subcommands to the
    policy; paginating subcommands require `--no-pager` to avoid blocking the pane.
  - PW-2 (fixed): extractor now *reports* truncation via a `PromptInfo.truncated`
    flag; policy no longer guesses from a copied `>= 270` constant.
  - PW-3 (fixed): added a mandatory golden-fixture-from-real-`claude`-output
    validation task; documented residual parsing risk.
  - WR-2 (fixed): documented runtime-bound non-goal; leans on existing
    stale-lane detection.
  - WR-1 (deferred): allowlist-maintenance-treadmill question handed to the
    external reviewer.
- **2026-05-25 — Re-pass after material revision (git + truncation changes).**
  - OW-2 (fixed): the `git` rule mis-located the subcommand (`tokens[1]` breaks on
    `git --no-pager log`) and ignored global-option code injection (`git -c
    core.pager=…`, `-c alias.x='!cmd'`). Rewrote step 6 to skip permitted globals,
    refuse all other pre-subcommand globals, and resolve the real subcommand.
  - Re-pass returned no further OW findings beyond the deferred WR-1 → cleared to
    dispatch external review.
