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
- `git`, `awk`, `sed`, `xargs` auto-approval (subcommand-shaped or can
  execute/mutate). Explicitly deferred.
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

def decide(command_preview: str) -> AutoApproveDecision: ...
```

Decision logic, in order (first match wins; default is **abstain**):

1. **Eligibility** — only `[Bash command]` previews are eligible. Any other tool
   header (`[Edit file]`, `[Write file]`, `[Read file]`, `[WebFetch]`, …) or the
   `[unknown tool]` / `<no context available>` fallbacks → **abstain**.
2. **Fail-safe on ambiguity** — strip the `[Bash command] ` prefix; if the
   remaining command string appears **truncated** or fails `shlex.split` →
   **abstain**. Never auto-approve a command we cannot fully see. Concrete
   truncation predicate: the extractor caps its excerpt at 280 chars
   (`_extract_command_preview`); treat the command as possibly-truncated when its
   length is `>= 270` (cap minus a safety margin). The implementation may tighten
   this, but it must err toward abstaining.
3. **Compound/redirect** — reuse `approval_rules._has_compound_structure`. Any
   pipe / redirect / `;` / `&&` / `||` / `$(` / backtick / control-flow keyword
   → **abstain**.
4. **Allowlist** — `program = shlex.split(cmd)[0]`. If `program` not in the
   ALLOWLIST → **abstain**.
5. **`find` denylist** — if `program == "find"` and any denylisted flag token is
   present → **abstain**.
6. Otherwise → **approve** with a reason naming the matched program.

**ALLOWLIST** (read-only heads):
`find, ls, cat, head, tail, wc, grep, rg, fd, tree, stat, file, du, pwd,
basename, dirname, realpath, readlink`

**DENYLIST** (`find` flags that mutate or execute):
`-exec, -execdir, -ok, -okdir, -delete, -fprint, -fprintf, -fls, -fprint0`

### Component 2 — `PermissionWatcher`

No behavior change. The existing `on_change(lane, info, None)` new-prompt
transition is the hook. (`PromptInfo.command_preview` is the input to `decide`.)

### Component 3 — server wiring (`server.py` ~1252)

When constructing the **live** `PermissionWatcher`, pass an `on_change` handler
**only if** `ctx.mission_config.auto_approve_readonly` is true. Handler:

- Fires only on a new-prompt transition (`info is not None and action is None`).
- Calls `auto_approve.decide(info.command_preview)`.
- On `approve`: `asyncio.create_task(...)` to run the existing async
  send-keys(`"1"`)→`clear_lane(lane, action="auto_approve")` path, then append an
  audit line. Fake-spawner path skips send-keys (mirrors the manual `respond`
  and inject handlers, detected via the `fake_emit` attribute).
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
- Abstain — denylist: `find . -delete`, `find . -exec rm {} \;`,
  `find . -execdir …`, `find . -ok …`.
- Abstain — structure: `ls | wc -l`, `cat a > b`, `find . ; rm x`,
  `cat $(echo x)`, backtick, `grep x && rm y`.
- Abstain — not allowlisted: `python3 x`, `git status`, `sed -i …`, `awk …`,
  `xargs rm`, `rm -rf`.
- Abstain — eligibility: `[Edit file] …`, `[Write file] …`, `[unknown tool] …`,
  `<no context available>`.
- Abstain — truncation: a `[Bash command]` preview at the 280-char cap.

**Integration (fake spawner):**
- A policy-approve prompt drives send-keys `"1"` + `clear_lane(action=
  "auto_approve")` + an audit line.
- A policy-abstain prompt leaves the prompt pending and writes no audit line.
- `auto_approve_readonly: false` → no `on_change` handler installed; an
  allowlisted command stays pending (operator-gated).

## Rollout / relationship to §9 MCP

This is the smallest structural fix that makes runs autonomous now. The §9 custom
MCP-server direction would later subsume the *protocol* surface, but §9 itself
notes raw exploration would still need either an MCP `survey_files` tool or this
auto-approver — so this work is not wasted by a later MCP bet.
