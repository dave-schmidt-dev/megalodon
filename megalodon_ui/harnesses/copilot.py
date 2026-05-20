"""Megalodon v9.1 — CopilotAdapter (EXPERIMENTAL — best-effort v9.1 support).

Wraps the ``copilot`` CLI (v1.0.48+) for non-interactive use.

EXPERIMENTAL — best-effort v9.1 support.

Verified invocation shape (research doc 2026-05-17):
    copilot -p "<prompt>" --model <id> --allow-all-tools --no-ask-user

stream-json: The ``copilot`` CLI does not expose a JSON-stream output mode in
v1.0.48.  Passing output_format="stream-json" falls back silently to the
plain-text invocation shape.  This is documented here as a known v9.1
limitation.

Auth: COPILOT_GITHUB_TOKEN (or ``gh auth`` interactive flow — not our concern
here).

Session logs: ~/.copilot/session-state/<session_id>/ (directory, not a file).
"""

from __future__ import annotations

import pathlib

from .base import Capabilities, Event, ModelSpec, _FollowupArgvDefault


class CopilotAdapter(_FollowupArgvDefault):
    """Concrete HarnessAdapter for the GitHub Copilot CLI."""

    name: str = "copilot"
    default_model: str = "claude-sonnet-4.6"
    supports_autonomous_loop = False  # CR-4

    available_models: tuple[ModelSpec, ...] = (
        ModelSpec(id="claude-sonnet-4.6", is_default=True),
        ModelSpec(id="claude-opus-4.7"),
        ModelSpec(id="gpt-5.2"),
        ModelSpec(id="gpt-5.4"),
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
            "copilot",
            "-p",
            prompt_or_launch_path,
            "--model",
            model,
            "--allow-all-tools",
            "--no-ask-user",
        ]
        return argv, {}

    # ------------------------------------------------------------------
    # parse_stream_line
    # ------------------------------------------------------------------

    def parse_stream_line(self, line: str) -> Event | None:
        line = line.rstrip("\n")
        if not line.strip():
            return None
        return Event(kind="text", text=line)

    # ------------------------------------------------------------------
    # session_log_path
    # ------------------------------------------------------------------

    def session_log_path(
        self, cwd: pathlib.Path, session_id: str
    ) -> pathlib.Path | None:
        # Returns the session directory; Copilot writes state files inside it.
        return pathlib.Path.home() / ".copilot" / "session-state" / session_id

    def session_log_dir(self, cwd: pathlib.Path) -> pathlib.Path | None:
        # Copilot does not expose a stable session-id discovery surface.
        return None

    # ------------------------------------------------------------------
    # auth / capabilities
    # ------------------------------------------------------------------

    def auth_env_keys(self) -> list[str]:
        return ["COPILOT_GITHUB_TOKEN"]

    def supports(self) -> Capabilities:
        return Capabilities(
            supports_autonomous_loop=False,
            supports_session_resume=False,
            supports_stream_json=False,
            supports_tool_use=True,
        )
