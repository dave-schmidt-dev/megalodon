"""Tests for megalodon_ui/mission_config/regex_builder.py (P1.3).

11 tests total: 10 unit tests + 1 semantic-equivalence test (CV-4 corpus).
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest

from megalodon_ui.mission_config.schema import (
    HarnessBinding,
    LaneConfig,
    MissionConfig,
    MissionInfo,
    TaskIdPattern,
)
from megalodon_ui.mission_config.regex_builder import (
    build_lane_re,
    build_lane_short_charclass,
    build_phase_header_re,
    build_status_row_re,
    build_task_id_re,
    build_task_line_re,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HARNESS = HarnessBinding(cli="claude", model="claude-sonnet-4-6")
_MISSION = MissionInfo(id="test-mission", utc_started="2024-01-01T00:00:00Z")


def _make_config(
    lane_names: list[str],
    lane_shorts: list[str | None] | None = None,
    phases: list[str] | None = None,
    patterns: list[str] | None = None,
) -> MissionConfig:
    if lane_shorts is None:
        lane_shorts = [None] * len(lane_names)
    lanes = [
        LaneConfig(name=n, short=s, harness=_HARNESS)
        for n, s in zip(lane_names, lane_shorts)
    ]
    return MissionConfig(
        mission=_MISSION,
        lanes=lanes,
        phases=phases or ["PHASE-A"],
        task_id_patterns=TaskIdPattern(patterns=patterns or [r"^[A-Z][A-Za-z0-9\-\.]*$"]),
    )


def _default_v9_0(tmp_path: Path) -> MissionConfig:
    from megalodon_ui.mission_config.default_v9_0_shape import synthesize
    return synthesize(tmp_path)


# ---------------------------------------------------------------------------
# Test 1: single lane produces anchored alternation
# ---------------------------------------------------------------------------

def test_single_lane_produces_anchored_alternation():
    config = _make_config(["ALPHA"], ["A"])
    pat = build_lane_re(config)
    assert pat.pattern == "^(ALPHA)$"
    assert pat.match("ALPHA")
    assert not pat.match("BETA")


# ---------------------------------------------------------------------------
# Test 2: multi-lane alternation
# ---------------------------------------------------------------------------

def test_multi_lane_alternation():
    config = _make_config(["ALPHA", "BETA", "GAMMA"], ["A", "B", "C"])
    pat = build_lane_re(config)
    assert pat.pattern == "^(ALPHA|BETA|GAMMA)$"
    assert pat.match("ALPHA")
    assert pat.match("BETA")
    assert pat.match("GAMMA")
    assert not pat.match("DELTA")


# ---------------------------------------------------------------------------
# Test 3: short charclass — six contiguous lanes A..F (default v9.0)
# ---------------------------------------------------------------------------

def test_short_charclass_six_contiguous_lanes(tmp_path):
    config = _default_v9_0(tmp_path)
    cc = build_lane_short_charclass(config)
    assert cc == "[A-F]"


# ---------------------------------------------------------------------------
# Test 4: short charclass — non-contiguous codes
# ---------------------------------------------------------------------------

def test_short_charclass_non_contiguous():
    config = _make_config(
        ["LANE1", "LANE2", "LANE3"],
        ["A", "C", "E"],
    )
    cc = build_lane_short_charclass(config)
    assert cc == "[ACE]"


# ---------------------------------------------------------------------------
# Test 5: short charclass — over 26 lanes (auto-assigned A..Z + AA)
# ---------------------------------------------------------------------------

def test_short_charclass_over_26_lanes():
    names = [f"LANE{i}" for i in range(27)]
    config = _make_config(names, [None] * 27, phases=["PHASE-A"])
    cc = build_lane_short_charclass(config)
    # Must be alternation form
    assert cc.startswith("(")
    assert cc.endswith(")")
    assert "A|B|" in cc
    assert "|AA" in cc


# ---------------------------------------------------------------------------
# Test 6: PM-8 — phase header sorted length-descending in pattern
# ---------------------------------------------------------------------------

def test_phase_header_length_descending_order():
    config = _make_config(
        ["AUDIT"],
        ["A"],
        phases=["PHASE", "PHASE-A", "PHASE-AUDIT-EXTENDED"],
    )
    pat = build_phase_header_re(config)
    p = pat.pattern

    # Extract the alternation body: content inside (?P<phase>...)
    m = re.search(r"\(\?P<phase>([^)]+)\)", p)
    assert m is not None, f"Could not find named group in pattern: {p}"
    alternation_body = m.group(1)

    # Split on | to get ordered alternatives
    alternatives = alternation_body.split("|")
    assert "PHASE-AUDIT-EXTENDED" in alternatives
    assert "PHASE-A" in alternatives
    assert "PHASE" in alternatives

    idx_extended = alternatives.index("PHASE-AUDIT-EXTENDED")
    # PHASE-A in alternation (not inside PHASE-AUDIT-EXTENDED): find its standalone pos
    idx_phase_a = alternatives.index("PHASE-A")
    # PHASE bare: find standalone occurrence (not as prefix of longer name)
    idx_phase_bare = next(
        i for i, alt in enumerate(alternatives) if alt == "PHASE"
    )

    assert idx_extended < idx_phase_a < idx_phase_bare, (
        f"Expected length-descending order in alternation {alternatives!r}"
    )


# ---------------------------------------------------------------------------
# Test 7: PM-8 — compiled regex captures longest match first
# ---------------------------------------------------------------------------

def test_phase_header_matches_longest_first():
    config = _make_config(
        ["AUDIT"],
        ["A"],
        phases=["PHASE", "PHASE-A", "PHASE-AUDIT-EXTENDED"],
    )
    pat = build_phase_header_re(config)
    m = pat.search("## PHASE-AUDIT-EXTENDED")
    assert m is not None
    assert m.group("phase") == "PHASE-AUDIT-EXTENDED"


# ---------------------------------------------------------------------------
# Test 8: task_line_re byte-equal to canonical v9.0 shape (with [A-F])
# ---------------------------------------------------------------------------

def test_task_line_round_trip_against_v9_0_default(tmp_path):
    """Builder must reproduce the canonical task-line pattern for default v9.0.

    The v9.0 server.py used the overly-broad ``[A-Z]`` by hand; the builder
    derives the correct ``[A-F]`` from config (6 lanes, shorts A-F).  This test
    pins the canonical form so future config changes don't silently drift the
    charclass.
    """
    config = _default_v9_0(tmp_path)
    built = build_task_line_re(config)

    # Canonical pattern: charclass is [A-F] (derived from 6-lane default config)
    canonical = (
        r"^\s*-\s*\[(?P<state_block>[^\]]*)\]\s*\[LANE-(?P<lane>[A-F])\]\s*"
        r"`(?P<task_id>[^`]+)`\s*(?:[—-]\s*(?P<description>.*))?$"
    )
    assert built.pattern == canonical, (
        f"Pattern drift detected.\n  built:    {built.pattern!r}\n"
        f"  expected: {canonical!r}"
    )
    assert built.flags & re.MULTILINE


# ---------------------------------------------------------------------------
# Test 9: task_id_re strips leading ^ and trailing $
# ---------------------------------------------------------------------------

def test_task_id_re_strips_leading_caret_trailing_dollar():
    config = _make_config(
        ["LANE1"],
        ["A"],
        patterns=["^foo$", "^bar$"],
    )
    pat = build_task_id_re(config)
    assert pat.pattern == "^(foo|bar)$", (
        f"Expected '^(foo|bar)$', got {pat.pattern!r}"
    )
    assert pat.match("foo")
    assert pat.match("bar")
    assert not pat.match("^foo$")


# ---------------------------------------------------------------------------
# Test 10: status_row_re byte-equal to v9.0 server.py shape
# ---------------------------------------------------------------------------

def test_status_row_re_byte_equal_to_v9_0(tmp_path):
    config = _default_v9_0(tmp_path)
    built = build_status_row_re(config)

    # Exact pattern from megalodon_ui/server.py lines 64-71
    v9_0_pattern = (
        r"^\|\s*(?P<lane>[A-Z][A-Z\- ]*?)\s*\|\s*"
        r"(?P<agent>[^|]+?)\s*\|\s*"
        r"(?P<state>[^|]+?)\s*\|\s*"
        r"(?P<last_utc>[^|]+?)\s*\|\s*"
        r"(?P<notes>.*?)\s*\|\s*$"
    )
    assert built.pattern == v9_0_pattern, (
        f"Status-row RE drift.\n  built:    {built.pattern!r}\n"
        f"  expected: {v9_0_pattern!r}"
    )
    assert built.flags & re.MULTILINE


# ---------------------------------------------------------------------------
# Test 11: semantic equivalence — CV-4 full corpus (≥60 strings)
# ---------------------------------------------------------------------------

def test_semantic_equivalence_v9_0_default(tmp_path):
    """Full corpus test proving v9.1 regex_builder output matches v9.0 TASK_ID_RE.

    Uses the three corpus helpers from _corpus.py (positive 30 + negative 30 +
    archive-extracted IDs). The only intentional v9.0→v9.1 semantic change is
    CHALLENGE-* acceptance (CR-5): v9.0's hardcoded TASK_ID_RE excluded CHALLENGE-*;
    v9.1's default_v9_0_shape includes CHALLENGE-[A-Z0-9_-]+ in task_id_patterns.
    CHALLENGE-* strings are asserted True on v9.1 and skipped on v9.0 comparison.
    """
    from megalodon_ui.mission_config.default_v9_0_shape import synthesize
    from megalodon_ui.mission_config.regex_builder import build_task_id_re
    from scripts._validation import TASK_ID_RE as V9_0_TASK_ID_RE
    from scripts.tests._corpus import archive_task_ids, positive_corpus, negative_corpus
    from pathlib import Path

    cfg = synthesize(Path("/tmp"))
    new_re = build_task_id_re(cfg)

    # Build the unified corpus.
    archive = archive_task_ids(Path("/Users/dave/Documents/Projects/megalodon"))
    corpus = positive_corpus() + negative_corpus() + archive

    for s in corpus:
        new_match = bool(new_re.match(s))
        v9_0_match = bool(V9_0_TASK_ID_RE.match(s))
        # CR-5 nuance: v9.1 ACCEPTS CHALLENGE-* (default_v9_0_shape includes
        # CHALLENGE-[A-Z0-9_-]+ in task_id_patterns); v9.0 hardcoded TASK_ID_RE
        # REJECTED CHALLENGE-*. Adapt the assertion: if s starts with
        # "CHALLENGE-", expect new=True and skip the v9.0 comparison.
        if s.startswith("CHALLENGE-"):
            assert new_match, f"v9.1 should accept CHALLENGE-* but rejected {s!r}"
            continue
        assert new_match == v9_0_match, (
            f"divergence on {s!r}: new={new_match}, v9.0={v9_0_match}"
        )


# ---------------------------------------------------------------------------
# Test 12: corpus size guard — regression-guard against accidental shrinking
# ---------------------------------------------------------------------------

def test_corpus_has_minimum_size():
    """Cheap regression-guard: positive and negative lists must stay at 30 each."""
    from scripts.tests._corpus import positive_corpus, negative_corpus

    pos = positive_corpus()
    neg = negative_corpus()

    assert len(pos) == 30, f"positive_corpus() has {len(pos)} entries, expected 30"
    assert len(neg) == 30, f"negative_corpus() has {len(neg)} entries, expected 30"
    assert len(pos) + len(neg) >= 60, (
        f"combined corpus too small: {len(pos) + len(neg)} < 60"
    )
