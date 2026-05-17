"""V9 M1 — verify scripts/_shared_state via the queue backend.

After Phase C swaps `_shared_state` to use `_backends.queue_client`,
these tests cover the integration: an in-process applier services the
requests submitted by the backend adapter and the existing 4-step
RULE-10 close lands all mutations through the queue.

Subprocess-applier coverage is in test_applier_subprocess (separate).
"""

import json
from pathlib import Path

import pytest

from scripts._backends import queue_client as backend


def test_backend_claim_dir_done_via_queue(mission_dir: Path):
    """claim_dir_done via queue backend lands `done` file."""
    result = backend.claim_dir_done(
        mission_dir, "TEST-1", "agent-abcd", "2026-05-16T22:30:00Z",
    )
    assert result["ok"] is True, result
    assert (mission_dir / "claims" / "TEST-1" / "done").exists()


def test_backend_tasks_bracket_via_queue(mission_dir: Path):
    """tasks_bracket via queue backend writes `[done: ... ]`."""
    result = backend.tasks_bracket(
        mission_dir, "TEST-1", "agent-abcd", "2026-05-16T22:30:00Z",
    )
    assert result["ok"] is True, result
    text = (mission_dir / "TASKS.md").read_text(encoding="utf-8")
    assert "[done: agent-abcd @ 2026-05-16T22:30:00Z]" in text


def test_backend_history_append_via_queue(mission_dir: Path):
    result = backend.history_append(
        mission_dir,
        agent="agent-abcd",
        lane_short="A",
        task_id="TEST-1",
        finding_path="findings/agent-abcd-A-TEST-1-2026-05-16T22-30Z.md",
        severity="DELTA",
        notes="sample close",
        utc="2026-05-16T22:30:00Z",
    )
    assert result["ok"] is True, result
    text = (mission_dir / "HISTORY.md").read_text(encoding="utf-8")
    assert "2026-05-16T22:30:00Z | agent-abcd | A | TEST-1" in text
    assert "| DELTA (sample close)" in text


def test_backend_status_update_via_queue(mission_dir: Path):
    result = backend.status_update(
        mission_dir, lane="AUDIT", agent="agent-abcd",
        task_id="TEST-1", summary="sample close",
        utc="2026-05-16T22:30:00Z",
    )
    assert result["ok"] is True, result
    text = (mission_dir / "STATUS.md").read_text(encoding="utf-8")
    assert "TEST-1 done — sample close" in text


def test_execute_close_via_queue_backend(mission_dir: Path):
    """The 4-step RULE-10 close lands all mutations through the queue.

    This is the headline Phase C verification: scripts/_shared_state now
    imports `queue_client as _backend`; the existing M3 happy-path test
    must still pass.
    """
    # M3 tests' minimal_mission fixture already has claims/TEST-1/.
    # Add findings stub so history-line shape is satisfied.
    (mission_dir / "findings").mkdir(parents=True, exist_ok=True)
    (mission_dir / "findings" / "f.md").write_text("body", encoding="utf-8")

    from scripts._shared_state import execute_close
    result = execute_close(
        mission_dir,
        request_id="rid-via-queue",
        task_id="TEST-1",
        lane="AUDIT",
        agent="agent-abcd",
        utc="2026-05-16T22:30:00Z",
        finding_path="findings/f.md",
        severity="DELTA",
        notes="via queue",
        summary="via queue",
    )
    assert result["ok"] is True, result
    assert result["completed"] == [
        "CLAIM_DIR_DONE", "TASKS_BRACKET", "HISTORY_APPEND", "STATUS_UPDATE",
    ]
    journal = json.loads(
        (mission_dir / ".scripts-journal" / "rid-via-queue.json").read_text()
    )
    assert journal["status"] == "COMPLETE"
