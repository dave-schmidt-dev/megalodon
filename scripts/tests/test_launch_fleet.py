"""V9 A2 — tests for scripts/launch_fleet.sh.

Covers flag parsing, print mode, dry-run AppleScript generation, and the
pre-flight checks (lane files, applier heartbeat). Does not actually open
iTerm — that's verified by hand via --no-launch spawn.
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skip(
    reason=(
        "v9.1 legacy AppleScript-flow tests for launch_fleet.sh. Task 1.8 "
        "replaced the script with the 3-mode dispatcher (print/--dry-run/--spawn); "
        "coverage for the new shape lives in test_launch_fleet_v92.py. These tests "
        "are kept on disk for archival reference but cannot pass against the v9.2 "
        "script. Delete or migrate as part of Phase 7's audit pass."
    )
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "launch_fleet.sh"
LANES = ["AUDIT", "ARCHITECT", "BACKEND", "FRONTEND", "TEST", "META"]
# /bin/bash on macOS is 3.2.57 — pinned here to catch any 4.x-only syntax
# leaking into launch_fleet.sh (e.g. ${var,,}, mapfile, declare -A).
BASH_32 = "/bin/bash"


def _make_mission(tmp_path: Path, with_lane_files: bool = True, with_fresh_applier: bool = False) -> Path:
    """Build a minimal mission dir with the requested preconditions."""
    mission = tmp_path / "mission"
    mission.mkdir()
    if with_lane_files:
        for lane in LANES:
            (mission / f"launch-{lane}.md").write_text(f"# launch-{lane}.md test stub\n")
    if with_fresh_applier:
        lock = mission / "queue" / ".applier.lock"
        lock.mkdir(parents=True)
        (lock / "heartbeat.txt").write_text("ok\n")
    return mission


def _run(*args: str, mission: Path | None = None) -> subprocess.CompletedProcess:
    cmd = ["bash", str(SCRIPT)]
    if mission is not None:
        cmd.append(str(mission))
    cmd.extend(args)
    return subprocess.run(cmd, capture_output=True, text=True)


def test_help_lists_all_lanes():
    r = _run("--help")
    assert r.returncode == 0, r.stderr
    for lane in LANES:
        assert lane in r.stdout, f"--help missing lane: {lane}"
    assert "--spawn" in r.stdout
    assert "--no-launch" in r.stdout


def test_print_mode_emits_six_commands(tmp_path):
    mission = _make_mission(tmp_path)
    r = _run(mission=mission)
    assert r.returncode == 0, r.stderr
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("cd ")]
    assert len(lines) == 6
    # First lane is AUDIT, last is META
    assert "launch-AUDIT.md" in lines[0]
    assert "launch-META.md" in lines[-1]


def test_print_mode_warns_on_missing_launch_files(tmp_path):
    mission = _make_mission(tmp_path, with_lane_files=False)
    r = _run(mission=mission)
    assert r.returncode == 0, "print mode should not fail on missing lane files"
    assert "warning" in r.stderr.lower()
    # All 6 missing → 6 warnings
    assert r.stderr.count("missing") >= 6


def test_print_mode_uses_correct_model_per_lane(tmp_path):
    mission = _make_mission(tmp_path)
    r = _run(mission=mission)
    assert r.returncode == 0, r.stderr
    # AUDIT and META = sonnet; builder lanes = opus. Aliases per `claude --help`.
    audit_line = next(ln for ln in r.stdout.splitlines() if "launch-AUDIT.md" in ln)
    meta_line = next(ln for ln in r.stdout.splitlines() if "launch-META.md" in ln)
    backend_line = next(ln for ln in r.stdout.splitlines() if "launch-BACKEND.md" in ln)
    assert "--model sonnet " in audit_line
    assert "--model sonnet " in meta_line
    assert "--model opus " in backend_line
    # Guard against the regression where "sonnet-4.6" / "opus-4.7" reappear —
    # those strings are not valid `claude --model` arguments.
    assert "sonnet-4.6" not in r.stdout
    assert "opus-4.7" not in r.stdout


def test_per_lane_cli_override(tmp_path):
    mission = _make_mission(tmp_path)
    r = _run("--cli-audit=codex", "--cli-meta=gemini", mission=mission)
    assert r.returncode == 0, r.stderr
    audit_line = next(ln for ln in r.stdout.splitlines() if "launch-AUDIT.md" in ln)
    meta_line = next(ln for ln in r.stdout.splitlines() if "launch-META.md" in ln)
    backend_line = next(ln for ln in r.stdout.splitlines() if "launch-BACKEND.md" in ln)
    assert "codex" in audit_line and "claude" not in audit_line.split("&&")[1]
    assert "gemini" in meta_line
    assert "claude" in backend_line  # not overridden


def test_unknown_flag_errors():
    r = _run("--this-is-not-a-flag")
    assert r.returncode == 1
    assert "unknown flag" in r.stderr


def test_dry_run_spawn_emits_applescript(tmp_path):
    mission = _make_mission(tmp_path)
    r = _run("--spawn", "--dry-run", "--skip-applier-check", mission=mission)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert 'tell application "iTerm"' in out
    assert "create window with default profile" in out
    assert out.count("split vertically with default profile") == 2
    assert out.count("split horizontally with default profile") == 3
    for lane in LANES:
        assert f'set name to "{lane}"' in out
        assert f"launch-{lane}.md" in out
    assert out.rstrip().endswith("end tell")


def test_dry_run_spawn_no_launch_uses_echo(tmp_path):
    mission = _make_mission(tmp_path)
    r = _run("--spawn", "--dry-run", "--skip-applier-check", "--no-launch", mission=mission)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    for lane in LANES:
        assert f"=== {lane} (test mode" in out
    # The would-be command line is dumped via a quoted echo so the shell
    # treats it as text, not as an exec. After sh_dquote landed, the embedded
    # cd uses a double-quoted path (was single-quoted in v1).
    assert '&& echo \\"cd ' in out
    # Live-mode shape (badge; cd /... && claude) must be absent.
    # Live: `printf ... ; cd "/..."` ; no-launch: `printf ... ; echo \"`
    assert ' ; cd \\"/private' not in out, "live-mode exec pattern leaked into --no-launch"
    # Confirm every pane's command after the badge starts with `echo` of the banner.
    assert out.count(' ; echo \\"===') == 6


def test_dry_run_spawn_includes_badge_prefix(tmp_path):
    """Each pane's command starts with iTerm's SetBadgeFormat escape."""
    mission = _make_mission(tmp_path)
    r = _run("--spawn", "--dry-run", "--skip-applier-check", "--no-launch", mission=mission)
    assert r.returncode == 0, r.stderr
    # Expect the badge printf 6 times (one per lane).
    assert r.stdout.count("SetBadgeFormat=%s") == 6
    # Base64 of "AUDIT" = QVVESVQ=
    assert "QVVESVQ=" in r.stdout


def test_spawn_missing_lane_files_errors(tmp_path):
    mission = _make_mission(tmp_path, with_lane_files=False)
    r = _run("--spawn", "--dry-run", "--skip-applier-check", mission=mission)
    assert r.returncode == 2
    assert "lane launch file" in r.stderr.lower()


def test_spawn_missing_applier_errors(tmp_path):
    mission = _make_mission(tmp_path, with_lane_files=True, with_fresh_applier=False)
    # No --skip-applier-check, no --no-launch → must check heartbeat.
    r = _run("--spawn", "--dry-run", mission=mission)
    assert r.returncode == 3
    assert "applier heartbeat" in r.stderr.lower()


def test_spawn_stale_applier_errors(tmp_path):
    mission = _make_mission(tmp_path, with_lane_files=True, with_fresh_applier=True)
    hb = mission / "queue" / ".applier.lock" / "heartbeat.txt"
    stale_time = time.time() - 120
    os.utime(hb, (stale_time, stale_time))
    r = _run("--spawn", "--dry-run", mission=mission)
    assert r.returncode == 4
    assert "stale" in r.stderr.lower()


def test_spawn_fresh_applier_passes_check(tmp_path):
    mission = _make_mission(tmp_path, with_lane_files=True, with_fresh_applier=True)
    r = _run("--spawn", "--dry-run", mission=mission)
    assert r.returncode == 0, r.stderr
    assert 'tell application "iTerm"' in r.stdout


def test_no_launch_skips_applier_check(tmp_path):
    """--no-launch should bypass the applier gate even without --skip-applier-check."""
    mission = _make_mission(tmp_path, with_lane_files=True, with_fresh_applier=False)
    r = _run("--spawn", "--dry-run", "--no-launch", mission=mission)
    assert r.returncode == 0, r.stderr


def test_prompt_override_replaces_read_launch(tmp_path):
    mission = _make_mission(tmp_path)
    r = _run(
        "--spawn", "--dry-run", "--skip-applier-check",
        "--prompt-override=say hello",
        mission=mission,
    )
    assert r.returncode == 0, r.stderr
    out = r.stdout
    # The override must appear in every lane's claude invocation.
    # AppleScript escapes the inner quotes, so we look for the \"-escaped form.
    assert out.count('\\"say hello\\"') == 6
    # And the default read-launch prompt must be absent.
    assert "read launch-AUDIT.md" not in out


def test_prompt_override_works_with_non_claude_cli(tmp_path):
    mission = _make_mission(tmp_path)
    r = _run(
        "--spawn", "--dry-run", "--skip-applier-check",
        "--cli-audit=codex",
        "--prompt-override=demo prompt",
        mission=mission,
    )
    assert r.returncode == 0, r.stderr
    # For non-claude CLIs the script prints 'Type: <prompt>' as a hint.
    # sh_dquote wraps the prompt in bash double-quotes; AppleScript then
    # escapes the embedded "s. So the AppleScript source carries
    # `Type: \"demo prompt\"`. The shell still renders it as `Type: demo prompt`
    # at runtime (bash concatenates adjacent quoted strings).
    assert 'Type: \\"demo prompt\\"' in r.stdout


def test_unknown_cli_in_spawn_mode_errors(tmp_path):
    mission = _make_mission(tmp_path)
    r = _run("--spawn", "--dry-run", "--skip-applier-check", "--cli-audit=bogus-cli", mission=mission)
    assert r.returncode == 5
    assert "unknown cli" in r.stderr.lower()


# ---------------------------------------------------------------------------
# Gap coverage (Tester pass).
# ---------------------------------------------------------------------------

def test_mission_dir_not_found_errors(tmp_path):
    """Non-existent mission dir → exit 1 with a clear error."""
    bogus = tmp_path / "does-not-exist"
    r = _run(mission=bogus)
    assert r.returncode == 1
    assert "mission dir not found" in r.stderr.lower()


@pytest.mark.parametrize(
    "flag,lane",
    [
        ("--cli-architect=codex", "ARCHITECT"),
        ("--cli-backend=codex", "BACKEND"),
        ("--cli-frontend=codex", "FRONTEND"),
        ("--cli-test=codex", "TEST"),
    ],
)
def test_per_lane_cli_override_all_lanes(tmp_path, flag, lane):
    """The remaining 4 lane CLI overrides (audit/meta covered elsewhere)."""
    mission = _make_mission(tmp_path)
    r = _run(flag, mission=mission)
    assert r.returncode == 0, r.stderr
    target_line = next(ln for ln in r.stdout.splitlines() if f"launch-{lane}.md" in ln)
    # The override should appear before the prompt, and other lanes still use claude.
    assert "codex" in target_line
    other_lanes = [ln for ln in r.stdout.splitlines() if ln.startswith("cd ") and f"launch-{lane}.md" not in ln]
    assert len(other_lanes) == 5
    for ol in other_lanes:
        # The CLI is the third whitespace-separated token of "cd <dir> && <cli> --model ..."
        assert " claude --model " in ol, f"non-overridden lane lost its claude CLI: {ol}"


def test_badge_base64_per_lane(tmp_path):
    """Every lane's badge escape must contain that lane's base64."""
    mission = _make_mission(tmp_path)
    r = _run("--spawn", "--dry-run", "--skip-applier-check", "--no-launch", mission=mission)
    assert r.returncode == 0, r.stderr
    for lane in LANES:
        b64 = base64.b64encode(lane.encode()).decode()
        assert b64 in r.stdout, f"badge base64 for {lane} missing: expected {b64}"


def test_applescript_split_order_and_session_binding(tmp_path):
    """The 2x3 layout depends on a specific split sequence.

    sessA (top-left, AUDIT) → split vertically → sessB (top-mid, ARCHITECT)
    sessB → split vertically → sessC (top-right, BACKEND)
    sessA → split horizontally → sessD (bottom-left, FRONTEND)
    sessB → split horizontally → sessE (bottom-mid, TEST)
    sessC → split horizontally → sessF (bottom-right, META)
    """
    mission = _make_mission(tmp_path)
    r = _run("--spawn", "--dry-run", "--skip-applier-check", "--no-launch", mission=mission)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    # Each split must appear inside the correct parent's `tell` block. Find the
    # ordered positions of key statements and verify the sequence.
    markers = [
        "set sessA to current session",
        "tell sessA",  # parent of sessB
        "set sessB to (split vertically",
        "tell sessB",  # parent of sessC
        "set sessC to (split vertically",
        "tell sessA",  # parent of sessD (second tell sessA)
        "set sessD to (split horizontally",
        "tell sessB",  # parent of sessE (second tell sessB)
        "set sessE to (split horizontally",
        "tell sessC",  # parent of sessF (second tell sessC)
        "set sessF to (split horizontally",
    ]
    last = -1
    for m in markers:
        idx = out.find(m, last + 1)
        assert idx > last, f"AppleScript out of order at marker: {m!r}"
        last = idx


def test_applescript_write_text_pairs_session_to_lane(tmp_path):
    """The `write text` block for sessA must contain AUDIT's prompt, etc."""
    mission = _make_mission(tmp_path)
    r = _run("--spawn", "--dry-run", "--skip-applier-check", mission=mission)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    pairs = list(zip(["sessA", "sessB", "sessC", "sessD", "sessE", "sessF"], LANES))
    for sess, lane in pairs:
        # Locate `tell <sess>` that is immediately followed by a `write text`.
        # Two `tell <sess>` blocks exist for the splits; the write-text one is
        # the second occurrence and comes after all the split tell blocks.
        write_idx = out.find(f"tell {sess}\n        write text")
        assert write_idx != -1, f"missing write-text block for {sess}"
        # The lane's launch file should appear inside that block.
        block_end = out.find("end tell", write_idx)
        block = out[write_idx:block_end]
        assert f"launch-{lane}.md" in block, f"{sess} write-text not bound to {lane}"


