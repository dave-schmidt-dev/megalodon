"""P3.5 — Grid generation tests for scripts/_launch_helpers.py.

Verifies:
- _grid(N) math: N=1,3,6,12.
- render_applescript() for N=1,3,6,12: correct split counts, pane names, session variables.
- CR-4: MANUAL TICK REQUIRED banner present for non-Claude lanes.
- --dry-run via subprocess: queue_mission returns 6 lane= lines, minimal_3_lane returns 3.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "launch_fleet.sh"

# ---------------------------------------------------------------------------
# Unit-level: import helpers directly
# ---------------------------------------------------------------------------

from scripts._launch_helpers import (  # noqa: E402  pre-existing module-level test scaffold position
    _grid,
    render_applescript,
    MANUAL_TICK_BANNER,
)


# ---------------------------------------------------------------------------
# Grid math tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n,expected_cols,expected_rows", [
    (1,  1, 1),
    (3,  2, 2),   # ceil(sqrt(3))=2 cols, ceil(3/2)=2 rows
    (6,  3, 2),   # ceil(sqrt(6))=3 cols, ceil(6/3)=2 rows
    (12, 4, 3),   # ceil(sqrt(12))=4 cols, ceil(12/4)=3 rows
])
def test_grid_math(n, expected_cols, expected_rows):
    cols, rows = _grid(n)
    assert cols == expected_cols, f"N={n}: expected cols={expected_cols}, got {cols}"
    assert rows == expected_rows, f"N={n}: expected rows={expected_rows}, got {rows}"


def test_grid_zero():
    assert _grid(0) == (0, 0)


# ---------------------------------------------------------------------------
# Helpers to build synthetic plans
# ---------------------------------------------------------------------------

def _make_plan(n: int, *, clis: list[str] | None = None) -> list[dict]:
    """Build a minimal plan for N lanes. clis overrides per-lane CLI names."""
    import string
    lane_names = list(string.ascii_uppercase[:n])  # A, B, C, ...
    if clis is None:
        clis = ["claude"] * n
    plan = []
    for i, (name, cli) in enumerate(zip(lane_names, clis)):
        model = "sonnet" if cli == "claude" else "gpt-4o"
        argv = ["claude", "--print", "--model", model, f"/tmp/launch-{name}.md"] if cli == "claude" \
            else [cli, "exec", "-m", model, f"/tmp/launch-{name}.md"]
        plan.append({
            "lane": name,
            "cli": cli,
            "model": model,
            "argv": argv,
            "env_overlay": {},
            "applescript_pane_index": i,
            "cwd": "/tmp/mission",
            "manual_tick": cli != "claude",
        })
    return plan


# ---------------------------------------------------------------------------
# AppleScript generation: N=1
# ---------------------------------------------------------------------------

def test_applescript_n1():
    """N=1: 1x1 grid, single session, no splits."""
    plan = _make_plan(1)
    out = render_applescript(plan)

    assert 'tell application "iTerm"' in out
    assert "set sessA to current session of newWindow" in out
    # No splits
    assert "split vertically" not in out
    assert "split horizontally" not in out
    # Lane name set
    assert 'set name to "A"' in out
    # write text present
    assert "write text" in out
    assert out.rstrip().endswith("end tell")


# ---------------------------------------------------------------------------
# AppleScript generation: N=3
# ---------------------------------------------------------------------------

def test_applescript_n3():
    """N=3: 2 cols x 2 rows. 1 vertical split (top row), 1 horizontal split (row 2 col 0 only)."""
    plan = _make_plan(3)
    out = render_applescript(plan)

    # 2 cols: sessA → split vertical → sessB
    assert out.count("split vertically with default profile") == 1
    # 1 horizontal: sessA → sessC (row 1, col 0). sessB has no pane (index 3 >= n).
    assert out.count("split horizontally with default profile") == 1
    # All 3 lanes named
    for lane in ["A", "B", "C"]:
        assert f'set name to "{lane}"' in out
    # 3 write text blocks
    assert out.count("write text") == 3


# ---------------------------------------------------------------------------
# AppleScript generation: N=6
# ---------------------------------------------------------------------------

def test_applescript_n6():
    """N=6: 3 cols x 2 rows. 2 vertical splits, 3 horizontal splits."""
    plan = _make_plan(6)
    out = render_applescript(plan)

    assert out.count("split vertically with default profile") == 2
    assert out.count("split horizontally with default profile") == 3
    # All 6 lanes
    for lane in ["A", "B", "C", "D", "E", "F"]:
        assert f'set name to "{lane}"' in out
    assert out.count("write text") == 6


# ---------------------------------------------------------------------------
# AppleScript generation: N=12
# ---------------------------------------------------------------------------

def test_applescript_n12():
    """N=12: 4 cols x 3 rows. 3 vertical splits, 8 horizontal splits."""
    plan = _make_plan(12)
    out = render_applescript(plan)

    # Top row: sessA → sessB → sessC → sessD (3 vertical splits)
    assert out.count("split vertically with default profile") == 3
    # Rows 1 and 2: 4 cols each = 8 horizontal splits
    assert out.count("split horizontally with default profile") == 8
    assert out.count("write text") == 12


# ---------------------------------------------------------------------------
# CR-4: MANUAL TICK REQUIRED banner
# ---------------------------------------------------------------------------

def test_cr4_manual_tick_present_for_non_claude():
    """Non-Claude lanes have MANUAL TICK REQUIRED banner in AppleScript."""
    plan = _make_plan(3, clis=["claude", "codex", "gemini"])
    out = render_applescript(plan)

    # Lane A (claude): no banner
    # Locate write text for sessA
    idx_a = out.find("tell sessA\n        write text")
    end_a = out.find("end tell", idx_a)
    block_a = out[idx_a:end_a]
    assert MANUAL_TICK_BANNER not in block_a, "claude lane should not have MANUAL TICK banner"

    # Lane B (codex): banner
    idx_b = out.find("tell sessB\n        write text")
    end_b = out.find("end tell", idx_b)
    block_b = out[idx_b:end_b]
    assert MANUAL_TICK_BANNER in block_b, "codex lane should have MANUAL TICK banner"

    # Lane C (gemini): banner
    idx_c = out.find("tell sessC\n        write text")
    end_c = out.find("end tell", idx_c)
    block_c = out[idx_c:end_c]
    assert MANUAL_TICK_BANNER in block_c, "gemini lane should have MANUAL TICK banner"


def test_cr4_all_claude_no_banner():
    """All-Claude fleet has no MANUAL TICK banner anywhere."""
    plan = _make_plan(4, clis=["claude", "claude", "claude", "claude"])
    out = render_applescript(plan)
    assert MANUAL_TICK_BANNER not in out


# ---------------------------------------------------------------------------
# Session variable naming
# ---------------------------------------------------------------------------

def test_session_var_names_alphabetic():
    """First 26 panes use sessA..sessZ naming."""
    plan = _make_plan(6)
    out = render_applescript(plan)
    for letter in ["A", "B", "C", "D", "E", "F"]:
        assert f"sess{letter}" in out


# ---------------------------------------------------------------------------
# --dry-run subprocess tests
# ---------------------------------------------------------------------------

def _run_dry(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), "--dry-run"] + list(args),
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


_V92_LAUNCH_FLEET_SKIP = pytest.mark.skip(
    reason="v9.2 Task 1.8 replaces launch_fleet.sh dry-run behavior; "
    "v9.1 'plan' subcommand was removed by CV-3 consolidation (preview moved to megalodon_ui.preview). "
    "Re-enable / migrate these tests as part of Task 1.8's new test_launch_fleet_v92.py."
)


@_V92_LAUNCH_FLEET_SKIP
def test_dry_run_queue_mission_six_lanes():
    """queue_mission (no .mission-config.yaml) → 6 lane= lines."""
    fixture = REPO_ROOT / "scripts" / "tests" / "fixtures" / "queue_mission"
    r = _run_dry("--mission-dir", str(fixture))
    assert r.returncode == 0, f"dry-run failed: {r.stderr}"
    lane_lines = [ln for ln in r.stdout.splitlines() if ln.startswith("lane=")]
    assert len(lane_lines) == 6, f"Expected 6 lane= lines, got {len(lane_lines)}: {r.stdout}"


@_V92_LAUNCH_FLEET_SKIP
def test_dry_run_queue_mission_grep():
    """Verify subprocess | grep -c 'lane=' returns 6."""
    fixture = REPO_ROOT / "scripts" / "tests" / "fixtures" / "queue_mission"
    r = _run_dry("--mission-dir", str(fixture))
    count = sum(1 for ln in r.stdout.splitlines() if "lane=" in ln)
    assert count == 6


@_V92_LAUNCH_FLEET_SKIP
def test_dry_run_minimal_3_lane_three_invocations():
    """minimal_3_lane → 3 lane invocations: ALPHA, BETA, GAMMA."""
    fixture = REPO_ROOT / "scripts" / "tests" / "fixtures" / "configs" / "minimal_3_lane"
    r = _run_dry("--mission-dir", str(fixture))
    assert r.returncode == 0, f"dry-run failed: {r.stderr}"
    lane_lines = [ln for ln in r.stdout.splitlines() if ln.startswith("lane=")]
    assert len(lane_lines) == 3
    lane_names = [ln.split("  ")[0].replace("lane=", "") for ln in lane_lines]
    assert lane_names == ["ALPHA", "BETA", "GAMMA"]


@_V92_LAUNCH_FLEET_SKIP
def test_dry_run_minimal_3_lane_cr4_banners():
    """BETA (codex) and GAMMA (gemini) get MANUAL TICK banner; ALPHA (claude) does not."""
    fixture = REPO_ROOT / "scripts" / "tests" / "fixtures" / "configs" / "minimal_3_lane"
    r = _run_dry("--mission-dir", str(fixture))
    assert r.returncode == 0, f"dry-run failed: {r.stderr}"
    lines = r.stdout.splitlines()

    # Find banner lines
    banner_lines = [ln for ln in lines if "MANUAL TICK REQUIRED" in ln]
    assert len(banner_lines) == 2, f"Expected 2 MANUAL TICK banners, got {len(banner_lines)}"

    # ALPHA (claude) must not be immediately followed by a banner
    alpha_idx = next(i for i, ln in enumerate(lines) if "lane=ALPHA" in ln)
    assert "MANUAL TICK" not in lines[alpha_idx], "ALPHA should not have banner on its own line"
    if alpha_idx + 1 < len(lines):
        assert "MANUAL TICK" not in lines[alpha_idx + 1], "Line after ALPHA should not be a banner"

    # BETA and GAMMA should be followed by banners
    beta_idx = next(i for i, ln in enumerate(lines) if "lane=BETA" in ln)
    assert "MANUAL TICK" in lines[beta_idx + 1], "BETA should be followed by MANUAL TICK banner"

    gamma_idx = next(i for i, ln in enumerate(lines) if "lane=GAMMA" in ln)
    assert "MANUAL TICK" in lines[gamma_idx + 1], "GAMMA should be followed by MANUAL TICK banner"


@_V92_LAUNCH_FLEET_SKIP
def test_dry_run_minimal_3_lane_cli_values():
    """ALPHA has claude, BETA has codex, GAMMA has gemini."""
    fixture = REPO_ROOT / "scripts" / "tests" / "fixtures" / "configs" / "minimal_3_lane"
    r = _run_dry("--mission-dir", str(fixture))
    assert r.returncode == 0, f"dry-run failed: {r.stderr}"
    lines = r.stdout.splitlines()

    alpha_line = next(ln for ln in lines if "lane=ALPHA" in ln)
    beta_line = next(ln for ln in lines if "lane=BETA" in ln)
    gamma_line = next(ln for ln in lines if "lane=GAMMA" in ln)

    assert "cli=claude" in alpha_line
    assert "cli=codex" in beta_line
    assert "cli=gemini" in gamma_line
