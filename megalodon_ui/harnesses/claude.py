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
        governor_settings: pathlib.Path | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        # Governor (Task 2.2 / Task 3.3): when a settings path is supplied,
        # attach --settings <governor-settings.json> right after --model <id>.
        # The governor PreToolUse hook is now the SOLE permission gate — the old
        # static --allowedTools allowlist (and its operator-pattern appending)
        # were removed in Task 3.3, since the governor's policy.decide already
        # default-allows bounded commands and applies operator approval-rules as
        # audited allow-overrides. A hook `allow` decision auto-approves a tool
        # without an operator prompt (proven live: governor-repl-validation,
        # 2026-05-25). When governor_settings is None, argv is unchanged.
        def _settings_args() -> list[str]:
            return ["--settings", str(governor_settings)] if governor_settings else []

        if live_repl:
            # No --allowedTools and no positional prompt: live_repl lanes get
            # their initial prompt via tmux send-keys, and the governor hook is
            # the gate (see module note above).
            return (["claude", "--model", model] + _settings_args(), {})
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
