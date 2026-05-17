"""Tests for megalodon_ui.mission_config.default_v9_0_shape (P1.2)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from megalodon_ui.mission_config.default_v9_0_shape import _synthesize_utc_started, synthesize
from megalodon_ui.mission_config.schema import MissionConfig, validate_task_id_with_config


# ─── Test 1 ───────────────────────────────────────────────────────────────────

def test_synthesize_returns_valid_mission_config(queue_mission: Path) -> None:
    """synthesize() returns a MissionConfig; round-trips through model_dump/model_validate."""
    config = synthesize(queue_mission)
    assert isinstance(config, MissionConfig)
    MissionConfig.model_validate(config.model_dump())


# ─── Test 2 ───────────────────────────────────────────────────────────────────

def test_init_is_first_phase(queue_mission: Path) -> None:
    """CR-10: INIT must be the first phase (matches index.html:23 phase-segment-INIT)."""
    config = synthesize(queue_mission)
    assert config.phases[0] == "INIT"


# ─── Test 3 ───────────────────────────────────────────────────────────────────

def test_six_canonical_lanes_in_order(queue_mission: Path) -> None:
    """Lane names and short codes are in the canonical v9.0 order."""
    config = synthesize(queue_mission)
    assert [lane.name for lane in config.lanes] == ["AUDIT", "ARCHITECT", "BACKEND", "FRONTEND", "TEST", "META"]
    assert [lane.short for lane in config.lanes] == ["A", "B", "C", "D", "E", "F"]


# ─── Test 4 ───────────────────────────────────────────────────────────────────

def test_challenge_pattern_present(queue_mission: Path) -> None:
    """CR-5: CHALLENGE-* task IDs must match at least one pattern (v9.0 hardcoded regex omitted these)."""
    config = synthesize(queue_mission)
    # Should not raise
    validate_task_id_with_config("CHALLENGE-X1Y2", config)


# ─── Test 5 ───────────────────────────────────────────────────────────────────

def test_utc_started_from_frontmatter(tmp_path: Path) -> None:
    """CR-8 precedence #1: valid utc_started in MISSION.md frontmatter is used verbatim."""
    mission_md = tmp_path / "MISSION.md"
    mission_md.write_text(
        '---\nutc_started: "2025-01-01T00:00:00Z"\n---\nBody text.\n',
        encoding="utf-8",
    )
    result = _synthesize_utc_started(tmp_path)
    assert result == "2025-01-01T00:00:00Z"

    # Malformed utc_started falls through to mtime (not the bad value itself)
    mission_md.write_text(
        '---\nutc_started: "not-a-date"\n---\nBody text.\n',
        encoding="utf-8",
    )
    result_bad = _synthesize_utc_started(tmp_path)
    assert result_bad != "not-a-date"
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", result_bad)


# ─── Test 6 ───────────────────────────────────────────────────────────────────

def test_utc_started_from_mission_md_mtime(tmp_path: Path) -> None:
    """CR-8 precedence #2: MISSION.md with no frontmatter → falls back to its mtime."""
    mission_md = tmp_path / "MISSION.md"
    mission_md.write_text("# No frontmatter here\n", encoding="utf-8")
    expected_ts = datetime.fromtimestamp(
        mission_md.stat().st_mtime, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    result = _synthesize_utc_started(tmp_path)
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", result)
    assert result == expected_ts


# ─── Test 7 ───────────────────────────────────────────────────────────────────

def test_utc_started_from_mission_events_mtime(tmp_path: Path) -> None:
    """CR-8 precedence #3: no MISSION.md but .mission-events exists → use its mtime."""
    events = tmp_path / ".mission-events"
    events.write_text("event data\n", encoding="utf-8")
    expected_ts = datetime.fromtimestamp(
        events.stat().st_mtime, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    result = _synthesize_utc_started(tmp_path)
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", result)
    assert result == expected_ts


# ─── Test 8 ───────────────────────────────────────────────────────────────────

def test_utc_started_falls_back_to_now(tmp_path: Path) -> None:
    """CR-8 precedence #4: empty dir (no MISSION.md, no .mission-events) → result is near-now."""
    before = datetime.now(timezone.utc)
    result = _synthesize_utc_started(tmp_path)
    after = datetime.now(timezone.utc)

    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", result)
    parsed = datetime.strptime(result, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    # Allow 1 second rounding slack on either side
    assert before.replace(microsecond=0) <= parsed <= after.replace(microsecond=0) + __import__("datetime").timedelta(seconds=1)
