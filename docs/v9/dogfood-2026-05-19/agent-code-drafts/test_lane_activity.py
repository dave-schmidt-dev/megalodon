"""Unit tests for _parse_stream_tail (S-LIVE-ACTIVITY BE helper)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from megalodon_ui.server import _parse_stream_tail  # noqa: E402


# Minimal ANSI-escaped tail that resembles a Claude TUI pipe-pane snapshot.
_SAMPLE_ANSI = (
    b"\x1b[38;5;174m\xe2\x9c\xb3\x1b[39m \x1b[38;5;174mMarinating\xe2\x80\xa6 "
    b"\x1b[38;5;246m(1m 20s \xc2\xb7 \xe2\x86\x93\x1b[1C4.2k tokens)\x1b[39m\n"
    b"\x1b[38;5;244m"
    + b"\xe2\x94\x80" * 80
    + b"\x1b[39m\n"
    b"\x1b[38;5;246m\xe2\x9d\xaf \x1b[39m\x1b[7m \x1b[27m\n"
    b"  \x1b[38;5;246mctx:\x1b[1C\x1b[32m56k/200k\x1b[1C(28%)\x1b[38;5;246m "
    b"|\x1b[1C5h:\x1b[1C\x1b[37m\x1b[1m74%\x1b[22m\n"
    b"  \x1b[33m\x1b[1mSonnet\x1b[1C4.6\x1b[22m\x1b[38;5;246m "
    b"|\x1b[1Ccache:\x1b[1C\x1b[32m94%\x1b[38;5;246m |\x1b[1Csession:\x1b[1C\x1b[35m3m\x1b[39m\n"
    b"writing finding for S-LIVE-ACTIVITY\n"
    b"  \x1b[38;5;246m/Users/dave/Documents/Projects/megalodon-fleet\x1b[39m\n"
)


def test_parse_stream_tail_extracts_token_ctx(tmp_path):
    log = tmp_path / "A.stream.log"
    log.write_bytes(_SAMPLE_ANSI)

    result = _parse_stream_tail(log)

    assert result["token_ctx"] == "56k/200k"


def test_parse_stream_tail_last_text_is_meaningful(tmp_path):
    log = tmp_path / "A.stream.log"
    log.write_bytes(_SAMPLE_ANSI)

    result = _parse_stream_tail(log)

    # Should skip the TUI chrome (ctx, Sonnet, session, path) and return
    # the agent output line.
    assert result["last_text"] == "writing finding for S-LIVE-ACTIVITY"


def test_parse_stream_tail_status_active_for_fresh_file(tmp_path):
    log = tmp_path / "A.stream.log"
    log.write_bytes(_SAMPLE_ANSI)
    # mtime defaults to now — should be "active"

    result = _parse_stream_tail(log)

    assert result["status"] == "active"


def test_parse_stream_tail_status_blocked_for_stale_file(tmp_path):
    log = tmp_path / "A.stream.log"
    log.write_bytes(_SAMPLE_ANSI)
    stale_ts = time.time() - 600  # 10 minutes ago
    import os
    os.utime(log, (stale_ts, stale_ts))

    result = _parse_stream_tail(log)

    assert result["status"] == "blocked"


def test_parse_stream_tail_missing_file_returns_blocked():
    result = _parse_stream_tail(Path("/nonexistent/.fleet/Z.stream.log"))

    assert result["status"] == "blocked"
    assert result["token_ctx"] is None
    assert result["last_text"] is None


def test_parse_stream_tail_last_activity_utc_format(tmp_path):
    log = tmp_path / "A.stream.log"
    log.write_bytes(b"hello world\n")

    result = _parse_stream_tail(log)

    utc = result["last_activity_utc"]
    assert utc is not None
    # Should match YYYY-MM-DDTHH-MM-SSZ
    import re
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z", utc)
