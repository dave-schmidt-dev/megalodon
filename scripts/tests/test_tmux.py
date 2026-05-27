"""Unit tests for megalodon_ui.tmux — all subprocess calls are mocked."""

import asyncio
import shlex
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from megalodon_ui import tmux


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_proc(returncode: int = 0) -> MagicMock:
    """Return a mock process whose wait() resolves to returncode."""
    proc = MagicMock()
    proc.wait = AsyncMock(return_value=returncode)
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.returncode = returncode
    return proc


@pytest.fixture
def socket_path(tmp_path):
    return tmp_path / ".fleet" / "tmux.sock"


# ---------------------------------------------------------------------------
# new_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_session_two_calls(socket_path):
    """new_session makes exactly 2 subprocess_exec calls in order:

    1. ``set-option -g remain-on-exit on`` CHAINED (``;``) ahead of
       ``new-session`` in a single tmux invocation, so the option applies before
       the pane's command can exit (race fix — see tmux.new_session docstring).
    2. ``set-environment`` for the fleet-owned marker.
    """
    procs = [_mock_proc(0), _mock_proc(0)]

    with patch.object(
        asyncio, "create_subprocess_exec", new=AsyncMock(side_effect=procs)
    ) as mock_exec:
        rc = await tmux.new_session(
            socket_path, "lane-AUDIT", ["sleep", "30"], Path("/tmp"), {}, 80, 24
        )

    assert rc == 0
    assert mock_exec.call_count == 2

    first_args = mock_exec.call_args_list[0][0]
    assert "tmux" == first_args[0]
    assert "-S" in first_args
    # remain-on-exit set globally, chained BEFORE new-session.
    assert "set-option" in first_args
    assert "-g" in first_args
    assert "remain-on-exit" in first_args
    assert "on" in first_args
    assert ";" in first_args  # tmux command separator
    assert "new-session" in first_args
    assert first_args.index("set-option") < first_args.index("new-session")
    assert "-d" in first_args
    assert "-s" in first_args
    assert "lane-AUDIT" in first_args
    assert "-x" in first_args
    assert "80" in first_args
    assert "-y" in first_args
    assert "24" in first_args
    assert "sleep" in first_args
    assert "30" in first_args

    second_args = mock_exec.call_args_list[1][0]
    assert "set-environment" in second_args
    assert "MEGALODON_FLEET_OWNED" in second_args
    assert "1" in second_args
    assert "lane-AUDIT" in second_args


@pytest.mark.asyncio
async def test_new_session_aborts_on_first_failure(socket_path):
    """If the first chained (set-option + new-session) call fails (rc!=0), the
    follow-up set-environment call is NOT made."""
    fail_proc = _mock_proc(1)

    with patch.object(
        asyncio, "create_subprocess_exec", new=AsyncMock(return_value=fail_proc)
    ) as mock_exec:
        rc = await tmux.new_session(
            socket_path, "lane-AUDIT", ["sleep", "30"], Path("/tmp"), {}, 80, 24
        )

    assert rc == 1
    assert mock_exec.call_count == 1


@pytest.mark.asyncio
async def test_new_session_socket_in_every_call(socket_path):
    """Every call must include '-S' <socket_path>."""
    procs = [_mock_proc(0), _mock_proc(0), _mock_proc(0)]

    with patch.object(
        asyncio, "create_subprocess_exec", new=AsyncMock(side_effect=procs)
    ) as mock_exec:
        await tmux.new_session(socket_path, "s", ["true"], Path("/"), {}, 200, 50)

    for c in mock_exec.call_args_list:
        args = list(c[0])
        idx = args.index("-S")
        assert args[idx + 1] == str(socket_path)


@pytest.mark.asyncio
async def test_new_session_env_overlay(socket_path):
    """Env overlay is merged into os.environ and passed as the env kwarg."""
    procs = [_mock_proc(0), _mock_proc(0), _mock_proc(0)]

    with patch.object(
        asyncio, "create_subprocess_exec", new=AsyncMock(side_effect=procs)
    ) as mock_exec:
        await tmux.new_session(
            socket_path, "s", ["true"], Path("/"), {"MY_KEY": "my_val"}, 80, 24
        )

    first_kwargs = mock_exec.call_args_list[0][1]
    passed_env = first_kwargs.get("env", {})
    assert passed_env["MY_KEY"] == "my_val"
    assert len(passed_env) > 1  # os.environ keys present too


# ---------------------------------------------------------------------------
# kill_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_session_argv(socket_path):
    proc = _mock_proc(0)
    with patch.object(
        asyncio, "create_subprocess_exec", new=AsyncMock(return_value=proc)
    ) as mock_exec:
        rc = await tmux.kill_session(socket_path, "lane-AUDIT")

    assert rc == 0
    args = mock_exec.call_args[0]
    assert "kill-session" in args
    assert "-t" in args
    assert "lane-AUDIT" in args
    assert str(socket_path) in args


