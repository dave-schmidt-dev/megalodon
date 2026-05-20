"""V9 M1.6 — legacy entry point preserved as a thin shim.

All logic lives in `megalodon_ui.server.make_app()` per V9 M1.6 (backend
unification). This shim preserves the operator's `python ui/server.py`
habit; it is functionally equivalent to `python -m megalodon_ui`.

The pre-M1.6 1,482-line implementation is removed; the canonical surface
is the FastAPI factory at `megalodon_ui.server.make_app()`. See
`docs/superpowers/specs/2026-05-16-v9-m1-queue-trio-design.md` §6.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Ensure the repo root is on sys.path so `megalodon_ui` resolves regardless
# of the operator's CWD.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="ui/server.py [legacy shim → megalodon_ui.make_app]",
    )
    parser.add_argument(
        "--mission-dir",
        default=os.environ.get("MEGALODON_MISSION_DIR"),
        help="Path to mission directory (default $MEGALODON_MISSION_DIR or parent of ui/).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(
            os.environ.get(
                "MEGALODON_PORT", os.environ.get("MEGALODON_UI_PORT", "8080")
            )
        ),
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("MEGALODON_HOST", "127.0.0.1"),
    )
    args = parser.parse_args()

    mission_dir = Path(
        args.mission_dir or Path(__file__).resolve().parent.parent
    ).resolve()

    # Lazy-import FastAPI / uvicorn so `--help` is fast.
    import uvicorn
    from megalodon_ui import make_app

    app = make_app(mission_dir=mission_dir, port=args.port)
    print(
        f"[ui/server.py] V9 M1.6 shim → make_app(mission={mission_dir}, port={args.port})",
        file=sys.stderr,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
