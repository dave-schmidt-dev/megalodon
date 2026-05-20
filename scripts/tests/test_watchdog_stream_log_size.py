"""P7.3 — watchdog stream-log size warn detector.

Plan §7 Task 7.3: ``detect_stream_log_size(stream_log, threshold)`` returns:

* ``"warn"`` when the file's size on disk is at or above the threshold,
* ``"ok"`` when the file exists but is below threshold,
* ``"skip"`` when the file does not exist (lane never wrote a byte).

Wired into ``poll_once`` for every lane using ``STREAM_LOG_WARN_BYTES`` from
``_v92_constants``. When ``"warn"`` fires, the AlertManager emits a
``STREAM-LOG-SIZE`` SIGNAL finding so the operator knows the per-lane log is
approaching the file-size we'd rather not unbounded-grow under a multi-day
mission.

Tests use ``os.truncate`` to create a sparse 600 MB file (no disk pressure)
so the detector exercises the same ``stat().st_size`` path as a real run.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from megalodon_ui._v92_constants import STREAM_LOG_WARN_BYTES
from megalodon_ui.watchdog import detectors


THRESHOLD = STREAM_LOG_WARN_BYTES  # 500 MB


def test_detect_stream_log_size_below_threshold_returns_ok(tmp_path: Path):
    log = tmp_path / "A.stream.log"
    log.write_bytes(b"hello\n")
    assert detectors.detect_stream_log_size(log, THRESHOLD) == "ok"


def test_detect_stream_log_size_missing_returns_skip(tmp_path: Path):
    """A lane that has not written any bytes yet must not trigger warn."""
    assert detectors.detect_stream_log_size(
        tmp_path / "nope.stream.log", THRESHOLD
    ) == "skip"


def test_detect_stream_log_size_at_or_above_threshold_returns_warn(tmp_path: Path):
    log = tmp_path / "big.stream.log"
    with log.open("wb") as fh:
        fh.truncate(THRESHOLD + 1)  # sparse — no physical bytes written
    assert detectors.detect_stream_log_size(log, THRESHOLD) == "warn"


def test_detect_stream_log_size_exactly_at_threshold_returns_warn(tmp_path: Path):
    """Boundary: file size == threshold must warn (>=, not >)."""
    log = tmp_path / "exact.stream.log"
    with log.open("wb") as fh:
        fh.truncate(THRESHOLD)
    assert detectors.detect_stream_log_size(log, THRESHOLD) == "warn"


def test_poll_once_fires_stream_log_size_alert(tmp_path: Path, monkeypatch):
    """poll_once integration: a warn-sized log must produce a STREAM-LOG-SIZE alert."""
    from megalodon_ui.mission_config.schema import HarnessBinding, LaneConfig
    from megalodon_ui.watchdog.daemon import poll_once

    fleet = tmp_path / ".fleet"
    fleet.mkdir()
    log_a = fleet / "A.stream.log"
    with log_a.open("wb") as fh:
        fh.truncate(THRESHOLD + 1)

    lane = LaneConfig(
        name="LANEA",
        short="A",
        role="role-a",
        harness=HarnessBinding(cli="claude", model="sonnet"),
        cadence_seconds=300,
        tick_offset_seconds=0,
    )

    # Bypass S1 (no pid file) + S2 (no STATUS.md) so only the new detector fires.
    (tmp_path / "STATUS.md").write_text("")  # parsed as empty, S2 -> unknown

    alerts = MagicMock()
    poll_once(tmp_path, alerts, cadence_seconds=300, lanes=[lane])

    # Find the STREAM-LOG-SIZE alert call.
    stream_size_calls = [
        c for c in alerts.alert.call_args_list
        if len(c.args) >= 2 and c.args[1] == "STREAM-LOG-SIZE"
    ]
    assert len(stream_size_calls) == 1, (
        f"expected exactly one STREAM-LOG-SIZE alert; got {alerts.alert.call_args_list!r}"
    )
    call = stream_size_calls[0]
    assert call.args[0] == "LANEA"
    evidence = list(call.kwargs.get("evidence", []))
    assert any(str(THRESHOLD + 1) in e for e in evidence), (
        f"evidence should include the actual file size; got {evidence!r}"
    )
