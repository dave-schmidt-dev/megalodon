"""Tests for megalodon_ui.legacy_history — read-only v9.0 HISTORY.md parser.

Covers all 4 legacy format variants (CV-10) plus a mixed-variant file.
"""

from __future__ import annotations

from pathlib import Path


from megalodon_ui.legacy_history import HistoryEntry, parse_file

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "legacy_history"


class TestParseVariant1:
    """Variant 1: LANE-{short} prefix, no (notes) suffix."""

    def test_parse_variant_1(self) -> None:
        entries = parse_file(FIXTURE_DIR / "variant_1.md")
        assert len(entries) == 5
        assert all(e.variant == 1 for e in entries), (
            f"Expected all variant=1, got: {[e.variant for e in entries]}"
        )

    def test_lane_short_normalized(self) -> None:
        entries = parse_file(FIXTURE_DIR / "variant_1.md")
        for e in entries:
            assert len(e.lane_short) == 1, (
                f"Expected single-char lane_short, got: {e.lane_short!r}"
            )
            assert e.lane_short.isupper(), (
                f"Expected upper-case lane, got: {e.lane_short!r}"
            )
            # LANE- prefix must have been stripped
            assert not e.lane_short.startswith("LANE"), (
                f"LANE- prefix not stripped: {e.lane_short!r}"
            )

    def test_specific_lanes(self) -> None:
        entries = parse_file(FIXTURE_DIR / "variant_1.md")
        lanes = [e.lane_short for e in entries]
        # B, E, B, C, B from the fixture
        assert lanes == ["B", "E", "B", "C", "B"]


class TestParseVariant2:
    """Variant 2: bare short lane code, no (notes) suffix."""

    def test_parse_variant_2(self) -> None:
        entries = parse_file(FIXTURE_DIR / "variant_2.md")
        assert len(entries) == 5
        assert all(e.variant == 2 for e in entries), (
            f"Expected all variant=2, got: {[e.variant for e in entries]}"
        )

    def test_notes_empty(self) -> None:
        entries = parse_file(FIXTURE_DIR / "variant_2.md")
        for e in entries:
            assert e.notes == "", (
                f"Expected empty notes for variant 2, got: {e.notes!r}"
            )

    def test_specific_lanes(self) -> None:
        entries = parse_file(FIXTURE_DIR / "variant_2.md")
        lanes = [e.lane_short for e in entries]
        assert lanes == ["A", "C", "D", "F", "A"]


class TestParseVariant3:
    """Variant 3: pipe-spacing drift (inconsistent whitespace around pipes)."""

    def test_parse_variant_3(self) -> None:
        entries = parse_file(FIXTURE_DIR / "variant_3.md")
        assert len(entries) == 5
        assert all(e.variant == 3 for e in entries), (
            f"Expected all variant=3, got: {[e.variant for e in entries]}"
        )

    def test_lanes_parsed_despite_drift(self) -> None:
        entries = parse_file(FIXTURE_DIR / "variant_3.md")
        for e in entries:
            assert len(e.lane_short) == 1
            assert e.lane_short in {"A", "B", "C", "D", "E", "F"}

    def test_all_fields_non_empty(self) -> None:
        entries = parse_file(FIXTURE_DIR / "variant_3.md")
        for e in entries:
            assert e.utc
            assert e.agent
            assert e.task_id
            assert e.finding_path
            assert e.severity


class TestParseVariant4:
    """Variant 4: frontmatter-style YAML-like prefix."""

    def test_parse_variant_4(self) -> None:
        entries = parse_file(FIXTURE_DIR / "variant_4.md")
        assert len(entries) == 4
        assert all(e.variant == 4 for e in entries), (
            f"Expected all variant=4, got: {[e.variant for e in entries]}"
        )

    def test_fields_extracted(self) -> None:
        entries = parse_file(FIXTURE_DIR / "variant_4.md")
        first = entries[0]
        assert first.utc == "2026-05-16T17:39Z"
        assert first.agent == "agent-dcbc"
        assert first.lane_short == "A"
        assert first.task_id == "P1-A"
        assert first.severity == "DELTA"

    def test_lanes(self) -> None:
        entries = parse_file(FIXTURE_DIR / "variant_4.md")
        lanes = [e.lane_short for e in entries]
        assert lanes == ["A", "E", "C", "A"]


class TestParseMixedFile:
    """Mixed variant file: all 4 variants in one file (CV-10)."""

    def test_all_variants_present(self) -> None:
        entries = parse_file(FIXTURE_DIR / "mixed_variants.md")
        variants_found = {e.variant for e in entries}
        assert variants_found == {1, 2, 3, 4}, (
            f"Expected all 4 variants, got: {variants_found}"
        )

    def test_total_entry_count(self) -> None:
        entries = parse_file(FIXTURE_DIR / "mixed_variants.md")
        # 11 data lines in the fixture (excluding header, comments, blank lines)
        assert len(entries) == 11

    def test_entries_are_history_entry_instances(self) -> None:
        entries = parse_file(FIXTURE_DIR / "mixed_variants.md")
        for e in entries:
            assert isinstance(e, HistoryEntry)
            assert e.variant in {1, 2, 3, 4}

    def test_all_lane_shorts_valid(self) -> None:
        entries = parse_file(FIXTURE_DIR / "mixed_variants.md")
        for e in entries:
            assert e.lane_short in {"A", "B", "C", "D", "E", "F"}, (
                f"Unexpected lane_short: {e.lane_short!r}"
            )
