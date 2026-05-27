"""Unit tests for __main__.py: fd handoff, bind-before-write ordering, and cleanup."""

from __future__ import annotations

import os
import socket
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator
from unittest.mock import patch

import pytest

# Every test here runs main(), which binds a real listener on the mission's
# DEFAULT_PORT (8080). Under `pytest -n auto` two of these on different workers
# collide → "port 8080 already in use" (exit 9). Pin the whole module to one
# xdist worker via a shared group so they serialize. Requires `--dist loadgroup`
# (the gate/Makefile sets it); harmless under serial runs.
pytestmark = pytest.mark.xdist_group("megalodon_main_port_bind")

# Pytest tmp_path is deep (~110 chars); patch the limit so it doesn't trip exit 10.
_LARGE_PATH_LIMIT = 512


@contextmanager
def _patched_main_env() -> Generator[None, None, None]:
    """Patch shared concerns: probe_or_exit_6 and the socket-path limit.

    D4: main() no longer opens a browser directly — the observed auto-open is a
    live-branch lifespan task, which _CapturingServer never runs. So there is no
    ``webbrowser.open`` call to patch here; main() only stages the open decision
    on app.state (asserted by the handoff tests below).
    """
    with (
        patch("megalodon_ui.__main__.probe_or_exit_6"),
        patch("megalodon_ui.__main__.SOCKET_PATH_LIMIT_BYTES", _LARGE_PATH_LIMIT),
    ):
        yield


def _run_main(tmp_path: Path, extra_argv: list[str] | None = None) -> None:
    """Invoke main() with a synthetic argv pointing at tmp_path."""
    argv = ["python-m-megalodon-ui", "--mission-dir", str(tmp_path)]
    if extra_argv:
        argv += extra_argv
    with patch.object(sys, "argv", argv):
        from megalodon_ui.__main__ import main

        main()


# ---------------------------------------------------------------------------
# Shared stub server
# ---------------------------------------------------------------------------


class _CapturingServer:
    """Replacement for uvicorn.Server that records the Config it received.

    On normal exit (no side_effect) the stub adopts and closes the fd it
    inherited, mirroring what real uvicorn does.  This pairs with the D3
    ``listener.detach()`` call that main() makes after run() returns, which
    clears the Python socket object's reference so no ResourceWarning is emitted.
    """

    captured: Any = None
    side_effect: BaseException | None = None

    def __init__(self, config: Any) -> None:
        _CapturingServer.captured = config

    def run(self) -> None:
        if _CapturingServer.side_effect is not None:
            raise _CapturingServer.side_effect
        # Mimic uvicorn: adopt the fd via socket.fromfd so Python owns the
        # cleanup, then close it properly so the port is released.
        cfg = _CapturingServer.captured
        if cfg is not None and getattr(cfg, "fd", None) is not None:
            try:
                s = socket.socket(fileno=cfg.fd)
                s.close()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Test: Config.fd is set and Config.port is absent / None
# ---------------------------------------------------------------------------


def test_fd_passed_to_uvicorn_config(tmp_path: Path) -> None:
    _CapturingServer.captured = None
    _CapturingServer.side_effect = None

    with (
        _patched_main_env(),
        patch("megalodon_ui.__main__.uvicorn.Server", _CapturingServer),
    ):
        _run_main(tmp_path)

    cfg = _CapturingServer.captured
    assert cfg is not None, "uvicorn.Config was never captured"
    assert isinstance(cfg.fd, int), f"expected Config.fd to be int, got {cfg.fd!r}"
    assert cfg.fd >= 0
    # uvicorn must have received fd, not a port; Config.port defaults to 8000 when
    # not set by caller but fd overrides the bind — the key assertion is that fd is set.
    assert cfg.fd > 0


# ---------------------------------------------------------------------------
# Test: bind() happens before token file open()
# ---------------------------------------------------------------------------


def test_bind_happens_before_token_write(tmp_path: Path) -> None:
    call_order: list[str] = []

    original_socket_class = socket.socket

    class _TrackingSocket(original_socket_class):  # type: ignore[misc]
        def bind(self, address: Any) -> None:
            call_order.append("bind")
            super().bind(address)

    original_os_open = os.open

    def _tracking_os_open(path: str, flags: int, mode: int = 0o777) -> int:
        call_order.append(f"os.open:{Path(path).name}")
        return original_os_open(path, flags, mode)

    _CapturingServer.captured = None
    _CapturingServer.side_effect = None

    with (
        _patched_main_env(),
        patch("megalodon_ui.__main__.socket.socket", _TrackingSocket),
        patch("megalodon_ui.__main__.os.open", _tracking_os_open),
        patch("megalodon_ui.auth.os.open", _tracking_os_open),
        patch("megalodon_ui.__main__.uvicorn.Server", _CapturingServer),
    ):
        _run_main(tmp_path)

    assert "bind" in call_order, "bind() was never called"
    token_opens = [i for i, ev in enumerate(call_order) if ev == "os.open:ui.token"]
    bind_indices = [i for i, ev in enumerate(call_order) if ev == "bind"]
    assert token_opens, "os.open for ui.token was never called"
    assert bind_indices[-1] < token_opens[0], (
        f"bind() must happen before os.open(ui.token); order was {call_order}"
    )


