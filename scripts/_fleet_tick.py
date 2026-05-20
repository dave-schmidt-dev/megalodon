"""V9 A9 worker-side ledger emission.

Workers SHOULD call ``record_tick(mission_dir, lane=LANE, agent=AGENT, ...)``
once per /loop tick. Writes a per-tick JSON file under
``<mission_dir>/.fleet-ledger/<lane>-tick-<N>-<utc>.json``.

Append-only (idempotent skip if file already exists). Tick numbers are
monotonic per lane. See spec
``docs/superpowers/specs/2026-05-17-v9-a9-fleet-ledger-design.md``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _next_tick_number(ledger_dir: Path, lane: str) -> int:
    """Return the next per-lane monotonic tick number (max existing + 1)."""
    if not ledger_dir.is_dir():
        return 1
    existing = list(ledger_dir.glob(f"{lane}-tick-*.json"))
    if not existing:
        return 1
    nums: list[int] = []
    for p in existing:
        # filename pattern: <lane>-tick-<N>-<utc>.json
        parts = p.stem.split("-tick-")
        if len(parts) != 2:
            continue
        n_str = parts[1].split("-", 1)[0]
        try:
            nums.append(int(n_str))
        except ValueError:
            continue
    return max(nums, default=0) + 1


def record_tick(mission_dir: Path, *, lane: str, agent: str, **fields: Any) -> Path:
    """Write a tick entry. Returns its path.

    Idempotent: if a file with the same lane/tick_number/utc already exists,
    leave it alone and return the existing path (first write wins).
    """
    mission_dir = Path(mission_dir)
    ledger_dir = mission_dir / ".fleet-ledger"
    n = fields.pop("tick_number", None) or _next_tick_number(ledger_dir, lane)
    started_utc = fields.pop("tick_started_utc", None) or _utc()
    entry: dict[str, Any] = {
        "lane": lane,
        "agent": agent,
        "tick_number": n,
        "tick_started_utc": started_utc,
        **fields,
    }
    filename = f"{lane}-tick-{n}-{started_utc.replace(':', '-')}.json"
    path = ledger_dir / filename
    if path.exists():
        return path  # Idempotent skip — first write wins
    _atomic_write(path, json.dumps(entry, indent=2))
    return path
