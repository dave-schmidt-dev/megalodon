"""Megalodon v9.1 — CodexAdapter.

Wraps the ``codex`` CLI (v0.130.0+) for non-interactive use.

Verified invocation shape (research doc 2026-05-17):
    codex exec -m <id> -s read-only --skip-git-repo-check "<prompt>"

stream-json: The ``codex`` CLI does not expose a dedicated JSON-stream flag
in v0.130.0.  Passing output_format="stream-json" falls back silently to the
plain-text invocation shape.  This is documented here as a known v9.1
limitation; a ``--json`` flag may be added in a future Codex release.

Auth: CODEX_API_KEY (or ``codex login`` interactive flow).
Session logs: ~/.codex/sessions/<session_id>/ (directory, not a single file).
"""

from __future__ import annotations

import json
import pathlib

from .base import Capabilities, Event, ModelSpec


class CodexAdapter:
    """Concrete HarnessAdapter for the OpenAI Codex CLI."""

    name: str = "codex"
    default_model: str = "gpt-5.5"
    supports_autonomous_loop = False  # CR-4

    available_models: tuple[ModelSpec, ...] = (
        ModelSpec(id="gpt-5.5", is_default=True),
        ModelSpec(id="gpt-5.4"),
        ModelSpec(id="gpt-5.4-mini"),
        ModelSpec(id="gpt-5.3-codex"),
        ModelSpec(id="gpt-5.3-codex-spark"),
        ModelSpec(id="gpt-5.2"),
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
        # stream-json is not supported in v9.1; fall back to text shape.
        argv = [
            "codex",
            "exec",
            "-m", model,
            "-s", "read-only",
            "--skip-git-repo-check",
            prompt_or_launch_path,
        ]
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
                text = parsed.get("text", parsed.get("content", ""))
                if isinstance(text, str):
                    return Event(kind="text", text=text, raw=parsed)
                return Event(kind="system", text="", raw=parsed)
            except json.JSONDecodeError:
                pass
        return Event(kind="text", text=line)

    # ------------------------------------------------------------------
    # session_log_path
    # ------------------------------------------------------------------

    def session_log_path(
        self, cwd: pathlib.Path, session_id: str
    ) -> pathlib.Path | None:
        # Returns the session directory; Codex writes multiple files inside it.
        return pathlib.Path.home() / ".codex" / "sessions" / session_id

    # ------------------------------------------------------------------
    # auth / capabilities
    # ------------------------------------------------------------------

    def auth_env_keys(self) -> list[str]:
        return ["CODEX_API_KEY"]

    def supports(self) -> Capabilities:
        return Capabilities(
            supports_autonomous_loop=False,
            supports_session_resume=True,
            supports_stream_json=False,
            supports_tool_use=True,
        )
