#!/usr/bin/env python3
"""poll — canonical mission-state read for v9 workers.

Usage:
    python3 scripts/poll.py [--brief | --full]
        [--mission-dir <PATH>] [--events-tail N] [--findings-recent N] [--debug]

Spec: docs/superpowers/specs/2026-05-16-v9-m3-helper-scripts-design.md §5.2
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._logging import get_logger
from scripts._state_read import (
    read_claims,
    read_events_tail,
    read_findings_recent,
    read_lanes,
    read_partial_journals,
    read_phase,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_args(argv):
    p = argparse.ArgumentParser(prog="poll", description="v9 mission-state read")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--brief", action="store_true")
    mode.add_argument("--full", action="store_true")
    p.add_argument("--mission-dir", default=None)
    p.add_argument("--events-tail", type=int, default=10)
    p.add_argument("--findings-recent", type=int, default=10)
    p.add_argument("--debug", action="store_true")
    return p.parse_args(argv)


def _resolve_mission(arg):
    candidate = Path(arg) if arg else Path.cwd()
    if not (candidate / "STATUS.md").exists() or not (candidate / "TASKS.md").exists():
        raise FileNotFoundError(f"mission dir invalid: {candidate}")
    return candidate.resolve()


def main(argv=None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    # Configure logging for side effects (file handler); poll writes results to
    # stdout, so the returned logger isn't bound.
    get_logger("poll", debug=args.debug)
    try:
        mission = _resolve_mission(args.mission_dir)
    except FileNotFoundError as e:
        sys.stderr.write(f"{e}\n")
        return 4

    phase, lock_owner = read_phase(mission)
    payload = {
        "utc": _utc_now(),
        "mission_dir": str(mission),
        "phase": phase,
        "phase_lock_owner": lock_owner,
        "lanes": read_lanes(mission),
        "claims": read_claims(mission),
    }
    if not args.brief:
        payload["events_tail"] = read_events_tail(mission, args.events_tail)
        include_body = bool(args.full)
        payload["findings_recent"] = read_findings_recent(
            mission,
            args.findings_recent,
            include_body=include_body,
        )
        payload["partial_journals"] = read_partial_journals(mission)

    sys.stdout.write(json.dumps(payload, indent=2 if args.full else None) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
