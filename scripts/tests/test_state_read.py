"""Tests for scripts/_state_read.py."""

from freezegun import freeze_time

from scripts._state_read import read_lanes, read_phase


def test_read_lanes_returns_six_rows(mission_dir):
    rows = read_lanes(mission_dir)
    assert len(rows) == 6
    lanes = {row["lane"] for row in rows}
    assert lanes == {"AUDIT", "ARCHITECT", "BACKEND", "FRONTEND", "TEST", "META"}


def test_read_lanes_parses_audit_row(mission_dir):
    rows = read_lanes(mission_dir)
    audit = next(r for r in rows if r["lane"] == "AUDIT")
    assert audit["agent"] == "agent-abcd"
    assert audit["state"] == "working: TEST-1"
    assert audit["last_utc"] == "2026-05-16T22:00:00Z"


@freeze_time("2026-05-16T22:00:30Z")
def test_read_lanes_computes_stale_seconds(mission_dir):
    rows = read_lanes(mission_dir)
    audit = next(r for r in rows if r["lane"] == "AUDIT")
    assert audit["stale_seconds"] == 30


def test_read_phase_returns_init_phase(mission_dir):
    phase, owner = read_phase(mission_dir)
    # Minimal fixture has only an INIT->PHASE-PLAN line; current phase is PHASE-PLAN.
    assert phase == "PHASE-PLAN"
    assert owner is None


from scripts._state_read import (  # noqa: E402
    read_claims,
    read_events_tail,
    read_findings_recent,
    read_partial_journals,
)


def test_read_claims_open_when_no_done_marker(mission_dir):
    claims = read_claims(mission_dir)
    assert any(c["task_id"] == "TEST-1" for c in claims["open"])
    assert claims["done"] == []


def test_read_claims_done_when_marker_present(mission_dir):
    (mission_dir / "claims" / "TEST-1" / "done").touch()
    claims = read_claims(mission_dir)
    assert claims["open"] == []
    assert any(c["task_id"] == "TEST-1" for c in claims["done"])


def test_read_events_tail_returns_n_lines(mission_dir):
    tail = read_events_tail(mission_dir, n=5)
    assert len(tail) == 1
    assert "INIT->PHASE-PLAN" in tail[0]


def test_read_findings_recent_returns_empty_for_empty_dir(mission_dir):
    findings = read_findings_recent(mission_dir, n=5, include_body=False)
    assert findings == []


def test_read_partial_journals_returns_only_partial_within_window(mission_dir, agent):
    # No journal dir yet → empty.
    assert read_partial_journals(mission_dir) == []
    # Create journals with status=PARTIAL.
    import json

    jdir = mission_dir / ".scripts-journal"
    jdir.mkdir()
    (jdir / "rid-old.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "request_id": "rid-old",
                "started_utc": "2026-05-15T00:00:00Z",
                "last_updated_utc": "2026-05-15T00:00:00Z",
                "status": "PARTIAL",
                "task_id": "X-1",
                "lane": "AUDIT",
                "agent": agent,
                "args": {
                    "finding": "f",
                    "severity": "DELTA",
                    "notes": "n",
                    "summary": "s",
                },
                "steps": [{"step": "CLAIM_DIR_DONE", "ok": True, "error": None}],
            }
        )
    )
    (jdir / "rid-new.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "request_id": "rid-new",
                "started_utc": "2026-05-16T22:00:00Z",
                "last_updated_utc": "2026-05-16T22:00:00Z",
                "status": "PARTIAL",
                "task_id": "X-2",
                "lane": "AUDIT",
                "agent": agent,
                "args": {
                    "finding": "f",
                    "severity": "DELTA",
                    "notes": "n",
                    "summary": "s",
                },
                "steps": [
                    {"step": "CLAIM_DIR_DONE", "ok": True, "error": None},
                    {"step": "TASKS_BRACKET", "ok": False, "error": "missing"},
                ],
            }
        )
    )
    # At 2026-05-16T22:30Z, rid-old is > 24h old, rid-new is 30 min old.
    with freeze_time("2026-05-16T22:30:00Z"):
        entries = read_partial_journals(mission_dir, max_age_seconds=86400)
    assert len(entries) == 1
    assert entries[0]["request_id"] == "rid-new"
    assert entries[0]["failed_step"] == "TASKS_BRACKET"
