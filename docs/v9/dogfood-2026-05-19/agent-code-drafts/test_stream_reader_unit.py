"""Unit tests for megalodon_ui.stream_reader.LaneStreamReader (CV-9, P2-C).

All I/O is to real tmpdir files (no mocking of file reads); tmux is not
involved. Tests exercise the five P1-C plan scenarios:
  1. run_delivers_parsed_events
  2. run_skips_none_parse_results
  3. run_waits_for_file_existence
  4. run_handles_file_rotation
  5. run_exits_on_stop_event
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from megalodon_ui.harnesses.base import Event
from megalodon_ui.stream_reader import (
    EOF_POLL_S,
    FILE_EXIST_POLL_S,
    FILE_EXIST_TIMEOUT_S,
    LaneStreamReader,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_adapter(parse_results: list[Event | None]) -> MagicMock:
    """Return an adapter whose parse_stream_line returns successive items."""
    adapter = MagicMock()
    adapter.parse_stream_line.side_effect = parse_results
    return adapter


async def _run_with_timeout(
    reader: LaneStreamReader,
    on_event,
    *,
    stop_event: asyncio.Event,
    timeout: float = 3.0,
) -> None:
    """Run reader.run() under a timeout; signal stop_event before timeout."""
    try:
        await asyncio.wait_for(
            reader.run(on_event, stop_event=stop_event),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        stop_event.set()


# ---------------------------------------------------------------------------
# 1. run_delivers_parsed_events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_delivers_parsed_events(tmp_path: Path):
    """Lines that parse to Event objects are delivered via on_event callback."""
    log = tmp_path / "C.stream.log"
    events_in = [
        Event(kind="text", text="hello"),
        Event(kind="text", text="world"),
    ]
    adapter = _mock_adapter([events_in[0], events_in[1]])

    received: list[Event] = []

    async def collect(ev: Event) -> None:
        received.append(ev)

    log.write_text("line one\nline two\n")

    stop = asyncio.Event()
    reader = LaneStreamReader(log, adapter, lane="C")

    async def _run():
        await reader.run(collect, stop_event=stop)

    task = asyncio.create_task(_run())
    # Allow the reader to drain the file, then stop it.
    await asyncio.sleep(EOF_POLL_S * 5)
    stop.set()
    await task

    assert received == events_in


# ---------------------------------------------------------------------------
# 2. run_skips_none_parse_results
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_skips_none_parse_results(tmp_path: Path):
    """Lines where adapter returns None are silently skipped."""
    log = tmp_path / "C.stream.log"
    adapter = _mock_adapter([None, Event(kind="text", text="kept"), None])

    received: list[Event] = []

    async def collect(ev: Event) -> None:
        received.append(ev)

    log.write_text("skip\nkeep\nskip2\n")

    stop = asyncio.Event()
    reader = LaneStreamReader(log, adapter, lane="C")
    task = asyncio.create_task(reader.run(collect, stop_event=stop))
    await asyncio.sleep(EOF_POLL_S * 5)
    stop.set()
    await task

    assert len(received) == 1
    assert received[0].text == "kept"


# ---------------------------------------------------------------------------
# 3. run_waits_for_file_existence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_waits_for_file_existence(tmp_path: Path):
    """Reader waits for the log file to appear, then processes it."""
    log = tmp_path / "C.stream.log"
    event_val = Event(kind="text", text="late")
    adapter = _mock_adapter([event_val])

    received: list[Event] = []

    async def collect(ev: Event) -> None:
        received.append(ev)

    stop = asyncio.Event()
    reader = LaneStreamReader(log, adapter, lane="C")
    task = asyncio.create_task(reader.run(collect, stop_event=stop))

    # Write the file after a short delay (within the 5s timeout).
    await asyncio.sleep(FILE_EXIST_POLL_S * 2)
    log.write_text("late line\n")

    await asyncio.sleep(EOF_POLL_S * 5)
    stop.set()
    await task

    assert len(received) == 1
    assert received[0].text == "late"


# ---------------------------------------------------------------------------
# 4. run_handles_file_rotation (shrinkage triggers re-seek)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_handles_file_rotation(tmp_path: Path):
    """When the file shrinks (rotation), reader re-seeks to offset 0."""
    log = tmp_path / "C.stream.log"
    before = Event(kind="text", text="before")
    after = Event(kind="text", text="after")
    adapter = _mock_adapter([before, after])

    received: list[Event] = []

    async def collect(ev: Event) -> None:
        received.append(ev)

    # Write first line, let reader consume it.
    log.write_text("before line\n")

    stop = asyncio.Event()
    reader = LaneStreamReader(log, adapter, lane="C")
    task = asyncio.create_task(reader.run(collect, stop_event=stop))

    await asyncio.sleep(EOF_POLL_S * 5)

    # Simulate rotation: overwrite with shorter content.
    log.write_text("after\n")

    await asyncio.sleep(EOF_POLL_S * 5)
    stop.set()
    await task

    assert any(e.text == "before" for e in received)
    # After rotation the reader re-seeks and picks up the new content.
    assert any(e.text == "after" for e in received)


# ---------------------------------------------------------------------------
# 5. run_exits_on_stop_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_exits_on_stop_event(tmp_path: Path):
    """Setting stop_event causes run() to exit without hanging."""
    log = tmp_path / "C.stream.log"
    log.write_text("")  # empty file — reader sits at EOF poll loop
    adapter = _mock_adapter([])

    stop = asyncio.Event()
    reader = LaneStreamReader(log, adapter, lane="C")

    task = asyncio.create_task(reader.run(lambda e: asyncio.sleep(0), stop_event=stop))
    await asyncio.sleep(EOF_POLL_S * 2)
    stop.set()

    # Should complete promptly (within 1s of stop signal).
    await asyncio.wait_for(task, timeout=1.0)


# ---------------------------------------------------------------------------
# 6. run_returns_gracefully_when_file_never_appears
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_returns_gracefully_when_file_never_appears(tmp_path: Path, monkeypatch):
    """If the file never appears, run() logs a warning and exits (no hang)."""
    import megalodon_ui.stream_reader as sr_mod

    monkeypatch.setattr(sr_mod, "FILE_EXIST_TIMEOUT_S", 0.15)
    monkeypatch.setattr(sr_mod, "FILE_EXIST_POLL_S", 0.05)

    log = tmp_path / "never.stream.log"
    adapter = MagicMock()
    stop = asyncio.Event()
    reader = LaneStreamReader(log, adapter, lane="X")

    # Should return within timeout + small margin, no exception.
    await asyncio.wait_for(
        reader.run(lambda e: asyncio.sleep(0), stop_event=stop),
        timeout=1.0,
    )
    adapter.parse_stream_line.assert_not_called()
