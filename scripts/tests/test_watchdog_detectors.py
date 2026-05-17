"""V9 A1 — watchdog detector tests (S1, S2, S3)."""

import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from megalodon_ui.watchdog import detectors


def test_detect_process_alive_returns_ok():
    pid = os.getpid()
    assert detectors.detect_process(pid) == "ok"


def test_detect_process_dead_returns_crashed():
    # Use a PID that should not exist.
    assert detectors.detect_process(2**30) == "crashed"


def test_detect_status_stale_above_threshold(tmp_path):
    status = tmp_path / "STATUS.md"
    old_utc = (datetime.now(timezone.utc) - timedelta(minutes=20)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    status.write_text(f"| AUDIT | agent-aaaa | working: x | {old_utc} | foo |\n")
    result = detectors.detect_status_stale(status, "AUDIT", threshold_seconds=900)
    assert result == "stale"


def test_detect_status_fresh_returns_ok(tmp_path):
    status = tmp_path / "STATUS.md"
    fresh_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    status.write_text(f"| AUDIT | agent-aaaa | working: x | {fresh_utc} | foo |\n")
    result = detectors.detect_status_stale(status, "AUDIT", threshold_seconds=900)
    assert result == "ok"


def test_detect_jsonl_missing_skips_silently():
    result = detectors.detect_jsonl_stale(Path("/nonexistent.jsonl"), threshold_seconds=300)
    assert result == "skip"


def test_detect_jsonl_stale_returns_hung(tmp_path):
    log = tmp_path / "session.jsonl"
    log.write_text("{}\n")
    old = time.time() - 600  # 10 min ago
    os.utime(log, (old, old))
    result = detectors.detect_jsonl_stale(log, threshold_seconds=300)
    assert result == "hung"
