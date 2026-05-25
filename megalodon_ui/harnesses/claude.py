"""Megalodon v9.1 — ClaudeAdapter.

Wraps the ``claude`` CLI (v2.1.133+) for non-interactive use.

Verified invocation shape (research doc 2026-05-17):
    claude --print --model <id> "<prompt>"
    claude --print --output-format stream-json --model <id> "<prompt>"

Auth: ANTHROPIC_API_KEY (or ``claude setup-token`` interactive flow — not our
concern here).  env_overlay is always empty because the key is expected to
already be in the caller's environment.

Session logs: ~/.claude/projects/<sanitized-cwd>/<uuid>.jsonl
  Sanitisation: strip leading slash, replace '/' with '-', collapse
  leading dashes.
"""

from __future__ import annotations

import json
import pathlib

from .base import Capabilities, Event, ModelSpec

# Forbidden command heads (2026-05-22 tool-surface policy): interpreters, network
# tools, installers, and destructive non-interpreters that policy never auto-
# approves — even via an operator approval-rule. Prefix-matched against the head
# of a Bash(<cmd> ...) pattern; scripts/ paths are bounded by location and exempt.
_FORBIDDEN_HEAD_CMDS = (
    "python",
    "uv run",
    "bash",
    "sh",
    "eval",
    "curl",
    "wget",
    "ssh",
    "scp",
    "pip",
    "npm",
    "npx",
    "find",
    "rm",
    "sudo",
    "chmod",
    "chown",
    "dd",
    "mv",
    "tee",
    "ln",
)
# Compound/background separators Claude Code's Bash matcher recognizes
# (code.claude.com/docs/en/permissions): && || ; | |& & newline — plus command
# substitution. Any presence marks the candidate pattern unbounded.
_COMPOUND_OPERATORS = ("&&", "||", ";", "|", "&", "\n", "$(", "`")


def _is_unbounded_tool(pattern: str) -> bool:
    """True if a candidate --allowedTools pattern names an unbounded interpreter,
    network tool, installer, destructive command, or shell escape. Filters
    operator-supplied PM-8 patterns so 'approve & remember' cannot re-admit them.

    A pattern whose Bash head is a ``scripts/`` path is bounded by location (the
    sanctioned tool dir) — consistent with the threat model (the concern is python
    re-admission and accidental broadening, not malicious scripts). Everything else
    is prefix-matched against the forbidden heads; compound separators anywhere
    also mark the pattern unbounded.
    """
    low = pattern.lower()
    if any(op in low for op in _COMPOUND_OPERATORS):
        return True
    if "bash(" not in low:
        return False  # native-tool patterns (Read/Edit/...) are always bounded
    head = low.split("bash(", 1)[1].strip().lstrip("./").strip()
    if ".." in head:
        return True  # path traversal escapes any location bound (scripts/../python3)
    if head.startswith("scripts/"):
        return False  # path-scoped script — bounded by location
    return any(head.startswith(cmd) for cmd in _FORBIDDEN_HEAD_CMDS)


