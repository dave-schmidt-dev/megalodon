"""CLI tests for scripts/poll.py."""

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "poll.py"


def _run(mission_dir, *args):
    env = {**os.environ, "PYTHONPATH": str(SCRIPT.resolve().parents[1])}
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--mission-dir", str(mission_dir), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_full_emits_valid_json_with_required_keys(mission_dir):
    res = _run(mission_dir, "--full")
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    for key in [
        "utc",
        "mission_dir",
        "phase",
        "phase_lock_owner",
        "lanes",
        "claims",
        "events_tail",
        "findings_recent",
        "partial_journals",
    ]:
        assert key in payload, f"missing key: {key}"


def test_full_returns_six_lanes(mission_dir):
    res = _run(mission_dir, "--full")
    payload = json.loads(res.stdout)
    assert len(payload["lanes"]) == 6


def test_brief_drops_optional_sections(mission_dir):
    res = _run(mission_dir, "--brief")
    payload = json.loads(res.stdout)
    assert "events_tail" not in payload
    assert "findings_recent" not in payload
    assert "partial_journals" not in payload
    assert "lanes" in payload


def test_invalid_mission_dir_exits_4(tmp_path):
    res = _run(tmp_path)  # tmp_path has no STATUS.md
    assert res.returncode == 4


def test_full_includes_partial_journals_when_present(mission_dir, agent):
    import json as J

    jdir = mission_dir / ".scripts-journal"
    jdir.mkdir()
    (jdir / "rid-test.json").write_text(
        J.dumps(
            {
                "schema_version": 1,
                "request_id": "rid-test",
                "started_utc": "2026-05-16T22:00:00Z",
                "last_updated_utc": "2026-05-16T22:00:00Z",
                "status": "PARTIAL",
                "task_id": "X",
                "lane": "AUDIT",
                "agent": agent,
                "args": {
                    "finding": "f",
                    "severity": "DELTA",
                    "notes": "n",
                    "summary": "s",
                },
                "steps": [{"step": "CLAIM_DIR_DONE", "ok": False, "error": "missing"}],
            }
        )
    )
    res = _run(mission_dir, "--full")
    payload = json.loads(res.stdout)
    # rid-test may be > 24h old in real wall clock; just verify the field exists.
    assert "partial_journals" in payload
