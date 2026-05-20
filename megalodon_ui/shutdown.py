"""Standalone shutdown CLI — kills the per-mission tmux server and unlinks
bootstrap artifacts.

Plan §6.7 + Task 7.2. Operator invocation:

    python -m megalodon_ui.shutdown --mission-dir <path-to-mission>

Mirrors the destructive behavior of ``DELETE /api/v1/fleet`` (P7.1) for use
when the dashboard server is unreachable or already gone. Idempotent: rerun
on a clean mission exits 0.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from . import tmux


_ARTIFACTS = ("ui.token", "tmux.sock", "dashboard.url", "approval-rules.json")


async def _run(mission_dir: Path) -> int:
    fleet = mission_dir / ".fleet"
    socket = fleet / "tmux.sock"
    try:
        await tmux.kill_server(socket)
    except FileNotFoundError:
        pass
    for name in _ARTIFACTS:
        (fleet / name).unlink(missing_ok=True)
    # Clean daily-rotated inject log files (glob pattern)
    for p in fleet.glob("inject-log-*.jsonl"):
        p.unlink(missing_ok=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="megalodon_ui.shutdown",
        description="Kill the per-mission tmux server and remove bootstrap files.",
    )
    parser.add_argument(
        "--mission-dir",
        required=True,
        type=Path,
        help="Absolute path to the mission directory.",
    )
    args = parser.parse_args(argv)
    mission_dir: Path = args.mission_dir
    if not mission_dir.exists() or not mission_dir.is_dir():
        print(
            f"megalodon_ui.shutdown: --mission-dir is not a directory: {mission_dir}",
            file=sys.stderr,
        )
        return 2
    return asyncio.run(_run(mission_dir))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
