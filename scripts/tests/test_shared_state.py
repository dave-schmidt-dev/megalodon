"""Tests for scripts/_shared_state.py + scripts/_backends/direct_fcntl.py."""

from pathlib import Path

import pytest

from scripts._backends import direct_fcntl as backend


# ---- Task 4: claim_dir_done ----


def test_claim_dir_done_happy_path(mission_dir: Path, agent: str):
    result = backend.claim_dir_done(
        mission_dir, "TEST-1", agent, "2026-05-16T22:30:00Z"
    )
    assert result["ok"] is True
    assert (mission_dir / "claims" / "TEST-1" / "done").exists()
    assert (
        mission_dir / "claims" / "TEST-1" / "owner.txt"
    ).read_text().strip() == agent


def test_claim_dir_done_idempotent_on_second_call(mission_dir: Path, agent: str):
    backend.claim_dir_done(mission_dir, "TEST-1", agent, "2026-05-16T22:30:00Z")
    second = backend.claim_dir_done(
        mission_dir, "TEST-1", agent, "2026-05-16T22:31:00Z"
    )
    assert second["ok"] is True
    assert second["idempotent"] is True


def test_claim_dir_done_fails_when_claim_dir_missing(mission_dir: Path, agent: str):
    result = backend.claim_dir_done(
        mission_dir, "DOES-NOT-EXIST", agent, "2026-05-16T22:30:00Z"
    )
    assert result["ok"] is False
    assert "claim dir missing" in result["error"]


# ---- Task 5: tasks_bracket ----


def test_tasks_bracket_marks_open_as_done(mission_dir: Path, agent: str):
    result = backend.tasks_bracket(mission_dir, "TEST-1", agent, "2026-05-16T22:30:00Z")
    assert result["ok"] is True
    text = (mission_dir / "TASKS.md").read_text(encoding="utf-8")
    assert "[done: agent-abcd @ 2026-05-16T22:30:00Z] [LANE-A] `TEST-1`" in text


def test_tasks_bracket_idempotent(mission_dir: Path, agent: str):
    backend.tasks_bracket(mission_dir, "TEST-1", agent, "2026-05-16T22:30:00Z")
    second = backend.tasks_bracket(mission_dir, "TEST-1", agent, "2026-05-16T22:31:00Z")
    assert second["ok"] is True
    assert second["idempotent"] is True


def test_tasks_bracket_fails_on_missing_task(mission_dir: Path, agent: str):
    result = backend.tasks_bracket(
        mission_dir, "TEST-MISSING", agent, "2026-05-16T22:30:00Z"
    )
    assert result["ok"] is False
    assert "not found" in result["error"]


# ---- Task 6: history_append ----


def test_history_append_writes_pipe_row(mission_dir: Path, agent: str):
    result = backend.history_append(
        mission_dir,
        agent=agent,
        lane_short="A",
        task_id="TEST-1",
        finding_path="findings/agent-abcd-A-TEST-1-2026-05-16T22-30Z.md",
        severity="DELTA",
        notes="sample close",
        utc="2026-05-16T22:30:00Z",
    )
    assert result["ok"] is True
    text = (mission_dir / "HISTORY.md").read_text(encoding="utf-8")
    assert "2026-05-16T22:30:00Z | agent-abcd | A | TEST-1 | " in text
    assert "| DELTA (sample close)" in text


def test_history_append_idempotent_within_60s(mission_dir: Path, agent: str):
    common = dict(
        agent=agent,
        lane_short="A",
        task_id="TEST-1",
        finding_path="findings/x.md",
        severity="DELTA",
        notes="first",
    )
    backend.history_append(mission_dir, **common, utc="2026-05-16T22:30:00Z")
    second = backend.history_append(mission_dir, **common, utc="2026-05-16T22:30:45Z")
    assert second["idempotent"] is True


# ---- Task 7: status_update ----


def test_status_update_writes_idle_row(mission_dir: Path, agent: str):
    result = backend.status_update(
        mission_dir,
        lane="AUDIT",
        agent=agent,
        task_id="TEST-1",
        summary="sample close",
        utc="2026-05-16T22:30:00Z",
    )
    assert result["ok"] is True
    text = (mission_dir / "STATUS.md").read_text(encoding="utf-8")
    assert "| AUDIT" in text
    assert "| idle" in text
    assert "2026-05-16T22:30:00Z" in text
    assert "TEST-1 done — sample close" in text