def test_print_mode_omits_badge_escapes(tmp_path):
    """Print mode is for copy/paste — no iTerm escape sequences."""
    mission = _make_mission(tmp_path)
    r = _run(mission=mission)
    assert r.returncode == 0, r.stderr
    assert "SetBadgeFormat" not in r.stdout
    assert "\\e]1337" not in r.stdout


def test_runs_under_bash_3_2(tmp_path):
    """Hard-pin /bin/bash (3.2.x on macOS) to catch bash 4-only syntax."""
    if not Path(BASH_32).exists():
        pytest.skip(f"{BASH_32} not present")
    mission = _make_mission(tmp_path)
    cmd = [BASH_32, str(SCRIPT), str(mission), "--spawn", "--dry-run", "--skip-applier-check"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, f"bash 3.2 invocation failed: stderr={r.stderr}"
    assert 'tell application "iTerm"' in r.stdout


def test_prompt_override_with_single_quote_does_not_break_shell(tmp_path):
    """Regression for single-quote injection in pane_cmd.

    Before sh_dquote was added, a prompt containing ' would terminate the
    surrounding `echo '...'` shell fragment and produce a malformed command.
    sh_dquote wraps interpolated values in shell double-quotes so single
    quotes pass through as literal characters.
    """
    mission = _make_mission(tmp_path)
    r = _run(
        "--spawn", "--dry-run", "--skip-applier-check",
        "--prompt-override=say 'hi'",
        mission=mission,
    )
    assert r.returncode == 0, r.stderr
    # The single quotes in the prompt must appear literally — not interpreted
    # as shell quoting. Every claude lane should contain the prompt verbatim.
    assert r.stdout.count("say 'hi'") == 6
    # And the surrounding shell command must still be double-quoted (not
    # single-quoted, which is what produced the bug).
    assert " ; cd \\\"" in r.stdout


def test_prompt_override_with_special_chars_does_not_break_applescript(tmp_path):
    """Prompts containing " or \\ must be properly escaped for AppleScript."""
    mission = _make_mission(tmp_path)
    r = _run(
        "--spawn", "--dry-run", "--skip-applier-check",
        '--prompt-override=hello "world" \\path',
        mission=mission,
    )
    assert r.returncode == 0, r.stderr
    # The outer write-text "..." string in AppleScript must remain well-formed:
    # every line that opens `write text "` must close with a matching unescaped ".
    for line in r.stdout.splitlines():
        s = line.lstrip()
        if s.startswith('write text "'):
            inner = s[len('write text "'):]
            # Strip trailing whitespace; line should end with an unescaped ".
            assert inner.endswith('"'), f"write text line not closed: {line!r}"
