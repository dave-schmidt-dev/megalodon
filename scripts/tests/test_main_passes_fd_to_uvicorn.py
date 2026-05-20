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

# Pytest tmp_path is deep (~110 chars); patch the limit so it doesn't trip exit 10.
_LARGE_PATH_LIMIT = 512


@contextmanager
def _patched_main_env() -> Generator[None, None, None]:
    """Patch shared concerns: probe_or_exit_6 and SOCKET_PATH_LIMIT_BYTES."""
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
    """Replacement for uvicorn.Server that records the Config it received."""

    captured: Any = None
    side_effect: BaseException | None = None

    def __init__(self, config: Any) -> None:
        _CapturingServer.captured = config

    def run(self) -> None:
        if _CapturingServer.side_effect is not None:
            raise _CapturingServer.side_effect


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
    assert not (fleet / "ui.token").exists(), "ui.token should be cleaned up after error"
    assert not (fleet / "dashboard.url").exists(), "dashboard.url should be cleaned up after error"


# ---------------------------------------------------------------------------
# Test: EADDRINUSE exits 9
# ---------------------------------------------------------------------------

def test_eaddrinuse_exits_9(tmp_path: Path) -> None:
    occupier = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupier.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    occupier.bind(("127.0.0.1", 0))
    occupied_port = occupier.getsockname()[1]
    try:
        argv = [
            "python-m-megalodon-ui",
            "--mission-dir", str(tmp_path),
            "--port", str(occupied_port),
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
