"""V9 A1 — watchdog main loop.

WR-3 Known Limitation (v9.1)
-----------------------------
The S3 JSONL-stale detector relies on ``~/.claude/projects/**/*.jsonl`` which
only Claude Code writes.  Non-Claude harnesses (codex, gemini, copilot, cursor,
vibe) do not write to that path, so S3 is unconditionally skipped for lanes
whose ``harness.cli != "claude"``.  A startup warning is emitted for each such
lane.  S1 (process-alive) and S2 (STATUS-row stale) continue to apply to ALL
lanes regardless of harness.  A SIGHUP-driven config reload is deferred; see
CV-8.
"""
from __future__ import annotations

import signal
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from megalodon_ui._v92_constants import STREAM_LOG_WARN_BYTES

from .alerts import AlertManager
from .detectors import (
    detect_jsonl_stale,
    detect_process,
    detect_status_stale,
    detect_stream_log_size,
)

if TYPE_CHECKING:
    from megalodon_ui.mission_config import LaneConfig, MissionConfig

PID_DIR = Path.home() / ".megalodon-pids"


def _read_pid(lane: str) -> int | None:
    f = PID_DIR / f"{lane}.pid"
    if not f.exists():
        return None
    try:
        return int(f.read_text().strip())
    except (OSError, ValueError):
        return None


def _find_jsonl(pid: int) -> Path | None:
    """Find Claude Code session JSONL for a pid. Best-effort."""
    proj_root = Path.home() / ".claude" / "projects"
    if not proj_root.is_dir():
        return None
    candidates = list(proj_root.glob("**/*.jsonl"))
    if not candidates:
        return None
    # Return most-recently-modified JSONL (best-effort association).
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _load_lanes(mission_dir: Path) -> list[LaneConfig]:
    """Load lane list from mission config, falling back to default v9.0 shape."""
    from megalodon_ui.mission_config import load_mission_config
    config = load_mission_config(mission_dir)
    return list(config.lanes)


def _emit_wr3_warnings(lanes: list[LaneConfig]) -> None:
    """WR-3: emit startup warnings for non-Claude lanes where S3 is skipped."""
    for lane in lanes:
        if lane.harness.cli != "claude":
            print(
                f"S3 detector skipped for lane {lane.name}"
                f" (cli={lane.harness.cli}); WR-3 known limitation in v9.1",
                file=sys.stderr,
            )


def poll_once(
    mission_dir: Path,
    alerts: AlertManager,
    cadence_seconds: int = 300,
    lanes: list[LaneConfig] | None = None,
) -> None:
    """One pass over all lanes.

    Parameters
    ----------
    mission_dir:
        Root of the mission directory (used for STATUS.md lookup).
    alerts:
        Alert manager instance.
    cadence_seconds:
        Polling cadence; used to compute the S2 staleness threshold.
    lanes:
        Pre-loaded list of LaneConfig objects.  When *None* (default) the
        config is loaded from *mission_dir* at each call — callers that want
        to avoid repeated disk I/O should load once and pass the list in.
    """
    if lanes is None:
        lanes = _load_lanes(mission_dir)

    status_md = mission_dir / "STATUS.md"
    status_threshold = max(900, cadence_seconds * 3)
    jsonl_threshold = 300

    for lane in lanes:
        name = lane.name
        pid = _read_pid(name)

        # S1 — process alive check (all lanes)
        if pid is not None:
            if detect_process(pid) == "crashed":
                alerts.alert(name, "CRASHED", evidence=[f"pid {pid} not alive"])
                continue

        # S2 — STATUS row freshness (all lanes)
        s2 = detect_status_stale(status_md, name, status_threshold)
        if s2 == "stale":
            alerts.alert(
                name,
                "STATUS-STALE",
                evidence=[f"STATUS row > {status_threshold}s old"],
            )
            continue

        # S3 — JSONL log freshness (Claude lanes only; WR-3)
        if lane.harness.cli == "claude" and pid is not None:
            jsonl = _find_jsonl(pid)
            if jsonl is not None:
                s3 = detect_jsonl_stale(jsonl, jsonl_threshold)
                if s3 == "hung":
                    alerts.alert(
                        name,
                        "HUNG",
                        evidence=[f"JSONL {jsonl.name} > {jsonl_threshold}s old"],
                    )
                    continue

        # S4 — stream-log size (P7.3)
        stream_log = mission_dir / ".fleet" / f"{lane.short}.stream.log"
        s4 = detect_stream_log_size(stream_log, STREAM_LOG_WARN_BYTES)
        if s4 == "warn":
            alerts.alert(
                name,
                "STREAM-LOG-SIZE",
                evidence=[
                    f"{stream_log.name} = {stream_log.stat().st_size} bytes"
                    f" >= {STREAM_LOG_WARN_BYTES}"
                ],
            )
            continue

        # Recovered
        alerts.recover(name)


def run(
    mission_dir: Path,
    poll_seconds: int = 60,
    cadence_seconds: int = 300,
    debug: bool = False,
) -> int:
    alerts = AlertManager(mission_dir)
    lanes = _load_lanes(mission_dir)
    stop = False

    def _stop(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    print(f"watchdog started for {mission_dir}", file=sys.stderr)
    _emit_wr3_warnings(lanes)

    while not stop:
        try:
            poll_once(mission_dir, alerts, cadence_seconds, lanes=lanes)
        except Exception as e:
            print(f"watchdog poll error: {e}", file=sys.stderr)
        for _ in range(poll_seconds * 10):
            if stop:
                break
            time.sleep(0.1)

    print("watchdog stopping", file=sys.stderr)
    return 0
