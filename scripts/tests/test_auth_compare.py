"""Tests for megalodon_ui.auth.compare_token — constant-time equality.

Plan §6.3 contract:
    def compare_token(supplied: str, stored: str | None) -> bool:
        return stored is not None and secrets.compare_digest(supplied, stored)

Must reject empty/None on either side and never short-circuit on length.
"""

from megalodon_ui.auth import compare_token


def test_compare_returns_true_when_equal():
    assert compare_token("abc123xyz", "abc123xyz") is True


def test_compare_returns_false_when_different():
    assert compare_token("abc123xyz", "abc123xyZ") is False


def test_compare_returns_false_when_stored_is_none():
    assert compare_token("abc123xyz", None) is False


def test_compare_returns_false_when_supplied_is_empty():
    assert compare_token("", "abc123xyz") is False


def test_compare_returns_false_when_stored_is_empty():
    assert compare_token("abc123xyz", "") is False


def test_compare_returns_false_when_both_empty():
    # Even matching empties must reject — empty bearer is never a valid credential.
    assert compare_token("", "") is False


def test_compare_returns_false_on_length_mismatch_without_raising():
    # secrets.compare_digest tolerates differing lengths; the wrapper must too.
    assert compare_token("short", "muchlongertoken") is False
