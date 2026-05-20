"""P6.1 — CodexAdapter.build_followup_argv.

Contract (plan section 4 Q2 + CR-2): when chaining to a prior session, the
codex CLI takes a different subcommand shape than the initial run.

The fresh-invocation shape is `codex` then `exec` then flags + prompt.
The follow-up shape is `codex` then `exec` then `resume` then the prior
session id then the prompt.

Without a prior session id, the adapter falls back to the fresh shape via
`build_argv`.

Implementer note (CR-2): verify the resume subcommand shape against the
codex help output on commit day. The CLI grew this subcommand in v0.130
and the shape may evolve.
"""

from __future__ import annotations

import pathlib

import pytest

from megalodon_ui.harnesses.codex import CodexAdapter


CWD = pathlib.Path("/tmp/cwd")
MODEL = "gpt-5.5"


def test_build_followup_argv_with_prior_session_id_uses_resume_subcommand() -> None:
    adapter = CodexAdapter()
    argv, env = adapter.build_followup_argv(
        "continue please",
        prior_session_id="codex-sess-9",
        model=MODEL,
        cwd=CWD,
    )
    assert argv[0] == "codex"
    assert argv[1] == "exec"
    assert argv[2] == "resume"
    assert "codex-sess-9" in argv
    assert "continue please" in argv
    # Order matters: <sid> must precede the prompt or codex interprets
    # the prompt as the session id.
    assert argv.index("codex-sess-9") < argv.index("continue please")
    assert env == {}


def test_build_followup_argv_without_prior_session_id_falls_back_to_fresh() -> None:
    adapter = CodexAdapter()
    argv, _ = adapter.build_followup_argv(
        "fresh",
        prior_session_id=None,
        model=MODEL,
        cwd=CWD,
    )
    # No prior session -> behave like build_argv (no resume subcommand).
    assert "resume" not in argv
    assert argv[0] == "codex"
    assert argv[1] == "exec"
    assert "fresh" in argv


@pytest.mark.parametrize("empty", ["", None])
def test_build_followup_argv_with_empty_or_none_session_id_is_fresh(empty) -> None:
    adapter = CodexAdapter()
    argv, _ = adapter.build_followup_argv(
        "p",
        prior_session_id=empty,
        model=MODEL,
        cwd=CWD,
    )
    assert "resume" not in argv
