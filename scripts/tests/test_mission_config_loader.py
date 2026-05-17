"""Tests for mission_config YAML loader and operator CLI (P1.4)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from megalodon_ui.mission_config import load_mission_config, MissionConfig
from megalodon_ui.mission_config.__main__ import main


# ── helpers ──────────────────────────────────────────────────────────────────

_MINIMAL_YAML = """\
schema_version: 1
mission:
  id: test-mission
  utc_started: "2025-01-01T00:00:00Z"
  type: software-engineering
  description: minimal test config
lanes:
  - name: AUDIT
    short: A
    role: auditor
    harness:
      cli: claude
      model: claude-sonnet-4-6
    cadence_seconds: 300
    tick_offset_seconds: 0
phases:
  - INIT
  - COMPLETE
task_id_patterns:
  patterns:
    - "^[A-Z][A-Za-z0-9\\\\-\\\\.]*$"
  description: ""
harness_rebinding_reserved: {}
"""


# ── tests ─────────────────────────────────────────────────────────────────────


def test_loads_existing_yaml(tmp_path: Path) -> None:
    """load_mission_config reads an existing YAML and returns matching MissionConfig."""
    config_file = tmp_path / ".mission-config.yaml"
    config_file.write_text(_MINIMAL_YAML, encoding="utf-8")

    config = load_mission_config(tmp_path)

    assert isinstance(config, MissionConfig)
    assert config.mission.id == "test-mission"
    assert len(config.lanes) == 1


def test_falls_back_to_default_shape(tmp_path: Path) -> None:
    """load_mission_config falls back to default_v9_0_shape when no YAML present."""
    config = load_mission_config(tmp_path)

    assert isinstance(config, MissionConfig)
    assert config.lanes[0].name == "AUDIT"
    assert config.phases[0] == "INIT"


def test_init_cli_writes_valid_yaml(tmp_path: Path) -> None:
    """init subcommand writes a .mission-config.yaml that loads as a valid MissionConfig."""
    result = main(["init", "--mission-dir", str(tmp_path)])
    assert result == 0

    yaml_path = tmp_path / ".mission-config.yaml"
    assert yaml_path.exists()

    config = load_mission_config(tmp_path)
    assert isinstance(config, MissionConfig)


def test_init_refuses_to_overwrite_without_force(tmp_path: Path, capsys) -> None:
    """init refuses to overwrite without --force; with --force it succeeds."""
    sentinel = "sentinel: content\n"
    config_file = tmp_path / ".mission-config.yaml"
    config_file.write_text(sentinel, encoding="utf-8")

    result = main(["init", "--mission-dir", str(tmp_path)])
    assert result == 1
    assert config_file.read_text(encoding="utf-8") == sentinel

    captured = capsys.readouterr()
    assert "force" in captured.err.lower() or "exists" in captured.err.lower()

    result2 = main(["init", "--mission-dir", str(tmp_path), "--force"])
    assert result2 == 0
    assert config_file.read_text(encoding="utf-8") != sentinel


def test_validate_cli_passes_on_default_shape_yaml(tmp_path: Path, capsys) -> None:
    """validate subcommand exits 0 and prints OK for a default-shape YAML."""
    init_result = main(["init", "--mission-dir", str(tmp_path)])
    assert init_result == 0

    yaml_path = tmp_path / ".mission-config.yaml"
    result = main(["validate", str(yaml_path)])
    assert result == 0

    captured = capsys.readouterr()
    assert "OK" in captured.out


def test_validate_cli_fails_on_malformed_yaml(tmp_path: Path, capsys) -> None:
    """validate subcommand exits 1 and writes to stderr for malformed YAML."""
    bad_path = tmp_path / "bad.yaml"
    bad_path.write_text("bad: : : yaml\n", encoding="utf-8")

    result = main(["validate", str(bad_path)])
    assert result == 1

    captured = capsys.readouterr()
    assert captured.err.strip() != ""


def test_scripts_loader_returns_default_shape_for_empty_dir(tmp_path: Path) -> None:
    """load_for_scripts falls back to default_v9_0_shape for an empty directory."""
    from scripts._config_loader import load_for_scripts

    cfg = load_for_scripts(tmp_path)

    assert isinstance(cfg, MissionConfig)
    assert cfg.lanes[0].name == "AUDIT"


def test_scripts_loader_resolves_relative_path(tmp_path: Path, monkeypatch) -> None:
    """load_for_scripts resolves a relative string path via .resolve()."""
    from scripts._config_loader import load_for_scripts

    config_file = tmp_path / ".mission-config.yaml"
    config_file.write_text(_MINIMAL_YAML, encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    cfg = load_for_scripts(".")

    assert isinstance(cfg, MissionConfig)
    assert cfg.mission.id == "test-mission"
