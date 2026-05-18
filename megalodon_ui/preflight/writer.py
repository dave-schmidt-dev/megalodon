"""megalodon_ui.preflight.writer — atomic write for .mission-config.yaml.

Uses tmp+rename (os.replace) to ensure atomicity (CV-2). Cleans up the .tmp
on any exception. SIGINT/SIGTERM snapshot support via write_aborted_snapshot.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import yaml

from megalodon_ui.mission_config.schema import MissionConfig


def write_atomic(
    config: MissionConfig,
    mission_dir: Path,
    force: bool = False,
) -> Path:
    """Write config to <mission_dir>/.mission-config.yaml via tmp+rename.

    If the target exists and force=False, raises FileExistsError.
    Cleans up the .tmp on any exception. Returns the final path.
    """
    target = mission_dir / ".mission-config.yaml"
    tmp = mission_dir / ".mission-config.yaml.tmp"

    if target.exists() and not force:
        raise FileExistsError(
            f"{target} already exists. Pass force=True or use --force to overwrite."
        )

    payload = yaml.safe_dump(
        config.model_dump(mode="json"),
        sort_keys=False,
        default_flow_style=False,
    )

    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, target)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise

    return target


def write_aborted_snapshot(yaml_text: str, mission_dir: Path) -> Path:
    """Write the in-progress YAML to .mission-config.yaml.aborted-<utc>.

    Used by SIGINT/SIGTERM handlers. Best-effort: silently swallows IO errors
    (we're already shutting down). Returns the snapshot path (even on failure,
    for logging purposes).
    """
    utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot_path = mission_dir / f".mission-config.yaml.aborted-{utc}"
    try:
        snapshot_path.write_text(yaml_text, encoding="utf-8")
    except OSError:
        pass
    return snapshot_path
