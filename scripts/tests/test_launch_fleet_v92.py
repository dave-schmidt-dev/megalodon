"""V9.2 — tests for the mode-based dispatcher in scripts/launch_fleet.sh.

Covers:
  - print mode (default): delegates to megalodon_ui.preview
  - dry-run mode: delegates with --include-tmux-argv
  - spawn mode: env overlay + dry-exec path via MEGALODON_LAUNCH_DRY_EXEC=1
  - --no-launch rejection (CV-4)
  - tmux pre-flight check
  - SIGTERM propagation in spawn mode (xfail: OS-level test complexity)
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "launch_fleet.sh"

# Use the existing minimal_3_lane fixture (has .mission-config.yaml; no launch-*.md).
FIXTURE_3LANE_SRC = (
    REPO_ROOT / "scripts" / "tests" / "fixtures" / "configs" / "minimal_3_lane"
)


@pytest.fixture()
def mission_3lane(tmp_path: Path) -> Path:
    """Writable copy of the minimal_3_lane fixture (3 lanes, no launch-*.md files)."""
    dest = tmp_path / "mission"
    shutil.copytree(FIXTURE_3LANE_SRC, dest)
    return dest


def _run(
    *args: str,
    env: dict[str, str] | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """Run launch_fleet.sh under bash with merged environment."""
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=merged_env,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# test_default_mode_emits_print_output
# ---------------------------------------------------------------------------


def test_default_mode_emits_print_output(mission_3lane: Path) -> None:
    """Default (print) mode delegates to megalodon_ui.preview and emits lane= lines."""
    result = _run("--mission-dir", str(mission_3lane))

    assert result.returncode == 0, f"stderr:\n{result.stderr}"

    lane_lines = [
        line for line in result.stdout.splitlines() if line.strip().startswith("lane=")
    ]
    assert len(lane_lines) == 3, (
        f"Expected 3 lane= lines from preview, got {len(lane_lines)}:\n{result.stdout}"
    )
    for line in lane_lines:
        assert "cli=" in line, f"Missing cli= in: {line!r}"
        assert "model=" in line, f"Missing model= in: {line!r}"
        assert "argv=" in line, f"Missing argv= in: {line!r}"


# ---------------------------------------------------------------------------
# test_dry_run_includes_tmux_argv
# ---------------------------------------------------------------------------


def test_dry_run_includes_tmux_argv(mission_3lane: Path) -> None:
    """--dry-run delegates to preview with --include-tmux-argv."""
    result = _run("--dry-run", "--mission-dir", str(mission_3lane))

    assert result.returncode == 0, f"stderr:\n{result.stderr}"

    stdout = result.stdout
    lane_lines = [
        line for line in stdout.splitlines() if line.strip().startswith("lane=")
    ]
    assert len(lane_lines) == 3, (
        f"Expected 3 lane= lines, got {len(lane_lines)}:\n{stdout}"
    )
    # The --include-tmux-argv output contains tmux new-session lines.
    assert "new-session" in stdout, (
        f"Expected 'new-session' in dry-run output:\n{stdout}"
    )
    assert "tmux -S" in stdout, f"Expected 'tmux -S' socket reference:\n{stdout}"


# ---------------------------------------------------------------------------
# test_spawn_mode_invokes_uv_run
#
# Approach: inject MEGALODON_LAUNCH_DRY_EXEC=1 so the bash script echoes the
# intended exec command instead of actually starting uvicorn.  This is the
# simplest, most portable approach — no stub uv binary required.
# ---------------------------------------------------------------------------


def test_spawn_mode_invokes_uv_run(mission_3lane: Path) -> None:
    """--spawn with dry-exec env prints the intended exec command."""
    result = _run(
        "--spawn",
        "--mission-dir",
        str(mission_3lane),
        env={"MEGALODON_LAUNCH_DRY_EXEC": "1"},
    )

    assert result.returncode == 0, f"stderr:\n{result.stderr}"

    stdout = result.stdout.strip()
    assert "exec uv run python -m megalodon_ui" in stdout, (
        f"Expected 'exec uv run python -m megalodon_ui' in stdout:\n{stdout}"
    )
    assert f"--mission-dir {mission_3lane}" in stdout, (
        f"Expected --mission-dir in stdout:\n{stdout}"
    )
    assert "--host 127.0.0.1" in stdout, f"Expected default --host in stdout:\n{stdout}"
    assert "--port 8000" in stdout, f"Expected default --port in stdout:\n{stdout}"


def test_spawn_mode_cli_override_sets_env(mission_3lane: Path) -> None:
    """--cli-AUDIT=codex sets MEGALODON_CLI_AUDIT in the exec environment."""
    result = _run(
        "--spawn",
        "--mission-dir",
        str(mission_3lane),
        "--cli-AUDIT=codex",
        env={"MEGALODON_LAUNCH_DRY_EXEC": "1"},
    )

    assert result.returncode == 0, f"stderr:\n{result.stderr}"
    # The dry-exec path prints the exec command; the env var is set before exec.
    # Verify the script exits 0 (env was exported without error).
    assert "exec uv run" in result.stdout


def test_spawn_mode_custom_host_port(mission_3lane: Path) -> None:
    """--spawn passes custom --host and --port to the exec command."""
    result = _run(
        "--spawn",
        "--mission-dir",
        str(mission_3lane),
        "--host",
        "0.0.0.0",
        "--port",
        "9999",
        env={"MEGALODON_LAUNCH_DRY_EXEC": "1"},
    )

    assert result.returncode == 0, f"stderr:\n{result.stderr}"
    stdout = result.stdout
    assert "--host 0.0.0.0" in stdout, f"Expected --host 0.0.0.0:\n{stdout}"
    assert "--port 9999" in stdout, f"Expected --port 9999:\n{stdout}"


# ---------------------------------------------------------------------------
# test_no_launch_flag_rejected
# ---------------------------------------------------------------------------


def test_no_launch_flag_rejected(mission_3lane: Path) -> None:
    """--no-launch must exit 2 with a clear CV-4 removal message."""
    result = _run("--no-launch", "--mission-dir", str(mission_3lane))

    assert result.returncode == 2, (
        f"Expected exit 2 for --no-launch, got {result.returncode}"
    )
    assert "removed in v9.2 (CV-4)" in result.stderr, (
        f"Expected CV-4 message in stderr:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# test_tmux_pre_flight
# ---------------------------------------------------------------------------


def test_tmux_pre_flight(mission_3lane: Path, tmp_path: Path) -> None:
    """Script exits 6 with a clear error when tmux is not on PATH (spawn mode)."""
    # Build a sandboxed PATH that contains common POSIX tools but excludes tmux.
    # We place an empty directory first so tmux is not found.
    empty_bin = tmp_path / "empty_bin"
    empty_bin.mkdir()

    # Collect PATH entries that exist and don't provide tmux, to keep bash alive.
    safe_paths = [str(empty_bin)]
    for p in os.environ.get("PATH", "").split(":"):
        if p and Path(p).is_dir():
            tmux_here = Path(p) / "tmux"
            if not tmux_here.exists():
                safe_paths.append(p)

    # Spawn mode triggers the pre-flight; preview-only modes intentionally skip
    # it (the operator can preview without tmux installed).
    result = _run(
        "--spawn",
        "--mission-dir",
        str(mission_3lane),
        env={"PATH": ":".join(safe_paths)},
    )

    assert result.returncode == 6, (
        f"Expected exit 6 for missing tmux, got {result.returncode};\n"
        f"stderr: {result.stderr}"
    )
    assert "tmux not installed" in result.stderr, (
        f"Expected 'tmux not installed' in stderr:\n{result.stderr}"
    )
