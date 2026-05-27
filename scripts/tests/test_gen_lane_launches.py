"""P3.7 — direct-import coverage for gen_lane_launches config-driven paths.

Complements test_lane_launch_codegen.py (which drives the CLI via subprocess and
the legacy generate_all path). These tests import the module functions directly
so the v10-critical config-driven helpers — generate_from_config,
_find_launch_md (both lookup branches), _write_atomic (success + failure
cleanup), and the CLI's config-absent fallback — are exercised in-process and
counted by coverage.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import gen_lane_launches
from scripts._config_loader import load_for_scripts

_FIXTURE_3_LANE = (
    Path(__file__).resolve().parent / "fixtures" / "configs" / "minimal_3_lane"
)


# ─── generate_from_config (direct import) ────────────────────────────────────


def test_generate_from_config_writes_one_file_per_lane(tmp_path):
    """generate_from_config returns the written Paths and emits one v9.1-header
    launch file per configured lane (claude/codex/gemini mix)."""
    config = load_for_scripts(_FIXTURE_3_LANE)
    written = gen_lane_launches.generate_from_config(config, _FIXTURE_3_LANE, tmp_path)

    assert sorted(p.name for p in written) == [
        "launch-ALPHA.md",
        "launch-BETA.md",
        "launch-GAMMA.md",
    ]
    for p in written:
        assert p.exists()

    alpha = (tmp_path / "launch-ALPHA.md").read_text()
    beta = (tmp_path / "launch-BETA.md").read_text()
    # v9.1 header fields are populated from the config, not the legacy table.
    assert "LANE: ALPHA" in alpha
    assert "ROLE: audit" in alpha
    assert "MODEL_HINT: sonnet" in alpha
    assert "HARNESS_CLI: claude" in alpha
    assert "HARNESS_CLI: codex" in beta
    assert "MODEL_HINT: gpt-4o" in beta


def test_generate_from_config_includes_launch_body(tmp_path):
    """The launch.md template body is appended after the per-lane header."""
    config = load_for_scripts(_FIXTURE_3_LANE)
    gen_lane_launches.generate_from_config(config, _FIXTURE_3_LANE, tmp_path)
    text = (tmp_path / "launch-ALPHA.md").read_text()
    # Header ends with the stagger fence; body (real launch.md) follows the rule.
    assert "Step 0 — Stagger wait" in text
    # Body comes from _find_launch_md → project-root launch.md (fixture has none).
    body = gen_lane_launches._find_launch_md(_FIXTURE_3_LANE)
    assert body and body in text


# ─── _find_launch_md — BOTH branches ─────────────────────────────────────────


def test_find_launch_md_prefers_mission_dir(tmp_path):
    """When mission_dir has its own launch.md, that copy wins (first branch)."""
    (tmp_path / "launch.md").write_text("LOCAL MISSION LAUNCH BODY\n")
    result = gen_lane_launches._find_launch_md(tmp_path)
    assert result == "LOCAL MISSION LAUNCH BODY\n"


def test_find_launch_md_falls_back_to_repo_root(tmp_path):
    """With no launch.md in mission_dir, fall back to the project-root copy."""
    # tmp_path has no launch.md, so the fallback (repo root) must be returned.
    assert not (tmp_path / "launch.md").exists()
    result = gen_lane_launches._find_launch_md(tmp_path)
    repo_root_launch = (
        Path(gen_lane_launches.__file__).resolve().parents[1] / "launch.md"
    )
    assert repo_root_launch.exists(), "repo-root launch.md missing — fixture invalid"
    assert result == repo_root_launch.read_text(encoding="utf-8")
    assert result != ""


# ─── _write_atomic — success + failure cleanup ───────────────────────────────


def test_write_atomic_success(tmp_path):
    """Happy path: file is created with the exact content; no .tmp left behind."""
    dest = tmp_path / "sub" / "out.md"
    gen_lane_launches._write_atomic(dest, "hello atomic\n")
    assert dest.read_text() == "hello atomic\n"
    # No sibling temp file should survive a successful write.
    leftovers = list(dest.parent.glob(".tmp-*"))
    assert leftovers == [], f"leftover temp files: {leftovers}"


def test_write_atomic_cleans_up_tmp_on_failure(tmp_path, monkeypatch):
    """If os.replace fails mid-write, the temp file is unlinked and the error
    propagates (no orphaned .tmp-* sibling)."""
    dest = tmp_path / "out.md"

    def boom_replace(_src, _dst):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(gen_lane_launches.os, "replace", boom_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        gen_lane_launches._write_atomic(dest, "content that never lands\n")

    # Target was never created (replace failed) ...
    assert not dest.exists()
    # ... and the temp sibling was cleaned up in the except branch.
    leftovers = list(tmp_path.glob(".tmp-*"))
    assert leftovers == [], f"temp file not cleaned up: {leftovers}"


# ─── CLI config-absent fallback path ─────────────────────────────────────────


def test_main_config_absent_falls_back_to_generate_all(tmp_path, capsys):
    """`main()` with no .mission-config.yaml takes the legacy generate_all path,
    writing the hardcoded 6-lane set and printing one 'wrote' line per lane."""
    mission_dir = tmp_path / "mission"
    mission_dir.mkdir()
    out_dir = tmp_path / "out"
    assert not (mission_dir / ".mission-config.yaml").exists()

    rc = gen_lane_launches.main(
        ["--mission-dir", str(mission_dir), "--out-dir", str(out_dir)]
    )
    assert rc == 0

    names = sorted(p.name for p in out_dir.glob("launch-*.md"))
    assert names == [
        "launch-ARCHITECT.md",
        "launch-AUDIT.md",
        "launch-BACKEND.md",
        "launch-FRONTEND.md",
        "launch-META.md",
        "launch-TEST.md",
    ]
    # Legacy header shape (no HARNESS_CLI) confirms the generate_all branch ran.
    audit = (out_dir / "launch-AUDIT.md").read_text()
    assert "LANE: AUDIT" in audit
    assert "HARNESS_CLI" not in audit

    out = capsys.readouterr().out
    assert out.count("wrote ") == 6


def test_main_config_present_uses_config_driven_path(tmp_path, capsys):
    """`main()` with a real .mission-config.yaml takes generate_from_config,
    producing v9.1-header files named after the configured lanes."""
    out_dir = tmp_path / "out"
    rc = gen_lane_launches.main(
        ["--mission-dir", str(_FIXTURE_3_LANE), "--out-dir", str(out_dir)]
    )
    assert rc == 0
    names = sorted(p.name for p in out_dir.glob("launch-*.md"))
    assert names == ["launch-ALPHA.md", "launch-BETA.md", "launch-GAMMA.md"]
    alpha = (out_dir / "launch-ALPHA.md").read_text()
    assert "HARNESS_CLI: claude" in alpha
    out = capsys.readouterr().out
    assert out.count("wrote ") == 3
