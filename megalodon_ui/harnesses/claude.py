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
    supports_autonomous_loop = True  # CR-4: only Claude supports autonomous loop in v9.1

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
    ) -> tuple[list[str], dict[str, str]]:
        argv = ["claude", "--print", "--model", model]
        if output_format == "stream-json":
            argv += ["--output-format", "stream-json"]
        argv.append(prompt_or_launch_path)
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
        # Sanitise: remove leading slash, replace '/' with '-', collapse
        # consecutive leading dashes produced by an absolute path like /tmp.
        raw = str(cwd).lstrip("/")
        sanitized = raw.replace("/", "-").lstrip("-") or "root"
        return (
            pathlib.Path.home()
            / ".claude"
            / "projects"
            / sanitized
            / f"{session_id}.jsonl"
        )

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
