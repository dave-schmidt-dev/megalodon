"""V9 A1 — watchdog main loop."""
from __future__ import annotations

import signal
import sys
import time
from pathlib import Path

from .alerts import AlertManager
from .detectors import detect_jsonl_stale, detect_process, detect_status_stale

DEFAULT_LANES = ("AUDIT", "ARCHITECT", "BACKEND", "FRONTEND", "TEST", "META")
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


def poll_once(
    mission_dir: Path,
    alerts: AlertManager,
    cadence_seconds: int = 300,
) -> None:
    """One pass over all lanes."""
    status_md = mission_dir / "STATUS.md"
    status_threshold = max(900, cadence_seconds * 3)
    jsonl_threshold = 300

    for lane in DEFAULT_LANES:
        pid = _read_pid(lane)

        # S1
        if pid is not None:
            if detect_process(pid) == "crashed":
                alerts.alert(lane, "CRASHED", evidence=[f"pid {pid} not alive"])
                continue

        # S2
        s2 = detect_status_stale(status_md, lane, status_threshold)
        if s2 == "stale":
            alerts.alert(
                lane,
                "STATUS-STALE",
                evidence=[f"STATUS row > {status_threshold}s old"],
            )
            continue

        # S3
        if pid is not None:
            jsonl = _find_jsonl(pid)
            if jsonl is not None:
                s3 = detect_jsonl_stale(jsonl, jsonl_threshold)
                if s3 == "hung":
                    alerts.alert(
                        lane,
                        "HUNG",
                        evidence=[f"JSONL {jsonl.name} > {jsonl_threshold}s old"],
                    )
                    continue

        # Recovered
        alerts.recover(lane)


def run(
    mission_dir: Path,
    poll_seconds: int = 60,
    cadence_seconds: int = 300,
    debug: bool = False,
) -> int:
    alerts = AlertManager(mission_dir)
    stop = False

    def _stop(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    print(f"watchdog started for {mission_dir}", file=sys.stderr)
    while not stop:
        try:
            poll_once(mission_dir, alerts, cadence_seconds)
        except Exception as e:
            print(f"watchdog poll error: {e}", file=sys.stderr)
        for _ in range(poll_seconds * 10):
            if stop:
                break
            time.sleep(0.1)

    print("watchdog stopping", file=sys.stderr)
    return 0
