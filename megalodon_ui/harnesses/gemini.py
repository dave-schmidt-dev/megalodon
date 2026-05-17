"""Megalodon v9.1 — GeminiAdapter.

Wraps the ``gemini`` CLI (v0.42.0+) for non-interactive use.

Verified invocation shape (research doc 2026-05-17):
    gemini -p "<prompt>" -m <id> --approval-mode plan
    gemini -p "<prompt>" -m <id> --approval-mode yolo   (write / destructive)

output_format "write" or "yolo" swaps --approval-mode plan → yolo.
All other output_format values use the default "plan" approval mode.

Gemini pipes plain text by default; there is no JSON-stream mode in v0.42.0.

Auth: GEMINI_API_KEY (or Google OAuth flow — not managed here).
Session logs: ~/.gemini/history/<cwd.name>/  (directory keyed to project name).
"""

from __future__ import annotations

import pathlib

from .base import Capabilities, Event, ModelSpec


class GeminiAdapter:
    """Concrete HarnessAdapter for the Google Gemini CLI."""

    name: str = "gemini"
    default_model: str = "gemini-3.1-pro-preview"
    supports_autonomous_loop = False  # CR-4

    available_models: tuple[ModelSpec, ...] = (
        ModelSpec(id="gemini-3.1-pro-preview", is_default=True),
        ModelSpec(id="gemini-3-flash-preview"),
        ModelSpec(id="gemini-3.1-flash-lite-preview"),
        ModelSpec(id="gemini-2.5-pro"),
        ModelSpec(id="gemini-2.5-flash"),
        ModelSpec(id="gemini-2.5-flash-lite"),
        ModelSpec(id="gemma-4-31b-it"),
        ModelSpec(id="gemma-4-26b-a4b-it"),
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
        approval = "yolo" if output_format in ("write", "yolo") else "plan"
        argv = [
            "gemini",
            "-p", prompt_or_launch_path,
            "-m", model,
            "--approval-mode", approval,
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
        return pathlib.Path.home() / ".gemini" / "history" / cwd.name

    # ------------------------------------------------------------------
    # auth / capabilities
    # ------------------------------------------------------------------

    def auth_env_keys(self) -> list[str]:
        return ["GEMINI_API_KEY"]

    def supports(self) -> Capabilities:
        return Capabilities(
            supports_autonomous_loop=False,
            supports_session_resume=False,
            supports_stream_json=False,
            supports_tool_use=True,
        )
