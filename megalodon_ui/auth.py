"""v9.2 auth machinery — bearer token + cookie session.

Plan §6.3: bearer token in `.fleet/ui.token` (mode 0600) is the one-time
exchange credential. Browser POSTs it to `/api/v1/auth/exchange`; server
mints a session id, stores it in :class:`SessionStore`, sets an
HttpOnly+SameSite=Strict cookie. The session id is the live credential
thereafter; the bearer is wiped from the URL via `history.replaceState`.

Compromising the cookie does not leak the bearer (and vice versa).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import time
from collections.abc import Callable
from pathlib import Path

from ._v92_constants import BEARER_TOKEN_BYTES, COOKIE_MAX_AGE_SECONDS

_log = logging.getLogger(__name__)


def generate_token() -> str:
    """Mint a fresh bearer token (≈43 base64-urlsafe chars)."""
    return secrets.token_urlsafe(BEARER_TOKEN_BYTES)


def write_token_atomic(path: Path, token: str) -> None:
    """Atomic 0600 write with O_EXCL; unlink and retry exactly once on collision.

    Triple-guarded against permissive umasks (research #7): umask(0o077)
    biases the create syscall, O_EXCL guarantees we own the inode, fchmod
    corrects mode if the filesystem ignored umask.
    """
    if not token:
        raise ValueError("refusing to write empty bearer token")
    old_umask = os.umask(0o077)
    try:
        for attempt in range(2):
            try:
                fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                if attempt == 0:
                    path.unlink(missing_ok=True)
                    continue
                raise
            else:
                try:
                    os.fchmod(fd, 0o600)
                    os.write(fd, token.encode())
                finally:
                    os.close(fd)
                return
    finally:
        os.umask(old_umask)


def read_token(path: Path) -> str | None:
    """Tolerant read: returns None if absent; raises on other IO errors."""
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return None


def compare_token(supplied: str, stored: str | None) -> bool:
    """Constant-time bearer equality; rejects empty/None on either side."""
    if not supplied or not stored:
        return False
    return secrets.compare_digest(supplied, stored)


def _hash(sid: str) -> str:
    """Return the SHA-256 hex digest of *sid* (the raw session id is never stored)."""
    return hashlib.sha256(sid.encode()).hexdigest()


class SessionStore:
    """Session map keyed by SHA-256 digest; optionally persisted to a 0600 JSON file.

    The bearer token is the one-time exchange credential; the session id
    returned by :meth:`create` is what gets stored in the HttpOnly cookie
    and validated on every subsequent request.  Only the SHA-256 digest of
    each session id is held in memory or written to disk — the raw value
    never touches storage (design rule WR-2).

    Args:
        path: If set, load+prune on construction and persist after every
            mutation.  Missing file is treated as an empty store; corrupt
            file logs a WARNING and starts empty.  When None (default),
            behavior is pure in-memory and nothing is ever written.
        now: Clock callable; defaults to ``time.time`` (wall-clock) so that
            expiry comparisons are meaningful across process restarts.
    """

    def __init__(
        self,
        *,
        path: Path | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._now = now
        self._path = path
        self._created_at: dict[str, float] = {}

        if path is not None:
            self._load_and_prune()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create(self) -> str:
        """Mint a new session id, store its digest, persist if path is set.

        Returns the raw session id (the caller places it in the HttpOnly
        cookie); only the SHA-256 digest is stored/persisted.
        """
        sid = secrets.token_urlsafe(BEARER_TOKEN_BYTES)
        self._created_at[_hash(sid)] = self._now()
        self._persist()
        return sid

    def validate(self, cookie_value: str | None) -> bool:
        """Return True iff the presented cookie maps to a non-expired session.

        Expired sessions are evicted from memory and (if path is set) from
        disk so stale digests do not accumulate (PW-1-self).
        """
        if not cookie_value:
            return False
        digest = _hash(cookie_value)
        created = self._created_at.get(digest)
        if created is None:
            return False
        if self._now() - created > COOKIE_MAX_AGE_SECONDS:
            del self._created_at[digest]
            self._persist()
            return False
        return True

    def revoke(self, cookie_value: str) -> None:
        """Immediately invalidate *cookie_value* and persist the removal."""
        self._created_at.pop(_hash(cookie_value), None)
        self._persist()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_and_prune(self) -> None:
        """Load digest→created_epoch from disk, drop expired entries, re-persist."""
        assert self._path is not None  # only called when path is set
        raw: dict[str, float] = {}
        parsed: object = {}
        try:
            text = self._path.read_text()
            parsed = json.loads(text)
        except FileNotFoundError:
            parsed = {}  # empty store; fine
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "SessionStore: could not load %s (%s) — starting empty",
                self._path,
                exc,
            )
            parsed = {}

        if isinstance(parsed, dict):
            raw = parsed
        else:
            # Valid JSON but not an object (e.g. [], "foo", 42): treat as corrupt.
            _log.warning(
                "SessionStore: %s is not a JSON object — starting empty",
                self._path,
            )
            raw = {}

        cutoff = self._now() - COOKIE_MAX_AGE_SECONDS
        self._created_at = {
            digest: created
            for digest, created in raw.items()
            if isinstance(digest, str)
            and isinstance(created, (int, float))
            and created > cutoff
        }
        self._persist()

    def _persist(self) -> None:
        """Atomically write digest map to disk at 0600; a write failure logs and returns."""
        if self._path is None:
            return
        tmp = self._path.with_suffix(".tmp")
        old_umask = os.umask(0o077)
        try:
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.fchmod(fd, 0o600)
                os.write(fd, json.dumps(self._created_at).encode())
            finally:
                os.close(fd)
            os.replace(str(tmp), str(self._path))
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "SessionStore: failed to persist session file %s (%s)",
                self._path,
                exc,
            )
            try:
                tmp.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
        finally:
            os.umask(old_umask)
