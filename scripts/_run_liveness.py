"""Liveness grammar for Megalodon runs (v9.4 run lifecycle).

`.mission-events` lines start with a structured first token. A run is LIVE
until a terminal token is written as the first token of the last non-blank
line. Used by new_run.sh (refuse to scaffold over a live run) and
archive_run.sh (refuse to archive a live run without --force).
"""

from __future__ import annotations

import sys
from pathlib import Path

TERMINAL_TOKENS = {"COMPLETE", "ABORTED", "DEGRADED-CLOSE"}


def last_token(events_path: Path) -> str | None:
    """First whitespace-delimited token of the last non-blank line, or None."""
    if not events_path.exists():
        return None
    last = None
    for line in events_path.read_text().splitlines():
        if line.strip():
            last = line.strip()
    if last is None:
        return None
    return last.split()[0]


def is_live(events_path: Path) -> bool:
    """True iff the run has events and the last token is not terminal."""
    tok = last_token(events_path)
    if tok is None:
        return False
    return tok not in TERMINAL_TOKENS


def main(argv: list[str]) -> int:
    """CLI: exit 0 if live, 1 if not-live/missing. Path is argv[1]."""
    if len(argv) != 2:
        print("usage: _run_liveness.py <path-to-.mission-events>", file=sys.stderr)
        return 2
    return 0 if is_live(Path(argv[1])) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
