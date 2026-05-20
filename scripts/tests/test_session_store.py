"""Tests for megalodon_ui.auth.SessionStore.

Plan §6.3 contract: in-memory bearer→session-id map; sessions expire
after ``COOKIE_MAX_AGE_SECONDS`` (86 400 s = 1 workday).

Clock is injected via the constructor's ``now`` parameter so the
expiry test does not need to sleep 24 h.
"""

from megalodon_ui._v92_constants import COOKIE_MAX_AGE_SECONDS
from megalodon_ui.auth import SessionStore


def test_create_returns_unique_id_each_call():
    store = SessionStore()
    ids = {store.create() for _ in range(50)}
    assert len(ids) == 50  # no collisions across 50 monotonic mints


def test_create_returns_nonempty_string():
    store = SessionStore()
    sid = store.create()
    assert isinstance(sid, str)
    assert len(sid) >= 32  # ≈ token_urlsafe(32) lower bound


def test_validate_returns_true_for_newly_created_id():
    store = SessionStore()
    sid = store.create()
    assert store.validate(sid) is True


def test_validate_returns_false_for_unknown_id():
    store = SessionStore()
    store.create()  # something exists, but we ask for a different value
    assert store.validate("not-a-real-session-id") is False


def test_validate_rejects_none_and_empty():
    store = SessionStore()
    store.create()
    assert store.validate("") is False
    assert store.validate(None) is False  # type: ignore[arg-type]


def test_revoke_invalidates_id():
    store = SessionStore()
    sid = store.create()
    store.revoke(sid)
    assert store.validate(sid) is False


def test_revoke_unknown_id_is_noop():
    store = SessionStore()
    store.revoke("never-existed")  # must not raise


def test_session_expires_after_ttl():
    clock = {"t": 1000.0}
    store = SessionStore(now=lambda: clock["t"])
    sid = store.create()
    assert store.validate(sid) is True

    # Walk forward to the last live second.
    clock["t"] = 1000.0 + COOKIE_MAX_AGE_SECONDS - 1
    assert store.validate(sid) is True

    # Step past the boundary.
    clock["t"] = 1000.0 + COOKIE_MAX_AGE_SECONDS + 1
    assert store.validate(sid) is False


def test_expired_session_is_not_resurrectable():
    """Once a session has aged out, revalidating later is still False."""
    clock = {"t": 1000.0}
    store = SessionStore(now=lambda: clock["t"])
    sid = store.create()
    clock["t"] = 1000.0 + COOKIE_MAX_AGE_SECONDS + 1
    assert store.validate(sid) is False
    clock["t"] = 1000.0 + COOKIE_MAX_AGE_SECONDS + 10
    assert store.validate(sid) is False


def test_revoke_is_idempotent():
    store = SessionStore()
    sid = store.create()
    store.revoke(sid)
    store.revoke(sid)  # second revoke must not raise
    assert store.validate(sid) is False
