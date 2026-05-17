"""V9 A1 — watchdog detectors S1, S2, S3."""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

_STATUS_ROW_RE = re.compile(
    r"^\|\s*(?P<lane>[A-Z][A-Z\- ]*?)\s*\|\s*"
    r"(?P<agent>[^|]+?)\s*\|\s*"
    r"(?P<state>[^|]+?)\s*\|\s*"
    r"(?P<last_utc>[^|]+?)\s*\|",
    re.MULTILINE,
)


def detect_process(pid: int) -> str:
    """S1 — return 'ok' if pid alive else 'crashed'."""
    try:
        os.kill(pid, 0)
        return "ok"
    except (OSError, ProcessLookupError):
        return "crashed"


def detect_status_stale(status_md: Path, lane: str, threshold_seconds: int) -> str:
    """S2 — return 'stale' if lane's last_utc older than threshold; else 'ok'.

    Returns 'unknown' if lane row not found.
    """
    if not status_md.exists():
        return "unknown"
    text = status_md.read_text(encoding="utf-8")
    for m in _STATUS_ROW_RE.finditer(text):
        if m["lane"].strip() == lane:
            last_utc = m["last_utc"].strip()
            try:
                dt = datetime.strptime(last_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                try:
                    dt = datetime.strptime(last_utc, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
                except ValueError:
                    return "unknown"
            age = (datetime.now(timezone.utc) - dt).total_seconds()
            return "stale" if age > threshold_seconds else "ok"
    return "unknown"


def detect_jsonl_stale(log_path: Path, threshold_seconds: int) -> str:
    """S3 — return 'hung' if mtime older than threshold; 'skip' if missing; else 'ok'."""
    if not log_path.exists():
        return "skip"
    age = time.time() - log_path.stat().st_mtime
    return "hung" if age > threshold_seconds else "ok"
