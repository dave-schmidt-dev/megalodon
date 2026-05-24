"""Race-free `python -m megalodon_ui` entrypoint (v9.2 CV-2 fix).

Binds the listener socket FIRST, holds it open through token write and
dashboard URL write, then hands the fd to uvicorn.Server so there is
no probe-close-rebind window (eliminates OW-2).

Phase 5 / Task D3: bearer token is now stable across restarts.
  - ``_resolve_token`` reuses an existing token (or generates a fresh one).
  - ``_rotate_clear`` wipes token + sessions before a new token is minted.
  - Normal exit no longer unlinks the token file so the URL stays valid.
  - ``--rotate-token`` forces a fresh token and revokes all active sessions.

Phase 5 / Task D5: harden token-URL exposure (PW-2).
  - ``_write_dashboard_url_atomic`` now creates the URL file at mode 0600.
  - ``_redact_token_url`` strips the bearer from log output (stdout unchanged).
  - ``_is_loopback_host`` detects non-local binds and issues a WARNING.
"""

from __future__ import annotations

import argparse
import errno
import ipaddress
import os
import socket
import sys
import webbrowser
from pathlib import Path

import uvicorn

from . import auth
from ._logging import get_logger
from ._tmux_version import probe_or_exit_6
from ._v92_constants import SOCKET_PATH_LIMIT_BYTES
from .constants import DEFAULT_PORT


# ---------------------------------------------------------------------------
# Token lifecycle helpers (extracted for unit-testability — D3)
# ---------------------------------------------------------------------------


def _resolve_token(token_path: Path) -> tuple[str, bool]:
    """Return (token, was_generated).

    If a non-empty token already exists at *token_path* it is reused and
    ``was_generated`` is False.  Otherwise a fresh token is minted, written
    atomically at 0600, and ``was_generated`` is True.

    Raises:
        FileExistsError: if ``write_token_atomic`` collides twice in a row
            (caller should translate this to exit 8).
    """
    existing = auth.read_token(token_path)
    if existing:  # non-None and non-empty after strip (read_token already strips)
        return existing, False
    token = auth.generate_token()
    auth.write_token_atomic(token_path, token)
    return token, True


def _rotate_clear(token_path: Path, sessions_path: Path) -> None:
    """Delete token and sessions files so the next resolve generates fresh credentials.

    Both unlinks are missing_ok — safe to call even if neither file exists.
    This is the core of ``--rotate-token``: removing sessions.json means the
    new SessionStore (constructed in make_app → lifespan) loads nothing,
    invalidating every existing cookie.
    """
    token_path.unlink(missing_ok=True)
    sessions_path.unlink(missing_ok=True)


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
        listener.close()
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
    """Atomic 0600 write of the dashboard URL to url_path.

    The URL embeds the bearer token, so it must be owner-only — the same
    triple-guard used by write_token_atomic: umask(0o077) biases the create
    syscall, O_CREAT|O_EXCL gives us exclusive ownership, fchmod corrects the
    mode if the filesystem ignored the umask.

    Sequence: create temp at 0600 → write → rename (atomic on POSIX).
    The temp file is created in the same directory so rename is a same-device
    move.  The temp itself is 0600 from creation — no world-readable window.
    """
    old_umask = os.umask(0o077)
    try:
        tmp = url_path.with_suffix(".tmp")
        # Remove any leftover temp from a previous interrupted run.
        tmp.unlink(missing_ok=True)
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, (url + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        tmp.rename(url_path)
    finally:
        os.umask(old_umask)


def _redact_token_url(url: str) -> str:
    """Replace the bearer value after ``#t=`` with ``<redacted>``.

    If ``#t=`` is not present in *url* the string is returned unchanged —
    the function is safe to call on any URL-like string.

    Used to scrub the bearer from log files while leaving stdout output
    (which the operator reads directly) unchanged.

    Examples::

        >>> _redact_token_url("http://127.0.0.1:8000/#t=SECRET")
        'http://127.0.0.1:8000/#t=<redacted>'
        >>> _redact_token_url("http://127.0.0.1:8000/")
        'http://127.0.0.1:8000/'
    """
    marker = "#t="
    idx = url.find(marker)
    if idx == -1:
        return url
    return url[: idx + len(marker)] + "<redacted>"


_LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})
_LOOPBACK_V4_NETWORK = ipaddress.IPv4Network("127.0.0.0/8")


def _is_loopback_host(host: str) -> bool:
    """Return True when *host* resolves to a loopback address.

    Loopback: ``127.0.0.1``, ``::1``, ``localhost``, or any address in the
    ``127.0.0.0/8`` range.  Everything else (``0.0.0.0``, LAN IPs, …) is
    non-loopback and warrants a security warning.

    Malformed addresses are treated as non-loopback (safe default).
    """
    if host in _LOOPBACK_NAMES:
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv4Address):
        return addr in _LOOPBACK_V4_NETWORK
    # IPv6: check the loopback flag directly.
    return addr.is_loopback


