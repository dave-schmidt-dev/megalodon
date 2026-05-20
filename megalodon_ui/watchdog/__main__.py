"""V9 A1 watchdog CLI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .daemon import run


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="megalodon_ui.watchdog")
    p.add_argument("--mission-dir", required=True, type=Path)
    p.add_argument("--poll-seconds", type=int, default=60)
    p.add_argument("--cadence-seconds", type=int, default=300)
    p.add_argument("--debug", action="store_true")
    args = p.parse_args(argv)
    return run(
        args.mission_dir.resolve(),
        poll_seconds=args.poll_seconds,
        cadence_seconds=args.cadence_seconds,
        debug=args.debug,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
