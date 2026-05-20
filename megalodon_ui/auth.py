"""v9.2 auth machinery — bearer token + cookie session.

Plan §6.3: bearer token in `.fleet/ui.token` (mode 0600) is the one-time
exchange credential. Browser POSTs it to `/api/v1/auth/exchange`; server
mints a session id, stores it in :class:`SessionStore`, sets an
HttpOnly+SameSite=Strict cookie. The session id is the live credential
thereafter; the bearer is wiped from the URL via `history.replaceState`.

Compromising the cookie does not leak the bearer (and vice versa).
"""

from __future__ import annotations

import os
import secrets
import time
from collections.abc import Callable
from pathlib import Path

from ._v92_constants import BEARER_TOKEN_BYTES, COOKIE_MAX_AGE_SECONDS


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


class SessionStore:
    """In-memory live-session map; entries expire after COOKIE_MAX_AGE_SECONDS.

    The bearer token is the one-time exchange credential; the session id
    returned by :meth:`create` is what gets stored in the HttpOnly cookie
    and validated on every subsequent request.
    """

    def __init__(self, *, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._created_at: dict[str, float] = {}

    def create(self) -> str:
        sid = secrets.token_urlsafe(BEARER_TOKEN_BYTES)
        self._created_at[sid] = self._now()
        return sid

    def validate(self, cookie_value: str | None) -> bool:
        if not cookie_value:
            return False
        created = self._created_at.get(cookie_value)
        if created is None:
            return False
        if self._now() - created > COOKIE_MAX_AGE_SECONDS:
            del self._created_at[cookie_value]
            return False
        return True

    def revoke(self, cookie_value: str) -> None:
        self._created_at.pop(cookie_value, None)
