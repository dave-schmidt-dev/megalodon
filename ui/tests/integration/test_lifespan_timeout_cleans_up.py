"""Integration test: lifespan timeout triggers exit 11.

Uses MEGALODON_LIFESPAN_SLEEP_S and MEGALODON_LIFESPAN_TIMEOUT_S env overrides
to deterministically trigger the asyncio.TimeoutError branch in the lifespan.

Because os._exit() is unrecoverable in-process, the test monkeypatches
megalodon_ui.server.os._exit to raise SystemExit(code) instead.  This is a
test-only injection: the production code still uses os._exit and cannot be
caught by exception handlers in the same process.  The patch is scoped to
the server module's reference to os._exit so it does not affect other uses of
os._exit elsewhere in the test process.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
def fix_three_lane():
    """Copy fix-medium to /tmp so the socket path stays under 100 bytes.

    macOS tmp_path resolves to /private/var/folders/... (>80 chars), which
    triggers the socket-path-length guard (exit 10) before the timeout guard
    (exit 11) that this test exercises.  Using /tmp keeps the path short.
    """
    import tempfile

    with tempfile.TemporaryDirectory(dir="/tmp", prefix="meg_") as td:
        dst = Path(td) / "m"
        shutil.copytree(FIXTURES / "fix-medium", dst)
        yield dst


@pytest.mark.asyncio
async def test_lifespan_timeout_raises_system_exit(fix_three_lane, monkeypatch):
    """Lifespan exits with code 11 when start_all takes longer than the timeout.

    Mechanism:
      - MEGALODON_LIFESPAN_SLEEP_S=1.0  causes the lifespan to sleep 1 s
        before calling start_all (which itself is near-instant with mocked
        tmux), making total startup time >> the timeout.
      - MEGALODON_LIFESPAN_TIMEOUT_S=0.2  sets the wait_for timeout to 0.2 s.
      - megalodon_ui.server.os._exit is replaced with a callable that raises
        SystemExit so the test can catch it.

    Note: in production, os._exit(11) is unrecoverable; this monkeypatch is
    strictly a test-time injection and should never appear in application code.
    """
    # Inject env overrides so lifespan picks them up when it runs.
    monkeypatch.setenv("MEGALODON_LIFESPAN_SLEEP_S", "1.0")
    monkeypatch.setenv("MEGALODON_LIFESPAN_TIMEOUT_S", "0.2")

    # Replace os._exit on the server module so SystemExit is catchable.
    # We patch only the `_exit` attribute on the os object that server_mod holds
    # so the override is scoped to the server module and does not affect other
    # users of os._exit in the test process.
    import megalodon_ui.server as server_mod

    def _fake_exit(code: int) -> None:
        raise SystemExit(code)

    monkeypatch.setattr(server_mod.os, "_exit", _fake_exit)

    # Mock tmux so list_sessions/new_session don't block or fail.
    import megalodon_ui.tmux as tmux_mod

    monkeypatch.setattr(tmux_mod, "list_sessions", AsyncMock(return_value=[]))
    monkeypatch.setattr(tmux_mod, "new_session", AsyncMock(return_value=0))
    monkeypatch.setattr(tmux_mod, "kill_session", AsyncMock(return_value=None))
    monkeypatch.setattr(
        tmux_mod, "display_message_pane_pipe", AsyncMock(return_value=None)
    )

    from megalodon_ui.server import make_app

    app = make_app(mission_dir=fix_three_lane)

    with pytest.raises(SystemExit) as exc_info:
        async with app.router.lifespan_context(app):
            pass  # should never reach yield

    assert exc_info.value.code == 11, (
        f"expected exit code 11 (timeout), got {exc_info.value.code}"
    )
