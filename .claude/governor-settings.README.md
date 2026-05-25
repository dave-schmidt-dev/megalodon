# governor-settings.json

Governor security settings for megalodon lane agents (Task 2.1).

- **NOT auto-loaded.** Applied only via `claude --settings .claude/governor-settings.json` (wired in Task 2.2).
- Schema confirmed against the official Claude Code docs (`code.claude.com/docs/en/hooks`, `code.claude.com/docs/en/permissions`), 2026-05-25.

## What it does

- `hooks.PreToolUse` with an empty matcher (`""` = all tools) runs the governor
  shim `"$CLAUDE_PROJECT_DIR"/scripts/governor_hook.py` on every tool call. The
  shim is the PRIMARY control (allow/deny via the policy engine).
- `permissions.deny` is the un-bypassable backstop floor — the catastrophic set
  (sudo, root-destructive `rm -rf`, secret-path reads, and anti-tamper on the
  governor's own files) that holds even if the hook is bypassed.

The note lives here, not in the JSON, so a future strict settings validator
cannot choke on a non-standard `_comment` key.
