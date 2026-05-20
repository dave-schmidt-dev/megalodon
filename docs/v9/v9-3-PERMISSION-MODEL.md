# v9.3 — Permission model for live_repl missions

**Status:** Operator-codified 2026-05-19. Applies to any mission spawned with `live_repl: true` lanes (Claude Code REPL + `/loop` autonomous).

## Principle

**Operators pre-authorize categories, not commands.** The agent reading from its own project workspace is a basic capability — not a per-command operator decision. Likewise the v9 protocol primitives (claim mkdir / rm) are pre-authorized because the operator accepts the protocol when they accept the agent into the mission. Anything genuinely operator-decision-shaped (running arbitrary Python, doing network calls, executing compound shells) surfaces to the dashboard permission banner where the operator approves / approves-and-remembers / denies.

## What's auto-approved at spawn

The `claude --allowedTools` flag is set by `megalodon_ui/harnesses/claude.py` when `live_repl=True`. The set is:

### Claude-native tools (always)

| Tool | Why |
|---|---|
| `Read` | File inspection — preferred over shell `cat`. |
| `Edit` | File modification — preferred over `sed -i`. |
| `Write` | File creation — preferred over shell `echo > file`. |
| `Grep` | Code search — preferred over shell `grep` for big searches. |
| `Glob` | File matching — preferred over shell `find`. |
| `ScheduleWakeup` | Required for /loop autonomous iteration. |
| `Task*` | Claude in-session task tracker (no system effect). |

### Read-only project-workspace shell (always)

The principle "read from project workspace" must not prompt. Auto-approved:

```
ls grep rg cat head tail wc echo
diff stat file realpath basename dirname pwd tree which date true false
```

None of these mutate state. None take a `-exec` or shell-eval flag that would let them invoke arbitrary commands.

### Read-only git (always)

```
git status   git diff   git log    git show
git branch   git rev-parse   git ls-files   git config --get
```

Same rule: read-only, no mutation, no `git commit -F -` shell-injection.

### v9 protocol primitives (always)

```
mkdir claims/<task-id>
rm -rf claims/<task-id>
rmdir claims/<task-id>
```

The atomic-mutex semantics of the claim-and-release protocol. Operator-authorized by mission acceptance.

## What surfaces to the operator (prompts)

These categories ALWAYS prompt via `permission_watcher` → dashboard banner:

- **Runtime execution**: `python3`, `python`, `uv`, `pytest`, `npx`, `node`, `ruby`, etc. Any interpreted runtime can do anything.
- **`find`**: it has `-exec` which is arbitrary-command-execution disguised as a search tool.
- **Compound shells**: `&&`, `|`, `;`, `||`, `$(...)`, backticks. The pattern matcher can't statically verify safety of a compound. Split into separate tool calls.
- **Network**: `curl`, `wget`, `ssh`, `scp`, `nc`. Always operator decision.
- **Writes outside protocol dirs via shell**: `echo > /etc/passwd` etc. (The Write tool is auto-approved because the agent uses it; shell-mediated writes are caught by Bash pattern matching.)
- **Anything not in the auto-approve list above** — default-deny, surface to operator.

## Operator response options (dashboard banner)

For each prompt the dashboard renders 3 buttons:

- **Approve** → sends Claude REPL menu option `1` (this command only).
- **Approve & remember** → sends option `2` (Claude's "Yes, and don't ask again for this pattern" — session-scoped).
- **Deny** → sends option `3` (rejects; agent must try a different approach).

Plus a header-level **Approve all** that fires Approve in parallel against every pending prompt.

## Inheritance for new missions

Every new live_repl mission inherits this policy automatically through `megalodon_ui/harnesses/claude.py`. To widen or narrow for a specific mission:

- Operator-facing knob: not yet exposed (TODO — make `live_repl_allowed_tools` a `LaneConfig` field, with the v9.3 default as the empty fallback).
- Today: edit the adapter source to amend the policy.

## Why not just allow everything?

We tried it. Within the first hour of dogfood, agents wanted to `cd /path && mkdir -p X && echo Y > X/owner.txt && cat X/owner.txt` — a compound that bundles legitimate protocol ops with a `cat` that could in principle exfiltrate. We also saw agents try `python3 -c "..."` to generate identities (now pre-baked at spawn instead). Blanket-allow defeats the safety boundary that makes self-modifying multi-agent runs sane for an operator to babysit. The category-based policy here is the minimum viable safety net.

## Why not lock it down harder?

We also tried "Claude tools only, every shell prompts." Agents are reading TASKS.md, listing claims/, inspecting findings/ as their normal work — they hit prompts dozens of times per iteration. The cognitive load on the operator becomes the bottleneck, defeats the autonomy goal, and operator-fatigue leads to rubber-stamping (which is worse than auto-approve because it trains the wrong instinct). Read-from-workspace is the line where "operator-fatigue cost" exceeds "safety gain."

## Future

- `S-ORCHESTRATOR-AUTO-LOOP` (TASKS pool) — once the orchestrator session auto-loops, it can apply this same policy programmatically and only escalate genuinely-operator-decision prompts.
- Mission-config-controllable allowlist — let operators override per mission via `mission_config.yaml`.
- Per-lane allowlist — TEST might need `Bash(npx playwright*)` auto-approved while no other lane should.
