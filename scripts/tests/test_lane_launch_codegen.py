"""V9 A2 — tests for per-lane launch file codegen."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import gen_lane_launches


def test_generates_6_files(tmp_path):
    gen_lane_launches.generate_all(tmp_path)
    files = sorted(tmp_path.glob("launch-*.md"))
    names = [f.name for f in files]
    assert names == [
        "launch-ARCHITECT.md",
        "launch-AUDIT.md",
        "launch-BACKEND.md",
        "launch-FRONTEND.md",
        "launch-META.md",
        "launch-TEST.md",
    ]


def test_header_has_lane(tmp_path):
    gen_lane_launches.generate_all(tmp_path)
    text = (tmp_path / "launch-AUDIT.md").read_text()
    assert "LANE: AUDIT" in text


def test_body_includes_launch_md_content(tmp_path):
    gen_lane_launches.generate_all(tmp_path)
    text = (tmp_path / "launch-AUDIT.md").read_text()
    # launch.md has at least the heading or RULE structure
    assert "## " in text or "# " in text


def test_offset_increases_per_lane(tmp_path):
    gen_lane_launches.generate_all(tmp_path)
    audit = (tmp_path / "launch-AUDIT.md").read_text()
    backend = (tmp_path / "launch-BACKEND.md").read_text()
    # AUDIT = 0, BACKEND = 90 (index 2 × 45)
    assert "TICK_OFFSET_SECONDS: 0" in audit
    assert "TICK_OFFSET_SECONDS: 90" in backend


def test_model_hint_uses_claude_alias(tmp_path):
    """Regression: MODEL_HINT must be a valid `claude --model` argument.

    The previous "sonnet-4.6" / "opus-4.7" labels looked like model IDs but
    are not accepted by the CLI (claude --help: aliases sonnet/opus/haiku, or
    canonical like claude-sonnet-4-6). Spawned panes ran `claude --model X`
    directly, so the bad strings broke at the shell.
    """
    gen_lane_launches.generate_all(tmp_path)
    audit = (tmp_path / "launch-AUDIT.md").read_text()
    backend = (tmp_path / "launch-BACKEND.md").read_text()
    meta = (tmp_path / "launch-META.md").read_text()
    assert "MODEL_HINT: sonnet" in audit
    assert "MODEL_HINT: opus" in backend
    assert "MODEL_HINT: sonnet" in meta
    # And the broken strings must not reappear in any generated file.
    for lane_file in tmp_path.glob("launch-*.md"):
        text = lane_file.read_text()
        assert "sonnet-4.6" not in text, f"{lane_file.name}: stale model label"
        assert "opus-4.7" not in text, f"{lane_file.name}: stale model label"
