"""Tests for megalodon_ui.narrator.board_state — deterministic board assembly.

Tests cover the pure assembler (assemble_lane_rows + _pick_latest) and the
async wrapper (build_lane_rows). All pure tests are free of real I/O.

The async-wrapper test mocks parse_session so no real JSONL is needed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from megalodon_ui.narrator.board_state import (
    _derive_liveness,
    _owning_agent_id,
    _pick_latest,
    _resolve_session_ids_by_agent,
    assemble_lane_rows,
    build_lane_rows,
)
from megalodon_ui.narrator.digest import SessionDigest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task(
    task_id: str,
    lane_name: str,
    description: str,
    claim_state: str,
    claim_utc: str | None = None,
    phase: str = "PHASE-PLAN",
) -> dict[str, Any]:
    """Build a minimal tasks_fe task record matching parse_tasks_fe_shape output."""
    rec: dict[str, Any] = {
        "task_id": task_id,
        "lane": lane_name,
        "description": description,
        "claim_state": claim_state,
        "phase": phase,
        "id": task_id,
        "state": claim_state,
    }
    if claim_utc is not None:
        rec["claim_utc"] = claim_utc
        rec["utc"] = claim_utc
    return rec


def _lane_cfg(
    name: str,
    short: str,
    role: str,
    cli: str = "claude",
) -> MagicMock:
    """Return a MagicMock shaped like a LaneConfig."""
    cfg = MagicMock()
    cfg.name = name
    cfg.short = short
    cfg.role = role
    cfg.harness = MagicMock()
    cfg.harness.cli = cli
    return cfg


def _session(session_id: str | None, cwd: Path | None = None) -> MagicMock:
    """Return a MagicMock shaped like a LaneSession."""
    s = MagicMock()
    s.session_id = session_id
    s.cwd = cwd or Path("/some/mission")
    return s


def _digest(tokens: int) -> SessionDigest:
    """Return a minimal SessionDigest with the given total input tokens."""
    d = SessionDigest(session_id="fake-sid")
    d.input_tokens = tokens
    return d


def _tasks_fe(phases: dict[str, list[dict]], cross: list[dict] | None = None) -> dict:
    return {"phases": phases, "cross": cross or []}


# ---------------------------------------------------------------------------
# _pick_latest — shared ordering helper
# ---------------------------------------------------------------------------


class TestPickLatest:
    """Direct tests of the _pick_latest helper."""

    def test_returns_none_for_empty(self) -> None:
        assert _pick_latest([], {}) is None

    def test_latest_by_utc(self) -> None:
        tasks = [
            _task("T1", "AUDIT", "first", "done", "2026-05-01T10:00:00Z"),
            _task("T2", "AUDIT", "second", "done", "2026-05-01T12:00:00Z"),
        ]
        doc_order = {"T1": 0, "T2": 1}
        result = _pick_latest(tasks, doc_order)
        assert result is not None
        assert result["task_id"] == "T2"

    def test_latest_by_utc_older_wins(self) -> None:
        """T1 has a later timestamp than T2."""
        tasks = [
            _task("T1", "AUDIT", "later", "done", "2026-05-02T08:00:00Z"),
            _task("T2", "AUDIT", "earlier", "done", "2026-05-01T08:00:00Z"),
        ]
        doc_order = {"T1": 0, "T2": 1}
        result = _pick_latest(tasks, doc_order)
        assert result is not None
        assert result["task_id"] == "T1"

    def test_tied_utc_falls_back_to_doc_order(self) -> None:
        """When timestamps are identical, higher doc-order index wins."""
        ts = "2026-05-01T10:00:00Z"
        tasks = [
            _task("T1", "AUDIT", "first", "done", ts),
            _task("T2", "AUDIT", "second", "done", ts),
        ]
        doc_order = {"T1": 0, "T2": 3}
        result = _pick_latest(tasks, doc_order)
        assert result is not None
        assert result["task_id"] == "T2"  # higher doc-order index

    def test_unparseable_utc_falls_back_to_doc_order(self) -> None:
        """Unparseable claim_utc → treat as None → doc-order fallback."""
        tasks = [
            _task("T1", "AUDIT", "a", "done", "not-a-date"),
            _task("T2", "AUDIT", "b", "done", "also-bad"),
        ]
        doc_order = {"T1": 5, "T2": 2}
        result = _pick_latest(tasks, doc_order)
        assert result is not None
        assert result["task_id"] == "T1"  # higher doc-order index

    def test_mixed_parseable_and_none_utc(self) -> None:
        """A parseable timestamp always beats a None/unparseable one.

        The spec fallback to doc-order applies within the unparseable group.
        A task with a real timestamp is considered "later" than one with no
        timestamp at all — the sort key treats (has_ts=1, ...) > (has_ts=0, ...).
        """
        tasks = [
            _task("T1", "AUDIT", "a", "done", "2026-05-01T10:00:00Z"),
            _task("T2", "AUDIT", "b", "done", None),
        ]
        doc_order = {"T1": 0, "T2": 10}
        # T1 has a parseable timestamp; T2 has None → T1 wins regardless of
        # doc-order because has-timestamp > no-timestamp.
        result = _pick_latest(tasks, doc_order)
        assert result is not None
        assert result["task_id"] == "T1"

    def test_tz_naive_loses_to_tz_aware(self) -> None:
        """A tz-naive string parses to None and loses to a tz-aware timestamp.

        ``_parse_utc`` returns None for tz-naive input, so T1 has has_ts=0 and
        T2 has has_ts=1. The has_ts flag ranks T2 first regardless of doc-order
        (T1's index is higher here precisely to prove the timestamp wins, not
        doc-order). No tz-mixed comparison ever occurs.
        """
        tasks = [
            _task("T1", "AUDIT", "a", "done", "2026-05-01 10:00:00"),  # tz-naive
            _task("T2", "AUDIT", "b", "done", "2026-05-01T11:00:00Z"),  # tz-aware
        ]
        doc_order = {"T1": 5, "T2": 0}  # T1 has the higher doc index
        result = _pick_latest(tasks, doc_order)
        assert result is not None
        assert result["task_id"] == "T2"  # tz-aware beats tz-naive→None

    def test_both_tz_naive_falls_back_to_doc_order(self) -> None:
        """Two tz-naive strings both parse to None → pure doc-order decides.

        Both have has_ts=0 and an equal placeholder datetime, so the only
        discriminator is doc_order (higher index wins). This is the path that
        exercises the None→placeholder branch without any comparison error.
        """
        tasks = [
            _task("T1", "AUDIT", "a", "done", "2026-05-01 10:00:00"),  # tz-naive
            _task("T2", "AUDIT", "b", "done", "2026-05-02 09:00:00"),  # tz-naive
        ]
        doc_order = {"T1": 0, "T2": 7}
        result = _pick_latest(tasks, doc_order)
        assert result is not None
        assert result["task_id"] == "T2"  # higher doc-order index

    def test_single_task_returned(self) -> None:
        tasks = [_task("T1", "AUDIT", "only", "done", "2026-05-01T10:00:00Z")]
        result = _pick_latest(tasks, {"T1": 0})
        assert result is not None
        assert result["task_id"] == "T1"

    def test_multiple_claimed_picks_one_deterministically(self) -> None:
        """Multiple claimed tasks → exactly one picked, same answer on repeat."""
        tasks = [
            _task("C1", "AUDIT", "claim 1", "claimed", "2026-05-01T09:00:00Z"),
            _task("C2", "AUDIT", "claim 2", "claimed", "2026-05-01T10:00:00Z"),
            _task("C3", "AUDIT", "claim 3", "claimed", "2026-05-01T08:00:00Z"),
        ]
        doc_order = {"C1": 0, "C2": 1, "C3": 2}
        result1 = _pick_latest(tasks, doc_order)
        result2 = _pick_latest(tasks, doc_order)
        assert result1 is not None
        assert result1["task_id"] == result2["task_id"]  # deterministic
        assert result1["task_id"] == "C2"  # latest timestamp


# ---------------------------------------------------------------------------
# assemble_lane_rows — pure assembler
# ---------------------------------------------------------------------------


class TestAssembleLaneRows:
    """Tests for assemble_lane_rows with in-memory fixture data."""

    def _make_lane_cfgs(self) -> list[MagicMock]:
        return [
            _lane_cfg("AUDIT", "A", "Audit all findings"),
            _lane_cfg("BUILD", "B", "Build the artefact"),
        ]

    def test_last_now_goal_from_claimed_and_done(self) -> None:
        """Lane with claimed + done tasks: last = latest done, now = claimed."""
        cfgs = self._make_lane_cfgs()
        tasks = [
            _task("A-1", "AUDIT", "first audit task", "done", "2026-05-01T08:00:00Z"),
            _task("A-2", "AUDIT", "second audit task", "done", "2026-05-01T10:00:00Z"),
            _task(
                "A-3", "AUDIT", "current audit task", "claimed", "2026-05-01T11:00:00Z"
            ),
        ]
        tasks_fe = _tasks_fe({"PHASE-PLAN": tasks})
        digests: dict[str, SessionDigest | None] = {"A": None, "B": None}
        doc_order = {"A-1": 0, "A-2": 1, "A-3": 2}
        rows = assemble_lane_rows(tasks_fe, cfgs, digests, doc_order)
        row = rows["A"]
        assert row.lane == "A"
        assert row.lane_name == "AUDIT"
        assert row.last is not None
        assert row.last["task_id"] == "A-2"  # latest done
        assert row.last["desc"] == "second audit task"
        assert row.last["phrase"] is None  # board_state deterministic; scheduler fills
        assert row.now is not None
        assert row.now["task_id"] == "A-3"
        assert row.now["desc"] == "current audit task"
        assert row.now["phrase"] is None  # scheduler fills this
        assert row.goal == "current audit task"  # now.desc
        assert row.narrator_ok is False
        assert row.tokens is None  # no digest

    def test_goal_fallback_to_last_when_no_now(self) -> None:
        """No claimed tasks → goal = last.desc."""
        cfgs = self._make_lane_cfgs()
        tasks = [
            _task("A-1", "AUDIT", "completed task", "done", "2026-05-01T10:00:00Z"),
        ]
        tasks_fe = _tasks_fe({"PHASE-PLAN": tasks})
        digests: dict[str, SessionDigest | None] = {"A": None, "B": None}
        doc_order = {"A-1": 0}
        rows = assemble_lane_rows(tasks_fe, cfgs, digests, doc_order)
        row = rows["A"]
        assert row.now is None
        assert row.last is not None
        assert row.goal == "completed task"

    def test_goal_role_fallback_when_no_tasks(self) -> None:
        """Lane with no done/claimed tasks → goal = lane role."""
        cfgs = self._make_lane_cfgs()
        tasks_fe = _tasks_fe({"PHASE-PLAN": []})
        digests: dict[str, SessionDigest | None] = {"A": None, "B": None}
        rows = assemble_lane_rows(tasks_fe, cfgs, digests, {})
        row_a = rows["A"]
        assert row_a.last is None
        assert row_a.now is None
        assert row_a.goal == "Audit all findings"
        row_b = rows["B"]
        assert row_b.goal == "Build the artefact"

    def test_multiple_done_latest_chosen_by_utc(self) -> None:
        """Multiple DONE tasks → latest by claim_utc wins."""
        cfgs = self._make_lane_cfgs()
        tasks = [
            _task("A-1", "AUDIT", "early done", "done", "2026-04-01T10:00:00Z"),
            _task("A-2", "AUDIT", "mid done", "done", "2026-04-15T10:00:00Z"),
            _task("A-3", "AUDIT", "latest done", "done", "2026-05-01T10:00:00Z"),
        ]
        tasks_fe = _tasks_fe({"PHASE-PLAN": tasks})
        digests: dict[str, SessionDigest | None] = {"A": None, "B": None}
        doc_order = {"A-1": 0, "A-2": 1, "A-3": 2}
        rows = assemble_lane_rows(tasks_fe, cfgs, digests, doc_order)
        assert rows["A"].last is not None
        assert rows["A"].last["task_id"] == "A-3"

    def test_tokens_from_digest(self) -> None:
        """When a digest is provided, tokens = digest.total_tokens."""
        cfgs = self._make_lane_cfgs()
        tasks_fe = _tasks_fe({"PHASE-PLAN": []})
        digest = _digest(500)
        digests: dict[str, SessionDigest | None] = {"A": digest, "B": None}
        rows = assemble_lane_rows(tasks_fe, cfgs, digests, {})
        assert rows["A"].tokens == 500
        assert rows["B"].tokens is None

    def test_narrator_ok_always_false_from_assembler(self) -> None:
        """board_state always emits narrator_ok=False; scheduler flips it."""
        cfgs = self._make_lane_cfgs()
        digest = _digest(100)
        digests: dict[str, SessionDigest | None] = {"A": digest, "B": None}
        rows = assemble_lane_rows(_tasks_fe({"PHASE-PLAN": []}), cfgs, digests, {})
        assert rows["A"].narrator_ok is False
        assert rows["B"].narrator_ok is False

    def test_digest_text_not_in_to_dict(self) -> None:
        """digest_text is internal; to_dict() payload must not expose it."""
        cfgs = [_lane_cfg("AUDIT", "A", "role")]
        digest = _digest(200)
        digests: dict[str, SessionDigest | None] = {"A": digest}
        rows = assemble_lane_rows(_tasks_fe({"PHASE-PLAN": []}), cfgs, digests, {})
        d = rows["A"].to_dict()
        assert "digest_text" not in d

    def test_to_dict_contains_expected_public_fields(self) -> None:
        """to_dict() includes lane, lane_name, state, last, now, goal, tokens, narrator_ok."""
        cfgs = [_lane_cfg("AUDIT", "A", "the role")]
        tasks = [_task("A-1", "AUDIT", "a task", "done", "2026-05-01T10:00:00Z")]
        tasks_fe = _tasks_fe({"PHASE-PLAN": tasks})
        rows = assemble_lane_rows(tasks_fe, cfgs, {"A": None}, {"A-1": 0})
        d = rows["A"].to_dict()
        assert d["lane"] == "A"
        assert d["lane_name"] == "AUDIT"
        assert "state" in d
        assert "last" in d
        assert "now" in d
        assert "goal" in d
        assert "tokens" in d
        assert "narrator_ok" in d

    def test_last_phrase_none_and_carried_in_to_dict(self) -> None:
        """OQ1: last includes phrase=None from board_state, carried in to_dict()."""
        cfgs = [_lane_cfg("AUDIT", "A", "role")]
        tasks = [_task("A-1", "AUDIT", "shipped auth", "done", "2026-05-01T10:00:00Z")]
        tasks_fe = _tasks_fe({"PHASE-PLAN": tasks})
        rows = assemble_lane_rows(tasks_fe, cfgs, {"A": None}, {"A-1": 0})
        row = rows["A"]
        # Deterministic: phrase is None from board_state (no narrate happened).
        assert row.last is not None
        assert row.last["phrase"] is None
        # to_dict carries the full last dict including the phrase slot.
        d = row.to_dict()
        assert d["last"]["phrase"] is None
        assert d["last"]["desc"] == "shipped auth"

    def test_open_and_blocked_tasks_ignored_for_last_and_now(self) -> None:
        """open/blocked tasks do not contribute to last or now."""
        cfgs = [_lane_cfg("AUDIT", "A", "role")]
        tasks = [
            _task("A-1", "AUDIT", "open task", "open"),
            _task("A-2", "AUDIT", "blocked task", "blocked"),
        ]
        tasks_fe = _tasks_fe({"PHASE-PLAN": tasks})
        rows = assemble_lane_rows(tasks_fe, cfgs, {"A": None}, {"A-1": 0, "A-2": 1})
        row = rows["A"]
        assert row.last is None
        assert row.now is None
        assert row.goal == "role"

    def test_cross_tasks_included(self) -> None:
        """Tasks in cross bucket are also considered."""
        cfgs = [_lane_cfg("AUDIT", "A", "role")]
        cross_tasks = [
            _task("A-X1", "AUDIT", "cross done", "done", "2026-05-01T10:00:00Z"),
        ]
        tasks_fe = _tasks_fe({"PHASE-PLAN": []}, cross=cross_tasks)
        rows = assemble_lane_rows(tasks_fe, cfgs, {"A": None}, {"A-X1": 0})
        assert rows["A"].last is not None
        assert rows["A"].last["task_id"] == "A-X1"

    def test_non_claude_harness_deterministic_only_row(self) -> None:
        """Non-Claude harness → deterministic-only row (tokens=None, narrator_ok=False)."""
        cfgs = [_lane_cfg("AUDIT", "A", "role", cli="codex")]
        tasks_fe = _tasks_fe({"PHASE-PLAN": []})
        rows = assemble_lane_rows(tasks_fe, cfgs, {"A": None}, {})
        row = rows["A"]
        assert row.tokens is None
        assert row.narrator_ok is False
        assert row.digest_text is None


# ---------------------------------------------------------------------------
# STATUS.md fallback: when a lane has no TASKS.md task rows, the board reflects
# the lane's STATUS.md state so live lane activity (working:/initialized) is not
# misrendered as IDLE. Task-derived state always takes precedence when present.
# ---------------------------------------------------------------------------


def _status_row(
    lane: str,
    state: str,
    *,
    agent: str = "agent-0001",
    last_utc: str = "2026-05-25T01:41:55Z",
    notes: str = "",
) -> dict[str, Any]:
    """Build a STATUS.md row dict matching server.parse_status() output."""
    return {
        "lane": lane,
        "agent": agent,
        "state": state,
        "last_utc": last_utc,
        "notes": notes,
        "staleness_seconds": 1.0,
        "is_stale": False,
    }


class TestStatusFallback:
    """assemble_lane_rows falls back to STATUS.md state when no task rows exist."""

    def _make_lane_cfgs(self) -> list[MagicMock]:
        return [
            _lane_cfg("AUDIT", "A", "Audit all findings"),
            _lane_cfg("BUILD", "B", "Build the artefact"),
        ]

    def test_working_status_makes_lane_running_when_no_tasks(self) -> None:
        """STATUS.md 'working: P1-B' with no task rows → state=claimed, now from notes.

        UPDATED (B1/B2 rework): a live ``working:`` marker is authoritative for
        current activity, so when its task_id is NOT in TASKS.md the board now
        falls back to the CLEAN status note for BOTH the Now line AND the Goal
        line. The old behavior (Goal stays the lane role even with a live
        working marker) encoded the buggy precedence and is intentionally
        changed: a working lane's Goal should describe what it is working on.
        """
        cfgs = self._make_lane_cfgs()
        tasks_fe = _tasks_fe({"PHASE-PLAN": []})
        digests: dict[str, SessionDigest | None] = {"A": None, "B": None}
        status_rows = [_status_row("AUDIT", "working: P1-B", notes="surveying surface")]
        rows = assemble_lane_rows(tasks_fe, cfgs, digests, {}, status_rows=status_rows)
        row = rows["A"]
        # state must map to a RUNNING-equivalent (resolvePill treats "claimed" as RUNNING).
        assert row.state == "claimed"
        assert row.now is not None
        assert row.now["task_id"] == "P1-B"
        assert row.now["desc"] == "surveying surface"
        assert row.now["phrase"] is None
        # Goal now reflects the live work (clean note) — not the lane role.
        assert row.goal == "surveying surface"

    def test_initialized_status_makes_lane_running(self) -> None:
        """STATUS.md 'initialized' (bootstrapped, no task) → non-idle state."""
        cfgs = self._make_lane_cfgs()
        tasks_fe = _tasks_fe({"PHASE-PLAN": []})
        digests: dict[str, SessionDigest | None] = {"A": None, "B": None}
        status_rows = [_status_row("AUDIT", "initialized", notes="bootstrap tick")]
        rows = assemble_lane_rows(tasks_fe, cfgs, digests, {}, status_rows=status_rows)
        assert rows["A"].state == "claimed"
        assert rows["A"].now is not None
        assert rows["A"].now["desc"] == "bootstrap tick"

    def test_unclaimed_status_stays_idle(self) -> None:
        """STATUS.md 'unclaimed' → state stays 'open' (IDLE), now stays None."""
        cfgs = self._make_lane_cfgs()
        tasks_fe = _tasks_fe({"PHASE-PLAN": []})
        digests: dict[str, SessionDigest | None] = {"A": None, "B": None}
        status_rows = [_status_row("AUDIT", "unclaimed", agent="unclaimed")]
        rows = assemble_lane_rows(tasks_fe, cfgs, digests, {}, status_rows=status_rows)
        assert rows["A"].state == "open"
        assert rows["A"].now is None

    def test_task_claim_takes_precedence_over_status(self) -> None:
        """A real claimed task wins over STATUS.md — fallback only fills the gap."""
        cfgs = self._make_lane_cfgs()
        tasks = [
            _task("A-1", "AUDIT", "real task", "claimed", "2026-05-01T11:00:00Z"),
        ]
        tasks_fe = _tasks_fe({"PHASE-PLAN": tasks})
        digests: dict[str, SessionDigest | None] = {"A": None, "B": None}
        # STATUS says unclaimed, but the task claim must still drive the row.
        status_rows = [_status_row("AUDIT", "unclaimed", agent="unclaimed")]
        rows = assemble_lane_rows(
            tasks_fe, cfgs, digests, {"A-1": 0}, status_rows=status_rows
        )
        assert rows["A"].state == "claimed"
        assert rows["A"].now is not None
        assert rows["A"].now["task_id"] == "A-1"
        assert rows["A"].now["desc"] == "real task"

    def test_no_status_rows_is_backward_compatible(self) -> None:
        """Omitting status_rows preserves the original open/IDLE behavior."""
        cfgs = self._make_lane_cfgs()
        tasks_fe = _tasks_fe({"PHASE-PLAN": []})
        digests: dict[str, SessionDigest | None] = {"A": None, "B": None}
        rows = assemble_lane_rows(tasks_fe, cfgs, digests, {})
        assert rows["A"].state == "open"
        assert rows["A"].now is None


class TestLiveWorkingPrecedence:
    """B1/B2/I3: STATUS.md ``working:<task_id>`` is authoritative for CURRENT
    activity and must override a prior DONE task.

    The operator's core complaint: a lane whose STATUS says ``working: P4-A``
    but which has a prior DONE ``P3-A`` was rendered IDLE with Goal = the DONE
    task and Now = "narrator warming up…". The live working marker now wins for
    state/now/goal, with the Now/Goal text resolved from the TASKS.md task
    DESCRIPTION (by task_id), never the raw STATUS note when it is a routing
    signal.
    """

    def _cfgs(self) -> list[MagicMock]:
        return [_lane_cfg("AUDIT", "A", "Audit all findings")]

    def test_working_overrides_prior_done_task(self) -> None:
        """B1: working: P4-A + prior done P3-A → RUNNING, now/goal = P4-A desc."""
        cfgs = self._cfgs()
        tasks = [
            _task(
                "P3-A", "AUDIT", "dummy phase-3 task", "done", "2026-05-01T08:00:00Z"
            ),
            _task("P4-A", "AUDIT", "phase-4 deep audit", "open"),
        ]
        tasks_fe = _tasks_fe({"PHASE-PLAN": tasks})
        digests: dict[str, SessionDigest | None] = {"A": None}
        status_rows = [_status_row("AUDIT", "working: P4-A", notes="auditing module x")]
        rows = assemble_lane_rows(
            tasks_fe, cfgs, digests, {"P3-A": 0, "P4-A": 1}, status_rows=status_rows
        )
        row = rows["A"]
        # Live working: wins — RUNNING, not IDLE/done.
        assert row.state == "claimed"
        assert row.now is not None
        assert row.now["task_id"] == "P4-A"
        # Now/Goal resolved from the TASKS.md DESCRIPTION of P4-A, NOT P3-A and
        # NOT "narrator warming up…" (that is a board.js baseline, never here).
        assert row.now["desc"] == "phase-4 deep audit"
        assert row.goal == "phase-4 deep audit"
        # The DONE task still populates `last` (history is preserved).
        assert row.last is not None
        assert row.last["task_id"] == "P3-A"
        assert row.last["desc"] == "dummy phase-3 task"

    def test_working_resolves_desc_even_with_signal_note(self) -> None:
        """I3: a [SIG ...] STATUS note must NOT become the Goal — task desc wins."""
        cfgs = self._cfgs()
        tasks = [
            _task("P4-A", "AUDIT", "phase-4 deep audit", "open"),
        ]
        tasks_fe = _tasks_fe({"PHASE-PLAN": tasks})
        digests: dict[str, SessionDigest | None] = {"A": None}
        status_rows = [
            _status_row(
                "AUDIT",
                "working: P4-A",
                notes="SIG-FROM-LANE-D: please rebase onto main",
            )
        ]
        rows = assemble_lane_rows(
            tasks_fe, cfgs, digests, {"P4-A": 0}, status_rows=status_rows
        )
        row = rows["A"]
        assert row.state == "claimed"
        assert row.now is not None
        # Goal/Now are the resolved task description, NOT the routing signal.
        assert row.now["desc"] == "phase-4 deep audit"
        assert row.goal == "phase-4 deep audit"
        assert "SIG-FROM-LANE-D" not in row.goal
        assert "SIG-FROM-LANE-D" not in row.now["desc"]

    def test_working_unknown_task_id_ignores_signal_note_for_goal(self) -> None:
        """I3: working: unknown id + a [SIG ...] note → fall back to status_state,
        never the signal text."""
        cfgs = self._cfgs()
        tasks_fe = _tasks_fe({"PHASE-PLAN": []})
        digests: dict[str, SessionDigest | None] = {"A": None}
        status_rows = [
            _status_row("AUDIT", "working: P9-Z", notes="[SIG broadcast handshake]")
        ]
        rows = assemble_lane_rows(tasks_fe, cfgs, digests, {}, status_rows=status_rows)
        row = rows["A"]
        assert row.state == "claimed"
        assert row.now is not None
        assert row.now["task_id"] == "P9-Z"
        # Signal note dropped; falls back to the (clean) status_state string.
        assert "SIG" not in row.goal
        assert row.now["desc"] == "working: P9-Z"
        assert row.goal == "working: P9-Z"

    def test_working_overrides_other_claimed_task(self) -> None:
        """Live working: P4-A wins over a stale CLAIMED P2-A row for now/goal."""
        cfgs = self._cfgs()
        tasks = [
            _task(
                "P2-A", "AUDIT", "stale claimed task", "claimed", "2026-05-01T08:00:00Z"
            ),
            _task("P4-A", "AUDIT", "live phase-4 task", "open"),
        ]
        tasks_fe = _tasks_fe({"PHASE-PLAN": tasks})
        digests: dict[str, SessionDigest | None] = {"A": None}
        status_rows = [_status_row("AUDIT", "working: P4-A", notes="")]
        rows = assemble_lane_rows(
            tasks_fe, cfgs, digests, {"P2-A": 0, "P4-A": 1}, status_rows=status_rows
        )
        row = rows["A"]
        assert row.now is not None
        assert row.now["task_id"] == "P4-A"
        assert row.now["desc"] == "live phase-4 task"
        assert row.goal == "live phase-4 task"

    def test_working_with_blocked_task_keeps_blocked_pill(self) -> None:
        """A blocked task out-ranks the working pill, but now/goal stay live work."""
        cfgs = self._cfgs()
        tasks = [
            _task("P4-A", "AUDIT", "phase-4 task", "open"),
            _task("P5-A", "AUDIT", "blocked dependency", "blocked"),
        ]
        tasks_fe = _tasks_fe({"PHASE-PLAN": tasks})
        digests: dict[str, SessionDigest | None] = {"A": None}
        status_rows = [_status_row("AUDIT", "working: P4-A", notes="grinding")]
        rows = assemble_lane_rows(
            tasks_fe, cfgs, digests, {"P4-A": 0, "P5-A": 1}, status_rows=status_rows
        )
        row = rows["A"]
        # Blocked alarm wins the pill; live work still drives now/goal.
        assert row.state == "blocked"
        assert row.now is not None
        assert row.now["task_id"] == "P4-A"
        assert row.goal == "phase-4 task"


# ---------------------------------------------------------------------------
# Liveness (Wave-3 CRITICAL fix): a dead/crashed lane must be visible
# immediately, not after the ~15 min STATUS-stale heuristic.
# ---------------------------------------------------------------------------


def _live_session(running: object = ..., exited_rc: object = ...) -> Any:
    """Return an object with only the requested liveness attributes.

    Uses a plain namespace so an OMITTED arg leaves the attribute genuinely
    absent (exercising _derive_liveness's defensive getattr), rather than a
    MagicMock auto-attr that would always be present.
    """
    import types

    s = types.SimpleNamespace()
    # build_lane_rows reads session_id before the harness check; keep it present
    # so this fixture survives the full async path. _derive_liveness never reads
    # it, so its presence does not weaken the liveness assertions.
    s.session_id = None
    if running is not ...:
        s.running = running
    if exited_rc is not ...:
        s.exited_rc = exited_rc
    return s


class TestDeriveLiveness:
    """Direct tests of the _derive_liveness helper."""

    def test_running_when_alive(self) -> None:
        assert (
            _derive_liveness(_live_session(running=True, exited_rc=None)) == "running"
        )

    def test_exited_when_rc_zero(self) -> None:
        assert _derive_liveness(_live_session(running=False, exited_rc=0)) == "exited"

    def test_dead_when_rc_nonzero(self) -> None:
        assert _derive_liveness(_live_session(running=False, exited_rc=1)) == "dead"

    def test_dead_when_rc_negative_signal(self) -> None:
        # A signal-killed pane reports a negative rc (e.g. -9): crashed → dead.
        assert _derive_liveness(_live_session(running=False, exited_rc=-9)) == "dead"

    def test_unknown_when_session_none(self) -> None:
        assert _derive_liveness(None) == "unknown"

    def test_unknown_when_attributes_missing(self) -> None:
        # A fake/partial session lacking both attrs cannot assert health.
        assert _derive_liveness(_live_session()) == "unknown"

    def test_unknown_when_not_running_and_no_rc(self) -> None:
        # Idle/not-yet-started lane: running False, rc None → unknown, not dead.
        assert (
            _derive_liveness(_live_session(running=False, exited_rc=None)) == "unknown"
        )

    def test_unknown_when_rc_unparseable(self) -> None:
        assert (
            _derive_liveness(_live_session(running=True, exited_rc="boom")) == "unknown"
        )


class TestAssembleLivenessField:
    """liveness flows through assemble_lane_rows and to_dict."""

    def test_liveness_defaults_unknown_when_not_supplied(self) -> None:
        cfgs = [_lane_cfg("AUDIT", "A", "role")]
        rows = assemble_lane_rows(_tasks_fe({"PHASE-PLAN": []}), cfgs, {"A": None}, {})
        assert rows["A"].liveness == "unknown"
        assert rows["A"].to_dict()["liveness"] == "unknown"

    def test_liveness_from_map(self) -> None:
        cfgs = [_lane_cfg("AUDIT", "A", "role"), _lane_cfg("BUILD", "B", "role")]
        rows = assemble_lane_rows(
            _tasks_fe({"PHASE-PLAN": []}),
            cfgs,
            {"A": None, "B": None},
            {},
            liveness_by_lane={"A": "dead", "B": "running"},
        )
        assert rows["A"].liveness == "dead"
        assert rows["B"].liveness == "running"
        assert rows["A"].to_dict()["liveness"] == "dead"

    @pytest.mark.asyncio
    async def test_build_lane_rows_sets_liveness_from_sessions(
        self, tmp_path: Path
    ) -> None:
        """A session with exited_rc=1 → 'dead'; running → 'running'; missing → 'unknown'."""
        mission_dir = tmp_path / "mission"
        mission_dir.mkdir()
        cfgs = [
            _lane_cfg("AUDIT", "A", "role", cli="codex"),  # codex → no transcript read
            _lane_cfg("BUILD", "B", "role", cli="codex"),
            _lane_cfg("CHECK", "C", "role", cli="codex"),
        ]
        sessions = {
            "A": _live_session(running=False, exited_rc=1),  # crashed
            "B": _live_session(running=True, exited_rc=None),  # alive
            # C: no session at all → unknown
        }
        rows = await build_lane_rows(
            mission_dir,
            _tasks_fe({"PHASE-PLAN": []}),
            sessions,
            lambda cli: MagicMock(),
            cfgs,
        )
        assert rows["A"].liveness == "dead"
        assert rows["B"].liveness == "running"
        assert rows["C"].liveness == "unknown"


# ---------------------------------------------------------------------------
# Session-id self-heal: recover a lane's transcript by agent-id correlation when
# the spawner's time-window discovery failed (live_repl + shared projects dir).
# ---------------------------------------------------------------------------


def _write_transcript(path: Path, *, agent_id: str | None, lead_lines: int = 5) -> None:
    """Write a minimal Claude-style JSONL transcript.

    Leading lines are metadata (no agent-id), mirroring real transcripts where
    the launch identity appears a few lines in. If agent_id is given it is
    embedded in a later 'user' line (the launch prompt).
    """
    import json as _json

    lines = []
    for _ in range(lead_lines):
        lines.append(_json.dumps({"type": "permission-mode", "mode": "default"}))
    if agent_id is not None:
        lines.append(
            _json.dumps(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": f"You are {agent_id}. Begin.",
                    },
                }
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestSessionIdSelfHeal:
    """_owning_agent_id / _resolve_session_ids_by_agent + build_lane_rows fallback."""

    def test_owning_agent_id_returns_first_match(self, tmp_path: Path) -> None:
        f = tmp_path / "s.jsonl"
        _write_transcript(f, agent_id="agent-58f3")
        assert _owning_agent_id(f) == "agent-58f3"

    def test_owning_agent_id_none_when_absent(self, tmp_path: Path) -> None:
        f = tmp_path / "s.jsonl"
        _write_transcript(f, agent_id=None)
        assert _owning_agent_id(f) is None

    def test_owning_agent_id_first_wins_over_later_crossref(
        self, tmp_path: Path
    ) -> None:
        """The lane's OWN id appears first; a later cross-ref to another lane loses."""
        import json as _json

        f = tmp_path / "s.jsonl"
        f.write_text(
            "\n".join(
                [
                    _json.dumps({"type": "permission-mode"}),
                    _json.dumps(
                        {
                            "type": "user",
                            "message": {
                                "role": "user",
                                "content": "You are agent-011f.",
                            },
                        }
                    ),
                    _json.dumps(
                        {
                            "type": "assistant",
                            "message": {"content": "noting agent-58f3 is unclaimed"},
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        assert _owning_agent_id(f) == "agent-011f"

    def test_resolve_maps_lanes_to_newest_matching_transcript(
        self, tmp_path: Path
    ) -> None:
        import os

        # Two transcripts for agent-011f (a relaunch); newest mtime must win.
        old = tmp_path / "old-011f.jsonl"
        new = tmp_path / "new-011f.jsonl"
        other = tmp_path / "aud.jsonl"
        _write_transcript(old, agent_id="agent-011f")
        _write_transcript(new, agent_id="agent-011f")
        _write_transcript(other, agent_id="agent-58f3")
        # Force mtimes: old older than new.
        os.utime(old, (1_000, 1_000))
        os.utime(new, (2_000, 2_000))

        resolved = _resolve_session_ids_by_agent(
            tmp_path, {"B": "agent-011f", "A": "agent-58f3"}
        )
        assert resolved["B"] == "new-011f"  # newest, not "old-011f"
        assert resolved["A"] == "aud"

    def test_resolve_skips_unmatched_agent(self, tmp_path: Path) -> None:
        _write_transcript(tmp_path / "x.jsonl", agent_id="agent-aaaa")
        resolved = _resolve_session_ids_by_agent(tmp_path, {"A": "agent-zzzz"})
        assert resolved == {}

    def test_resolve_missing_dir_is_empty(self, tmp_path: Path) -> None:
        assert (
            _resolve_session_ids_by_agent(tmp_path / "nope", {"A": "agent-aaaa"}) == {}
        )

    @pytest.mark.asyncio
    async def test_build_lane_rows_self_heals_session_id(self, tmp_path: Path) -> None:
        """A claude lane with session_id=None recovers via STATUS agent-id → digest read."""
        mission_dir = tmp_path / "mission"
        mission_dir.mkdir()
        (mission_dir / "TASKS.md").write_text("## PHASE-PLAN\n", encoding="utf-8")

        proj = tmp_path / "proj"
        proj.mkdir()
        _write_transcript(proj / "uuid-aud.jsonl", agent_id="agent-58f3")

        tasks_fe = _tasks_fe({"PHASE-PLAN": []})
        session = _session(session_id=None, cwd=mission_dir)
        sessions = {"A": session}
        lane_cfgs = [_lane_cfg("AUDIT", "A", "Audit role", cli="claude")]
        status_rows = [_status_row("AUDIT", "working: P1-A", agent="agent-58f3")]

        fake_adapter = MagicMock()
        fake_adapter.session_log_dir.return_value = proj
        fake_adapter.session_log_path.side_effect = lambda cwd, sid: (
            proj / f"{sid}.jsonl"
        )

        with patch(
            "megalodon_ui.narrator.board_state.parse_session",
            return_value=_digest(4321),
        ):
            rows = await build_lane_rows(
                mission_dir,
                tasks_fe,
                sessions,
                lambda cli: fake_adapter,
                lane_cfgs,
                status_rows=status_rows,
            )

        # session_id recovered + persisted; digest read happened → narratable.
        assert session.session_id == "uuid-aud"
        assert rows["A"].tokens == 4321
        assert rows["A"].digest_text is not None
        assert (
            mission_dir / ".fleet" / "A.session.txt"
        ).read_text().strip() == "uuid-aud"

    @pytest.mark.asyncio
    async def test_build_lane_rows_skips_unclaimed_agent(self, tmp_path: Path) -> None:
        """An 'unclaimed' STATUS agent is not correlated (no transcript guesswork)."""
        mission_dir = tmp_path / "mission"
        mission_dir.mkdir()
        (mission_dir / "TASKS.md").write_text("## PHASE-PLAN\n", encoding="utf-8")
        proj = tmp_path / "proj"
        proj.mkdir()
        _write_transcript(proj / "uuid-x.jsonl", agent_id="agent-58f3")

        session = _session(session_id=None, cwd=mission_dir)
        fake_adapter = MagicMock()
        fake_adapter.session_log_dir.return_value = proj

        rows = await build_lane_rows(
            mission_dir,
            _tasks_fe({"PHASE-PLAN": []}),
            {"A": session},
            lambda cli: fake_adapter,
            [_lane_cfg("AUDIT", "A", "Audit role", cli="claude")],
            status_rows=[_status_row("AUDIT", "unclaimed", agent="unclaimed")],
        )
        assert session.session_id is None
        assert rows["A"].tokens is None


# ---------------------------------------------------------------------------
# CR-4: blocked task state
# ---------------------------------------------------------------------------


class TestBlockedTaskState:
    """CR-4: assemble_lane_rows surfaces state='blocked' for blocked tasks.

    Precedence: blocked > claimed > done > open.
    last/now are unaffected by blocked tasks (only claimed/done contribute).
    """

    def _cfgs(self) -> list[MagicMock]:
        return [_lane_cfg("AUDIT", "A", "Audit all findings")]

    def test_blocked_task_yields_state_blocked(self) -> None:
        """A lane with a blocked task → state == 'blocked'."""
        cfgs = self._cfgs()
        tasks = [_task("A-1", "AUDIT", "blocked task", "blocked")]
        tasks_fe = _tasks_fe({"PHASE-PLAN": tasks})
        rows = assemble_lane_rows(tasks_fe, cfgs, {"A": None}, {"A-1": 0})
        assert rows["A"].state == "blocked"

    def test_blocked_beats_claimed(self) -> None:
        """blocked takes precedence over claimed."""
        cfgs = self._cfgs()
        tasks = [
            _task("A-1", "AUDIT", "claimed task", "claimed", "2026-05-01T10:00:00Z"),
            _task("A-2", "AUDIT", "blocked task", "blocked"),
        ]
        tasks_fe = _tasks_fe({"PHASE-PLAN": tasks})
        rows = assemble_lane_rows(tasks_fe, cfgs, {"A": None}, {"A-1": 0, "A-2": 1})
        assert rows["A"].state == "blocked"

    def test_blocked_beats_done(self) -> None:
        """blocked takes precedence over done."""
        cfgs = self._cfgs()
        tasks = [
            _task("A-1", "AUDIT", "done task", "done", "2026-05-01T10:00:00Z"),
            _task("A-2", "AUDIT", "blocked task", "blocked"),
        ]
        tasks_fe = _tasks_fe({"PHASE-PLAN": tasks})
        rows = assemble_lane_rows(tasks_fe, cfgs, {"A": None}, {"A-1": 0, "A-2": 1})
        assert rows["A"].state == "blocked"

    def test_blocked_beats_open(self) -> None:
        """blocked takes precedence over open."""
        cfgs = self._cfgs()
        tasks = [
            _task("A-1", "AUDIT", "open task", "open"),
            _task("A-2", "AUDIT", "blocked task", "blocked"),
        ]
        tasks_fe = _tasks_fe({"PHASE-PLAN": tasks})
        rows = assemble_lane_rows(tasks_fe, cfgs, {"A": None}, {"A-1": 0, "A-2": 1})
        assert rows["A"].state == "blocked"

    def test_no_blocked_task_state_unaffected(self) -> None:
        """A lane with no blocked task continues to derive state from claimed/done/open."""
        cfgs = self._cfgs()
        tasks = [
            _task("A-1", "AUDIT", "claimed task", "claimed", "2026-05-01T10:00:00Z"),
            _task("A-2", "AUDIT", "done task", "done", "2026-04-01T10:00:00Z"),
        ]
        tasks_fe = _tasks_fe({"PHASE-PLAN": tasks})
        rows = assemble_lane_rows(tasks_fe, cfgs, {"A": None}, {"A-1": 0, "A-2": 1})
        assert rows["A"].state == "claimed"

    def test_blocked_task_does_not_populate_last_or_now(self) -> None:
        """Blocked tasks do not contribute to last/now — only claimed/done tasks do."""
        cfgs = self._cfgs()
        tasks = [
            _task("A-1", "AUDIT", "blocked task", "blocked"),
        ]
        tasks_fe = _tasks_fe({"PHASE-PLAN": tasks})
        rows = assemble_lane_rows(tasks_fe, cfgs, {"A": None}, {"A-1": 0})
        row = rows["A"]
        assert row.state == "blocked"
        assert row.last is None
        assert row.now is None
        assert row.goal == "Audit all findings"  # role fallback

    def test_blocked_with_done_preserves_last(self) -> None:
        """When blocked + done tasks coexist, last is still populated from done."""
        cfgs = self._cfgs()
        tasks = [
            _task("A-1", "AUDIT", "done task", "done", "2026-05-01T10:00:00Z"),
            _task("A-2", "AUDIT", "blocked task", "blocked"),
        ]
        tasks_fe = _tasks_fe({"PHASE-PLAN": tasks})
        rows = assemble_lane_rows(tasks_fe, cfgs, {"A": None}, {"A-1": 0, "A-2": 1})
        row = rows["A"]
        assert row.state == "blocked"
        assert row.last is not None
        assert row.last["task_id"] == "A-1"
        assert row.now is None

    def test_state_blocked_in_to_dict(self) -> None:
        """state='blocked' is included in the public to_dict() payload."""
        cfgs = self._cfgs()
        tasks = [_task("A-1", "AUDIT", "blocked task", "blocked")]
        tasks_fe = _tasks_fe({"PHASE-PLAN": tasks})
        rows = assemble_lane_rows(tasks_fe, cfgs, {"A": None}, {"A-1": 0})
        d = rows["A"].to_dict()
        assert d["state"] == "blocked"

    def test_other_lane_unaffected_by_blocked(self) -> None:
        """Blocked tasks in one lane do not affect other lanes."""
        cfgs = [_lane_cfg("AUDIT", "A", "role A"), _lane_cfg("BUILD", "B", "role B")]
        tasks = [
            _task("A-1", "AUDIT", "blocked", "blocked"),
            _task("B-1", "BUILD", "claimed", "claimed", "2026-05-01T10:00:00Z"),
        ]
        tasks_fe = _tasks_fe({"PHASE-PLAN": tasks})
        rows = assemble_lane_rows(
            tasks_fe, cfgs, {"A": None, "B": None}, {"A-1": 0, "B-1": 1}
        )
        assert rows["A"].state == "blocked"
        assert rows["B"].state == "claimed"


# ---------------------------------------------------------------------------
# build_lane_rows — async wrapper
# ---------------------------------------------------------------------------


class TestBuildLaneRows:
    """Async wrapper tests — mock parse_session so no real I/O."""

    @pytest.mark.asyncio
    async def test_claude_lane_with_session_id_reads_transcript(
        self, tmp_path: Path
    ) -> None:
        """Claude lane + valid session_id → digest read, tokens populated."""
        mission_dir = tmp_path / "mission"
        mission_dir.mkdir()
        # Write a minimal TASKS.md so doc-order capture works
        (mission_dir / "TASKS.md").write_text(
            "## PHASE-PLAN\n- [ ] [A] `T-1` — task one\n",
            encoding="utf-8",
        )

        tasks_fe = _tasks_fe({"PHASE-PLAN": []})

        session = _session(session_id="abc123", cwd=mission_dir)
        sessions = {"A": session}

        # Stub the adapter_resolver to return a ClaudeAdapter-like mock
        fake_adapter = MagicMock()
        # session_log_path returns a plausible path (doesn't need to exist for mock)
        fake_path = tmp_path / "abc123.jsonl"
        fake_adapter.session_log_path.return_value = fake_path

        def adapter_resolver(cli: str) -> MagicMock:
            return fake_adapter

        lane_cfgs = [_lane_cfg("AUDIT", "A", "Audit role", cli="claude")]

        fake_digest = _digest(1234)

        with patch(
            "megalodon_ui.narrator.board_state.parse_session",
            return_value=fake_digest,
        ):
            rows = await build_lane_rows(
                mission_dir, tasks_fe, sessions, adapter_resolver, lane_cfgs
            )

        assert rows["A"].tokens == 1234
        assert rows["A"].narrator_ok is False  # scheduler flips this
        # digest_text is set post-assembly from the read digest. The fake digest
        # has no events, so render_for_prompt returns the empty-activity marker.
        assert rows["A"].digest_text == "- (no activity yet)"

    @pytest.mark.asyncio
    async def test_non_claude_lane_skips_transcript(self, tmp_path: Path) -> None:
        """Non-claude lane → no transcript read, tokens=None."""
        mission_dir = tmp_path / "mission"
        mission_dir.mkdir()
        (mission_dir / "TASKS.md").write_text(
            "## PHASE-PLAN\n",
            encoding="utf-8",
        )

        tasks_fe = _tasks_fe({"PHASE-PLAN": []})
        session = _session(session_id="xyz999", cwd=mission_dir)
        sessions = {"A": session}

        lane_cfgs = [_lane_cfg("AUDIT", "A", "Audit role", cli="codex")]
        fake_adapter = MagicMock()

        def adapter_resolver(cli: str) -> MagicMock:
            return fake_adapter

        call_count = 0

        def counting_parse(path: Any) -> SessionDigest:
            nonlocal call_count
            call_count += 1
            return _digest(999)

        with patch(
            "megalodon_ui.narrator.board_state.parse_session",
            side_effect=counting_parse,
        ):
            rows = await build_lane_rows(
                mission_dir, tasks_fe, sessions, adapter_resolver, lane_cfgs
            )

        assert call_count == 0  # not called
        assert rows["A"].tokens is None

    @pytest.mark.asyncio
    async def test_none_session_id_skips_transcript(self, tmp_path: Path) -> None:
        """Claude lane with session_id=None → no transcript, tokens=None."""
        mission_dir = tmp_path / "mission"
        mission_dir.mkdir()
        (mission_dir / "TASKS.md").write_text("## PHASE-PLAN\n", encoding="utf-8")

        tasks_fe = _tasks_fe({"PHASE-PLAN": []})
        session = _session(session_id=None, cwd=mission_dir)
        sessions = {"A": session}

        lane_cfgs = [_lane_cfg("AUDIT", "A", "role", cli="claude")]
        fake_adapter = MagicMock()
        fake_adapter.session_log_path.return_value = None

        def adapter_resolver(cli: str) -> MagicMock:
            return fake_adapter

        call_count = 0

        def counting_parse(path: Any) -> SessionDigest:
            nonlocal call_count
            call_count += 1
            return _digest(42)

        with patch(
            "megalodon_ui.narrator.board_state.parse_session",
            side_effect=counting_parse,
        ):
            rows = await build_lane_rows(
                mission_dir, tasks_fe, sessions, adapter_resolver, lane_cfgs
            )

        assert call_count == 0
        assert rows["A"].tokens is None

    @pytest.mark.asyncio
    async def test_status_rows_forwarded_to_assembler(self, tmp_path: Path) -> None:
        """build_lane_rows threads status_rows into the assembler (no task rows)."""
        mission_dir = tmp_path / "mission"
        mission_dir.mkdir()
        (mission_dir / "TASKS.md").write_text("## PHASE-PLAN\n", encoding="utf-8")

        tasks_fe = _tasks_fe({"PHASE-PLAN": []})
        sessions = {"A": _session(session_id=None, cwd=mission_dir)}
        lane_cfgs = [_lane_cfg("AUDIT", "A", "Audit role", cli="claude")]
        status_rows = [_status_row("AUDIT", "working: P1-B", notes="surveying")]

        def adapter_resolver(cli: str) -> MagicMock:
            return MagicMock()

        rows = await build_lane_rows(
            mission_dir,
            tasks_fe,
            sessions,
            adapter_resolver,
            lane_cfgs,
            status_rows=status_rows,
        )

        assert rows["A"].state == "claimed"
        assert rows["A"].now is not None
        assert rows["A"].now["task_id"] == "P1-B"

    @pytest.mark.asyncio
    async def test_non_utf8_tasks_md_does_not_crash(self, tmp_path: Path) -> None:
        """A TASKS.md with a non-UTF8 byte must not raise (BUG 1).

        ``_capture_doc_order`` is called unguarded from ``build_lane_rows`` and
        the exception propagates through the narrator tick → scheduler loop,
        killing the narrator permanently. Reading with errors='replace' inside a
        try/except must keep build_lane_rows returning rows.
        """
        mission_dir = tmp_path / "mission"
        mission_dir.mkdir()
        # Write a raw non-UTF8 byte (0xFF is invalid as a UTF-8 start byte).
        (mission_dir / "TASKS.md").write_bytes(
            b"## PHASE-PLAN\n- [ ] [A] `T-1` \xff bad byte\n"
        )

        tasks_fe = _tasks_fe({"PHASE-PLAN": []})
        sessions = {"A": _session(session_id=None, cwd=mission_dir)}
        lane_cfgs = [_lane_cfg("AUDIT", "A", "Audit role", cli="claude")]

        def adapter_resolver(cli: str) -> MagicMock:
            return MagicMock()

        rows = await build_lane_rows(
            mission_dir, tasks_fe, sessions, adapter_resolver, lane_cfgs
        )
        assert "A" in rows
