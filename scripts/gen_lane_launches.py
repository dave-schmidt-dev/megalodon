"""V9.1 — generate per-lane launch files, config-driven.

CLI:
    python3 scripts/gen_lane_launches.py [--mission-dir PATH] [--out-dir PATH]

Reads mission config via scripts._config_loader.load_for_scripts(mission_dir).
Emits one launch-<LANE>.md per lane in out_dir (default: mission_dir).
Writing is atomic (tmp + rename, CV-2 pattern). Re-running is idempotent.

Back-compat: generate_all(out_dir) retains the v9.0 hardcoded 6-lane shape so
existing callers and tests continue to work unchanged.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# ─── Back-compat constants (v9.0 hardcoded shape) ────────────────────────────

DEFAULT_LANES = ["AUDIT", "ARCHITECT", "BACKEND", "FRONTEND", "TEST", "META"]

DEFAULT_MODEL = "opus"

LANE_MODELS = {
    "AUDIT": "sonnet",
    "META": "sonnet",
}

LANE_CADENCE = {
    "AUDIT": 300,
    "ARCHITECT": 300,
    "BACKEND": 180,
    "FRONTEND": 180,
    "TEST": 180,
    "META": 420,
}

# ─── Templates ───────────────────────────────────────────────────────────────

# Back-compat header (v9.0 shape — no HARNESS_CLI).
_HEADER_LEGACY = """\
# launch-{lane}.md — pre-bound launch for {lane} lane

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

# Config-driven header (v9.1 shape — adds HARNESS_CLI and ROLE).
_HEADER_V91 = """\
# launch-{lane}.md — pre-bound launch for {lane} lane

> Generated from launch.md by scripts/gen_lane_launches.py — DO NOT EDIT.
> Regenerate with: `python3 scripts/gen_lane_launches.py --mission-dir <mission-dir>`

## Pre-binding

- LANE: {lane}
- ROLE: {role}
- CADENCE_SECONDS: {cadence}
- TICK_OFFSET_SECONDS: {offset}
- MODEL_HINT: {model}
- HARNESS_CLI: {harness_cli}

## Step 0 — Stagger wait (A6)

Before /loop arm, sleep for TICK_OFFSET_SECONDS to spread tick load across lanes.

```bash
sleep {offset}
```

---

"""


# ─── Back-compat API (v9.0) ──────────────────────────────────────────────────


def generate_one(lane: str, lane_index: int, repo_root: Path) -> str:
    """Build the lane-bound launch text for a single lane (legacy shape)."""
    launch_md = (repo_root / "launch.md").read_text(encoding="utf-8")
    return (
        _HEADER_LEGACY.format(
            lane=lane,
            cadence=LANE_CADENCE.get(lane, 300),
            offset=lane_index * 45,
            model=LANE_MODELS.get(lane, DEFAULT_MODEL),
        )
        + launch_md
    )


def generate_all(out_dir: Path) -> None:
    """Generate all 6 lane launch files into out_dir (legacy hardcoded shape).

    Preserved for back-compat. New code should use generate_from_config().
    """
    repo_root = Path(__file__).resolve().parents[1]
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, lane in enumerate(DEFAULT_LANES):
        text = generate_one(lane, i, repo_root)
        (out_dir / f"launch-{lane}.md").write_text(text, encoding="utf-8")


# ─── Config-driven API (v9.1) ────────────────────────────────────────────────


def _find_launch_md(mission_dir: Path) -> str:
    """Locate launch.md: check mission_dir first, then project root."""
    candidate = mission_dir / "launch.md"
    if candidate.exists():
        return candidate.read_text(encoding="utf-8")
    repo_root = Path(__file__).resolve().parents[1]
    root_candidate = repo_root / "launch.md"
    if root_candidate.exists():
        return root_candidate.read_text(encoding="utf-8")
    return ""


def _write_atomic(path: Path, text: str) -> None:
    """Write text to path atomically via a sibling tmp file + rename (CV-2)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".tmp-" + path.name + "-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def generate_from_config(config, mission_dir: Path, out_dir: Path) -> list[Path]:
    """Generate one launch-<LANE>.md per lane using MissionConfig.

    Args:
        config: MissionConfig instance (from load_for_scripts).
        mission_dir: Path used to locate launch.md template.
        out_dir: Directory to write files into.

    Returns:
        List of Paths written.
    """
    launch_body = _find_launch_md(mission_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for lane in config.lanes:
        header = _HEADER_V91.format(
            lane=lane.name,
            role=lane.role or "",
            cadence=lane.cadence_seconds,
            offset=lane.tick_offset_seconds,
            model=lane.harness.model,
            harness_cli=lane.harness.cli,
        )
        text = header + launch_body
        dest = out_dir / f"launch-{lane.name}.md"
        _write_atomic(dest, text)
        written.append(dest)
    return written


# ─── CLI ─────────────────────────────────────────────────────────────────────


def main(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(
        description="Generate per-lane launch files from mission config.",
    )
    p.add_argument(
        "--mission-dir",
        type=Path,
        default=None,
        help="Mission directory containing .mission-config.yaml (default: cwd).",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory (default: mission-dir).",
    )
    args = p.parse_args(argv)

    mission_dir = (args.mission_dir or Path(".")).resolve()
    out_dir = (args.out_dir or mission_dir).resolve()

    # Ensure project root is on sys.path so scripts package is importable
    # whether the script is invoked from the repo root or any subdirectory.
    _project_root = str(Path(__file__).resolve().parents[1])
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)

    # If there is no real .mission-config.yaml in mission_dir, fall back to the
    # legacy generate_all() path so that `python3 scripts/gen_lane_launches.py`
    # (no --mission-dir) round-trips cleanly against the committed launch-*.md
    # files, which were generated by generate_all() (hardcoded 6-lane legacy
    # shape with short model aliases and per-lane offsets).  Only enter the
    # config-driven path when a real config file is present — that is the only
    # case where generate_from_config() would produce headers that match.
    _config_file = mission_dir / ".mission-config.yaml"
    if not _config_file.exists():
        out_dir.mkdir(parents=True, exist_ok=True)
        generate_all(out_dir)
        for lane in DEFAULT_LANES:
            print(f"wrote {out_dir / f'launch-{lane}.md'}")
        return 0

    # Import here so FastAPI/uvicorn are NOT required at module level.
    from scripts._config_loader import load_for_scripts

    config = load_for_scripts(mission_dir)
    written = generate_from_config(config, mission_dir, out_dir)
    for path in written:
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