# ---------------------------------------------------------------------------
# has_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_has_session_true_on_rc0(socket_path):
    proc = _mock_proc(0)
    with patch.object(
        asyncio, "create_subprocess_exec", new=AsyncMock(return_value=proc)
    ):
        result = await tmux.has_session(socket_path, "lane-AUDIT")
    assert result is True


@pytest.mark.asyncio
async def test_has_session_false_on_nonzero(socket_path):
    proc = _mock_proc(1)
    with patch.object(
        asyncio, "create_subprocess_exec", new=AsyncMock(return_value=proc)
    ):
        result = await tmux.has_session(socket_path, "lane-AUDIT")
    assert result is False


@pytest.mark.asyncio
async def test_has_session_argv(socket_path):
    proc = _mock_proc(0)
    with patch.object(
        asyncio, "create_subprocess_exec", new=AsyncMock(return_value=proc)
    ) as mock_exec:
        await tmux.has_session(socket_path, "lane-AUDIT")
    args = mock_exec.call_args[0]
    assert "has-session" in args
    assert "lane-AUDIT" in args


# ---------------------------------------------------------------------------
# pipe_pane
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipe_pane_shell_cmd_no_stdbuf(socket_path, tmp_path):
    """pipe_pane shell command must be 'cat >> <quoted_dest>' with no stdbuf."""
    proc = _mock_proc(0)
    dest = tmp_path / "stream.log"
    expected_shell_cmd = f"cat >> {shlex.quote(str(dest))}"

    with patch.object(
        asyncio, "create_subprocess_exec", new=AsyncMock(return_value=proc)
    ) as mock_exec:
        await tmux.pipe_pane(socket_path, "lane-AUDIT", dest)

    args = mock_exec.call_args[0]
    assert expected_shell_cmd in args, f"expected {expected_shell_cmd!r} in {args}"
    combined = " ".join(str(a) for a in args)
    assert "stdbuf" not in combined


@pytest.mark.asyncio
async def test_pipe_pane_argv_structure(socket_path, tmp_path):
    """pipe_pane must pass pipe-pane -O -t <name> to tmux via exec."""
    proc = _mock_proc(0)
    dest = tmp_path / "stream.log"
    with patch.object(
        asyncio, "create_subprocess_exec", new=AsyncMock(return_value=proc)
    ) as mock_exec:
        await tmux.pipe_pane(socket_path, "lane-AUDIT", dest)

    args = mock_exec.call_args[0]
    assert "pipe-pane" in args
    assert "-O" in args
    assert "-t" in args
    assert "lane-AUDIT" in args


@pytest.mark.asyncio
async def test_pipe_pane_dest_with_spaces(socket_path, tmp_path):
    """Dest paths with spaces must produce a quoted shell_cmd argument."""
    proc = _mock_proc(0)
    dest = tmp_path / "path with spaces" / "stream.log"
    with patch.object(
        asyncio, "create_subprocess_exec", new=AsyncMock(return_value=proc)
    ) as mock_exec:
        await tmux.pipe_pane(socket_path, "lane-AUDIT", dest)

    args = mock_exec.call_args[0]
    shell_cmd = next((a for a in args if "cat >>" in str(a)), None)
    assert shell_cmd is not None
    # shlex.quote wraps path in single-quotes
    assert "'" in shell_cmd


# ---------------------------------------------------------------------------
# respawn_pane
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_respawn_pane_set_env_before_respawn(socket_path):
    """set-environment calls must precede the respawn-pane call."""
    calls_log: list[tuple] = []

    async def fake_exec(*args, **kwargs):
        calls_log.append(args)
        return _mock_proc(0)

    with patch.object(
        asyncio, "create_subprocess_exec", new=AsyncMock(side_effect=fake_exec)
    ):
        await tmux.respawn_pane(
            socket_path, "lane-AUDIT", ["echo", "hi"], {"FOO": "bar", "BAZ": "qux"}
        )

    set_env_indices = [i for i, c in enumerate(calls_log) if "set-environment" in c]
    respawn_indices = [i for i, c in enumerate(calls_log) if "respawn-pane" in c]
    assert len(set_env_indices) == 2
    assert len(respawn_indices) == 1
    assert all(i < respawn_indices[0] for i in set_env_indices)


