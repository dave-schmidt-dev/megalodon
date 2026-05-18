"""Stub HarnessAdapter for tests — no real harness binary required."""

from __future__ import annotations

from pathlib import Path

from megalodon_ui.harnesses.base import Capabilities, Event, ModelSpec


class StubAdapter:
    """No-op test adapter; spawns stub_harness.sh."""

    name: str = "stub"
    default_model: str = "stub-happy"
    available_models: tuple[ModelSpec, ...] = (
        ModelSpec(id="stub-happy", is_default=True),
        ModelSpec(id="stub-error"),
        ModelSpec(id="stub-long"),
    )
    supports_autonomous_loop: bool = False

    def build_argv(
        self,
        prompt: str,
        *,
        model: str,
        cwd: Path,
        **_,
    ) -> tuple[list[str], dict[str, str]]:
        script = Path(__file__).parent / "stub_harness.sh"
        mode = model.removeprefix("stub-")
        return [str(script), mode], {}

    def build_followup_argv(
        self,
        prompt: str,
        *,
        prior_session_id: str | None,
        model: str,
        cwd: Path,
        **_,
    ) -> tuple[list[str], dict[str, str]]:
        script = Path(__file__).parent / "stub_harness.sh"
        return [str(script), "followup-aware", prompt], {}

    def parse_stream_line(self, line: str) -> Event | None:
        line = line.rstrip("\n")
        return Event(kind="text", text=line) if line else None

    def session_log_path(self, cwd: Path, session_id: str) -> Path | None:
        return None

    def auth_env_keys(self) -> list[str]:
        return []

    def supports(self) -> Capabilities:
        return Capabilities(
            supports_autonomous_loop=False,
            supports_session_resume=False,
            supports_stream_json=True,
            supports_tool_use=False,
        )
