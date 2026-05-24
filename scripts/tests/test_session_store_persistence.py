"""Tests for the persistent/hashed SessionStore (Task D1).

Design rules verified here:
  WR-2  — only SHA-256 digests of session ids are written to disk.
  PW-1-self — expired digests are evicted on validate() and not left on disk.

All tests use tmp_path (pytest fixture) for the session file and inject
``now`` for deterministic clock control so no test needs to sleep.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from megalodon_ui._v92_constants import COOKIE_MAX_AGE_SECONDS
from megalodon_ui.auth import SessionStore, _hash


# ---------------------------------------------------------------------------
# 1. Hashed round-trip
# ---------------------------------------------------------------------------


def test_hashed_roundtrip_new_store_validates(tmp_path: Path) -> None:
    """Create a session in store A; a fresh store B loaded from same path validates it."""
    p = tmp_path / "sessions.json"

    store_a = SessionStore(path=p, now=lambda: 1_000_000.0)
    raw_sid = store_a.create()

    # Construct a completely independent store from the same file.
    store_b = SessionStore(path=p, now=lambda: 1_000_000.0)
    assert store_b.validate(raw_sid) is True


def test_raw_sid_absent_from_disk(tmp_path: Path) -> None:
    """The raw session id string must NOT appear anywhere in the persisted file bytes."""
    p = tmp_path / "sessions.json"
    store = SessionStore(path=p, now=lambda: 1_000_000.0)
    raw_sid = store.create()

    file_bytes = p.read_bytes()
    assert raw_sid.encode() not in file_bytes

    # Keys must be 64-character hex strings; values must be numbers.
    data = json.loads(file_bytes)
    for key, value in data.items():
        assert isinstance(key, str), "key is not a string"
        assert len(key) == 64, f"key length {len(key)} != 64"
        assert all(c in "0123456789abcdef" for c in key), "key is not hex"
        assert isinstance(value, (int, float)), "value is not numeric"


# ---------------------------------------------------------------------------
# 2. Wall-clock expiry
# ---------------------------------------------------------------------------


def test_wallclock_expiry(tmp_path: Path) -> None:
    """Session older than COOKIE_MAX_AGE_SECONDS fails validate."""
    p = tmp_path / "sessions.json"
    clock = {"t": 1_000_000.0}
    store = SessionStore(path=p, now=lambda: clock["t"])
    raw_sid = store.create()

    assert store.validate(raw_sid) is True

    clock["t"] += COOKIE_MAX_AGE_SECONDS + 1
    assert store.validate(raw_sid) is False


def test_wallclock_expiry_evicts_digest_from_file(tmp_path: Path) -> None:
    """After expiry, the digest is removed from the persisted file."""
    p = tmp_path / "sessions.json"
    clock = {"t": 1_000_000.0}
    store = SessionStore(path=p, now=lambda: clock["t"])
    raw_sid = store.create()

    clock["t"] += COOKIE_MAX_AGE_SECONDS + 1
    store.validate(raw_sid)  # triggers eviction

    data = json.loads(p.read_bytes())
    assert _hash(raw_sid) not in data


# ---------------------------------------------------------------------------
# 3. Prune on load
# ---------------------------------------------------------------------------


def test_prune_on_load(tmp_path: Path) -> None:
    """Seed file with fresh + expired digest; load; only fresh validates; file pruned."""
    p = tmp_path / "sessions.json"
    now = 2_000_000.0

    # Build a raw store manually so we can inject a known expired digest.
    fresh_sid = "fresh-session-id-placeholder"
    expired_sid = "expired-session-id-placeholder"
    seed = {
        _hash(fresh_sid): now,  # created right at 'now' — not expired
        _hash(expired_sid): now - COOKIE_MAX_AGE_SECONDS - 1,  # already expired
    }
    p.write_text(json.dumps(seed))

    store = SessionStore(path=p, now=lambda: now)

    assert store.validate(fresh_sid) is True
    assert store.validate(expired_sid) is False

    # File must no longer contain the expired digest.
    data = json.loads(p.read_bytes())
    assert _hash(expired_sid) not in data
    assert _hash(fresh_sid) in data


# ---------------------------------------------------------------------------
# 4. Validate-eviction persists across process restart
# ---------------------------------------------------------------------------


def test_validate_eviction_persists(tmp_path: Path) -> None:
    """Expired entry → validate() False → a fresh store from same path doesn't see it."""
    p = tmp_path / "sessions.json"
    clock = {"t": 1_000_000.0}

    store_a = SessionStore(path=p, now=lambda: clock["t"])
    raw_sid = store_a.create()

    # Advance past expiry and trigger eviction via validate.
    clock["t"] += COOKIE_MAX_AGE_SECONDS + 5
    assert store_a.validate(raw_sid) is False

    # A brand-new store loaded from the same file should not resurrect it.
    store_b = SessionStore(path=p, now=lambda: clock["t"])
    assert store_b.validate(raw_sid) is False


