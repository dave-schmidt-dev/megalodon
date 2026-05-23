"""Regression tests for the v9.3 live-REPL /loop bootstrap prompt.

Root cause this guards (v94h dogfood): a bare ``launch-<NAME>.md`` filename in
the bootstrap prompt makes the spawned agent run ``ls``/``find`` to locate the
file before reading it. Under the hardened tool surface those gate to a
permission prompt that stalls the lane indefinitely when the operator is AFK.
The fix is a ``./``-prefixed, cwd-relative path the Read tool resolves with no
shell (the agent is spawned with cwd = mission dir, which Claude Code injects
into its environment). The prompt must also stay under the ~57-char
paste-detection ceiling, or ``tmux send-keys`` buffers it as a "[Pasted text]"
placeholder and the bootstrap never fires.
"""

from __future__ import annotations

from pathlib import Path

from megalodon_ui.mission_config.default_v9_3_live_repl import synthesize

# Observed-safe ceiling: above this, Claude Code's TUI treats the send-keys
# burst as a paste and the /loop never submits. Keep margin; do not raise
# without re-verifying live bootstrap behavior.
_PASTE_CEILING = 57

_TMPL = (
    Path(__file__).resolve().parents[2]
    / "templates"
    / "run"
    / ".mission-config.yaml.tmpl"
)


def test_loop_prompt_uses_cwd_relative_path(queue_mission: Path) -> None:
    """Each lane's bootstrap prompt points at ./launch-<NAME>.md, never a bare name."""
    config = synthesize(queue_mission)
    for lane in config.lanes:
        prompt = lane.initial_prompt
        assert prompt is not None, f"{lane.name}: no initial_prompt"
        assert prompt.startswith("/loop "), f"{lane.name}: must start with /loop"
        assert f"./launch-{lane.name}.md" in prompt, (
            f"{lane.name}: prompt must reference the cwd-relative path "
            f"./launch-{lane.name}.md, got: {prompt!r}"
        )
        # Regression guard: a bare " launch-<NAME>.md" (space-prefixed, no ./)
        # is exactly the form that triggers the orienting ls/find.
        assert f" launch-{lane.name}.md" not in prompt, (
            f"{lane.name}: bare filename (no ./) reintroduces the find-probe bug"
        )


def test_loop_prompt_under_paste_ceiling(queue_mission: Path) -> None:
    """Every lane's prompt stays under the send-keys paste-detection ceiling."""
    config = synthesize(queue_mission)
    for lane in config.lanes:
        n = len(lane.initial_prompt or "")
        assert n <= _PASTE_CEILING, (
            f"{lane.name}: prompt is {n} chars (> {_PASTE_CEILING}); send-keys "
            f"will buffer it as a paste and the bootstrap will not fire"
        )


def test_factory_and_template_prompts_match(queue_mission: Path) -> None:
    """The static .tmpl and the factory emit identical bootstrap prompts (no drift)."""
    config = synthesize(queue_mission)
    tmpl_text = _TMPL.read_text(encoding="utf-8")
    for lane in config.lanes:
        line = f"initial_prompt: {lane.initial_prompt}"
        assert line in tmpl_text, (
            f"{lane.name}: factory prompt not found verbatim in {_TMPL.name} "
            f"(template drifted from factory): expected line {line!r}"
        )
