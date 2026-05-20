"""V9 A9 — tests for worker-side fleet tick ledger emission."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts._fleet_tick import record_tick, _next_tick_number


def test_first_tick_is_n_1(tmp_path):
    path = record_tick(tmp_path, lane="AUDIT", agent="agent-aaaa")
    data = json.loads(path.read_text())
    assert data["tick_number"] == 1


def test_tick_increments_per_lane(tmp_path):
    record_tick(
        tmp_path, lane="AUDIT", agent="a", tick_started_utc="2026-01-01T00:00:00Z"
    )
    record_tick(
        tmp_path, lane="AUDIT", agent="a", tick_started_utc="2026-01-01T00:01:00Z"
    )
    n = _next_tick_number(tmp_path / ".fleet-ledger", "AUDIT")
    assert n == 3


def test_tick_idempotent_same_n_utc(tmp_path):
    p1 = record_tick(
        tmp_path,
        lane="AUDIT",
        agent="a",
        tick_number=1,
        tick_started_utc="2026-01-01T00:00:00Z",
        custom_field="first",
    )
    p2 = record_tick(
        tmp_path,
        lane="AUDIT",
        agent="a",
        tick_number=1,
        tick_started_utc="2026-01-01T00:00:00Z",
        custom_field="second",
    )
    assert p1 == p2
    data = json.loads(p1.read_text())
    assert data["custom_field"] == "first"  # First write wins


def test_atomic_write(tmp_path):
    record_tick(
        tmp_path, lane="AUDIT", agent="a", tick_started_utc="2026-01-01T00:00:00Z"
    )
    # No .tmp leftover
    tmp_leftover = list((tmp_path / ".fleet-ledger").glob("*.tmp"))
    assert tmp_leftover == []


def test_fields_persisted(tmp_path):
    path = record_tick(
        tmp_path,
        lane="AUDIT",
        agent="agent-x",
        tick_started_utc="2026-01-01T00:00:00Z",
        walltime_seconds=30,
        tasks_completed=["P5-A"],
        cas_retries=2,
    )
    data = json.loads(path.read_text())
    assert data["walltime_seconds"] == 30
    assert data["tasks_completed"] == ["P5-A"]
    assert data["cas_retries"] == 2


def test_independent_lanes_independent_counters(tmp_path):
    record_tick(
        tmp_path, lane="AUDIT", agent="a", tick_started_utc="2026-01-01T00:00:00Z"
    )
    record_tick(
        tmp_path, lane="AUDIT", agent="a", tick_started_utc="2026-01-01T00:01:00Z"
    )
    record_tick(
        tmp_path, lane="BACKEND", agent="b", tick_started_utc="2026-01-01T00:02:00Z"
    )
    n_audit = _next_tick_number(tmp_path / ".fleet-ledger", "AUDIT")
    n_backend = _next_tick_number(tmp_path / ".fleet-ledger", "BACKEND")
    assert n_audit == 3
    assert n_backend == 2
