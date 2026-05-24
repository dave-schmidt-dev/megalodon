"""Tests for D5 token-URL exposure hardening in megalodon_ui.__main__.

Covers:
  1. dashboard.url is written at mode 0600.
  2. No world/group-readable window: final mode & 0o077 == 0.
  3. _redact_token_url replaces the secret after ``#t=`` and leaves unrelated
     URLs unchanged; the original secret must NOT appear in the redacted form.
  4. _is_loopback_host: True for loopback addresses, False for non-loopback.
  5. _redact_token_url is what main() passes to the INFO log (helper contract).

main() blocks on uvicorn so full integration tests of the log output are out
of scope; the helper contract (items 3 and 5) is unit-tested directly.

Run with ``pytest -W error`` per project convention.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from megalodon_ui.__main__ import (
    _is_loopback_host,
    _redact_token_url,
    _write_dashboard_url_atomic,
)


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------


def _mode_low9(p: Path) -> int:
    """Return the low 9 permission bits of *p*."""
    return stat.S_IMODE(p.stat().st_mode)


# ---------------------------------------------------------------------------
# 1 & 2: dashboard.url is 0600 with no group/world-readable window
# ---------------------------------------------------------------------------


def test_write_dashboard_url_creates_file_at_0600(tmp_path: Path) -> None:
    """_write_dashboard_url_atomic must produce a 0600 file."""
    url_path = tmp_path / "dashboard.url"
    url = "http://127.0.0.1:8000/#t=abc"

    _write_dashboard_url_atomic(url_path, url)

    assert url_path.exists()
    assert _mode_low9(url_path) == 0o600


def test_write_dashboard_url_content_is_correct(tmp_path: Path) -> None:
    """The written file must contain the URL (with trailing newline)."""
    url_path = tmp_path / "dashboard.url"
    url = "http://127.0.0.1:8000/#t=abc"

    _write_dashboard_url_atomic(url_path, url)

    assert url_path.read_text(encoding="utf-8") == url + "\n"


def test_write_dashboard_url_no_group_or_other_bits(tmp_path: Path) -> None:
    """mode & 0o077 must be 0 — no group/world read, write, or execute bits."""
    url_path = tmp_path / "dashboard.url"

    _write_dashboard_url_atomic(url_path, "http://127.0.0.1:8000/#t=xyz")

    assert _mode_low9(url_path) & 0o077 == 0, (
        f"Expected no group/other bits; got mode {oct(_mode_low9(url_path))}"
    )


def test_write_dashboard_url_0600_under_permissive_umask(tmp_path: Path) -> None:
    """Mode must be 0600 even when the caller runs under umask(0o000)."""
    url_path = tmp_path / "dashboard.url"
    old_umask = os.umask(0o000)  # adversarially permissive
    try:
        _write_dashboard_url_atomic(url_path, "http://127.0.0.1:8000/#t=TOKEN")
    finally:
        os.umask(old_umask)

    assert _mode_low9(url_path) == 0o600


def test_write_dashboard_url_overwrites_existing(tmp_path: Path) -> None:
    """A second call must update the file (idempotent for repeated restarts)."""
    url_path = tmp_path / "dashboard.url"

    _write_dashboard_url_atomic(url_path, "http://127.0.0.1:8000/#t=FIRST")
    _write_dashboard_url_atomic(url_path, "http://127.0.0.1:8000/#t=SECOND")

    assert "SECOND" in url_path.read_text(encoding="utf-8")
    assert _mode_low9(url_path) == 0o600


# ---------------------------------------------------------------------------
# 3: _redact_token_url
# ---------------------------------------------------------------------------


def test_redact_token_url_replaces_token_with_redacted() -> None:
    url = "http://h:8000/#t=SECRET"
    result = _redact_token_url(url)
    assert result == "http://h:8000/#t=<redacted>"


def test_redact_token_url_preserves_prefix() -> None:
    """Everything up to and including ``#t=`` must be unchanged."""
    url = "http://127.0.0.1:8000/#t=SECRET"
    result = _redact_token_url(url)
    assert result.startswith("http://127.0.0.1:8000/#t=")


def test_redact_token_url_original_secret_not_in_output() -> None:
    """The raw token value must NOT appear anywhere in the redacted string."""
    secret = "supersecrettoken"
    url = f"http://127.0.0.1:8000/#t={secret}"
    result = _redact_token_url(url)
    assert secret not in result


def test_redact_token_url_no_marker_returns_unchanged() -> None:
    """A URL without ``#t=`` must be returned as-is."""
    url = "http://127.0.0.1:8000/"
    assert _redact_token_url(url) == url


def test_redact_token_url_empty_string_unchanged() -> None:
    assert _redact_token_url("") == ""


def test_redact_token_url_realistic_token() -> None:
    """Realistic base64-urlsafe token (43 chars) is fully redacted."""
    import secrets as _secrets

    token = _secrets.token_urlsafe(32)  # ~43 chars
    url = f"http://127.0.0.1:8000/#t={token}"
    result = _redact_token_url(url)
    assert result == "http://127.0.0.1:8000/#t=<redacted>"
    assert token not in result


# ---------------------------------------------------------------------------
# 4: _is_loopback_host
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "::1",
        "localhost",
        "127.0.0.5",
        "127.255.255.255",
    ],
)
def test_is_loopback_host_true(host: str) -> None:
    assert _is_loopback_host(host) is True, f"Expected {host!r} to be loopback"


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0",
        "192.168.1.10",
        "10.0.0.1",
        "172.16.0.1",
        "8.8.8.8",
    ],
)
def test_is_loopback_host_false(host: str) -> None:
    assert _is_loopback_host(host) is False, f"Expected {host!r} to be non-loopback"


def test_is_loopback_host_malformed_returns_false() -> None:
    """Malformed addresses are treated as non-loopback (safe default)."""
    assert _is_loopback_host("not-an-ip") is False
    assert _is_loopback_host("") is False


# ---------------------------------------------------------------------------
# 5: _redact_token_url is what the log receives (helper contract)
# ---------------------------------------------------------------------------


def test_redact_token_url_is_log_safe() -> None:
    """Verify that passing the redacted form to a log call would not leak the token.

    This tests the contract: main() calls log.info("Dashboard: %s", _redact_token_url(url)).
    The redacted string must contain ``<redacted>`` and must not contain the
    original token — confirming the helper is suitable for log use.
    """
    import secrets as _secrets

    token = _secrets.token_urlsafe(32)
    dashboard_url = f"http://127.0.0.1:8000/#t={token}"

    log_arg = _redact_token_url(dashboard_url)

    # The log record gets the redacted form.
    assert "<redacted>" in log_arg
    assert token not in log_arg
    # The full URL (what stdout gets) still contains the token.
    assert token in dashboard_url
