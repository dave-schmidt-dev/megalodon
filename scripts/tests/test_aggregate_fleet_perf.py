"""V9 A9 — tests for fleet ledger aggregator."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.aggregate_fleet_perf import aggregate


def test_aggregates_per_lane(tmp_path):
    led = tmp_path / ".fleet-ledger"
    led.mkdir()
    (led / "AUDIT-tick-1-2026-01-01T00-00-00Z.json").write_text(
        json.dumps(
            {
                "lane": "AUDIT",
                "tick_number": 1,
                "tasks_completed": ["P1"],
                "cas_retries": 0,
            }
        )
    )
    (led / "AUDIT-tick-2-2026-01-01T00-01-00Z.json").write_text(
        json.dumps(
            {
                "lane": "AUDIT",
                "tick_number": 2,
                "tasks_completed": ["P2", "P3"],
                "cas_retries": 1,
            }
        )
    )
    out = tmp_path / "fleet-perf.json"
    summary = aggregate(tmp_path, out)
    assert summary["AUDIT"]["tick_count"] == 2
    assert summary["AUDIT"]["tasks_completed"] == 3
    assert summary["AUDIT"]["cas_retries"] == 1


def test_sums_cas_retries(tmp_path):
    led = tmp_path / ".fleet-ledger"
    led.mkdir()
    for i, retries in enumerate([3, 5, 2]):
        (led / f"BACKEND-tick-{i + 1}-2026-01-01T00-0{i}-00Z.json").write_text(
            json.dumps(
                {"lane": "BACKEND", "tick_number": i + 1, "cas_retries": retries}
            )
        )
    out = tmp_path / "fleet-perf.json"
    summary = aggregate(tmp_path, out)
    assert summary["BACKEND"]["cas_retries"] == 10


def test_handles_missing_ledger_dir(tmp_path):
    out = tmp_path / "fleet-perf.json"
    summary = aggregate(tmp_path, out)
    assert summary == {}


def test_writes_output_file(tmp_path):
    (tmp_path / ".fleet-ledger").mkdir()
    out = tmp_path / "fleet-perf.json"
    aggregate(tmp_path, out)
    assert out.exists()
    assert json.loads(out.read_text()) == {"lanes": {}}
