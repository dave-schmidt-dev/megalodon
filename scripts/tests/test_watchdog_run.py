"""P3.7 — coverage for megalodon_ui.watchdog.daemon.run().

run() is the standalone-CLI driver loop: it installs SIGTERM/SIGINT handlers,
emits WR-3 warnings, then loops `check_lanes_once` on a `poll_seconds` timer
until a stop signal flips its `stop` flag. The asyncio lifespan calls
`check_lanes_once` directly, so run() itself was uncovered.

Strategy: drive one real poll cycle, then break the loop deterministically by
having the patched per-tick sleep raise SIGTERM into the process — the run()
SIGINT/SIGTERM handler sets stop=True and the loop exits. We assert the
observable effect of that one cycle (check_lanes_once was invoked with the
loaded lane list) and a clean return code.
"""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from megalodon_ui.watchdog import daemon


@pytest.fixture
def _restore_signal_handlers():
    """run() overwrites SIGTERM/SIGINT; restore the originals after the test."""
    orig_term = signal.getsignal(signal.SIGTERM)
    orig_int = signal.getsignal(signal.SIGINT)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, orig_term)
        signal.signal(signal.SIGINT, orig_int)


def _minimal_mission(tmp_path: Path) -> Path:
    """A mission dir with no .mission-config.yaml (default v9.0 lane shape)."""
    (tmp_path / "STATUS.md").write_text(
        "| Lane | Agent | State | Last UTC | Notes |\n|---|---|---|---|---|\n"
    )
    return tmp_path


def test_run_polls_once_then_stops_on_sigterm(
    tmp_path, monkeypatch, _restore_signal_handlers
):
    mission = _minimal_mission(tmp_path)

    calls = []

    def fake_check(mission_dir, alerts, cadence_seconds, lanes=None):
        # Record what run() passed so we can prove a REAL cycle ran with the
        # lane list run() loaded up front (not None / not re-loaded per call).
        calls.append(
            {
                "mission_dir": mission_dir,
                "cadence": cadence_seconds,
                "lane_count": len(lanes) if lanes is not None else None,
            }
        )

    monkeypatch.setattr(daemon, "check_lanes_once", fake_check)

    # The first per-tick sleep raises SIGTERM into our own process; run()'s
    # handler flips stop=True so the inner sleep loop breaks and the outer
    # `while not stop` exits after exactly one check_lanes_once cycle.
    sleep_calls = {"n": 0}

    def fake_sleep(_secs):
        sleep_calls["n"] += 1
        os.kill(os.getpid(), signal.SIGTERM)

    monkeypatch.setattr(daemon.time, "sleep", fake_sleep)

    rc = daemon.run(mission, poll_seconds=1, cadence_seconds=120)

    assert rc == 0
    # Exactly one poll cycle's observable effect.
    assert len(calls) == 1, f"expected one poll cycle, got {len(calls)}: {calls}"
    assert calls[0]["mission_dir"] == mission
    assert calls[0]["cadence"] == 120
    # run() pre-loads lanes once and passes the list in (default shape ≥ 1 lane).
    assert calls[0]["lane_count"] and calls[0]["lane_count"] >= 1
    # The sleep loop ran at least once before the signal landed.
    assert sleep_calls["n"] >= 1


def test_run_swallows_poll_exception_and_still_stops(
    tmp_path, monkeypatch, _restore_signal_handlers, capsys
):
    """A check_lanes_once exception is caught (logged to stderr), and the loop
    still terminates on the stop signal — the daemon does not crash on a single
    bad poll."""
    mission = _minimal_mission(tmp_path)

    def boom_check(mission_dir, alerts, cadence_seconds, lanes=None):
        raise RuntimeError("poll exploded")

    monkeypatch.setattr(daemon, "check_lanes_once", boom_check)

    def fake_sleep(_secs):
        os.kill(os.getpid(), signal.SIGTERM)

    monkeypatch.setattr(daemon.time, "sleep", fake_sleep)

    rc = daemon.run(mission, poll_seconds=1, cadence_seconds=300)

    assert rc == 0
    err = capsys.readouterr().err
    assert "watchdog poll error: poll exploded" in err
    assert "watchdog stopping" in err
