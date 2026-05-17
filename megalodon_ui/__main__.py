"""`python -m megalodon_ui` entrypoint.

Per TEST P4-E-to-C SIGNAL #2 (still-standing post-WITHDRAW of SSE signal #1).
Delegates to `make_app` + `uvicorn.run` so the canonical launch matches
MISSION.md:20 exit-criterion #2: `python ui/server.py --mission-dir ... --port 8765`.
Equivalent invocation: `python -m megalodon_ui --mission-dir ... --port 8765`.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m megalodon_ui",
        description="Megalodon orchestrator-console FastAPI server.",
    )
    parser.add_argument(
        "--mission-dir",
        default=os.environ.get("MEGALODON_MISSION_DIR"),
        help="Path to mission directory (default: $MEGALODON_MISSION_DIR or repo root).",
    )
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("MEGALODON_PORT", "8080"))
    )
    parser.add_argument(
        "--host", default=os.environ.get("MEGALODON_HOST", "127.0.0.1")
    )
    args = parser.parse_args()

    mission_dir = Path(
        args.mission_dir
        or Path(__file__).resolve().parent.parent
    ).resolve()

    # Lazy-import FastAPI / uvicorn so `python -m megalodon_ui --help` is fast.
    import uvicorn
    from .server import make_app

    app = make_app(mission_dir=mission_dir, port=args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
