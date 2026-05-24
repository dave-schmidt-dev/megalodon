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
    _pick_latest,
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