def _open_dashboard(url: str, *, enabled: bool, log) -> None:
    """Auto-open the dashboard in the operator's browser.

    Fleet observability is the product's whole point: a spawned fleet the
    operator cannot see is a failed launch. So the spawn path opens the
    dashboard automatically. The listener socket is already bound and
    ``listen()``-ing before this runs, so the browser's connection is queued by
    the kernel and served the instant uvicorn accepts — no connection-refused
    race.

    A browser-launch failure (headless host, no default browser) must NEVER
    crash the server: we log and fall back to the URL already printed to stdout.
    """
    if not enabled:
        log.info("Dashboard auto-open disabled (--no-browser); open manually: %s", url)
        return
    try:
        opened = webbrowser.open(url, new=2)
    except Exception as exc:  # noqa: BLE001 — browser launch must not be fatal
        log.warning("Could not auto-open dashboard (%s); open manually: %s", exc, url)
        return
    if opened:
        log.info("Opened dashboard in browser: %s", url)
    else:
        log.warning("No browser available to auto-open; open manually: %s", url)


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
    parser.add_argument(
        "--no-browser",
        action="store_true",
        default=os.environ.get("MEGALODON_NO_BROWSER") == "1",
        help="Do not auto-open the dashboard in a browser (for headless/CI runs).",
    )
    parser.add_argument(
        "--rotate-token",
        action="store_true",
        default=os.environ.get("MEGALODON_ROTATE_TOKEN") == "1",
        help=(
            "Delete the existing token + all sessions and mint a fresh token "
            "(revokes open dashboards)."
        ),
    )
    args = parser.parse_args()

    log = get_logger("megalodon_ui.main", debug=args.debug)

    # Step 0 (D5 PW-2): warn when bound to a non-loopback address.
    # The token file, URL file, and session store are designed for single-
    # operator localhost use.  A non-local bind exposes those credentials over
    # the network without any additional transport security.
    if not _is_loopback_host(args.host):
        log.warning(
            "Non-loopback bind detected (--host %s): persisted credentials "
            "(token file, URL file, sessions) are designed for localhost "
            "single-operator use only. Binding to a non-local address is "
            "unsupported and exposes the bearer token over the network.",
            args.host,
        )

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

    token_path = fleet_dir / "ui.token"
    url_path = fleet_dir / "dashboard.url"
    sessions_path = fleet_dir / "sessions.json"

    # Step 5 (D3 OW-5): --rotate-token clears token + sessions BEFORE make_app so
    # the new SessionStore (constructed in lifespan) loads nothing, invalidating all
    # prior cookies.  The force-open behavior triggered by rotation is wired in D4.
    if args.rotate_token:
        _rotate_clear(token_path, sessions_path)
        log.info("--rotate-token: existing token and sessions cleared")

    # Step 6: bind listener and hold it open.
    listener = _bind_listener(args.host, args.port)

    # Step 7: lazy import app factory now that we know mission_dir is valid.
    from .server import make_app  # noqa: PLC0415

    app = make_app(mission_dir=mission_dir, port=args.port)

    # Step 8 (cleanup-guarded block): covers token write, URL write, uvicorn.
    # Initialise before the try so the except branch can always read it safely.
    token_was_generated: bool = False
    try:
        # Step 9: reuse existing token or generate a fresh one (D3 stable-token).
        try:
            token, token_was_generated = _resolve_token(token_path)
        except FileExistsError:
            sys.stderr.write(
                f"failed to write bearer token to {token_path} after retry; exit 8\n"
            )
            sys.exit(8)
        if token_was_generated:
            log.debug("Minted fresh bearer token → %s", token_path)
        else:
            log.debug("Reusing existing bearer token from %s", token_path)

        # Step 10: compose + emit dashboard URL (stdout, log, file).
        # stdout: full URL (operator copies it to open the dashboard).
        # log file: redacted URL (bearer must not appear in log files, PW-2).
        # url file: full URL at 0600 (owner-only, bearer embedded).
        dashboard_url = f"http://{args.host}:{args.port}/#t={token}"
        print(dashboard_url, flush=True)
        log.info("Dashboard: %s", _redact_token_url(dashboard_url))
        _write_dashboard_url_atomic(url_path, dashboard_url)

        # Step 10b: auto-open the dashboard. The listener is already bound +
        # listening (Step 6), so the browser's request queues until uvicorn
        # accepts it below — no connection-refused race. Non-fatal on failure.
        _open_dashboard(dashboard_url, enabled=not args.no_browser, log=log)

        # Step 11: hand fd to uvicorn — it adopts the socket, no re-bind.
        config = uvicorn.Config(
            app=app,
            fd=listener.fileno(),
            log_level="debug" if args.debug else "info",
            lifespan="on",
        )
        uvicorn.Server(config).run()
        # Normal exit: uvicorn closed the underlying fd; call listener.detach()
        # so the Python socket object releases ownership and does not emit
        # ResourceWarning when GC'd.
        listener.detach()

    except BaseException:
        # Best-effort cleanup on any error (CV-7 / D3): only remove the token
        # and URL files if THIS run created them.  Never delete a reused token —
        # the next restart would lose its stable URL.
        if token_was_generated:
            token_path.unlink(missing_ok=True)
            url_path.unlink(missing_ok=True)
        try:
            listener.close()
        except OSError:
            pass
        raise
    else:
        # Normal shutdown (D3): token + URL persist so the next restart reuses
        # the same token and the dashboard URL stays valid for an open browser tab.
        # uvicorn closed the listener socket above.
        pass


if __name__ == "__main__":
    main()
