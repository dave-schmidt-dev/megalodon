"""CV-3 / WR-3 — watchdog behaviour for non-Claude lanes.

CV-3: S2 (STATUS row stale) must fire for non-Claude lanes exactly as it does
      for Claude lanes.
WR-3: S3 (JSONL log stale) must be skipped for non-Claude lanes; the known
      limitation is documented in daemon.py and announced at startup.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.mission_config.schema import (
    HarnessBinding,
    LaneConfig,
    MissionConfig,
    MissionInfo,
    TaskIdPattern,
)
from megalodon_ui.watchdog.alerts import AlertManager
from megalodon_ui.watchdog.daemon import poll_once, _emit_wr3_warnings


def _make_codex_config() -> tuple[MissionConfig, list[LaneConfig]]:
    """Build a minimal MissionConfig with a single Codex-bound lane."""
    codex_lane = LaneConfig(
        name="CODEX",
        short="X",
        harness=HarnessBinding(cli="codex", model="o4-mini"),
        cadence_seconds=300,
    )
    config = MissionConfig(
        mission=MissionInfo(
            id="test-mission",
            utc_started="2026-01-01T00:00:00Z",
        ),
        lanes=[codex_lane],
        phases=["INIT", "PHASE-BUILD"],
        task_id_patterns=TaskIdPattern(patterns=[r"^[A-Z][A-Za-z0-9\-\.]*$"]),
    )
    return config, list(config.lanes)


def _stale_utc(minutes: int = 20) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def test_s2_stale_status_alerts_for_non_claude_lane(tmp_path, monkeypatch):
    """CV-3: S2 (STATUS row stale) fires for non-Claude lanes too.

    Build a MissionConfig with one Codex-bound lane; simulate a STATUS.md row
    with stale last_utc; assert the watchdog's S2 detector emits an alert for
    that lane. JSONL detector (S3) is intentionally skipped per WR-3.
    """
    mission = tmp_path / "m"
    mission.mkdir()
    (mission / "findings").mkdir()
    (mission / ".scratch").mkdir()

    status = mission / "STATUS.md"
    status.write_text(
        f"| CODEX | agent-codex | working: task | {_stale_utc(20)} | foo |\n"
    )

    # Patch _read_pid so S1 and S3 are bypassed (no pid → S1 skipped, S3 gated on pid)
    monkeypatch.setattr("megalodon_ui.watchdog.daemon._read_pid", lambda lane: None)

    _, lanes = _make_codex_config()

    alerts = AlertManager(mission)
    poll_once(mission, alerts, cadence_seconds=300, lanes=lanes)

    written = list((mission / "findings").glob("watchdog-ALERT-*.md"))
    assert len(written) == 1, f"expected 1 alert, got {len(written)}: {written}"
    alert_text = written[0].read_text()
    assert "lane: CODEX" in alert_text
    assert "alert-type: STATUS-STALE" in alert_text


def test_s3_jsonl_detector_skipped_for_non_claude_lane(tmp_path, monkeypatch, capsys):
    """WR-3: S3 (JSONL log stale) is skipped for non-Claude lanes.

    Even with NO JSONL file present for the Codex lane, no S3 alert is emitted.
    The startup warning for the WR-3 skip appears on stderr.
    """
    mission = tmp_path / "m"
    mission.mkdir()
    (mission / "findings").mkdir()
    (mission / ".scratch").mkdir()

    # STATUS row is fresh → S2 will NOT fire; S3 must also not fire.
    status = mission / "STATUS.md"
    fresh_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    status.write_text(f"| CODEX | agent-codex | working: task | {fresh_utc} | foo |\n")

    # Give the lane a PID so S3 *would* run if not gated.
    fake_pid = 99999
    monkeypatch.setattr("megalodon_ui.watchdog.daemon._read_pid", lambda lane: fake_pid)
    # S1: declare the fake pid alive so the loop doesn't stop at CRASHED.
    monkeypatch.setattr("megalodon_ui.watchdog.daemon.detect_process", lambda pid: "ok")
    # _find_jsonl returns None (no JSONL on disk) — but even if it returned a
    # path, S3 must be skipped for non-Claude lanes.
    monkeypatch.setattr("megalodon_ui.watchdog.daemon._find_jsonl", lambda pid: None)

    _, lanes = _make_codex_config()

    # Emit startup warnings (normally done in run(); call directly here).
    _emit_wr3_warnings(lanes)

    alerts = AlertManager(mission)
    poll_once(mission, alerts, cadence_seconds=300, lanes=lanes)

    # No alerts should have been written.
    written = list((mission / "findings").glob("watchdog-ALERT-*.md"))
    assert written == [], f"unexpected alerts: {written}"

    # Startup warning must mention the lane name, cli, and WR-3.
    captured = capsys.readouterr()
    assert "WR-3" in captured.err
    assert "CODEX" in captured.err
    assert "codex" in captured.err
