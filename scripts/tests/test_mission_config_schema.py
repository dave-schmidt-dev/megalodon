"""Tests for megalodon_ui.mission_config.schema (Task P1.1)."""

import pytest
from pydantic import ValidationError

from megalodon_ui.mission_config import (
    MissionConfig,
    MissionInfo,
    LaneConfig,
    HarnessBinding,
    TaskIdPattern,
    _assert_no_path_traversal,
)


# ─── Helpers ─────────────────────────────────────────────────────────

def _harness(**kwargs) -> HarnessBinding:
    defaults = dict(cli="claude", model="claude-opus-4-5")
    defaults.update(kwargs)
    return HarnessBinding(**defaults)


def _lane(name: str, **kwargs) -> LaneConfig:
    defaults = dict(harness=_harness())
    defaults.update(kwargs)
    return LaneConfig(name=name, **defaults)


def _mission_info(**kwargs) -> MissionInfo:
    defaults = dict(id="test-mission", utc_started="2026-05-17T13:00:00Z")
    defaults.update(kwargs)
    return MissionInfo(**defaults)


def _minimal_config(**kwargs) -> MissionConfig:
    defaults = dict(
        mission=_mission_info(),
        lanes=[_lane("ALPHA")],
        phases=["INIT"],
    )
    defaults.update(kwargs)
    return MissionConfig(**defaults)


# ─── Tests ───────────────────────────────────────────────────────────

def test_valid_load_minimum_required():
    cfg = _minimal_config()
    dumped = cfg.model_dump()
    restored = MissionConfig.model_validate(dumped)
    assert restored.mission.id == "test-mission"
    assert restored.mission.utc_started == "2026-05-17T13:00:00Z"
    assert len(restored.lanes) == 1
    assert restored.phases == ["INIT"]


def test_duplicate_lane_names_rejected():
    with pytest.raises(ValidationError) as excinfo:
        _minimal_config(lanes=[_lane("AUDIT"), _lane("AUDIT")])
    assert "duplicate lane names" in str(excinfo.value)


def test_duplicate_phase_names_rejected():
    with pytest.raises(ValidationError) as excinfo:
        _minimal_config(phases=["INIT", "INIT"])
    assert "duplicate phase names" in str(excinfo.value)


def test_auto_assign_short_codes_1_char():
    lanes = [_lane(chr(ord("A") + i) + "LANE") for i in range(6)]
    cfg = _minimal_config(lanes=lanes)
    shorts = [lane.short for lane in cfg.lanes]
    assert shorts == ["A", "B", "C", "D", "E", "F"]


def test_auto_assign_short_codes_2_char():
    lanes = [_lane(f"LANE{i:02d}") for i in range(27)]
    cfg = _minimal_config(lanes=lanes)
    assert cfg.lanes[26].short == "AA"


def test_pattern_compile_validation():
    with pytest.raises(ValidationError):
        TaskIdPattern(patterns=["[unbalanced"])


def test_path_traversal_guard_forward_slash():
    with pytest.raises(ValueError) as excinfo:
        _assert_no_path_traversal("foo/bar")
    assert "'/'" in str(excinfo.value)


def test_path_traversal_guard_backslash():
    with pytest.raises(ValueError) as excinfo:
        _assert_no_path_traversal("foo\\bar")
    assert "'\\\\'" in str(excinfo.value)


def test_path_traversal_guard_dotdot():
    with pytest.raises(ValueError) as excinfo:
        _assert_no_path_traversal("foo..bar")
    assert "'..'" in str(excinfo.value)


def test_path_traversal_guard_null_byte():
    with pytest.raises(ValueError) as excinfo:
        _assert_no_path_traversal("foo\x00bar")
    assert "v9-1-MISSION-CONFIG.md#task-id-grammar" in str(excinfo.value)


def test_manual_short_codes_unique_required():
    lane_a = _lane("ALPHA", short="A")
    lane_b = _lane("BETA", short="A")
    with pytest.raises(ValidationError) as excinfo:
        _minimal_config(lanes=[lane_a, lane_b])
    assert "duplicate short code: A" in str(excinfo.value)


def test_schema_version_1_round_trip():
    cfg = _minimal_config()
    assert cfg.schema_version == 1
    restored = MissionConfig.model_validate(cfg.model_dump())
    assert restored.schema_version == 1


def test_orchestrator_pseudo_lane_default():
    cfg = _minimal_config()
    assert cfg.orchestrator_pseudo_lane == "ORCHESTRATOR"
    # Custom valid value
    cfg2 = _minimal_config(orchestrator_pseudo_lane="META")
    assert cfg2.orchestrator_pseudo_lane == "META"
    # Pattern: must start with uppercase letter, then uppercase alphanumeric/dash/underscore
    cfg3 = _minimal_config(orchestrator_pseudo_lane="ORCH-PRIMARY")
    assert cfg3.orchestrator_pseudo_lane == "ORCH-PRIMARY"
    # Invalid: lowercase rejected
    with pytest.raises(ValidationError):
        _minimal_config(orchestrator_pseudo_lane="meta")


def test_task_sections_default_and_override():
    cfg = _minimal_config()
    assert cfg.task_sections == ["PHASE-PLAN", "OPERATOR-ACCEPTANCE"]
    # Override with arbitrary list
    custom = ["SECTION-ONE", "SECTION-TWO", "SECTION-THREE"]
    cfg2 = _minimal_config(task_sections=custom)
    assert cfg2.task_sections == custom
    # Empty string element rejected (min_length=1)
    with pytest.raises(ValidationError):
        _minimal_config(task_sections=[""])
