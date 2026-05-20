"""Megalodon v9.1 — CursorAdapter (EXPERIMENTAL — best-effort v9.1 support).

Wraps the ``cursor-agent`` CLI (v2026.05.16+) for non-interactive use.

EXPERIMENTAL — best-effort v9.1 support.

Verified invocation shape (research doc 2026-05-17):
    cursor-agent -p --model <id> --force --trust "<prompt>"

Note: the CLI binary is ``cursor-agent``, NOT ``cursor``.

stream-json: The ``cursor-agent`` CLI does not expose a dedicated JSON-stream
flag in v2026.05.16.  Passing output_format="stream-json" falls back silently
to the plain-text invocation shape.  This is documented here as a known v9.1
limitation.

Auth: CURSOR_API_KEY (or ``cursor-agent login`` interactive flow).

Session logs: ~/.cursor/chats/<session_id>/ (directory, not a file).
"""

from __future__ import annotations

import pathlib

from .base import Capabilities, Event, ModelSpec, _FollowupArgvDefault


class CursorAdapter(_FollowupArgvDefault):
    """Concrete HarnessAdapter for the Cursor Agent CLI."""

    name: str = "cursor"
    default_model: str = "auto"
    supports_autonomous_loop = False  # CR-4

    available_models: tuple[ModelSpec, ...] = (
        ModelSpec(id="auto", is_default=True),
        ModelSpec(id="composer-2-fast"),
        ModelSpec(id="composer-2"),
        ModelSpec(id="gpt-5.5-high"),
        ModelSpec(id="gpt-5.4-high"),
        ModelSpec(id="gpt-5.3-codex-xhigh"),
        ModelSpec(id="claude-opus-4-7-thinking-high"),
        ModelSpec(id="claude-4.6-opus-high-thinking"),
        ModelSpec(id="sonnet-4-thinking"),
        ModelSpec(id="kimi-k2.5"),
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
            "cursor-agent",
            "-p",
            "--model", model,
            "--force",
            "--trust",
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
        return Event(kind="text", text=line)

    # ------------------------------------------------------------------
    # session_log_path
    # ------------------------------------------------------------------

    def session_log_path(
        self, cwd: pathlib.Path, session_id: str
    ) -> pathlib.Path | None:
        # Returns the session directory; cursor-agent writes chat files inside it.
        return pathlib.Path.home() / ".cursor" / "chats" / session_id

    def session_log_dir(self, cwd: pathlib.Path) -> pathlib.Path | None:
        # cursor-agent has no stable per-cwd session manifest dir.
        return None

    # ------------------------------------------------------------------
    # auth / capabilities
    # ------------------------------------------------------------------

    def auth_env_keys(self) -> list[str]:
        return ["CURSOR_API_KEY"]

    def supports(self) -> Capabilities:
        return Capabilities(
            supports_autonomous_loop=False,
            supports_session_resume=True,
            supports_stream_json=False,
            supports_tool_use=True,
        )
