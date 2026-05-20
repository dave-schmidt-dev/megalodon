"""P6.1 — GeminiAdapter.build_followup_argv default behavior.

Gemini, Copilot, Cursor, and Vibe adapters do not override the Protocol
default, so their `build_followup_argv` must match what `build_argv`
returns. The prior_session_id is ignored — those CLIs do not expose a
session-resume affordance v9.2 can hook into.

If we ever lose this property (e.g., a future change adds a stray flag in
the default impl), follow-up prompts on these adapters would diverge from
the initial spawn — a silent functional regression. This test is the
tripwire.
"""

from __future__ import annotations

import pathlib

import pytest

from megalodon_ui.harnesses.gemini import GeminiAdapter


CWD = pathlib.Path("/tmp/cwd")
MODEL = "gemini-2.5-pro"


def test_followup_matches_build_argv_for_default_inheriting_adapter() -> None:
    adapter = GeminiAdapter()
    fresh_argv, fresh_env = adapter.build_argv("hello", model=MODEL, cwd=CWD)
    followup_argv, followup_env = adapter.build_followup_argv(
        "hello",
        prior_session_id="ignored-sid",
        model=MODEL,
        cwd=CWD,
    )
    assert followup_argv == fresh_argv
    assert followup_env == fresh_env


def test_followup_ignores_prior_session_id_for_gemini() -> None:
    adapter = GeminiAdapter()
    a, _ = adapter.build_followup_argv(
        "p", prior_session_id="sid-A", model=MODEL, cwd=CWD
    )
    b, _ = adapter.build_followup_argv(
        "p", prior_session_id="sid-B", model=MODEL, cwd=CWD
    )
    c, _ = adapter.build_followup_argv("p", prior_session_id=None, model=MODEL, cwd=CWD)
    assert a == b == c, "gemini ignores prior_session_id; argv must be identical"


@pytest.mark.parametrize(
    "adapter_module,adapter_class,default_model",
    [
        ("megalodon_ui.harnesses.gemini", "GeminiAdapter", "gemini-2.5-pro"),
        ("megalodon_ui.harnesses.copilot", "CopilotAdapter", None),
        ("megalodon_ui.harnesses.cursor", "CursorAdapter", None),
        ("megalodon_ui.harnesses.vibe", "VibeAdapter", None),
    ],
)
def test_default_adapters_follow_build_argv_for_followup(
    adapter_module, adapter_class, default_model
):
    """Every non-claude / non-codex adapter must forward build_followup_argv to build_argv."""
    import importlib

    mod = importlib.import_module(adapter_module)
    AdapterCls = getattr(mod, adapter_class)
    adapter = AdapterCls()
    model = default_model or adapter.default_model
    fresh, _ = adapter.build_argv("ping", model=model, cwd=CWD)
    followup, _ = adapter.build_followup_argv(
        "ping",
        prior_session_id="any-sid",
        model=model,
        cwd=CWD,
    )
    assert followup == fresh, (
        f"{adapter_class}.build_followup_argv diverged from build_argv: "
        f"fresh={fresh!r} followup={followup!r}"
    )
