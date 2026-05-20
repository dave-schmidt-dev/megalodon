"""Megalodon v9.1 harness adapter contract.

Defines the Protocol that all harness adapters must satisfy, plus the shared
dataclasses used at runtime.  No implementation lives here — only the contract.

CR-4 note: ``supports_autonomous_loop`` is True only for ClaudeAdapter in v9.1.
All other adapters set it to False; the autonomous-loop wrapper is out of scope
for this phase.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Shared dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Event:
    """A single parsed output event from a running harness process.

    kind: one of "text", "tool_use", "tool_result", "system", "error".
    text: human-readable text payload (may be empty for non-text events).
    raw:  original parsed dict from JSON stream, or None for plain-text lines.
    """

    kind: str
    text: str = ""
    raw: dict | None = None


@dataclass(frozen=True)
class Capabilities:
    """Feature flags for a harness adapter.

    These are static per adapter/version, not per-invocation.
    """

    supports_autonomous_loop: bool
    supports_session_resume: bool
    supports_stream_json: bool
    supports_tool_use: bool


@dataclass(frozen=True)
class ModelSpec:
    """Descriptor for a single model offered by a harness.

    id:         canonical model identifier accepted by the CLI.
    aliases:    short names the CLI also accepts (e.g. "opus", "sonnet").
    is_default: True for exactly one ModelSpec per adapter.
    """

    id: str
    aliases: tuple[str, ...] = field(default_factory=tuple)
    is_default: bool = False


# ---------------------------------------------------------------------------
# Adapter Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class HarnessAdapter(Protocol):
    """Contract every harness adapter must satisfy.

    Attributes
    ----------
    name:
        Short stable identifier — "claude" | "codex" | "gemini" | …
    default_model:
        Canonical model ID used when the caller does not specify a model.
    available_models:
        Ordered tuple of ModelSpec instances; first is typically the default.
    supports_autonomous_loop:
        CR-4 flag.  True only for ClaudeAdapter in v9.1.
    """

    name: str
    default_model: str
    available_models: tuple[ModelSpec, ...]
    supports_autonomous_loop: bool

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
        """Build CLI argv and environment overlay for a single invocation.

        Returns
        -------
        (argv, env_overlay)
            argv:        list of strings to pass to subprocess.
            env_overlay: dict of env vars to merge into os.environ before
                         spawning.  Empty dict means "no overrides".
        """
        ...

    def parse_stream_line(self, line: str) -> Event | None:
        """Parse one line of stdout from the harness process.

        Returns None for blank lines, garbage, or lines that should be
        silently discarded.
        """
        ...

    def build_followup_argv(
        self,
        prompt: str,
        *,
        prior_session_id: str | None,
        model: str,
        cwd: pathlib.Path,
        output_format: str = "text",
        extra_env: dict[str, str] | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        """Build argv for a follow-up invocation after a prior pane run completed.

        Plan section 4 Q2 contract: when ``prior_session_id`` is set and the
        adapter's CLI exposes a session-resume affordance, the returned argv
        must chain to that prior session. Otherwise the call must be
        functionally identical to ``build_argv`` (a fresh invocation).

        Default implementation lives in ``_FollowupArgvDefault`` below;
        ``ClaudeAdapter`` and ``CodexAdapter`` override directly.
        """
        ...

    def session_log_path(
        self, cwd: pathlib.Path, session_id: str
    ) -> pathlib.Path | None:
        """Return the filesystem path where this harness writes session logs.

        Returns None if the harness does not persist session logs.
        """
        ...

    def session_log_dir(self, cwd: pathlib.Path) -> pathlib.Path | None:
        """Return the parent directory where new session log entries appear.

        Used by FleetSpawner for the before/after snapshot diff that
        discovers a freshly-spawned harness's session id (CR-1, PM-6).
        Returns None for harnesses that do not persist sessions on disk.
        """
        ...

    def auth_env_keys(self) -> list[str]:
        """Return the names of env vars this adapter reads for auth."""
        ...

    def supports(self) -> Capabilities:
        """Return static capability flags for this adapter."""
        ...


# ---------------------------------------------------------------------------
# Default mixin for build_followup_argv
# ---------------------------------------------------------------------------


class _FollowupArgvDefault:
    """Mixin: forwards ``build_followup_argv`` to ``build_argv``.

    Adapters whose CLIs do not expose a session-resume affordance (gemini,
    copilot, cursor, vibe) inherit from this so a follow-up prompt behaves
    like a fresh invocation. ``prior_session_id`` is intentionally ignored —
    surfacing it as a no-op rather than an error keeps the orchestrator side
    free of per-adapter branching.
    """

    def build_followup_argv(
        self,
        prompt: str,
        *,
        prior_session_id: str | None,
        model: str,
        cwd: pathlib.Path,
        output_format: str = "text",
        extra_env: dict[str, str] | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        del prior_session_id  # default impl ignores prior session
        return self.build_argv(  # type: ignore[attr-defined]
            prompt,
            model=model,
            cwd=cwd,
            output_format=output_format,
            extra_env=extra_env,
        )
