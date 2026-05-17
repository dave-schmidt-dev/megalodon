from pathlib import Path

from megalodon_ui.mission_config.schema import (
    MissionConfig,
    MissionInfo,
    LaneConfig,
    HarnessBinding,
    TaskIdPattern,
    validate_task_id_with_config,
    _assert_no_path_traversal,
)


def load_mission_config(mission_dir: Path) -> MissionConfig:
    """Load .mission-config.yaml from mission_dir, falling back to default_v9_0_shape.

    Precedence:
      1. <mission_dir>/.mission-config.yaml exists -> parse YAML -> MissionConfig.model_validate.
      2. Otherwise -> default_v9_0_shape.synthesize(mission_dir).

    Raises:
      pydantic.ValidationError if YAML exists but doesn't parse as MissionConfig.
      yaml.YAMLError if YAML exists but is malformed.
    """
    import yaml
    from megalodon_ui.mission_config import default_v9_0_shape

    config_path = mission_dir / ".mission-config.yaml"
    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        return MissionConfig.model_validate(raw)
    return default_v9_0_shape.synthesize(mission_dir)


__all__ = [
    "MissionConfig",
    "MissionInfo",
    "LaneConfig",
    "HarnessBinding",
    "TaskIdPattern",
    "validate_task_id_with_config",
    "_assert_no_path_traversal",
    "load_mission_config",
]
