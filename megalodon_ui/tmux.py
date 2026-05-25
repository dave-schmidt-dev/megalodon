"""Async, socket-scoped tmux wrapper for the Megalodon fleet spawner."""

import asyncio
import os
import shlex
from pathlib import Path


async def new_session(
    socket: Path,
    name: str,
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    cols: int,
    rows: int,
) -> int:
    """Create a new detached tmux session with remain-on-exit ON, fleet-owned.

    ``remain-on-exit`` is set GLOBALLY and CHAINED ahead of ``new-session`` in a
    single tmux invocation, so it applies before the pane's command can run. The
    earlier ordering (new-session, THEN a separate set-option) raced: a command
    that exits instantly (e.g. a harness that crashes on spawn) destroyed the
    pane — and the single-session server — before the option was applied, so the
    session vanished instead of staying visible for inspection.
    """
    proc = await asyncio.create_subprocess_exec(
        "tmux",
        "-S",
        str(socket),
        "set-option",
        "-g",
        "remain-on-exit",
        "on",
        ";",
        "new-session",
        "-d",
        "-s",
        name,
        "-x",
        str(cols),
        "-y",
        str(rows),
        "-c",
        str(cwd),
        *argv,
        env={**os.environ, **env},
    )
    rc = await proc.wait()
    if rc != 0:
        return rc

    proc3 = await asyncio.create_subprocess_exec(
        "tmux",
        "-S",
        str(socket),
        "set-environment",
        "-t",
        name,
        "MEGALODON_FLEET_OWNED",
        "1",
    )
    return await proc3.wait()


async def kill_session(socket: Path, name: str) -> int:
    """Kill the named tmux session; no-op (rc 0) if already absent."""
    proc = await asyncio.create_subprocess_exec(
        "tmux",
        "-S",
        str(socket),
        "kill-session",
        "-t",
        name,
    )
    return await proc.wait()


async def has_session(socket: Path, name: str) -> bool:
    """Return True if the named session exists on the given socket."""
    proc = await asyncio.create_subprocess_exec(
        "tmux",
        "-S",
        str(socket),
        "has-session",
        "-t",
        name,
    )
    rc = await proc.wait()
    return rc == 0


async def pipe_pane(socket: Path, name: str, dest: Path) -> int:
    """Attach pipe-pane to the named session, appending PTY bytes to dest."""
    # The shell command is the argument to tmux pipe-pane; tmux itself is
    # invoked via exec (no shell wrapping of tmux).  The redirect inside the
    # shell_cmd is intentional and required — pipe-pane passes this string to
    # sh(1) internally.  shlex.quote on dest prevents path injection.
    shell_cmd = f"cat >> {shlex.quote(str(dest))}"
    proc = await asyncio.create_subprocess_exec(
        "tmux",
        "-S",
        str(socket),
        "pipe-pane",
        "-O",
        "-t",
        name,
        shell_cmd,
    )
    return await proc.wait()


async def respawn_pane(
    socket: Path,
    name: str,
    argv: list[str],
    env: dict[str, str],
) -> int:
    """Set env vars then respawn the pane in the named session with new argv."""
    for key, value in env.items():
        proc = await asyncio.create_subprocess_exec(
            "tmux",
            "-S",
            str(socket),
            "set-environment",
            "-t",
            name,
            key,
            value,
        )
        rc = await proc.wait()
        if rc != 0:
            return rc

    proc = await asyncio.create_subprocess_exec(
        "tmux",
        "-S",
        str(socket),
        "respawn-pane",
        "-t",
        name,
        "-k",
        *argv,
    )
    return await proc.wait()


async def list_sessions(socket: Path) -> list[str]:
    """Return a list of session names on the given socket; empty list on error."""
    proc = await asyncio.create_subprocess_exec(
        "tmux",
        "-S",
        str(socket),
        "list-sessions",
        "-F",
        "#{session_name}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return []
    return [line for line in stdout.decode().splitlines() if line]


async def kill_server(socket: Path) -> int:
    """Kill the tmux server at the given socket path."""
    proc = await asyncio.create_subprocess_exec(
        "tmux",
        "-S",
        str(socket),
        "kill-server",
    )
    return await proc.wait()


async def display_message_pane_dead(socket: Path, name: str) -> tuple[bool, int | None]:
    """Query a pane's dead-ness + exit status (CV-8 lazy probe).

    Runs ``tmux display-message -p -F '#{pane_dead}|#{pane_dead_status}'``
    on the named session's pane 0.0 and parses the single-line output.

    Returns:
        (dead, status_or_None)
        * ``dead=False`` for a running pane; status is ``None``.
        * ``dead=True`` for an exited pane; status is the integer rc tmux
          captured (may be 0).
        * Any non-zero rc or unparseable output → ``(False, None)`` so the
          caller treats the query as "no signal" rather than "pane is dead
          but rc unknown".
    """
    spawn = asyncio.create_subprocess_exec
    proc = await spawn(
        "tmux",
        "-S",
        str(socket),
        "display-message",
        "-p",
        "-F",
        "#{pane_dead}|#{pane_dead_status}",
        "-t",
        name,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return False, None
    text = stdout.decode("utf-8", errors="replace").strip()
    if "|" not in text:
        return False, None
    dead_str, status_str = text.split("|", 1)
    dead = dead_str.strip() == "1"
    status_str = status_str.strip()
    if not status_str or not dead:
        return dead, None
    try:
        return dead, int(status_str)
    except ValueError:
        return dead, None


async def send_keys(socket: Path, name: str, keys: str, *, enter: bool = True) -> int:
    """Type ``keys`` into the named session's active pane.

    Used by FleetSpawner to deliver the per-lane initial_prompt into a
    live-REPL CLI after it's had time to render its TUI. ``enter=True``
    appends an Enter keystroke so a slash command or prompt fires.
    """
    proc = await asyncio.create_subprocess_exec(
        "tmux",
        "-S",
        str(socket),
        "send-keys",
        "-t",
        name,
        keys,
        *(["Enter"] if enter else []),
    )
    return await proc.wait()


async def display_message_pane_pid(socket: Path, name: str) -> int | None:
    """Return the OS pid of the named session's pane child process, or None.

    Runs ``tmux display-message -p -F '#{pane_pid}'`` on pane 0.0. tmux reports
    the pid of the process tmux forked for the pane (the harness CLI). Used by
    the spawner to write ``~/.megalodon-pids/<lane>.pid`` so the watchdog's S1
    process-alive detector has a pid to probe.

    Returns:
        The integer pid, or None on any non-zero rc / unparseable output (the
        caller degrades to tmux ``has_session`` liveness).
    """
    spawn = asyncio.create_subprocess_exec
    proc = await spawn(
        "tmux",
        "-S",
        str(socket),
        "display-message",
        "-p",
        "-F",
        "#{pane_pid}",
        "-t",
        name,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return None
    text = stdout.decode("utf-8", errors="replace").strip()
    try:
        return int(text)
    except ValueError:
        return None


async def display_message_pane_pipe(socket: Path, name: str) -> bool:
    """Return True if pipe-pane is currently active on the named session's pane."""
    proc = await asyncio.create_subprocess_exec(
        "tmux",
        "-S",
        str(socket),
        "display-message",
        "-t",
        name,
        "-p",
        "#{pane_pipe}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode().strip() == "1"
