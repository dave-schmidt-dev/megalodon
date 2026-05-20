"""Integration test: lifespan startup-timeout (exit 11) cleans up token, URL, and listener.

This test invokes `python -m megalodon_ui` as a subprocess with a very short
timeout and a lifespan that sleeps past that timeout, then verifies:
  - process exits with code 11
  - .fleet/ui.token does not exist
  - .fleet/dashboard.url does not exist
  - the listener port is rebindable (no EADDRINUSE)

NOTE: The lifespan timeout path (exit 11) is wired in Task 1.5. The current
lifespan is a no-op that returns immediately, so the server exits 0 without
triggering cleanup. This entire test is marked xfail until Task 1.5 lands.
TODO: remove xfail marker when Task 1.5 lifespan timeout integration is merged.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.xfail(
    reason=(
        "depends on Task 1.5 lifespan timeout integration: current lifespan is a "
        "no-op and exits 0 without raising the startup-timeout error (exit 11). "
        "Remove this xfail once LIFESPAN_STARTUP_TIMEOUT_SECONDS / "
        "MEGALODON_LIFESPAN_TIMEOUT_S is honoured in the lifespan context."
    ),
    strict=False,
)
def test_startup_timeout_cleans_up_token_and_listener(tmp_path: Path) -> None:
    """Subprocess exits 11 and leaves no credential files or held port."""
    port = _find_free_port()
    fleet_dir = tmp_path / ".fleet"
    token_path = fleet_dir / "ui.token"
    url_path = fleet_dir / "dashboard.url"

    env = os.environ.copy()
    # Task 1.5 lifespan reads these to simulate a slow startup that exceeds timeout.
    env["MEGALODON_LIFESPAN_SLEEP_S"] = "5"
    env["MEGALODON_LIFESPAN_TIMEOUT_S"] = "0.5"
    env["MEGALODON_DEBUG"] = "0"

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "megalodon_ui",
            "--mission-dir",
            str(tmp_path),
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