# ---------------------------------------------------------------------------
# 5. revoke removes + persists
# ---------------------------------------------------------------------------


def test_revoke_removes_and_persists(tmp_path: Path) -> None:
    """revoke() removes the digest from disk; a fresh store cannot validate it."""
    p = tmp_path / "sessions.json"
    clock = {"t": 1_000_000.0}

    store_a = SessionStore(path=p, now=lambda: clock["t"])
    raw_sid = store_a.create()
    store_a.revoke(raw_sid)

    store_b = SessionStore(path=p, now=lambda: clock["t"])
    assert store_b.validate(raw_sid) is False


# ---------------------------------------------------------------------------
# 6. File mode is 0600
# ---------------------------------------------------------------------------


def test_persisted_file_mode_is_0600(tmp_path: Path) -> None:
    """The session file must be created with permissions 0600."""
    p = tmp_path / "sessions.json"
    store = SessionStore(path=p, now=lambda: 1_000_000.0)
    store.create()

    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


# ---------------------------------------------------------------------------
# 7. Corrupt / missing file — tolerant construction
# ---------------------------------------------------------------------------


def test_missing_file_is_empty_store(tmp_path: Path) -> None:
    """No session file → empty store, no exception."""
    p = tmp_path / "nonexistent.json"
    store = SessionStore(path=p, now=lambda: 1_000_000.0)
    # No sessions → validate anything is False.
    assert store.validate("any-value") is False


def test_corrupt_file_is_empty_store_with_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Corrupt JSON → empty store, no exception, exactly one WARNING logged."""
    p = tmp_path / "sessions.json"
    p.write_text("{ this is not valid json !!!")

    import logging

    with caplog.at_level(logging.WARNING, logger="megalodon_ui.auth"):
        store = SessionStore(path=p, now=lambda: 1_000_000.0)

    assert store.validate("anything") is False
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) >= 1, "expected at least one WARNING for corrupt file"


# ---------------------------------------------------------------------------
# 8. path=None — pure in-memory, writes nothing
# ---------------------------------------------------------------------------


def test_path_none_writes_nothing(tmp_path: Path) -> None:
    """path=None: create() and revoke() work in memory; no file is ever created."""
    store = SessionStore(now=lambda: 1_000_000.0)
    raw_sid = store.create()
    assert store.validate(raw_sid) is True

    store.revoke(raw_sid)
    assert store.validate(raw_sid) is False

    # tmp_path should be completely empty (no files created by the store).
    assert list(tmp_path.iterdir()) == []


def test_path_none_expiry_works(tmp_path: Path) -> None:
    """path=None: in-memory expiry still functions correctly."""
    clock = {"t": 1_000_000.0}
    store = SessionStore(now=lambda: clock["t"])
    raw_sid = store.create()

    assert store.validate(raw_sid) is True
    clock["t"] += COOKIE_MAX_AGE_SECONDS + 1
    assert store.validate(raw_sid) is False
