"""SR-1 combined P1+P2 commit-gate integration test (plan §Task 2.5).

The "P1 spawn ships green but P1+P2 integration is only exercised at P5
Playwright" gap that the pre-mortem flagged. This test orchestrates the
P1 lifespan + P2 auth surface at the lifespan boundary so any regression
between them lights up at P2 commit instead of five phases later.

Scope here:
- Lifespan runs cleanly (uses ``async_client_with_lifespan``, which sets
  ``MEGALODON_LIFESPAN_TEST_MODE=1`` so the fleet-spawn path is bypassed
  on machines where the macOS 104-byte tmux socket path is exceeded by
  pytest tmp_path).
- ``.fleet/ui.token`` exists with mode 0600 (written via
  ``megalodon_ui.auth.write_token_atomic`` to match the production write
  path in ``megalodon_ui/__main__.py``).
- Bearer-token → cookie exchange succeeds.
- Cookie-bearing call to a v9.2-NEW gated path (``/api/v1/lane/<NAME>/*``)
  passes the middleware — proves the auth gate is wired end-to-end in the
  lifespan-bound context, not just in a unit-test app.

Real-tmux spawn coverage lives in ``scripts/tests/test_real_tmux_spawn.py``;
the SR-1 concern was the *integration* between lifespan and auth, not the
spawn primitive itself.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from megalodon_ui.auth import write_token_atomic


pytestmark = pytest.mark.integration


@pytest.fixture
def seeded_token(fix_medium: Path) -> str:
    """Write a bearer token at ``.fleet/ui.token`` the same way ``__main__.py`` does."""
    fleet = fix_medium / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    token = "sr1-commit-gate-token"
    write_token_atomic(fleet / "ui.token", token)
    return token


@pytest.mark.asyncio
async def test_lifespan_then_token_then_exchange_then_gated_call(
    async_client_with_lifespan, seeded_token: str, fix_medium: Path
):
    # (b) Token file present + 0600 — proves write_token_atomic's mode invariant
    # holds under the integration-test umask, not just the unit-test one.
    token_path = fix_medium / ".fleet" / "ui.token"
    assert token_path.exists()
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600

    # P2 surface: exchange the bearer for a cookie.
    exch = await async_client_with_lifespan.post(
        "/api/v1/auth/exchange", json={"token": seeded_token}
    )
    assert exch.status_code == 200, exch.text
    assert "mui_session=" in exch.headers.get("set-cookie", "")

    # (c, narrowed) cookie-bearing call to a gated v9.2-new path passes the
    # middleware. The SSE handler itself lands in P4 Task 4.2 — for SR-1 we
    # just need to prove the middleware/cookie pipeline is intact end-to-end.
    follow = await async_client_with_lifespan.get("/api/v1/lane/AUDIT/pane-stream")
    assert follow.status_code != 401, (
        f"middleware rejected post-exchange cookie request: "
        f"{follow.status_code} / {follow.text}"
    )


@pytest.mark.asyncio
async def test_lifespan_without_token_still_rejects_gated_calls(
    async_client_with_lifespan,
):
    """Negative: lifespan up but no token written → gated paths still 401."""
    r = await async_client_with_lifespan.get("/api/v1/lane/AUDIT/pane-stream")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_token_write_mode_survives_permissive_umask_in_lifespan(
    async_client_with_lifespan, fix_medium: Path
):
    """If a downstream integration runs under an adversarial umask, the token
    write boundary must still produce 0600 — the audit boundary that protects
    the bearer file across the whole lifespan-up window."""
    import os

    fleet = fix_medium / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    token_path = fleet / "ui.token"
    if token_path.exists():
        token_path.unlink()
    old = os.umask(0o000)
    try:
        write_token_atomic(token_path, "umask-stress-token")
    finally:
        os.umask(old)
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
