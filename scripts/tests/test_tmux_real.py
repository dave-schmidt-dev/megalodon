"""Integration tests for megalodon_ui.tmux — require a real tmux binary."""

import asyncio
import shutil
from pathlib import Path

import pytest
import pytest_asyncio

from megalodon_ui import tmux

pytestmark = pytest.mark.skipif(
    shutil.which("tmux") is None,
    reason="tmux not on PATH",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmux_socket(tmp_path: Path) -> Path:
    """Return a per-test socket path under tmp_path."""
    return tmp_path / "tmux.sock"


@pytest_asyncio.fixture(autouse=True)
async def _kill_server_on_teardown(tmux_socket: Path):
    """Kill the tmux server after every test; suppress errors if already gone."""
    yield
    try:
        await tmux.kill_server(tmux_socket)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Happy path: new_session / has_session / kill_session lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_session_has_session_kill_session(
    tmux_socket: Path, tmp_path: Path
) -> None:
    rc = await tmux.new_session(
        tmux_socket, "test-lane", ["sleep", "30"], tmp_path, {}, 80, 24
    )
    assert rc == 0, f"new_session returned {rc}"

    assert await tmux.has_session(tmux_socket, "test-lane") is True

    rc_kill = await tmux.kill_session(tmux_socket, "test-lane")
    assert rc_kill == 0

    assert await tmux.has_session(tmux_socket, "test-lane") is False


# ---------------------------------------------------------------------------
# MEGALODON_FLEET_OWNED marker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fleet_owned_env_marker(tmux_socket: Path, tmp_path: Path) -> None:
    rc = await tmux.new_session(
        tmux_socket, "test-lane", ["sleep", "30"], tmp_path, {}, 80, 24
    )
    assert rc == 0

    proc = await asyncio.create_subprocess_exec(
        "tmux", "-S", str(tmux_socket),
        "show-environment", "-t", "test-lane",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    assert b"MEGALODON_FLEET_OWNED" in stdout, (
        f"MEGALODON_FLEET_OWNED not found in show-environment output: {stdout!r}"
    )


# ---------------------------------------------------------------------------
# remain-on-exit regression
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remain_on_exit_session_survives_false(
    tmux_socket: Path, tmp_path: Path
) -> None:
    """Session must still exist after pane exits with rc=1 (remain-on-exit on)."""
    rc = await tmux.new_session(
        tmux_socket, "test-lane", ["false"], tmp_path, {}, 80, 24
    )
    assert rc == 0

    await asyncio.sleep(0.5)

    still_alive = await tmux.has_session(tmux_socket, "test-lane")
    assert still_alive, (
        "Session disappeared after pane exit — remain-on-exit on may not be set"
    )


# ---------------------------------------------------------------------------
# pipe_pane: byte delivery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipe_pane_byte_delivery(tmux_socket: Path, tmp_path: Path) -> None:
    stream_log = tmp_path / "stream.log"
    stream_log.touch()

    rc = await tmux.new_session(
        tmux_socket,
        "test-lane",
        ["sh", "-c", "echo hello-pipe; sleep 5"],
        tmp_path,
        {},
        80,
        24,
    )
    assert rc == 0

    pipe_rc = await tmux.pipe_pane(tmux_socket, "test-lane", stream_log)
    assert pipe_rc == 0

    await asyncio.sleep(0.5)

    content = stream_log.read_bytes()
    assert b"hello-pipe" in content, (
        f"Expected b'hello-pipe' in stream log; got {content!r}"
    )


# ---------------------------------------------------------------------------
# respawn_pane: byte accumulation after respawn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_respawn_pane_accumulates_output(
    tmux_socket: Path, tmp_path: Path
) -> None:
    stream_log = tmp_path / "stream.log"
    stream_log.touch()

    rc = await tmux.new_session(
        tmux_socket,
        "test-lane",
        ["sleep", "30"],
        tmp_path,
        {},
        80,
        24,
    )
    assert rc == 0

    pipe_rc = await tmux.pipe_pane(tmux_socket, "test-lane", stream_log)
    assert pipe_rc == 0

    respawn_rc = await tmux.respawn_pane(
        tmux_socket, "test-lane", ["sh", "-c", "echo respawned; sleep 5"], {}
    )
    assert respawn_rc == 0

    # Re-pipe after respawn (new pane_id from respawn-pane)
    await tmux.pipe_pane(tmux_socket, "test-lane", stream_log)

    await asyncio.sleep(0.5)

    content = stream_log.read_bytes()
    assert b"respawned" in content, (
        f"Expected b'respawned' in stream log; got {content!r}"
    )

    assert await tmux.has_session(tmux_socket, "test-lane") is True


# ---------------------------------------------------------------------------
# list_sessions: multiple sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_sessions_multiple(tmux_socket: Path, tmp_path: Path) -> None:
    rc1 = await tmux.new_session(
        tmux_socket, "lane-alpha", ["sleep", "30"], tmp_path, {}, 80, 24
    )
    assert rc1 == 0

    rc2 = await tmux.new_session(
        tmux_socket, "lane-beta", ["sleep", "30"], tmp_path, {}, 80, 24
    )
    assert rc2 == 0

    sessions = await tmux.list_sessions(tmux_socket)
    assert "lane-alpha" in sessions
    assert "lane-beta" in sessions
