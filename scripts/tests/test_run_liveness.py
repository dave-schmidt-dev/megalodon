"""v9.4 — run liveness grammar.

A run is LIVE iff the first whitespace-token of the last .mission-events line
is not a terminal token. Terminal tokens: COMPLETE | ABORTED | DEGRADED-CLOSE.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _run_liveness import TERMINAL_TOKENS, is_live, last_token  # noqa: E402


def test_terminal_tokens_frozen():
    assert TERMINAL_TOKENS == {"COMPLETE", "ABORTED", "DEGRADED-CLOSE"}


def test_run_start_is_live(tmp_path: Path):
    ev = tmp_path / ".mission-events"
    ev.write_text("RUN-START 2026-05-22T16-30Z slug=demo\n")
    assert is_live(ev) is True
    assert last_token(ev) == "RUN-START"


def test_complete_is_not_live(tmp_path: Path):
    ev = tmp_path / ".mission-events"
    ev.write_text("RUN-START ...\nCOMPLETE 2026-05-22T20-00Z all lanes drained\n")
    assert is_live(ev) is False


def test_missing_file_is_not_live(tmp_path: Path):
    assert is_live(tmp_path / "nope") is False


def test_blank_trailing_lines_ignored(tmp_path: Path):
    ev = tmp_path / ".mission-events"
    ev.write_text("ABORTED operator killed run\n\n  \n")
    assert is_live(ev) is False


def test_empty_file_is_not_live(tmp_path: Path):
    ev = tmp_path / ".mission-events"
    ev.write_text("")
    assert is_live(ev) is False
