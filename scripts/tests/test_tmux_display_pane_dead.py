"""P6.4 — tmux.display_message_pane_dead() primitive.

CV-8 mitigation: instead of polling every pane's status in the background,
the state endpoint runs ``tmux display-message -p -F
'#{pane_dead}|#{pane_dead_status}' -t lane-<NAME>`` on demand, with a 1 s
TTL cache on LaneSession. This unit test pins the argv shape and the
output-parsing contract.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from megalodon_ui import tmux


@pytest.fixture
def socket_path(tmp_path):
    return tmp_path / ".fleet" / "tmux.sock"


class _FakeProc:
    def __init__(self, stdout: bytes, rc: int = 0) -> None:
        self._stdout = stdout
        self._rc = rc

    async def communicate(self):
        return self._stdout, b""

    async def wait(self):
        return self._rc

    @property
    def returncode(self):
        return self._rc


@pytest.mark.asyncio
async def test_display_message_pane_dead_returns_dead_true_with_status(socket_path):
    fake = _FakeProc(stdout=b"1|17\n", rc=0)
    with patch(
        "megalodon_ui.tmux.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake),
    ):
        dead, status = await tmux.display_message_pane_dead(socket_path, "lane-A")
    assert dead is True
    assert status == 17


@pytest.mark.asyncio
async def test_display_message_pane_dead_returns_dead_false_when_running(socket_path):
    fake = _FakeProc(stdout=b"0|\n", rc=0)
    with patch(
        "megalodon_ui.tmux.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake),
    ):
        dead, status = await tmux.display_message_pane_dead(socket_path, "lane-A")
    assert dead is False
    assert status is None


@pytest.mark.asyncio
async def test_display_message_pane_dead_returns_dead_true_with_zero_status(socket_path):
    fake = _FakeProc(stdout=b"1|0\n", rc=0)
    with patch(
        "megalodon_ui.tmux.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake),
    ):
        dead, status = await tmux.display_message_pane_dead(socket_path, "lane-A")
    assert dead is True
    assert status == 0


@pytest.mark.asyncio
async def test_display_message_pane_dead_handles_unknown_session(socket_path):
    """Non-zero rc (e.g., session not found) returns (False, None) — caller
    interprets this as 'pane not dead, just not queryable right now'."""
    fake = _FakeProc(stdout=b"", rc=1)
    with patch(
        "megalodon_ui.tmux.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake),
    ):
        dead, status = await tmux.display_message_pane_dead(socket_path, "lane-Z")
    assert dead is False
    assert status is None


@pytest.mark.asyncio
async def test_display_message_pane_dead_argv_shape(socket_path):
    """Pin the tmux argv shape so a regression on the format string is loud."""
    fake = _FakeProc(stdout=b"0|\n", rc=0)
    mock = AsyncMock(return_value=fake)
    with patch("megalodon_ui.tmux.asyncio.create_subprocess_exec", new=mock):
        await tmux.display_message_pane_dead(socket_path, "lane-A")
    args = mock.call_args.args
    assert args[0] == "tmux"
    assert args[1] == "-S"
    assert args[2] == str(socket_path)
    assert args[3] == "display-message"
    assert "-p" in args
    assert "-F" in args
    fmt_idx = args.index("-F")
    assert args[fmt_idx + 1] == "#{pane_dead}|#{pane_dead_status}"
    assert "-t" in args
    t_idx = args.index("-t")
    assert args[t_idx + 1] == "lane-A"
