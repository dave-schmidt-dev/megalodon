"""V9 A4 — tests for deterministic agent IDs."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts._agent_id import deterministic_agent_id


def test_returns_agent_prefix_plus_4_hex():
    aid = deterministic_agent_id("mission-1", "AUDIT", "2026-05-17T00:00:00Z")
    assert aid.startswith("agent-")
    assert len(aid) == 6 + 4
    for c in aid[6:]:
        assert c in "0123456789abcdef"


def test_same_inputs_same_id():
    a = deterministic_agent_id("mission-1", "AUDIT", "2026-05-17T00:00:00Z")
    b = deterministic_agent_id("mission-1", "AUDIT", "2026-05-17T00:00:00Z")
    assert a == b


def test_different_mission_different_id():
    a = deterministic_agent_id("mission-1", "AUDIT", "2026-05-17T00:00:00Z")
    b = deterministic_agent_id("mission-2", "AUDIT", "2026-05-17T00:00:00Z")
    assert a != b


def test_different_lane_different_id():
    a = deterministic_agent_id("m", "AUDIT", "2026-05-17T00:00:00Z")
    b = deterministic_agent_id("m", "BACKEND", "2026-05-17T00:00:00Z")
    assert a != b


def test_different_utc_different_id():
    a = deterministic_agent_id("m", "AUDIT", "2026-05-17T00:00:00Z")
    b = deterministic_agent_id("m", "AUDIT", "2026-05-17T00:01:00Z")
    assert a != b
