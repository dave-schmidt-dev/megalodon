"""Megalodon v9.1 — VibeAdapter (EXPERIMENTAL — best-effort v9.1 support).

Wraps the ``vibe`` CLI (Mistral Vibe) for non-interactive use.

EXPERIMENTAL — best-effort v9.1 support.

vibe has no --model flag in v9.1; the model is selected via
~/.vibe/config.toml ``active_model =``. v9.1 documents this limitation.

The ``model`` argument passed to ``build_argv`` is silently ignored — vibe
does not accept it via CLI.  Callers should set the desired model in
~/.vibe/config.toml before invoking.

Verified invocation shape (research doc 2026-05-17):
    vibe --prompt "<prompt>" --agent auto-approve --output json

stream-json: vibe outputs JSON by default; ``parse_stream_line`` attempts
JSON parse first and falls back to plain text.

Auth: MISTRAL_API_KEY.

Session logs: ~/.vibe/sessions/<session_id>/ (directory).
"""

from __future__ import annotations

import json
import pathlib

from .base import Capabilities, Event, ModelSpec, _FollowupArgvDefault


class VibeAdapter(_FollowupArgvDefault):
    """Concrete HarnessAdapter for the Mistral Vibe CLI."""

    name: str = "vibe"
    default_model: str = "mistral-medium-3.5"
    supports_autonomous_loop = False  # CR-4

    available_models: tuple[ModelSpec, ...] = (
        ModelSpec(id="mistral-medium-3.5", is_default=True),
        ModelSpec(id="mistral-large-2"),
        ModelSpec(id="codestral-25.08"),
        ModelSpec(id="devstral-2-large"),
        ModelSpec(id="devstral-2-small"),
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
        # model is silently ignored — vibe has no --model flag in v9.1.
        # See module docstring for the config.toml workaround.
        argv = [
            "vibe",
            "--prompt", prompt_or_launch_path,
            "--agent", "auto-approve",
            "--output", "json",
        ]
        return argv, {}

    # ------------------------------------------------------------------
    # parse_stream_line
    # ------------------------------------------------------------------

    def parse_stream_line(self, line: str) -> Event | None:
        line = line.rstrip("\n")
        if not line.strip():
            return None
        # vibe outputs JSON by default; try JSON parse first, fall back to text.
        if line.lstrip().startswith("{"):
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    text = str(parsed.get("text", parsed))
                    return Event(kind="text", text=text, raw=parsed)
            except json.JSONDecodeError:
                pass
        return Event(kind="text", text=line)

    # ------------------------------------------------------------------
    # session_log_path
    # ------------------------------------------------------------------

    def session_log_path(
        self, cwd: pathlib.Path, session_id: str
    ) -> pathlib.Path | None:
        # Returns the session directory; vibe writes session files inside it.
        return pathlib.Path.home() / ".vibe" / "sessions" / session_id

    def session_log_dir(self, cwd: pathlib.Path) -> pathlib.Path | None:
        # vibe has no pre-spawn session manifest surface for snapshot diff.
        return None

    # ------------------------------------------------------------------
    # auth / capabilities
    # ------------------------------------------------------------------

    def auth_env_keys(self) -> list[str]:
        return ["MISTRAL_API_KEY"]

    def supports(self) -> Capabilities:
        return Capabilities(
            supports_autonomous_loop=False,
            supports_session_resume=False,
            supports_stream_json=True,
            supports_tool_use=False,
        )
