"""Shared helper to authenticate an integration-test client.

The deny-by-default auth gate (v9.2 security inversion) requires a valid
``mui_session`` cookie for every ``/api/**`` request. The integration fixtures
yield an UNauthenticated client, so tests that exercise gated endpoints call
``authenticate(client)`` once to mint a session and attach the cookie.

This mints the session directly via the app's in-memory ``SessionStore`` (test
mode uses ``path=None``), avoiding any dependency on a token file in the
fixture. It mirrors exactly what ``POST /api/v1/auth/exchange`` does on success.
"""

from __future__ import annotations

SESSION_COOKIE_NAME = "mui_session"


def authenticate(client) -> str:
    """Mint a session on the client's app and set the ``mui_session`` cookie.

    Returns the session id. Idempotent enough for per-test use — each call mints
    a fresh session, which is fine for the single-operator localhost model.
    """
    app = client._transport.app
    ctx = app.state.megalodon
    sid = ctx.session_store.create()
    client.cookies.set(SESSION_COOKIE_NAME, sid)
    return sid