# ---------------------------------------------------------------------------
# Test: cleanup unlinks token + dashboard.url when Server.run() raises
# ---------------------------------------------------------------------------


def test_cleanup_on_server_run_exception(tmp_path: Path) -> None:
    _CapturingServer.captured = None
    _CapturingServer.side_effect = OSError("startup-timeout (synthetic)")

    with (
        _patched_main_env(),
        patch("megalodon_ui.__main__.uvicorn.Server", _CapturingServer),
        pytest.raises(OSError, match="startup-timeout"),
    ):
        _run_main(tmp_path)

    fleet = tmp_path / ".fleet"
    assert not (fleet / "ui.token").exists(), (
        "ui.token should be cleaned up after error"
    )
    assert not (fleet / "dashboard.url").exists(), (
        "dashboard.url should be cleaned up after error"
    )


# ---------------------------------------------------------------------------
# Test: a REUSED token survives an error during Server.run() (D3 security path)
#
# The except-branch cleanup is guarded by `if token_was_generated:`. When the
# token was reused (pre-existing file → was_generated=False), an error must NOT
# delete it — a restart would otherwise lose its stable URL. This is the other
# arm of the guard that test_cleanup_on_server_run_exception (generated case)
# does not exercise.
# ---------------------------------------------------------------------------


def test_reused_token_survives_server_run_exception(tmp_path: Path) -> None:
    # Pre-create .fleet/ui.token so _resolve_token reuses it (was_generated=False).
    fleet = tmp_path / ".fleet"
    fleet.mkdir(mode=0o700)
    token_path = fleet / "ui.token"
    original_token = "preexisting-stable-token"
    token_path.write_text(original_token)

    _CapturingServer.captured = None
    _CapturingServer.side_effect = OSError("startup-timeout (synthetic)")

    with (
        _patched_main_env(),
        patch("megalodon_ui.__main__.uvicorn.Server", _CapturingServer),
        pytest.raises(OSError, match="startup-timeout"),
    ):
        _run_main(tmp_path)

    # The reused token must NOT be unlinked, and its content must be unchanged.
    assert token_path.exists(), (
        "a reused token must survive a Server.run() error (never deleted)"
    )
    assert token_path.read_text() == original_token, (
        "reused token content must be preserved untouched after error"
    )
    # dashboard.url is written from the reused token, so it is preserved too.
    assert (fleet / "dashboard.url").exists(), (
        "dashboard.url should be preserved when the token was reused"
    )


# ---------------------------------------------------------------------------
# Test: EADDRINUSE exits 9
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Test: D4 auto-open handoff — main() stages the decision on app.state
# instead of opening a browser directly. The actual observe-and-open is a
# live-branch lifespan task (covered e2e by D6's restart-reconnect spec);
# _CapturingServer never runs the lifespan, so no browser opens here.
# ---------------------------------------------------------------------------


def test_dashboard_open_handoff_default(tmp_path: Path) -> None:
    """Default launch: open enabled, not forced, URL carries the token."""
    _CapturingServer.captured = None
    _CapturingServer.side_effect = None

    with (
        _patched_main_env(),
        patch("megalodon_ui.__main__.uvicorn.Server", _CapturingServer),
    ):
        _run_main(tmp_path)

    cfg = _CapturingServer.captured
    assert cfg is not None
    state = cfg.app.state
    assert state.dashboard_open_enabled is True
    assert state.dashboard_force_open is False
    url = state.dashboard_open_url
    assert url.startswith("http://"), url
    assert "#t=" in url, f"dashboard URL must carry the auth token: {url}"


def test_no_browser_flag_disables_open_handoff(tmp_path: Path) -> None:
    """--no-browser → open disabled on the handoff (the watch never opens)."""
    _CapturingServer.captured = None
    _CapturingServer.side_effect = None

    with (
        _patched_main_env(),
        patch("megalodon_ui.__main__.uvicorn.Server", _CapturingServer),
    ):
        _run_main(tmp_path, extra_argv=["--no-browser"])

    state = _CapturingServer.captured.app.state
    assert state.dashboard_open_enabled is False
    assert state.dashboard_force_open is False


def test_rotate_token_forces_open_handoff(tmp_path: Path) -> None:
    """--rotate-token → force-open True (immediate open, skip the observe window)."""
    _CapturingServer.captured = None
    _CapturingServer.side_effect = None

    with (
        _patched_main_env(),
        patch("megalodon_ui.__main__.uvicorn.Server", _CapturingServer),
    ):
        _run_main(tmp_path, extra_argv=["--rotate-token"])

    state = _CapturingServer.captured.app.state
    assert state.dashboard_open_enabled is True
    assert state.dashboard_force_open is True


def test_eaddrinuse_exits_9(tmp_path: Path) -> None:
    occupier = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupier.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    occupier.bind(("127.0.0.1", 0))
    occupied_port = occupier.getsockname()[1]
    try:
        argv = [
            "python-m-megalodon-ui",
            "--mission-dir",
            str(tmp_path),
            "--port",
            str(occupied_port),
        ]
        with (
            _patched_main_env(),
            patch.object(sys, "argv", argv),
            pytest.raises(SystemExit) as exc_info,
        ):
            from megalodon_ui.__main__ import main

            main()
        assert exc_info.value.code == 9
    finally:
        occupier.close()
