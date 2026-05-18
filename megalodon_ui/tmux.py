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
    """Create a new detached tmux session, set remain-on-exit, and mark it fleet-owned."""
    proc = await asyncio.create_subprocess_exec(
        "tmux",
        "-S", str(socket),
        "new-session",
        "-d",
        "-s", name,
        "-x", str(cols),
        "-y", str(rows),
        "-c", str(cwd),
        *argv,
        env={**os.environ, **env},
    )
    rc = await proc.wait()
    if rc != 0:
        return rc

    proc2 = await asyncio.create_subprocess_exec(
        "tmux", "-S", str(socket),
        "set-option", "-t", name,
        "remain-on-exit", "on",
    )
    rc2 = await proc2.wait()
    if rc2 != 0:
        return rc2

    proc3 = await asyncio.create_subprocess_exec(
        "tmux", "-S", str(socket),
        "set-environment", "-t", name,
        "MEGALODON_FLEET_OWNED", "1",
    )
    return await proc3.wait()


async def kill_session(socket: Path, name: str) -> int:
    """Kill the named tmux session; no-op (rc 0) if already absent."""
    proc = await asyncio.create_subprocess_exec(
        "tmux", "-S", str(socket),
        "kill-session", "-t", name,
    )
    return await proc.wait()


async def has_session(socket: Path, name: str) -> bool:
    """Return True if the named session exists on the given socket."""
    proc = await asyncio.create_subprocess_exec(
        "tmux", "-S", str(socket),
        "has-session", "-t", name,
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
        "tmux", "-S", str(socket),
        "pipe-pane", "-O", "-t", name,
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
            "tmux", "-S", str(socket),
            "set-environment", "-t", name,
            key, value,
        )
        rc = await proc.wait()
        if rc != 0:
            return rc

    proc = await asyncio.create_subprocess_exec(
        "tmux", "-S", str(socket),
        "respawn-pane", "-t", name, "-k",
        *argv,
    )
    return await proc.wait()


async def list_sessions(socket: Path) -> list[str]:
    """Return a list of session names on the given socket; empty list on error."""
    proc = await asyncio.create_subprocess_exec(
        "tmux", "-S", str(socket),
        "list-sessions", "-F", "#{session_name}",
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
        "tmux", "-S", str(socket),
        "kill-server",
    )
    return await proc.wait()


async def display_message_pane_pipe(socket: Path, name: str) -> bool:
    """Return True if pipe-pane is currently active on the named session's pane."""
    proc = await asyncio.create_subprocess_exec(
        "tmux", "-S", str(socket),
        "display-message", "-t", name, "-p", "#{pane_pipe}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode().strip() == "1"
