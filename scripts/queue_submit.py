#!/usr/bin/env python3
"""queue_submit — path-scoped queue-intent submission for v9 workers.

Thin wrapper over megalodon_ui.queue.queue_client.main so agents can submit
queue intents via an allowlisted PATH (Bash(scripts/queue_submit.py:*)) instead
of `python -m megalodon_ui.queue.queue_client` (an unbounded `python -m`).

Usage (identical to queue_client CLI):
    scripts/queue_submit.py --mission-dir <PATH> --agent <ID> --lane <LANE> \\
        <status|claim|done|history|event|claim-dir|claim-done> [subcommand args]

Exit codes: forwarded from queue_client.main (0 ok, 2 arg error).

Spec: docs/superpowers/specs/2026-05-22-agent-tool-surface-policy-design.md
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `scripts/queue_submit.py` from project root without install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from megalodon_ui.queue.queue_client import main as _qc_main


def main(argv: list[str] | None = None) -> int:
    return _qc_main(argv if argv is not None else sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
