"""V9 M6 — tests for intent-declared parsing + expiry detection."""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts._intent_expired import is_expired, parse_intent


def test_parse_intent_valid():
    notes = "intent-declared: REPAIR-5 @ 2026-05-17T00:00:00Z walltime: 20m"
    intent = parse_intent(notes)
    assert intent["task_id"] == "REPAIR-5"
    assert intent["walltime_minutes"] == 20


def test_parse_intent_missing_walltime_defaults_12():
    notes = "intent-declared: REPAIR-5 @ 2026-05-17T00:00:00Z"
    intent = parse_intent(notes)
    assert intent["walltime_minutes"] == 12


def test_parse_intent_no_intent_returns_none():
    assert parse_intent("just regular notes") is None
    assert parse_intent("") is None


def test_is_expired_true_after_threshold():
    intent = {
        "task_id": "X",
        "declared_utc": "2026-05-17T00:00:00Z",
        "walltime_minutes": 12,
    }
    now = datetime(2026, 5, 17, 0, 18, 0, tzinfo=timezone.utc)  # 18 min later
    assert is_expired(intent, now) is True


def test_is_expired_false_before_threshold():
    intent = {
        "task_id": "X",
        "declared_utc": "2026-05-17T00:00:00Z",
        "walltime_minutes": 12,
    }
    now = datetime(2026, 5, 17, 0, 10, 0, tzinfo=timezone.utc)
    assert is_expired(intent, now) is False


def test_is_expired_walltime_extends_threshold():
    intent = {
        "task_id": "X",
        "declared_utc": "2026-05-17T00:00:00Z",
        "walltime_minutes": 30,
    }
    now = datetime(2026, 5, 17, 0, 34, 0, tzinfo=timezone.utc)
    assert is_expired(intent, now) is False  # 34 < 30+5 = 35


def test_is_expired_at_boundary():
    intent = {
        "task_id": "X",
        "declared_utc": "2026-05-17T00:00:00Z",
        "walltime_minutes": 12,
    }
    # 12 min boundary (max(12, 12+5) = 17 minutes); 18 min later → expired.
    now = datetime(2026, 5, 17, 0, 18, 0, tzinfo=timezone.utc)
    assert is_expired(intent, now) is True


def test_parse_complex_task_ids():
    notes = "intent-declared: REPAIR-MUTATIONS-E2E-3-ACTION-PANEL @ 2026-05-17T00:00:00Z"
    intent = parse_intent(notes)
    assert intent["task_id"] == "REPAIR-MUTATIONS-E2E-3-ACTION-PANEL"
