"""Tests for scripts/_validation.py — Codex CR-4 regex coverage."""

import pytest

from scripts._validation import (
    LANE_LONG_TO_SHORT,
    validate_agent,
    validate_lane,
    validate_notes,
    validate_severity,
    validate_summary,
    validate_task_id,
)


@pytest.mark.parametrize("task_id", [
    "P1-A", "P2.5-B", "P2-A-to-F", "P5-RUN-MUTATIONS-E2E",
    "REPAIR-MUTATIONS-E2E-3-ACTION-PANEL", "OPERATOR-ACCEPTANCE-REQUEST",
    "S-8",
])
def test_task_id_accepts_cr4_inventory(task_id):
    validate_task_id(task_id)  # raises if invalid


@pytest.mark.parametrize("bad", [
    "", "p1-a", "P1-Z", "P1-A; rm -rf /", "P1-A && echo",
    "../etc/passwd", "P1-A`whoami`",
])
def test_task_id_rejects_invalid(bad):
    with pytest.raises(ValueError):
        validate_task_id(bad)


@pytest.mark.parametrize("lane", ["AUDIT", "ARCHITECT", "BACKEND", "FRONTEND", "TEST", "META"])
def test_lane_accepts_valid(lane):
    validate_lane(lane)


@pytest.mark.parametrize("bad", ["audit", "A", "LANE-A", "OTHER", "", "AUDIT;"])
def test_lane_rejects_invalid(bad):
    with pytest.raises(ValueError):
        validate_lane(bad)


def test_lane_long_to_short_map_complete():
    assert LANE_LONG_TO_SHORT == {
        "AUDIT": "A", "ARCHITECT": "B", "BACKEND": "C",
        "FRONTEND": "D", "TEST": "E", "META": "F",
    }


@pytest.mark.parametrize("agent", ["agent-abcd", "agent-0123", "agent-dead", "agent-9bba"])
def test_agent_accepts_valid(agent):
    validate_agent(agent)


@pytest.mark.parametrize("bad", ["agent-ABCD", "agent-12345", "agent-abc", "agent_abcd", ""])
def test_agent_rejects_invalid(bad):
    with pytest.raises(ValueError):
        validate_agent(bad)


@pytest.mark.parametrize("sev", [
    "DELTA", "NIT", "MAJOR", "BLOCKING", "TIER-1", "TIER-2",
    "MEDIUM", "MINOR", "TERMINAL", "RECOVERY", "EXEC-PASS", "BLOCKED-DEGRADED",
])
def test_severity_accepts_valid(sev):
    validate_severity(sev)


def test_severity_rejects_invalid():
    with pytest.raises(ValueError):
        validate_severity("CRITICAL")


def test_notes_accepts_normal():
    validate_notes("Run-2 closed degraded. 7/16 e2e. Operator-acked.")


def test_notes_rejects_shell_meta():
    for bad in ["foo `whoami`", "foo $HOME", "foo; rm", "foo | grep", "foo > /tmp"]:
        with pytest.raises(ValueError):
            validate_notes(bad)


def test_notes_rejects_overlong():
    with pytest.raises(ValueError):
        validate_notes("x" * 2001)


def test_summary_rejects_overlong():
    with pytest.raises(ValueError):
        validate_summary("x" * 201)
