"""Scripts-side mission config loader.

Thin facade over megalodon_ui.mission_config. Helper scripts (atomic_close,
validation, watchdog) import from here, NOT from megalodon_ui directly. The
package's transitive import graph is stdlib + pydantic + pyyaml — no FastAPI
needed at module import time.
"""

from __future__ import annotations

from pathlib import Path

from megalodon_ui.mission_config import MissionConfig, load_mission_config


def load_for_scripts(mission_dir: str | Path) -> MissionConfig:
    """Load mission config for scripts-side callers.

    Accepts str OR Path (CLI arg convenience); resolves to absolute Path
    before calling load_mission_config (P1.4's note on absolute paths so
    mission.id defaults correctly to directory basename).

    Returns:
        MissionConfig — from <mission_dir>/.mission-config.yaml if present,
        else default_v9_0_shape.synthesize(mission_dir).
    """
    return load_mission_config(Path(mission_dir).resolve())


__all__ = ["MissionConfig", "load_for_scripts"]
