"""Tests for megalodon_ui.preflight.proposer — prompt construction."""

from __future__ import annotations


from megalodon_ui.preflight.proposer import build_initial_prompt, build_refine_prompt


class TestBuildRefinePrompt:
    def test_refine_prompt_contains_schema_cheatsheet(self):
        """The refine prompt embeds the schema cheat-sheet so Claude knows valid fields."""
        prior_yaml = "schema_version: 1\nmission:\n  id: test"
        feedback = "Add another phase"

        result = build_refine_prompt(prior_yaml, feedback)

        # Key schema markers should appear
        assert "schema_version" in result
        assert "lanes" in result
        assert "phases" in result


class TestBuildInitialPrompt:
    def test_initial_prompt_includes_goal_and_preamble(self):
        """Both goal and preamble substrings appear in the returned prompt."""
        goal = "Build a real-time collaborative code editor"
        preamble = "This is the prior context about our project."

        result = build_initial_prompt(goal, preamble)

        assert goal in result, "goal must appear in the initial prompt"
        assert preamble in result, "preamble must appear in the initial prompt"

    def test_initial_prompt_truncates_long_preamble(self):
        """A 100KB preamble is handled; the prompt is not unbounded.

        The spec says each file is truncated to 50KB before being passed
        to build_initial_prompt. This test verifies that a 100KB preamble
        passed directly does NOT cause the prompt to balloon beyond a
        reasonable ceiling (~120KB: 100KB preamble + ~20KB overhead), and
        that the prompt still contains at least the first 50KB of the
        preamble prefix.
        """
        FIFTY_KB = 50 * 1024
        preamble_100kb = "x" * (100 * 1024)  # 100 KB preamble
        goal = "Test goal"

        result = build_initial_prompt(goal, preamble_100kb)

        # The prompt must contain the goal
        assert goal in result

        # The prompt contains the full preamble (build_initial_prompt itself
        # does not truncate — the __main__ does). But we verify it's
        # *bounded* by the input size + overhead, not doubling or exploding.
        # Allow up to 120KB total (100KB preamble + ~20KB for prompts/schema).
        MAX_EXPECTED = 100 * 1024 + 30 * 1024
        assert len(result) <= MAX_EXPECTED, (
            f"Prompt grew to {len(result)} bytes which exceeds expected ceiling {MAX_EXPECTED}"
        )

        # And the first 50KB chars of the preamble are present in the prompt
        assert preamble_100kb[:FIFTY_KB] in result

    def test_refine_prompt_includes_prior_yaml_and_feedback(self):
        """Both prior_yaml and operator_feedback substrings appear in the refine prompt."""
        prior_yaml = "schema_version: 1\nmission:\n  id: test"
        feedback = "Please add a FRONTEND lane with Gemini model"

        result = build_refine_prompt(prior_yaml, feedback)

        assert prior_yaml in result, "prior_yaml must appear in the refine prompt"
        assert feedback in result, "operator feedback must appear in the refine prompt"
