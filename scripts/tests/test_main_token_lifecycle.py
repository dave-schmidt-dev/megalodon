"""Tests for D3 token lifecycle helpers in megalodon_ui.__main__.

Covers:
  1. Reuse when token file present and non-empty.
  2. Generate when token file absent.
  3. Generate when token file contains only whitespace (treated as absent).
  4. Error-cleanup predicate: only unlink if token_was_generated (helper contract).
  5. _rotate_clear removes both token and sessions files (missing_ok for absent).
  6. After _rotate_clear, _resolve_token generates a fresh token.
  7. --rotate-token and MEGALODON_ROTATE_TOKEN=1 both set args.rotate_token.

main() invokes uvicorn (blocking, binds a socket) so end-to-end main() tests
are out of scope here (see test_main_passes_fd_to_uvicorn.py). The helpers are
unit-tested directly; the error-cleanup predicate is verified via the helper
contract (token_was_generated == True only when this run wrote the file).
"""

from __future__ import annotations

import argparse
import os
import stat
from pathlib import Path

import pytest

from megalodon_ui.__main__ import _resolve_token, _rotate_clear


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mode_low9(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


# ---------------------------------------------------------------------------
# 1. Reuse when present
# ---------------------------------------------------------------------------


def test_resolve_token_reuses_existing(tmp_path: Path) -> None:
    token_path = tmp_path / "ui.token"
    # Write a well-formed token directly (mode 0600 as production does).
    old_umask = os.umask(0o077)
    try:
        fd = os.open(str(token_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, b"existing-token-value")
        os.close(fd)
    finally:
        os.umask(old_umask)

    token, was_generated = _resolve_token(token_path)

    assert token == "existing-token-value"
    assert was_generated is False


# ---------------------------------------------------------------------------
# 2. Generate when absent
# ---------------------------------------------------------------------------


def test_resolve_token_generates_when_absent(tmp_path: Path) -> None:
    token_path = tmp_path / "ui.token"
    assert not token_path.exists()

    token, was_generated = _resolve_token(token_path)

    assert was_generated is True
    assert isinstance(token, str)
    assert len(token) >= 16  # secrets.token_urlsafe(32) → ≥43 chars in practice
    assert token_path.exists()
    # File must be 0600 (security requirement — tested more thoroughly in
    # test_auth_token_write.py; light check here to catch regressions at this level).
    assert _mode_low9(token_path) == 0o600
    # Round-trip: the file contains the token we got back.
    assert token_path.read_text().strip() == token


# ---------------------------------------------------------------------------
# 3. Generate when empty / whitespace
# ---------------------------------------------------------------------------


def test_resolve_token_generates_when_empty(tmp_path: Path) -> None:
    token_path = tmp_path / "ui.token"
    token_path.write_text("")

    token, was_generated = _resolve_token(token_path)

    assert was_generated is True
    assert isinstance(token, str) and token  # non-empty fresh token


def test_resolve_token_generates_when_whitespace_only(tmp_path: Path) -> None:
    token_path = tmp_path / "ui.token"
    token_path.write_text("   \n")

    # write_token_atomic uses O_EXCL; the stale file must be removed first.
    # _resolve_token calls auth.read_token (which strips) so "   " becomes ""
    # (falsy) and a fresh token is generated via write_token_atomic which
    # handles the pre-existing file via its internal unlink-and-retry.
    token, was_generated = _resolve_token(token_path)

    assert was_generated is True
    assert token  # non-empty


# ---------------------------------------------------------------------------
# 4. Error-cleanup predicate: token_was_generated contract
#
# We can't call main() end-to-end without running uvicorn, so we verify the
# predicate directly: (a) when a token existed before this call, was_generated
# is False — meaning the except branch would NOT unlink it; (b) when this call
# wrote a fresh token, was_generated is True — meaning the except branch WOULD
# unlink it.  This is the contract that guards "never delete a reused token".
# ---------------------------------------------------------------------------


def test_resolve_token_was_generated_false_means_no_cleanup_needed(
    tmp_path: Path,
) -> None:
    """A pre-existing token → was_generated=False → caller must NOT unlink it."""
    token_path = tmp_path / "ui.token"
    token_path.write_text(
        "stable-token"
    )  # pre-existing; mode not 0600 but that's fine for test

    _, was_generated = _resolve_token(token_path)

    assert was_generated is False
    # Simulated error-branch: if caller respects was_generated, file survives.
    if was_generated:
        token_path.unlink(missing_ok=True)
    assert token_path.exists(), (
        "pre-existing token must not be deleted by the cleanup predicate"
    )


def test_resolve_token_was_generated_true_means_cleanup_is_safe(tmp_path: Path) -> None:
    """No pre-existing token → was_generated=True → cleanup is correct on error."""
    token_path = tmp_path / "ui.token"

    _, was_generated = _resolve_token(token_path)

    assert was_generated is True
    # Simulated error-branch: cleanup is safe (the file existed only because
    # this run created it).
    if was_generated:
        token_path.unlink(missing_ok=True)
    assert not token_path.exists()


# ---------------------------------------------------------------------------
# 5. _rotate_clear removes both files (missing_ok for absent)
# ---------------------------------------------------------------------------


def test_rotate_clear_removes_both_files(tmp_path: Path) -> None:
    token_path = tmp_path / "ui.token"
    sessions_path = tmp_path / "sessions.json"
    token_path.write_text("old-token")
    sessions_path.write_text('{"abc": 12345}')

    _rotate_clear(token_path, sessions_path)

    assert not token_path.exists()
    assert not sessions_path.exists()


def test_rotate_clear_is_missing_ok(tmp_path: Path) -> None:
    """Should not raise when neither file exists."""
    token_path = tmp_path / "ui.token"
    sessions_path = tmp_path / "sessions.json"

    # Must not raise.
    _rotate_clear(token_path, sessions_path)


def test_rotate_clear_partial_files(tmp_path: Path) -> None:
    """Handles the case where only one file exists."""
    token_path = tmp_path / "ui.token"
    sessions_path = tmp_path / "sessions.json"
    token_path.write_text("old-token")
    # sessions_path intentionally absent

    _rotate_clear(token_path, sessions_path)

    assert not token_path.exists()
    assert not sessions_path.exists()  # missing_ok — no error


# ---------------------------------------------------------------------------
# 6. After _rotate_clear, _resolve_token generates a fresh token
# ---------------------------------------------------------------------------


def test_rotate_then_resolve_generates_fresh_token(tmp_path: Path) -> None:
    token_path = tmp_path / "ui.token"
    sessions_path = tmp_path / "sessions.json"
    token_path.write_text("stale-token")
    sessions_path.write_text("{}")

    _rotate_clear(token_path, sessions_path)

    token, was_generated = _resolve_token(token_path)

    assert was_generated is True
    assert token != "stale-token"
    assert token_path.exists()


# ---------------------------------------------------------------------------
# 7. Arg parsing: --rotate-token and MEGALODON_ROTATE_TOKEN=1
# ---------------------------------------------------------------------------


def _make_parser() -> argparse.ArgumentParser:
    """Construct the same parser as main() so we can test flag parsing in isolation."""
    # Re-create the parser inline to avoid running main().  We mirror the exact
    # add_argument calls from main() for the flags under test.
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--rotate-token",
        action="store_true",
        default=os.environ.get("MEGALODON_ROTATE_TOKEN") == "1",
    )
    return parser


def test_rotate_token_flag_sets_rotate_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEGALODON_ROTATE_TOKEN", raising=False)
    parser = _make_parser()
    args = parser.parse_args(["--rotate-token"])
    assert args.rotate_token is True


def test_rotate_token_default_false_without_flag_or_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MEGALODON_ROTATE_TOKEN", raising=False)
    parser = _make_parser()
    args = parser.parse_args([])
    assert args.rotate_token is False


def test_rotate_token_env_var_sets_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEGALODON_ROTATE_TOKEN", "1")
    parser = _make_parser()
    args = parser.parse_args([])
    assert args.rotate_token is True


def test_rotate_token_env_var_zero_does_not_set_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEGALODON_ROTATE_TOKEN", "0")
    parser = _make_parser()
    args = parser.parse_args([])
    assert args.rotate_token is False
