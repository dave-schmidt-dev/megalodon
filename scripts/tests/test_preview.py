"""Tests for megalodon_ui.preview — per-lane argv preview CLI (CR-8 + CV-3)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_3_LANE = (
    REPO_ROOT / "scripts" / "tests" / "fixtures" / "configs" / "minimal_3_lane"
)


def _run_preview(*extra_args: str, mission_dir: Path) -> subprocess.CompletedProcess:
    """Invoke megalodon_ui.preview as a subprocess and return the result."""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "megalodon_ui.preview",
            "--mission-dir",
            str(mission_dir),
            *extra_args,
        ],
        capture_output=True,
        text=True,
        env=os.environ | {"PYTHONPATH": str(REPO_ROOT)},
    )


@pytest.fixture()
def three_lane_mission(tmp_path: Path) -> Path:
    """Writable copy of the minimal 3-lane fixture."""
    dest = tmp_path / "mission"
    shutil.copytree(FIXTURE_3_LANE, dest)
    return dest


# ---------------------------------------------------------------------------
# test_preview_default_mode_3_lane_fixture
# ---------------------------------------------------------------------------


def test_preview_default_mode_3_lane_fixture(three_lane_mission: Path) -> None:
    """Default mode prints one lane= line per lane with cli=, model=, argv= fields."""
    result = _run_preview(mission_dir=three_lane_mission)

    assert result.returncode == 0, f"stderr: {result.stderr}"

    lane_lines = [
        line for line in result.stdout.splitlines() if line.strip().startswith("lane=")
    ]
    assert len(lane_lines) == 3, (
        f"Expected 3 lane= lines, got {len(lane_lines)}:\n{result.stdout}"
    )

    for line in lane_lines:
        assert "cli=" in line, f"Missing cli= in: {line!r}"
        assert "model=" in line, f"Missing model= in: {line!r}"
        assert "argv=" in line, f"Missing argv= in: {line!r}"


# ---------------------------------------------------------------------------
# test_preview_include_tmux_argv_adds_tmux_lines
# ---------------------------------------------------------------------------


def test_preview_include_tmux_argv_adds_tmux_lines(three_lane_mission: Path) -> None:
    """--include-tmux-argv adds tmux new-session and MEGALODON_FLEET_OWNED lines."""
    result = _run_preview("--include-tmux-argv", mission_dir=three_lane_mission)

    assert result.returncode == 0, f"stderr: {result.stderr}"

    stdout = result.stdout
    assert "new-session" in stdout, "Expected 'new-session' in tmux output"
    # The socket path appears in tmux -S <socket> lines
    assert "tmux -S" in stdout, "Expected 'tmux -S' in tmux output"
    assert "MEGALODON_FLEET_OWNED 1" in stdout, (
        "Expected set-environment MEGALODON_FLEET_OWNED 1"
    )

    lane_lines = [
        line for line in stdout.splitlines() if line.strip().startswith("lane=")
    ]
    assert len(lane_lines) == 3, f"Expected 3 lane= lines, got {len(lane_lines)}"


# ---------------------------------------------------------------------------
# test_preview_exit_1_on_missing_mission_dir
# ---------------------------------------------------------------------------


def test_preview_exit_1_on_missing_mission_dir(tmp_path: Path) -> None:
    """Nonexistent mission dir produces rc=1 and non-empty stderr."""
    nonexistent = tmp_path / "does_not_exist"
    result = _run_preview(mission_dir=nonexistent)

    assert result.returncode == 1, f"Expected rc=1, got {result.returncode}"
    assert result.stderr.strip(), "Expected non-empty stderr for missing mission dir"


# ---------------------------------------------------------------------------
# test_launch_helpers_plan_subcommand_removed
# ---------------------------------------------------------------------------


def test_launch_helpers_plan_subcommand_removed() -> None:
    """The 'plan' subcommand of _launch_helpers.py must no longer succeed."""
    helpers_path = REPO_ROOT / "scripts" / "_launch_helpers.py"
    if not helpers_path.exists():
        # File was deleted entirely — that's also acceptable.
        return

    result = subprocess.run(
        [sys.executable, str(helpers_path), "plan", "--mission-dir", "/tmp"],
        capture_output=True,
        text=True,
        env=os.environ | {"PYTHONPATH": str(REPO_ROOT)},
    )
    # plan subcommand removed — must not exit 0 with useful output
    assert result.returncode != 0, (
        f"'plan' subcommand should be removed but exited 0.\nstdout: {result.stdout}"
    )


# ---------------------------------------------------------------------------
# test_no_plan_launches_callers
# ---------------------------------------------------------------------------


def test_no_plan_launches_callers() -> None:
    """No Python file under scripts/ or megalodon_ui/ may reference plan_launches."""
    offending: list[str] = []
    for search_root in (REPO_ROOT / "scripts", REPO_ROOT / "megalodon_ui"):
        for py_file in sorted(search_root.rglob("*.py")):
            try:
                text = py_file.read_text(encoding="utf-8")
            except OSError:
                continue
            if "plan_launches" in text:
                if py_file == Path(__file__).resolve():
                    continue
                offending.append(str(py_file))

    assert not offending, (
        "These files still reference 'plan_launches' (should be zero after CV-3):\n"
        + "\n".join(offending)
    )
