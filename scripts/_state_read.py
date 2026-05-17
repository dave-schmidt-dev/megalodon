"""Read-only mission state aggregation for scripts/poll.py.

All functions are pure: no side effects, no fcntl. Reads stay direct per
V9-ROADMAP M1 Option A.
"""

from __future__ import annotations

import json as _json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._backends.direct_fcntl import STATUS_ROW_RE
from ._validation import LANE_LONG_TO_SHORT

_PHASE_FLIP_RE = re.compile(
    r"^(?P<utc>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) "
    r"(?P<from>[A-Z0-9_-]+)->(?P<to>[A-Z0-9_-]+) by (?P<agent>\S+)"
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc(s: str) -> datetime | None:
    """Parse UTC stamp accepting both second (22:08:00Z) and minute (22:08Z) precision.

    Production STATUS.md rows sometimes use minute precision when edited by humans;
    fixture and Python-generated stamps use second precision. Accept both.
    """
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%MZ"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def read_lanes(mission_dir: Path) -> list[dict[str, Any]]:
    text = (mission_dir / "STATUS.md").read_text(encoding="utf-8")
    now = _utc_now()
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        m = STATUS_ROW_RE.match(line)
        if not m:
            continue
        last_utc_str = m["last_utc"].strip()
        last_utc_dt = _parse_utc(last_utc_str)
        stale = int((now - last_utc_dt).total_seconds()) if last_utc_dt else None
        lane = m["lane"].strip()
        rows.append({
            "lane": lane,
            "lane_short": LANE_LONG_TO_SHORT[lane],
            "agent": m["agent"].strip(),
            "state": m["state"].strip(),
            "last_utc": last_utc_str,
            "stale_seconds": stale,
            "notes": m["notes"].strip(),
        })
    return rows


def read_phase(mission_dir: Path) -> tuple[str, str | None]:
    """Return (current_phase, lock_owner_or_None).

    Current phase derives from the last PHASE-X->PHASE-Y line in .mission-events.
    Lock owner derives from .phase-flip-locks/*/owner.txt if any lock dir exists.
    """
    events_path = mission_dir / ".mission-events"
    current_phase = "PHASE-PLAN"
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            m = _PHASE_FLIP_RE.match(line)
            if m:
                current_phase = m["to"]
    lock_owner = None
    lock_dir = mission_dir / ".phase-flip-locks"
    if lock_dir.is_dir():
        for child in lock_dir.iterdir():
            if child.is_dir():
                owner_file = child / "owner.txt"
                if owner_file.exists():
                    lock_owner = owner_file.read_text().strip()
                    break
    return current_phase, lock_owner


def read_claims(mission_dir: Path) -> dict[str, list[dict[str, Any]]]:
    claims_dir = mission_dir / "claims"
    open_: list[dict[str, Any]] = []
    done: list[dict[str, Any]] = []
    if not claims_dir.is_dir():
        return {"open": open_, "done": done}
    for child in sorted(claims_dir.iterdir()):
        if not child.is_dir():
            continue
        owner_file = child / "owner.txt"
        owner = owner_file.read_text().strip() if owner_file.exists() else None
        done_marker = child / "done"
        entry = {
            "task_id": child.name,
            "owner": owner,
            "has_done_marker": done_marker.exists(),
        }
        if done_marker.exists():
            entry["done_marker_mtime_utc"] = datetime.fromtimestamp(
                done_marker.stat().st_mtime, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            done.append(entry)
        else:
            entry["created_utc"] = datetime.fromtimestamp(
                child.stat().st_mtime, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            open_.append(entry)
    return {"open": open_, "done": done}


def read_events_tail(mission_dir: Path, n: int) -> list[str]:
    path = mission_dir / ".mission-events"
    if not path.exists():
        return []
    lines = [
        ln for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    return lines[-n:]


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> dict[str, str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out


def read_findings_recent(
    mission_dir: Path, n: int, include_body: bool
) -> list[dict[str, Any]]:
    findings_dir = mission_dir / "findings"
    if not findings_dir.is_dir():
        return []
    files = [p for p in findings_dir.iterdir() if p.is_file() and p.suffix == ".md"]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[dict[str, Any]] = []
    for path in files[:n]:
        text = path.read_text(encoding="utf-8")
        fm = _parse_frontmatter(text)
        out.append({
            "path": str(path.relative_to(mission_dir)),
            "mtime_utc": datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "lane": fm.get("lane"),
            "task_id": fm.get("task-id"),
            "severity": fm.get("severity"),
            "body": text if include_body else None,
        })
    return out


def read_partial_journals(
    mission_dir: Path, max_age_seconds: int = 86400
) -> list[dict[str, Any]]:
    jdir = mission_dir / ".scripts-journal"
    if not jdir.is_dir():
        return []
    now = _utc_now()
    out: list[dict[str, Any]] = []
    for path in sorted(jdir.glob("*.json")):
        try:
            data = _json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("status") != "PARTIAL":
            continue
        last = _parse_utc(data.get("last_updated_utc", ""))
        if last is None:
            continue
        age = int((now - last).total_seconds())
        if age > max_age_seconds:
            continue
        completed = [s["step"] for s in data.get("steps", []) if s.get("ok")]
        failed = next(
            (s["step"] for s in data.get("steps", []) if not s.get("ok")),
            None,
        )
        out.append({
            "request_id": data["request_id"],
            "started_utc": data.get("started_utc"),
            "last_updated_utc": data["last_updated_utc"],
            "task_id": data["task_id"],
            "lane": data["lane"],
            "agent": data["agent"],
            "completed_steps": completed,
            "failed_step": failed,
            "error": next(
                (s.get("error") for s in data.get("steps", []) if not s.get("ok")),
                None,
            ),
            "age_seconds": age,
            "resume_hint": (
                f"python3 scripts/atomic_close.py --resume {data['request_id']}"
            ),
        })
    out.sort(key=lambda e: e["last_updated_utc"], reverse=True)
    return out
