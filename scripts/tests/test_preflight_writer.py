"""Tests for megalodon_ui.preflight.writer — atomic write operations."""

from __future__ import annotations

import textwrap
from unittest.mock import patch

import pytest
import yaml

from megalodon_ui.mission_config.schema import (
    HarnessBinding,
    LaneConfig,
    MissionConfig,
    MissionInfo,
    TaskIdPattern,
)
from megalodon_ui.preflight.writer import write_aborted_snapshot, write_atomic


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def minimal_config() -> MissionConfig:
    """Return a minimal valid MissionConfig for writer tests."""
    return MissionConfig(
        schema_version=1,
        mission=MissionInfo(
            id="writer-test",
            utc_started="2026-01-01T00:00:00Z",
            type="software-engineering",
            description="writer test config",
        ),
        lanes=[
            LaneConfig(
                name="BACKEND",
                short="A",
                harness=HarnessBinding(cli="claude", model="claude-opus-4-7"),
                cadence_seconds=300,
            )
        ],
        phases=["INIT", "COMPLETE"],
        task_id_patterns=TaskIdPattern(patterns=[r"^[A-Z][A-Za-z0-9\-\.]*$"]),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWriteAtomic:
    def test_write_atomic_creates_file(self, tmp_path, minimal_config):
        """File appears at expected path; content parses back to the same MissionConfig."""
        result_path = write_atomic(minimal_config, tmp_path)

        assert result_path == tmp_path / ".mission-config.yaml"
        assert result_path.exists()

        raw = yaml.safe_load(result_path.read_text(encoding="utf-8"))
        loaded = MissionConfig.model_validate(raw)
        assert loaded.mission.id == minimal_config.mission.id
        assert len(loaded.lanes) == len(minimal_config.lanes)
        assert loaded.phases == minimal_config.phases

    def test_write_refuses_overwrite_without_force(self, tmp_path, minimal_config):
        """Second call without force=True raises FileExistsError."""
        write_atomic(minimal_config, tmp_path)

        with pytest.raises(FileExistsError):
            write_atomic(minimal_config, tmp_path, force=False)

    def test_write_overwrite_with_force(self, tmp_path, minimal_config):
        """force=True allows overwriting an existing .mission-config.yaml."""
        first_path = write_atomic(minimal_config, tmp_path)
        assert first_path.exists()

        # Modify the config slightly
        updated = minimal_config.model_copy(
            update={
                "mission": minimal_config.mission.model_copy(
                    update={"id": "updated-id"}
                )
            }
        )

        second_path = write_atomic(updated, tmp_path, force=True)
        assert second_path == first_path

        raw = yaml.safe_load(second_path.read_text(encoding="utf-8"))
        loaded = MissionConfig.model_validate(raw)
        assert loaded.mission.id == "updated-id"

    def test_write_atomic_tmp_cleaned_on_exception(self, tmp_path, minimal_config):
        """If os.replace raises, the .tmp file is unlinked before re-raising."""
        tmp_file = tmp_path / ".mission-config.yaml.tmp"

        with patch("os.replace", side_effect=OSError("simulated rename failure")):
            with pytest.raises(OSError, match="simulated rename failure"):
                write_atomic(minimal_config, tmp_path)

        # The .tmp must have been cleaned up
        assert not tmp_file.exists(), ".tmp file should be cleaned up after exception"


class TestWriteAbortedSnapshot:
    def test_aborted_snapshot_written(self, tmp_path):
        """write_aborted_snapshot produces .mission-config.yaml.aborted-<utc> with provided text."""
        yaml_text = textwrap.dedent("""\
            schema_version: 1
            mission:
              id: aborted-test
        """)

        snapshot_path = write_aborted_snapshot(yaml_text, tmp_path)

        assert snapshot_path.exists(), "aborted snapshot file must be created"
        assert snapshot_path.name.startswith(".mission-config.yaml.aborted-")
        assert snapshot_path.read_text(encoding="utf-8") == yaml_text

    def test_aborted_snapshot_filename_format(self, tmp_path):
        """Snapshot filename matches .mission-config.yaml.aborted-<utcstamp> pattern."""
        import re

        snapshot_path = write_aborted_snapshot("yaml: content", tmp_path)

        # Pattern: .mission-config.yaml.aborted-YYYYMMDDTHHMMSSZ
        pattern = r"^\.mission-config\.yaml\.aborted-\d{8}T\d{6}Z$"
        assert re.match(pattern, snapshot_path.name), (
            f"Snapshot filename {snapshot_path.name!r} does not match expected pattern"
        )

    def test_aborted_snapshot_swallows_io_error(self, tmp_path):
        """write_aborted_snapshot silently swallows IOError (best-effort)."""
        # Point at a non-existent directory to trigger IOError
        bad_dir = tmp_path / "nonexistent" / "deep"

        # Should not raise
        result = write_aborted_snapshot("some yaml", bad_dir)
        # Returns a path even on failure
        assert result is not None
        assert ".aborted-" in result.name
