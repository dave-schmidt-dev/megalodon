"""Race-free `python -m megalodon_ui` entrypoint (v9.2 CV-2 fix).

Binds the listener socket FIRST, holds it open through token write and
dashboard URL write, then hands the fd to uvicorn.Server so there is
no probe-close-rebind window (eliminates OW-2).
"""

from __future__ import annotations

import argparse
import errno
import os
import socket
import sys
from pathlib import Path

import uvicorn

from . import auth
from ._logging import get_logger
from ._tmux_version import probe_or_exit_6
from ._v92_constants import SOCKET_PATH_LIMIT_BYTES
from .constants import DEFAULT_PORT


def _bind_listener(host: str, port: int) -> socket.socket:
    """Create, bind and listen on (host, port); exit 9 on EADDRINUSE."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # SO_REUSEADDR lets us re-bind to a port in TIME_WAIT (common in dev/CI
    # restart loops). It does NOT enable concurrent listeners — a second active
    # listener still raises EADDRINUSE. So "two megalodon-ui on the same port"
    # still fails loudly, which is the property the safety guard cares about.
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind((host, port))
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            sys.stderr.write(
                f"port {port} already in use; another megalodon-ui server may be "
                "running on this mission\n"
            )
            sys.exit(9)
        raise
    listener.listen(128)
    return listener


def _write_dashboard_url_atomic(url_path: Path, url: str) -> None:
    """Atomic write dashboard URL to url_path (mode 0644)."""
    old_umask = os.umask(0o022)
    try:
        tmp = url_path.with_suffix(".tmp")
        tmp.write_text(url + "\n", encoding="utf-8")
        tmp.rename(url_path)
    finally:
        os.umask(old_umask)


def main() -> None:
    """Parse args, bind socket, write token + URL, hand fd to uvicorn."""
    parser = argparse.ArgumentParser(
        prog="python -m megalodon_ui",
        description="Megalodon orchestrator-console FastAPI server.",
    )
    parser.add_argument(
        "--mission-dir",
        default=os.environ.get("MEGALODON_MISSION_DIR"),
        help="Path to mission directory (default: $MEGALODON_MISSION_DIR or repo root).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MEGALODON_PORT", str(DEFAULT_PORT))),
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("MEGALODON_HOST", "127.0.0.1"),
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=os.environ.get("MEGALODON_DEBUG") == "1",
    )
    args = parser.parse_args()

    log = get_logger("megalodon_ui.main", debug=args.debug)

    # Step 1: resolve mission_dir — exit 7 if unusable.
    raw_mission = args.mission_dir or str(Path(__file__).resolve().parent.parent)
    mission_dir = Path(raw_mission).resolve()
    if not mission_dir.exists() or not mission_dir.is_dir():
        sys.stderr.write(
            f"mission directory does not exist or is not a directory: {mission_dir}\n"
        )
        sys.exit(7)

    # Step 2: tmux availability + version >= 2.6.
    probe_or_exit_6()

    # Step 3: mkdir .fleet/ (0700).
    fleet_dir = mission_dir / ".fleet"
    try:
        fleet_dir.mkdir(mode=0o700, parents=False, exist_ok=True)
    except OSError as exc:
        sys.stderr.write(f".fleet/ not writable under {mission_dir}: {exc}\n")
        sys.exit(7)

    # Step 4: socket path length check (exit 10).
    if len((fleet_dir / "tmux.sock").as_posix().encode()) > SOCKET_PATH_LIMIT_BYTES:
        sys.stderr.write(
            f"socket path exceeds {SOCKET_PATH_LIMIT_BYTES} bytes; shorten the mission path\n"
        )
        sys.exit(10)

    # Step 5: bind listener and hold it open.
    listener = _bind_listener(args.host, args.port)

    # Step 6: lazy import app factory now that we know mission_dir is valid.
    from .server import make_app  # noqa: PLC0415

    app = make_app(mission_dir=mission_dir, port=args.port)

    token_path = fleet_dir / "ui.token"
    url_path = fleet_dir / "dashboard.url"

    # Step 6 (cleanup-guarded block): covers token write, URL write, uvicorn.
    try:
        # Step 7: generate + atomically write bearer token.
        token = auth.generate_token()
        try:
            auth.write_token_atomic(token_path, token)
        except FileExistsError:
            sys.stderr.write(
                f"failed to write bearer token to {token_path} after retry; exit 8\n"
            )
            sys.exit(8)

        # Step 8: compose + emit dashboard URL (stdout, log, file).
        dashboard_url = f"http://{args.host}:{args.port}/#t={token}"
        print(dashboard_url, flush=True)
        log.info("Dashboard: %s", dashboard_url)
        _write_dashboard_url_atomic(url_path, dashboard_url)

        # Step 9: hand fd to uvicorn — it adopts the socket, no re-bind.
        config = uvicorn.Config(
            app=app,
            fd=listener.fileno(),
            log_level="debug" if args.debug else "info",
            lifespan="on",
        )
        uvicorn.Server(config).run()

    except BaseException:
        # Best-effort cleanup on any error (CV-7).
        token_path.unlink(missing_ok=True)
        url_path.unlink(missing_ok=True)
        try:
            listener.close()
        except OSError:
            pass
        raise
    else:
        # Normal shutdown: clean up credential files; uvicorn closed the listener.
        token_path.unlink(missing_ok=True)
        url_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
