"""ANSI byte-preservation smoke test for pipe-pane (Task 3.5 — SR-4).

Plan §6.5 / SR-4: the pipe-pane → bytes-file → tail → base64-SSE pipeline
must be byte-transparent. If anything along the way mangles ANSI escape
sequences (SGR colours, cursor moves, clear-screen), xterm.js in the
browser will render garbage and the operator loses visual fidelity.

This test spawns a real tmux session whose initial command emits three
canonical SGR escape sequences via ``printf``, waits briefly for
pipe-pane to flush, then asserts each sequence appears verbatim in the
captured stream log.

Marked ``@pytest.mark.isolated`` (CI ``pytest -p forked -m isolated``)
because shared event-loop state between this test and a fast follow-up
test can interleave pipe-pane fd writes.

Skipped where tmux is not installed. The tmux control socket binds under a
short /tmp dir (shared ``tmux_socket`` fixture in conftest.py) so it fits the
104-byte sun_path limit — the deep pytest tmp_path would otherwise blow it.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
import pytest_asyncio

from megalodon_ui import tmux


pytestmark = [
    pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not on PATH"),
    pytest.mark.isolated,
]


# tmux_socket: shared short-path fixture from conftest.py (avoids the 104-byte
# sun_path limit that the deep pytest tmp_path would otherwise exceed).


@pytest_asyncio.fixture(autouse=True)
async def _kill_server_on_teardown(tmux_socket: Path):
    yield
    try:
        await tmux.kill_server(tmux_socket)
    except Exception:
        pass


# Canonical SGR sequences from plan §6.5 SR-4.
_SGR_RED = b"\x1b[31mred\x1b[0m"
_SGR_HIGHLIGHT = b"\x1b[1;7mhighlight\x1b[0m"
_SGR_CLEAR_AND_HOME = b"\x1b[2J\x1b[H"


@pytest.mark.asyncio
async def test_pipe_pane_preserves_canonical_sgr_escape_sequences(
    tmux_socket: Path, tmp_path: Path
) -> None:
    stream_log = tmp_path / "stream.log"
    stream_log.touch()

    # printf-driven emission so no echo-added newline can confuse the check.
    # The escapes \033 == \x1b are octal-escaped for the shell.
    payload = (
        r"\033[31mred\033[0m"
        r"\033[1;7mhighlight\033[0m"
        r"\033[2J\033[H"
    )
    # Lead with a short sleep so pipe-pane attaches BEFORE printf emits —
    # pipe-pane (-O) only captures future pane output; bytes printed before the
    # attach are missed. The 3s poll-loop below then catches the post-attach emit.
    rc = await tmux.new_session(
        tmux_socket,
        "test-lane",
        ["sh", "-c", f"sleep 0.5; printf '{payload}'; sleep 5"],
        tmp_path,
        {},
        80,
        24,
    )
    assert rc == 0

    pipe_rc = await tmux.pipe_pane(tmux_socket, "test-lane", stream_log)
    assert pipe_rc == 0

    # Allow pipe-pane to flush; the shell sleep keeps the pane alive.
    deadline = asyncio.get_running_loop().time() + 3.0
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.1)
        data = stream_log.read_bytes()
        if _SGR_RED in data and _SGR_HIGHLIGHT in data and _SGR_CLEAR_AND_HOME in data:
            break

    content = stream_log.read_bytes()
    assert _SGR_RED in content, (
        f"SGR red escape not preserved in stream log; got {content!r}"
    )
    assert _SGR_HIGHLIGHT in content, (
        f"SGR highlight escape not preserved; got {content!r}"
    )
    assert _SGR_CLEAR_AND_HOME in content, (
        f"SGR clear+home escape not preserved; got {content!r}"
    )
