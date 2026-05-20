"""P6.1 — ClaudeAdapter.build_followup_argv.

Contract (plan §4 Q2): the follow-up invocation must chain to the prior
session via ``claude --resume <prior_session_id>`` when a prior session id
is available. Without one, it behaves like ``build_argv`` (the dashboard
starts a fresh conversation).

The whole point of the respawn-style follow-up flow is that the operator
keeps the same conversation thread across multiple prompts. Losing
``--resume`` here would silently break that property: every follow-up
would start a fresh session, and the operator's context would vanish on
the next prompt with no error to point at.
"""

from __future__ import annotations

import pathlib

import pytest

from megalodon_ui.harnesses.claude import ClaudeAdapter


CWD = pathlib.Path("/tmp/cwd")
MODEL = "claude-opus-4-7"


def test_build_followup_argv_with_prior_session_id_includes_resume() -> None:
    adapter = ClaudeAdapter()
    argv, env = adapter.build_followup_argv(
        "tell me more",
        prior_session_id="abc-123-def",
        model=MODEL,
        cwd=CWD,
    )
    assert "--resume" in argv, f"argv must carry --resume, got {argv!r}"
    resume_idx = argv.index("--resume")
    assert argv[resume_idx + 1] == "abc-123-def", (
        f"argv[--resume+1] must be the prior_session_id, got {argv[resume_idx+1]!r}"
    )
    # prompt is preserved
    assert "tell me more" in argv
    # env overlay still empty (ANTHROPIC_API_KEY assumed in caller env)
    assert env == {}


def test_build_followup_argv_without_prior_session_id_omits_resume() -> None:
    adapter = ClaudeAdapter()
    argv, _ = adapter.build_followup_argv(
        "fresh prompt",
        prior_session_id=None,
        model=MODEL,
        cwd=CWD,
    )
    assert "--resume" not in argv
    assert "fresh prompt" in argv


@pytest.mark.parametrize("output_format", ["text", "stream-json"])
def test_build_followup_argv_respects_output_format(output_format: str) -> None:
    adapter = ClaudeAdapter()
    argv, _ = adapter.build_followup_argv(
        "p",
        prior_session_id="sid",
        model=MODEL,
        cwd=CWD,
        output_format=output_format,
    )
    if output_format == "stream-json":
        assert "stream-json" in argv
    else:
        assert "stream-json" not in argv


def test_build_followup_argv_preserves_model_flag() -> None:
    adapter = ClaudeAdapter()
    argv, _ = adapter.build_followup_argv(
        "p",
        prior_session_id="sid",
        model="claude-sonnet-4-6",
        cwd=CWD,
    )
    # --model must appear and precede the model id
    assert "--model" in argv
    m_idx = argv.index("--model")
    assert argv[m_idx + 1] == "claude-sonnet-4-6"


def test_build_followup_argv_with_empty_prior_session_id_treated_as_none() -> None:
    """Empty-string prior_session_id should behave like None (no --resume)."""
    adapter = ClaudeAdapter()
    argv, _ = adapter.build_followup_argv(
        "p",
        prior_session_id="",
        model=MODEL,
        cwd=CWD,
    )
    assert "--resume" not in argv
