"""V9 A9 — merge worker ledger entries into per-mission fleet-perf.json.

Reads every ``<mission_dir>/.fleet-ledger/<lane>-tick-<N>-<utc>.json``
written by ``scripts._fleet_tick.record_tick`` and emits a per-lane
summary suitable for feeding A3 fleet-matrix decisions on the next mission.

Operator runs post-mission:
    python3 scripts/aggregate_fleet_perf.py --mission-dir <mission>
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def aggregate(mission_dir: Path, output: Path) -> dict:
    """Aggregate ledger ticks under mission_dir, write summary to output.

    Returns the per-lane summary dict (matches the ``lanes`` payload).
    Missing ledger dir is treated as an empty mission (returns ``{}``).
    """
    mission_dir = Path(mission_dir)
    output = Path(output)
    ledger_dir = mission_dir / ".fleet-ledger"
    by_lane: dict[str, list] = defaultdict(list)
    if ledger_dir.is_dir():
        for path in sorted(ledger_dir.glob("*-tick-*-*.json")):
            try:
                entry = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(entry, dict):
                continue
            by_lane[entry.get("lane", "UNKNOWN")].append(entry)

    summary: dict[str, dict] = {}
    for lane, ticks in by_lane.items():
        summary[lane] = {
            "tick_count": len(ticks),
            "tasks_completed": sum(len(t.get("tasks_completed", []) or []) for t in ticks),
            "cas_retries": sum(t.get("cas_retries", 0) or 0 for t in ticks),
            "repair_injections_received": sum(
                len(t.get("repair_injections_received", []) or []) for t in ticks
            ),
            "total_walltime_seconds": sum(t.get("walltime_seconds", 0) or 0 for t in ticks),
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"lanes": summary}, indent=2))
    return summary


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    p.add_argument("--mission-dir", required=True, type=Path)
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args(argv)
    out = args.out or args.mission_dir / "fleet-perf.json"
    summary = aggregate(args.mission_dir.resolve(), out)
    print(f"wrote {out}", file=sys.stderr)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