def test_status_update_rejects_owner_mismatch(mission_dir: Path):
    result = backend.status_update(
        mission_dir,
        lane="AUDIT",
        agent="agent-zzzz",
        task_id="TEST-1",
        summary="sample close",
        utc="2026-05-16T22:30:00Z",
    )
    assert result["ok"] is False
    assert "owner mismatch" in result["error"]


def test_status_update_idempotent(mission_dir: Path, agent: str):
    backend.status_update(
        mission_dir,
        lane="AUDIT",
        agent=agent,
        task_id="TEST-1",
        summary="sample close",
        utc="2026-05-16T22:30:00Z",
    )
    second = backend.status_update(
        mission_dir,
        lane="AUDIT",
        agent=agent,
        task_id="TEST-1",
        summary="sample close",
        utc="2026-05-16T22:31:00Z",
    )
    assert second["idempotent"] is True


# ---- Task 8: execute_close ----

import json as _json  # noqa: E402  (section-local import, kept beside its use)

from scripts._shared_state import execute_close, resume_close  # noqa: E402


def test_execute_close_happy_path(mission_dir: Path, agent: str):
    (mission_dir / "findings").mkdir(parents=True, exist_ok=True)
    (mission_dir / "findings" / "f.md").write_text("body", encoding="utf-8")
    result = execute_close(
        mission_dir,
        request_id="rid-happy",
        task_id="TEST-1",
        lane="AUDIT",
        agent=agent,
        utc="2026-05-16T22:30:00Z",
        finding_path="findings/f.md",
        severity="DELTA",
        notes="happy path",
        summary="happy path",
    )
    assert result["ok"] is True
    assert result["completed"] == [
        "CLAIM_DIR_DONE",
        "TASKS_BRACKET",
        "HISTORY_APPEND",
        "STATUS_UPDATE",
    ]
    assert result["failed_step"] is None
    journal = mission_dir / ".scripts-journal" / "rid-happy.json"
    assert journal.exists()
    data = _json.loads(journal.read_text())
    assert data["status"] == "COMPLETE"


def test_execute_close_partial_on_missing_claim(mission_dir: Path, agent: str):
    """If claims/TEST-2 doesn't exist, CLAIM_DIR_DONE fails first; no further steps."""
    result = execute_close(
        mission_dir,
        request_id="rid-partial",
        task_id="TEST-2",
        lane="AUDIT",
        agent=agent,
        utc="2026-05-16T22:30:00Z",
        finding_path="findings/x.md",
        severity="DELTA",
        notes="will fail",
        summary="fail",
    )
    assert result["ok"] is False
    assert result["failed_step"] == "CLAIM_DIR_DONE"
    assert result["resume_hint"] is not None
    journal = _json.loads(
        (mission_dir / ".scripts-journal" / "rid-partial.json").read_text()
    )
    assert journal["status"] == "PARTIAL"


# ---- Task 9: resume_close ----


def test_resume_close_completes_partial(mission_dir: Path, agent: str):
    # First, cause a PARTIAL by targeting a missing claim:
    execute_close(
        mission_dir,
        request_id="rid-resume",
        task_id="TEST-2",  # missing — will fail at CLAIM_DIR_DONE
        lane="AUDIT",
        agent=agent,
        utc="2026-05-16T22:30:00Z",
        finding_path="findings/x.md",
        severity="DELTA",
        notes="setup partial",
        summary="partial",
    )
    # Create the missing claim dir + matching TASKS line + resume:
    (mission_dir / "claims" / "TEST-2").mkdir()
    (mission_dir / "claims" / "TEST-2" / "owner.txt").write_text(f"{agent}\n")
    with open(mission_dir / "TASKS.md", "a", encoding="utf-8") as f:
        f.write("- [ ] [LANE-A] `TEST-2` — second sample\n")
    result = resume_close(mission_dir, "rid-resume")
    assert result["ok"] is True
    assert "STATUS_UPDATE" in result["completed"]


def test_resume_close_rejects_when_journal_missing(mission_dir: Path):
    with pytest.raises(FileNotFoundError):
        resume_close(mission_dir, "rid-does-not-exist")
