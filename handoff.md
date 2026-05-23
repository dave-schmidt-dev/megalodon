# Handoff: Megalodon — Agent Tool-Surface Policy

- **Active Plan:** `docs/superpowers/plans/2026-05-22-agent-tool-surface-policy.md` (tasks: `~/Documents/Projects/.plans/megalodon/agent-tool-surface-policy-2026-05-22-tasks.md`)
- **Current Task:** Plan is warp-complete and committed (`44810b9`); **nothing implemented yet**. Next action = begin execution at **Task 1** (extract `queue_client.main(argv)`).
- **Critical Files:** `megalodon_ui/harnesses/claude.py` (the `--allowedTools` string + new `_is_unbounded_tool` filter), `megalodon_ui/queue/queue_client.py` (extract `main()`), `launch.md` (protocol rewrite), `scripts/tests/test_harness_claude.py` (keystone enforcement test), `megalodon_ui/spawn.py` (`{{AGENT_ID}}` bake)

## Strategic Momentum
Just finished a full warp-tier planning pass for the tool-surface hardening triggered by the abandoned v94-ui-dogfood (agents drowned in `python`/compound permission prompts). Four cross-model reviewers (GPT-5.5, Gemini 3.1 Pro, Opus, Kimi K2.5) returned 17 accept / 2 acknowledge / 1 reject / 1 escalate(resolved); the decisive find — verified against Claude Code docs — was to **drop explicit read-only-git patterns** (Claude auto-runs read-only builtins, and `Bash(git diff*)` was broadening to `--output` writes). The immediate next move is a **separate execution session**: run the plan via subagent-driven development (off the live system), then re-run the v9.4 UI dogfood on the hardened surface. Note: implementation is intentionally a separate session — the plan is read-only until execution starts.

## Active Subagents
None. (All 4 review CLIs exited 0; a stray CI-watcher background task also completed exit 0. No live fleet — the dogfood run was archived `DEGRADED-CLOSE`; the FastAPI dashboard, tmux fleet, and queue applier are all down.)
