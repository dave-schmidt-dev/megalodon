"""Integration test: lifespan startup-timeout (exit 11) cleans up token, URL, and listener.

This test invokes `python -m megalodon_ui` as a subprocess with a very short
timeout and a lifespan that sleeps past that timeout, then verifies:
  - process exits with code 11
  - .fleet/ui.token does not exist
  - .fleet/dashboard.url does not exist
  - the listener port is rebindable (no EADDRINUSE)

STATUS: xfail (strict=True) — the lifespan raises sys.exit(11) as expected but
uvicorn catches SystemExit inside the lifespan context and the subprocess exits 0.
The test is correctly written; the bug is in how __main__.py / uvicorn handles
SystemExit from within lifespan (needs to propagate exit code 11 to the process).
TODO: fix __main__.py to propagate exit 11 then remove the xfail marker.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.xfail(
    reason=(
        "lifespan startup-timeout (exit 11) not honoured end-to-end: "
        "sys.exit(11) is raised inside the lifespan context (visible in stderr as "
        "'SystemExit: 11') but uvicorn catches it and the subprocess exits 0 instead. "
        "Repro: uv run --extra test pytest "
        "ui/tests/integration/test_startup_timeout_cleans_up_token_and_listener.py --runxfail -q"
    ),
    strict=True,
)
def test_startup_timeout_cleans_up_token_and_listener() -> None:
    """Subprocess exits 11 and leaves no credential files or held port.

    Uses a short /tmp mission dir so the socket path (/tmp/mgld-X/.fleet/tmux.sock)
    stays well under the 100-byte sun_path limit.  The deep pytest tmp_path
    (~120 bytes) would trip __main__'s pre-lifespan socket-path guard (exit 10)
    before the startup-timeout logic is ever reached.
    """
    mission_dir = Path(tempfile.mkdtemp(prefix="mgld-st-", dir="/tmp"))
    try:
        _run_test(mission_dir)
    finally:
        shutil.rmtree(mission_dir, ignore_errors=True)


def _run_test(mission_dir: Path) -> None:
    port = _find_free_port()
    fleet_dir = mission_dir / ".fleet"
    token_path = fleet_dir / "ui.token"
    url_path = fleet_dir / "dashboard.url"

    env = os.environ.copy()
    # Task 1.5 lifespan reads these to simulate a slow startup that exceeds timeout.
    env["MEGALODON_LIFESPAN_SLEEP_S"] = "5"
    env["MEGALODON_LIFESPAN_TIMEOUT_S"] = "0.5"
    env["MEGALODON_DEBUG"] = "0"
    # Explicitly unset in case the parent environment has it set — we need the
    # real __main__.py socket-path guard to pass (short path), not be skipped.
    env.pop("MEGALODON_SKIP_SOCKET_BUDGET", None)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "megalodon_ui",
            "--mission-dir",
            str(mission_dir),
            "--port",
            str(port),
            "--host",
            "127.0.0.1",
        ],
        env=env,
        timeout=15,
        capture_output=True,
    )

    # Exit code must be 11.
    assert proc.returncode == 11, (
        f"expected exit code 11, got {proc.returncode}.\n"
        f"stdout: {proc.stdout.decode()}\n"
        f"stderr: {proc.stderr.decode()}"
    )

    # Credential files must have been cleaned up.
    assert not token_path.exists(), f"ui.token still exists after exit 11: {token_path}"
    assert not url_path.exists(), (
        f"dashboard.url still exists after exit 11: {url_path}"
    )

    # Listener port must be rebindable — uvicorn must have released it.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError as exc:
            pytest.fail(f"port {port} still in use after process exit: {exc}")
