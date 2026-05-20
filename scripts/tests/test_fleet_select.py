"""V9 A3 — tests for per-lane model selection with override support."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.fleet_select import select


def test_default_lookup(tmp_path):
    assert select("AUDIT", tmp_path) == "sonnet-4.6"
    assert select("BACKEND", tmp_path) == "opus-4.7"


def test_override_file_takes_precedence(tmp_path):
    scratch = tmp_path / ".scratch"
    scratch.mkdir()
    (scratch / "fleet-matrix-override.json").write_text(
        json.dumps({"lanes": {"AUDIT": {"model": "haiku-4.5"}}})
    )
    assert select("AUDIT", tmp_path) == "haiku-4.5"


def test_unknown_lane_returns_default():
    assert select("OBSERVER-7", Path("/tmp")) == "opus-4.7"