@pytest.mark.asyncio
async def test_respawn_pane_argv_structure(socket_path):
    """respawn-pane must use -t <name> -k <argv...>."""
    procs = [_mock_proc(0), _mock_proc(0)]

    with patch.object(
        asyncio, "create_subprocess_exec", new=AsyncMock(side_effect=procs)
    ) as mock_exec:
        await tmux.respawn_pane(socket_path, "lane-AUDIT", ["echo", "hello"], {"K": "V"})

    last_args = mock_exec.call_args_list[-1][0]
    assert "respawn-pane" in last_args
    assert "-t" in last_args
    assert "lane-AUDIT" in last_args
    assert "-k" in last_args
    assert "echo" in last_args
    assert "hello" in last_args


@pytest.mark.asyncio
async def test_respawn_pane_no_env_single_call(socket_path):
    """With empty env dict, only one call (respawn-pane) is made."""
    proc = _mock_proc(0)
    with patch.object(
        asyncio, "create_subprocess_exec", new=AsyncMock(return_value=proc)
    ) as mock_exec:
        await tmux.respawn_pane(socket_path, "lane-AUDIT", ["true"], {})

    assert mock_exec.call_count == 1
    args = mock_exec.call_args[0]
    assert "respawn-pane" in args


@pytest.mark.asyncio
async def test_respawn_pane_aborts_if_set_env_fails(socket_path):
    """If a set-environment call fails, respawn-pane must NOT be called."""
    fail_proc = _mock_proc(1)

    with patch.object(
        asyncio, "create_subprocess_exec", new=AsyncMock(return_value=fail_proc)
    ) as mock_exec:
        rc = await tmux.respawn_pane(socket_path, "lane-AUDIT", ["true"], {"K": "V"})

    assert rc == 1
    for c in mock_exec.call_args_list:
        assert "respawn-pane" not in c[0]


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_sessions_returns_names(socket_path):
    proc = _mock_proc(0)
    proc.communicate = AsyncMock(return_value=(b"lane-AUDIT\nlane-ARCH\n", b""))
    proc.returncode = 0

    with patch.object(
        asyncio, "create_subprocess_exec", new=AsyncMock(return_value=proc)
    ):
        result = await tmux.list_sessions(socket_path)

    assert result == ["lane-AUDIT", "lane-ARCH"]


@pytest.mark.asyncio
async def test_list_sessions_empty_on_error(socket_path):
    proc = _mock_proc(1)
    proc.communicate = AsyncMock(return_value=(b"", b"error"))
    proc.returncode = 1

    with patch.object(
        asyncio, "create_subprocess_exec", new=AsyncMock(return_value=proc)
    ):
        result = await tmux.list_sessions(socket_path)

    assert result == []


@pytest.mark.asyncio
async def test_list_sessions_argv(socket_path):
    proc = _mock_proc(0)
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.returncode = 0

    with patch.object(
        asyncio, "create_subprocess_exec", new=AsyncMock(return_value=proc)
    ) as mock_exec:
        await tmux.list_sessions(socket_path)

    args = mock_exec.call_args[0]
    assert "list-sessions" in args
    assert "-F" in args
    assert "#{session_name}" in args


# ---------------------------------------------------------------------------
# kill_server
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_server_argv(socket_path):
    proc = _mock_proc(0)
    with patch.object(
        asyncio, "create_subprocess_exec", new=AsyncMock(return_value=proc)
    ) as mock_exec:
        rc = await tmux.kill_server(socket_path)

    assert rc == 0
    args = mock_exec.call_args[0]
    assert "kill-server" in args
    assert str(socket_path) in args


# ---------------------------------------------------------------------------
# display_message_pane_pipe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_display_message_pane_pipe_true_on_1(socket_path):
    proc = _mock_proc(0)
    proc.communicate = AsyncMock(return_value=(b"1\n", b""))

    with patch.object(
        asyncio, "create_subprocess_exec", new=AsyncMock(return_value=proc)
    ):
        result = await tmux.display_message_pane_pipe(socket_path, "lane-AUDIT")

    assert result is True


@pytest.mark.asyncio
async def test_display_message_pane_pipe_false_on_0(socket_path):
    proc = _mock_proc(0)
    proc.communicate = AsyncMock(return_value=(b"0\n", b""))

    with patch.object(
        asyncio, "create_subprocess_exec", new=AsyncMock(return_value=proc)
    ):
        result = await tmux.display_message_pane_pipe(socket_path, "lane-AUDIT")

    assert result is False


@pytest.mark.asyncio
async def test_display_message_pane_pipe_argv(socket_path):
    proc = _mock_proc(0)
    proc.communicate = AsyncMock(return_value=(b"0", b""))

    with patch.object(
        asyncio, "create_subprocess_exec", new=AsyncMock(return_value=proc)
    ) as mock_exec:
        await tmux.display_message_pane_pipe(socket_path, "lane-AUDIT")

    args = mock_exec.call_args[0]
    assert "display-message" in args
    assert "-t" in args
    assert "lane-AUDIT" in args
    assert "-p" in args
    assert "#{pane_pipe}" in args
