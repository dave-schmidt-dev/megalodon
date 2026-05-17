"""V9 A1 — watchdog alert manager + integration test."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from megalodon_ui.watchdog.alerts import AlertManager


def test_alert_writes_finding_with_frontmatter(tmp_path):
    mgr = AlertManager(tmp_path)
    path = mgr.alert("AUDIT", "CRASHED", evidence=["pid 12345 dead"])
    text = path.read_text(encoding="utf-8")
    assert "signal-type: WATCHDOG-ALERT" in text
    assert "lane: AUDIT" in text
    assert "alert-type: CRASHED" in text


def test_alert_dedup_suppresses_duplicate(tmp_path):
    mgr = AlertManager(tmp_path)
    p1 = mgr.alert("AUDIT", "CRASHED", evidence=["pid dead"])
    p2 = mgr.alert("AUDIT", "CRASHED", evidence=["pid dead"])
    assert p1 is not None
    assert p2 is None  # Dedup


def test_alert_clears_on_recovery(tmp_path):
    mgr = AlertManager(tmp_path)
    mgr.alert("AUDIT", "CRASHED", evidence=[])
    mgr.recover("AUDIT")
    p2 = mgr.alert("AUDIT", "CRASHED", evidence=[])
    assert p2 is not None  # Cleared dedup, alert fires again


def test_state_file_persisted_atomically(tmp_path):
    mgr = AlertManager(tmp_path)
    mgr.alert("AUDIT", "CRASHED", evidence=[])
    state_file = tmp_path / ".scratch" / "watchdog" / "state.json"
    assert state_file.exists()
    state = json.loads(state_file.read_text())
    assert state["lanes"]["AUDIT"]["last_alert_type"] == "CRASHED"


def test_daemon_poll_alerts_on_stale_status(tmp_path, monkeypatch):
    """Integration: stale STATUS row → poll_once writes an alert finding."""
    from megalodon_ui.watchdog.daemon import poll_once
    from megalodon_ui.watchdog.alerts import AlertManager
    from datetime import datetime, timezone, timedelta

    mission = tmp_path / "m"
    mission.mkdir()
    (mission / "findings").mkdir()
    (mission / ".scratch").mkdir()
    status = mission / "STATUS.md"
    # Build a STATUS.md with a stale row for AUDIT
    old = (datetime.now(timezone.utc) - timedelta(minutes=20)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    status.write_text(
        f"| AUDIT | agent-aaaa | working: x | {old} | foo |\n"
        f"| ARCHITECT | - | idle | {old} | - |\n"
    )

    # Pretend no pids known (skip S1 + S3)
    monkeypatch.setattr("megalodon_ui.watchdog.daemon._read_pid", lambda lane: None)

    alerts = AlertManager(mission)
    poll_once(mission, alerts, cadence_seconds=300)

    alerts_written = list((mission / "findings").glob("watchdog-ALERT-*.md"))
    assert len(alerts_written) >= 1
    assert any("AUDIT" in p.name for p in alerts_written)
