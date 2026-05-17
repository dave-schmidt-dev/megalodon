"""V9 A2 — generate per-lane launch files from launch.md template.

Reads the canonical ``launch.md`` and prepends a lane-bound header (LANE,
CADENCE_SECONDS, TICK_OFFSET_SECONDS, MODEL_HINT) plus a Step 0 stagger
``sleep <offset>`` instruction. Emits 6 files into ``--out-dir`` (default
``.``). Re-running is idempotent: same inputs produce identical output.
"""
from __future__ import annotations

import sys
from pathlib import Path

DEFAULT_LANES = ["AUDIT", "ARCHITECT", "BACKEND", "FRONTEND", "TEST", "META"]
# Claude CLI accepts "sonnet"/"opus"/"haiku" aliases (latest of each family) or
# canonical IDs like "claude-sonnet-4-6". The version-numbered shorthand we
# used previously ("opus-4.7", "sonnet-4.6") is NOT a valid --model arg and
# breaks `claude --model <X>` at the shell. Use aliases.
DEFAULT_MODEL = "opus"

# Sonnet for observer lanes (cheaper, sufficient for synthesis/audit work).
LANE_MODELS = {
    "AUDIT": "sonnet",
    "META": "sonnet",
}
# Faster cadence for builder lanes; slower for META observer.
LANE_CADENCE = {
    "AUDIT": 300,
    "ARCHITECT": 300,
    "BACKEND": 180,
    "FRONTEND": 180,
    "TEST": 180,
    "META": 420,
}


HEADER = """# launch-{lane}.md — pre-bound launch for {lane} lane

> Generated from launch.md by scripts/gen_lane_launches.py — DO NOT EDIT.
> Regenerate with: `python3 scripts/gen_lane_launches.py`

## Pre-binding

- LANE: {lane}
- CADENCE_SECONDS: {cadence}
- TICK_OFFSET_SECONDS: {offset}
- MODEL_HINT: {model}

## Step 0 — Stagger wait (A6)

Before /loop arm, sleep for TICK_OFFSET_SECONDS to spread tick load across lanes.

```bash
sleep {offset}
```

---

"""


def generate_one(lane: str, lane_index: int, repo_root: Path) -> str:
    """Build the lane-bound launch text for a single lane."""
    launch_md = (repo_root / "launch.md").read_text(encoding="utf-8")
    return (
        HEADER.format(
            lane=lane,
            cadence=LANE_CADENCE.get(lane, 300),
            offset=lane_index * 45,
            model=LANE_MODELS.get(lane, DEFAULT_MODEL),
        )
        + launch_md
    )


def generate_all(out_dir: Path) -> None:
    """Generate all 6 lane launch files into ``out_dir``."""
    repo_root = Path(__file__).resolve().parents[1]
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, lane in enumerate(DEFAULT_LANES):
        text = generate_one(lane, i, repo_root)
        (out_dir / f"launch-{lane}.md").write_text(text, encoding="utf-8")


def main(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(
        description="Generate per-lane launch files from launch.md template.",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=Path("."),
        help="Output directory (default: current working dir).",
    )
    args = p.parse_args(argv)
    generate_all(args.out_dir)
    print(f"Generated 6 lane launch files in {args.out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
