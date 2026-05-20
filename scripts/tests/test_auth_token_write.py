"""Tests for megalodon_ui.auth token IO helpers.

Plan §6.3 contract:
    - ``generate_token() -> str`` returns secrets.token_urlsafe(BEARER_TOKEN_BYTES).
    - ``write_token_atomic(path, token)`` uses ``umask(0o077) + O_EXCL + fchmod(0o600)``;
      unlinks and retries exactly once on a pre-existing file.
    - ``read_token(path) -> str | None`` returns ``None`` when the file is absent.

Mode-0600 must hold even when the test runs under a permissive umask
(research #7: NFS/FUSE may ignore umask, so fchmod is the safety net).
"""

import os
import stat
from pathlib import Path

import pytest

from megalodon_ui._v92_constants import BEARER_TOKEN_BYTES
from megalodon_ui.auth import generate_token, read_token, write_token_atomic


def _mode_low9(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_generate_token_returns_str_of_expected_entropy():
    tok = generate_token()
    assert isinstance(tok, str)
    # token_urlsafe(n) yields ceil(n * 4 / 3) base64-ish chars; for n=32, len >= 32.
    assert len(tok) >= BEARER_TOKEN_BYTES


def test_generate_token_is_random_each_call():
    assert generate_token() != generate_token()


def test_write_creates_file_with_mode_0600(tmp_path: Path):
    target = tmp_path / "ui.token"
    write_token_atomic(target, "abc123")
    assert target.exists()
    assert _mode_low9(target) == 0o600


def test_write_under_permissive_umask_still_0600(tmp_path: Path):
    """Belt: umask. Suspenders: O_EXCL + 0o600. Brace: fchmod. All three."""
    target = tmp_path / "ui.token"
    old = os.umask(0o000)  # adversarially permissive
    try:
        write_token_atomic(target, "abc123")
    finally:
        os.umask(old)
    assert _mode_low9(target) == 0o600


def test_write_unlinks_and_retries_once_on_collision(tmp_path: Path):
    """A stale token must be replaced, but only on a single retry (plan §6.3)."""
    target = tmp_path / "ui.token"
    target.write_text("stale-token")
    write_token_atomic(target, "fresh-token")
    assert target.read_text() == "fresh-token"
    assert _mode_low9(target) == 0o600


def test_read_token_returns_none_when_missing(tmp_path: Path):
    assert read_token(tmp_path / "absent.token") is None


def test_read_token_round_trips_written_value(tmp_path: Path):
    target = tmp_path / "ui.token"
    write_token_atomic(target, "round-trip-value")
    assert read_token(target) == "round-trip-value"


def test_write_rejects_empty_token(tmp_path: Path):
    """Empty bearer is never valid; refuse early at the write boundary."""
    target = tmp_path / "ui.token"
    with pytest.raises(ValueError):
        write_token_atomic(target, "")