class ClaudeAdapter:
    """Concrete HarnessAdapter for the Claude Code CLI."""

    name: str = "claude"
    default_model: str = "claude-opus-4-7"
    supports_autonomous_loop = (
        True  # CR-4: only Claude supports autonomous loop in v9.1
    )

    available_models: tuple[ModelSpec, ...] = (
        ModelSpec(id="claude-opus-4-7", aliases=("opus",), is_default=True),
        ModelSpec(id="claude-sonnet-4-6", aliases=("sonnet",)),
        ModelSpec(id="claude-haiku-4-5-20251001", aliases=("haiku",)),
        # Legacy models (research doc 2026-05-17)
        ModelSpec(id="claude-opus-4-6"),
        ModelSpec(id="claude-opus-4-5-20251101"),
        ModelSpec(id="claude-sonnet-4-5-20250929"),
    )

    # ------------------------------------------------------------------
    # build_argv
    # ------------------------------------------------------------------

    def build_argv(
        self,
        prompt_or_launch_path: str,
        *,
        model: str,
        cwd: pathlib.Path,
        session_id: str | None = None,
        output_format: str = "text",
        extra_env: dict[str, str] | None = None,
        live_repl: bool = False,
        extra_allowed_tools: list[str] | None = None,
        governor_settings: pathlib.Path | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        # Governor (Task 2.2): when a settings path is supplied, attach
        # --settings <governor-settings.json> right after --model <id> (before
        # any positional prompt / before --allowedTools). ADDITIVE — the
        # --allowedTools allowlist below is left exactly as-is (a hook `allow`
        # still suppresses prompts for non-allowlisted tools; the allowlist is
        # the documented broad fallback). When None, argv is unchanged from
        # before, so callers that don't pass it (e.g. preview.py) are unaffected.
        def _settings_args() -> list[str]:
            return ["--settings", str(governor_settings)] if governor_settings else []

        if live_repl:
            # --allowedTools policy (2026-05-22 tool-surface hardening):
            #
            # PRINCIPLE: never allowlist an unbounded interpreter. Every agent
            # operation reaches a native tool or a dedicated, path-scoped script.
            # Origin: v94-ui-dogfood approval-friction finding + operator
            # constraint "i am not approving python".
            #
            # AUTO-APPROVED (no operator prompt):
            #  * Native tools: Read/Edit/Write/Grep/Glob, ScheduleWakeup, Task*.
            #    All file reads + ad-hoc inspection go through Read/Grep.
            #  * Path-scoped scripts (the sanctioned shell mutation/inspection
            #    paths — added here; they are NOT in the pre-policy allowlist):
            #      poll.py (state inspection), atomic_close.py (RULE-10 close),
            #      claim.sh (claims/ mutex), queue_submit.py (queue intents),
            #      run_e2e.sh (Playwright), run_tests.sh (full pytest suite).
            #  * Bounded non-interpreter utilities: sleep/date/printf (stagger
            #    wait, UTC stamp, terminal title — no code-exec, no -exec escape).
            #
            # DELIBERATELY NOT LISTED (Claude auto-runs these read-only builtins
            # without a prompt in every mode — code.claude.com/docs/en/permissions):
            #  * cat/ls/grep/find/head/tail/wc/echo/pwd/which/diff/stat AND
            #    read-only git (status/diff/log/show/rev-parse/ls-files). Listing
            #    them is redundant, and an explicit `Bash(git diff*)` would BROADEN
            #    to write-forms like `git diff --output=<file>` (CR-5). Write-form
            #    git (branch/commit/push) and write-form builtins still prompt.
            #
            # PERMANENTLY OFF THE ALLOWLIST (surface to operator if ever needed):
            #  * python / python3 / uv run / bare pytest (arbitrary code or
            #    missing test-extra deps — tests run via run_tests.sh)
            #  * bash -c / sh -c / eval / compound chains (&& | ; & newline)
            #  * curl / wget / ssh / scp (queue now via queue_submit.py)
            #  * find (-exec), rm/sudo/chmod/dd, installers
            allowed = (
                # Claude-native tools
                "Read Edit Write Grep Glob "
                "ScheduleWakeup TaskCreate TaskUpdate TaskGet TaskList TaskOutput "
                # Path-scoped scripts — the sanctioned shell paths
                "Bash(scripts/poll.py:*) Bash(scripts/atomic_close.py:*) "
                "Bash(scripts/claim.sh:*) Bash(scripts/queue_submit.py:*) "
                "Bash(scripts/run_e2e.sh:*) Bash(./scripts/run_e2e.sh:*) "
                "Bash(scripts/run_tests.sh:*) "
                # Bounded non-interpreter utilities (read-only git + cat/ls/grep
                # are NOT listed — Claude auto-runs read-only builtins regardless)
                "Bash(sleep:*) Bash(date:*) Bash(printf:*)"
            )
            # PM-8: append operator-approved patterns from .fleet/approval-rules.json,
            # but FILTER unbounded patterns first (2026-05-22 tool-surface policy).
            # An operator "approve & remember" must never silently re-admit
            # python/uv-run/curl/compound shells via approval-rules.json — the
            # exact loop that broadened the surface during the v94 dogfood.
            if extra_allowed_tools:
                safe_extra = [
                    p for p in extra_allowed_tools if not _is_unbounded_tool(p)
                ]
                if safe_extra:
                    allowed = allowed + " " + " ".join(safe_extra)
            return (
                ["claude", "--model", model]
                + _settings_args()
                + ["--allowedTools", allowed],
                {},
            )
        argv = ["claude", "--print", "--model", model] + _settings_args()
        if output_format == "stream-json":
            argv += ["--output-format", "stream-json"]
        argv.append(prompt_or_launch_path)
        return argv, {}

    # ------------------------------------------------------------------
    # build_followup_argv (P6.1) — adds --resume <prior_session_id>
    # ------------------------------------------------------------------

    def build_followup_argv(
        self,
        prompt: str,
        *,
        prior_session_id: str | None,
        model: str,
        cwd: pathlib.Path,
        output_format: str = "text",
        extra_env: dict[str, str] | None = None,
        governor_settings: pathlib.Path | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        argv = ["claude", "--print", "--model", model]
        # Governor (Task 2.2): --settings right after --model, before --resume /
        # the trailing positional prompt. ADDITIVE; None leaves argv unchanged.
        if governor_settings:
            argv += ["--settings", str(governor_settings)]
        if prior_session_id:
            argv += ["--resume", prior_session_id]
        if output_format == "stream-json":
            argv += ["--output-format", "stream-json"]
        argv.append(prompt)
        return argv, {}

    # ------------------------------------------------------------------
    # parse_stream_line
    # ------------------------------------------------------------------

    def parse_stream_line(self, line: str) -> Event | None:
        line = line.rstrip("\n")
        if not line.strip():
            return None
        if line.lstrip().startswith("{"):
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                return Event(kind="text", text=line)
            # Extract text from common Claude stream-json shapes
            event_type = parsed.get("type", "")
            if event_type == "text":
                return Event(kind="text", text=parsed.get("text", ""), raw=parsed)
            if "content" in parsed:
                content = parsed["content"]
                text = content if isinstance(content, str) else ""
                return Event(kind="text", text=text, raw=parsed)
            # JSON but not a recognised text event — surface as system
            return Event(kind="system", text="", raw=parsed)
        # Plain text line
        return Event(kind="text", text=line)

    # ------------------------------------------------------------------
    # session_log_path
    # ------------------------------------------------------------------

    def session_log_path(
        self, cwd: pathlib.Path, session_id: str
    ) -> pathlib.Path | None:
        return self.session_log_dir(cwd) / f"{session_id}.jsonl"  # type: ignore[operator]

    def session_log_dir(self, cwd: pathlib.Path) -> pathlib.Path | None:
        # Sanitise to match Claude Code's on-disk project-dir encoding EXACTLY:
        # every '/' AND every '.' becomes '-' (underscores are preserved). The
        # leading '/' of an absolute path therefore becomes a leading '-' — it
        # must NOT be stripped, or the computed dir won't match the real one
        # (verified against real entries, e.g. -Users-dave-Documents-... and
        # -Users-dave--launchd from /Users/dave/.launchd). A previous version
        # did `.lstrip("/").lstrip("-")` and dropped the leading dash, which
        # silently broke session-log discovery and transcript reads.
        sanitized = str(cwd).replace("/", "-").replace(".", "-")
        # Degenerate cwd ("/" → "-", or empty) keeps the historical sentinel.
        if sanitized in ("", "-"):
            sanitized = "root"
        return pathlib.Path.home() / ".claude" / "projects" / sanitized

    # ------------------------------------------------------------------
    # auth / capabilities
    # ------------------------------------------------------------------

    def auth_env_keys(self) -> list[str]:
        return ["ANTHROPIC_API_KEY"]

    def supports(self) -> Capabilities:
        return Capabilities(
            supports_autonomous_loop=True,
            supports_session_resume=True,
            supports_stream_json=True,
            supports_tool_use=True,
        )
