"""V9 M1 — Journal (WAL) tests.

Verifies write-ahead-log discipline: PENDING-without-APPLIED entries
must be reported as PENDING_INDOUBT on replay so the applier can
reconcile crash-mid-apply on restart.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.queue.journal import Journal


def test_journal_write_pending_and_applied(tmp_path):
    j = Journal(tmp_path / "journal.log")
    j.write_pending("rid1", "HISTORY_APPEND", "HISTORY.md", {"line": "test"})
    j.write_applied("rid1", "ok")
    terminal = j.replay()
    assert terminal["rid1"] == "APPLIED"


def test_journal_pending_without_applied_is_indoubt(tmp_path):
    j = Journal(tmp_path / "journal.log")
    j.write_pending("rid1", "HISTORY_APPEND", "HISTORY.md", {"line": "test"})
    terminal = j.replay()
    assert terminal["rid1"] == "PENDING_INDOUBT"


def test_journal_rejected_is_terminal(tmp_path):
    j = Journal(tmp_path / "journal.log")
    j.write_pending("rid1", "HISTORY_APPEND", "HISTORY.md", {"line": "test"})
    j.write_rejected("rid1", "schema invalid")
    terminal = j.replay()
    assert terminal["rid1"] == "REJECTED"


def test_journal_multiple_entries(tmp_path):
    j = Journal(tmp_path / "journal.log")
    for i in range(5):
        j.write_pending(f"rid{i}", "HISTORY_APPEND", "HISTORY.md", {})
        if i % 2 == 0:
            j.write_applied(f"rid{i}", "ok")
        else:
            j.write_rejected(f"rid{i}", "bad")
    terminal = j.replay()
    assert sum(1 for v in terminal.values() if v == "APPLIED") == 3
    assert sum(1 for v in terminal.values() if v == "REJECTED") == 2


def test_journal_append_only_no_truncate(tmp_path):
    j = Journal(tmp_path / "journal.log")
    j.write_pending("rid1", "X", "Y", {})
    size1 = (tmp_path / "journal.log").stat().st_size
    j.write_applied("rid1", "ok")
    size2 = (tmp_path / "journal.log").stat().st_size
    assert size2 > size1


def test_journal_persists_across_instances(tmp_path):
    log = tmp_path / "journal.log"
    j1 = Journal(log)
    j1.write_pending("rid1", "X", "Y", {})
    j1.write_applied("rid1", "ok")
    j2 = Journal(log)
    assert j2.replay()["rid1"] == "APPLIED"


def test_journal_empty_replay_returns_empty(tmp_path):
    j = Journal(tmp_path / "journal.log")
    assert j.replay() == {}


def test_journal_skips_malformed_lines(tmp_path):
    log = tmp_path / "journal.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        "not-json garbage\n"
        '{"rid":"rid1","status":"PENDING","intent":"X","target":"Y","payload":{},"utc":"2026-01-01T00:00:00Z"}\n'
    )
    j = Journal(log)
    terminal = j.replay()
    assert "rid1" in terminal
    assert terminal["rid1"] == "PENDING_INDOUBT"
