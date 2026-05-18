"""Megalodon v9.1 harness adapters package."""
from __future__ import annotations

from functools import lru_cache

from .base import HarnessAdapter


@lru_cache(maxsize=1)
def _build_registry() -> dict[str, HarnessAdapter]:
    from .claude import ClaudeAdapter
    from .codex import CodexAdapter
    from .copilot import CopilotAdapter
    from .cursor import CursorAdapter
    from .gemini import GeminiAdapter
    from .vibe import VibeAdapter

    return {
        "claude": ClaudeAdapter(),
        "codex": CodexAdapter(),
        "gemini": GeminiAdapter(),
        "copilot": CopilotAdapter(),
        "vibe": VibeAdapter(),
        "cursor": CursorAdapter(),
    }


def get_adapter(cli_name: str) -> HarnessAdapter:
    """Return a HarnessAdapter instance for the given CLI name.

    Adapters are stateless and the registry is cached, so callers in a tight
    loop (e.g. FleetSpawner.start_all) pay the construction cost once.
    Raises KeyError if cli_name is not registered.
    """
    registry = _build_registry()
    if cli_name not in registry:
        raise KeyError(f"unknown harness adapter: {cli_name!r}")
    return registry[cli_name]
