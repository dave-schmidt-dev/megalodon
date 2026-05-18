"""Tests for megalodon_ui.preflight.interview — REPL state machine."""

from __future__ import annotations

import textwrap

import pytest
import yaml

from megalodon_ui.mission_config.schema import MissionConfig

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_VALID_YAML = textwrap.dedent("""\
    schema_version: 1
    mission:
      id: test-mission
      utc_started: '2026-01-01T00:00:00Z'
      type: software-engineering
      description: ''
    lanes:
      - name: BACKEND
        short: A
        role: ''
        harness:
          cli: claude
          model: claude-opus-4-7
          extra_args: []
          auth_env: []
        cadence_seconds: 300
        tick_offset_seconds: 0
    phases:
      - INIT
      - COMPLETE
    task_id_patterns:
      patterns:
        - '^[A-Z][A-Za-z0-9\\-\\.]*$'
      description: ''
    orchestrator_pseudo_lane: ORCHESTRATOR
    task_sections:
      - PHASE-PLAN
      - OPERATOR-ACCEPTANCE
    harness_rebinding_reserved: {}
""")

_UPDATED_YAML = textwrap.dedent("""\
    schema_version: 1
    mission:
      id: test-mission-updated
      utc_started: '2026-01-01T00:00:00Z'
      type: software-engineering
      description: updated
    lanes:
      - name: FRONTEND
        short: A
        role: ''
        harness:
          cli: claude
          model: claude-opus-4-7
          extra_args: []
          auth_env: []
        cadence_seconds: 300
        tick_offset_seconds: 0
    phases:
      - INIT
      - PHASE-BUILD
      - COMPLETE
    task_id_patterns:
      patterns:
        - '^[A-Z][A-Za-z0-9\\-\\.]*$'
      description: ''
    orchestrator_pseudo_lane: ORCHESTRATOR
    task_sections:
      - PHASE-PLAN
      - OPERATOR-ACCEPTANCE
    harness_rebinding_reserved: {}
""")


def _make_mock_runner(yaml_responses: list[str]):
    """Return a claude_runner that pops YAML responses from the front of the list."""
    responses = list(yaml_responses)

    def runner(argv: list[str], env_overlay: dict) -> str:
        if not responses:
            raise RuntimeError("Mock runner exhausted — no more responses queued")
        return responses.pop(0)

    return runner


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRunInterviewApprovePath:
    def test_repl_approve_path(self, monkeypatch):
        """Mock returns valid YAML; operator types 'approve'; returns (config, None)."""
        from megalodon_ui.preflight.interview import run_interview

        mock_runner = _make_mock_runner([_VALID_YAML])
        monkeypatch.setattr("builtins.input", lambda _: "approve")

        config, last_yaml = run_interview(
            goal="Build a backend service",
            preamble="",
            max_refine=10,
            claude_runner=mock_runner,
        )

        assert config is not None, "should return a config on approve"
        assert last_yaml is None, "last_yaml should be None on approve"
        assert isinstance(config, MissionConfig)
        assert config.mission.id == "test-mission"


class TestRunInterviewAbandonPath:
    def test_repl_abandon_path(self, monkeypatch):
        """Operator types 'abandon'; returns (None, last_yaml)."""
        from megalodon_ui.preflight.interview import run_interview

        mock_runner = _make_mock_runner([_VALID_YAML])
        monkeypatch.setattr("builtins.input", lambda _: "abandon")

        config, last_yaml = run_interview(
            goal="Build something",
            preamble="",
            max_refine=10,
            claude_runner=mock_runner,
        )

        assert config is None, "should return None config on abandon"
        assert last_yaml is not None, "should return last_yaml on abandon"
        # The last_yaml should parse back to the valid YAML structure
        parsed = yaml.safe_load(last_yaml)
        assert parsed["mission"]["id"] == "test-mission"


class TestRunInterviewRevisionIterates:
    def test_repl_revision_iterates(self, monkeypatch):
        """Operator types a revision; mock returns updated YAML; on approve returns updated config."""
        from megalodon_ui.preflight.interview import run_interview

        mock_runner = _make_mock_runner([_VALID_YAML, _UPDATED_YAML])

        inputs = iter(["add a FRONTEND lane please", "approve"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))

        config, last_yaml = run_interview(
            goal="Build a full-stack app",
            preamble="",
            max_refine=10,
            claude_runner=mock_runner,
        )

        assert config is not None
        assert last_yaml is None
        # Should have the updated config
        assert config.mission.id == "test-mission-updated"
        assert config.lanes[0].name == "FRONTEND"


class TestRunInterviewMaxRefineCap:
    def test_max_refine_cap_enforced(self, monkeypatch, capsys):
        """max_refine=1; operator tries to refine once; second refine attempt prints
        warning and forces approve/abandon."""
        from megalodon_ui.preflight.interview import run_interview

        # First call: initial proposal; second call: after first refinement
        mock_runner = _make_mock_runner([_VALID_YAML, _UPDATED_YAML])

        # Sequence: first input is a revision (triggers refine), second input
        # is another revision (at cap, should be rejected), third is "approve"
        inputs = iter(["please add more phases", "another revision attempt", "approve"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))

        config, last_yaml = run_interview(
            goal="Build something",
            preamble="",
            max_refine=1,
            claude_runner=mock_runner,
        )

        captured = capsys.readouterr()

        # The warning about max refinements must appear
        assert "Max refinements" in captured.out or "Max refinement" in captured.out, (
            f"Expected max refinement warning in stdout, got: {captured.out!r}"
        )

        # Should have ended with approval
        assert config is not None
        assert last_yaml is None
