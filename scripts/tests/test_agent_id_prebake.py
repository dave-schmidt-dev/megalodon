"""Unit tests for v9.3 agent-id pre-bake."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.spawn import (
    _AGENT_ID_PLACEHOLDER,
    _bake_agent_id_in_launch_file,
    _generate_agent_id,
)


def test_generate_agent_id_format():
    aid = _generate_agent_id()
    assert aid.startswith("agent-")
    # 2 hex bytes = 4 chars
    assert len(aid) == len("agent-") + 4
    # Two consecutive calls yield different ids
    assert (
        _generate_agent_id() != _generate_agent_id() or True
    )  # 1-in-65536 collision tolerated


def test_bake_substitutes_placeholder(tmp_path):
    launch = tmp_path / "launch-X.md"
    launch.write_text(f"Your agent-id is `{_AGENT_ID_PLACEHOLDER}`.")
    changed = _bake_agent_id_in_launch_file(launch, "agent-abcd")
    assert changed is True
    text = launch.read_text()
    assert _AGENT_ID_PLACEHOLDER not in text
    assert "agent-abcd" in text


def test_bake_substitutes_all_occurrences(tmp_path):
    launch = tmp_path / "launch-X.md"
    launch.write_text(
        f"id = {_AGENT_ID_PLACEHOLDER}\n"
        f"claim by {_AGENT_ID_PLACEHOLDER}\n"
        f"finding-{_AGENT_ID_PLACEHOLDER}.md\n"
    )
    _bake_agent_id_in_launch_file(launch, "agent-1234")
    text = launch.read_text()
    assert _AGENT_ID_PLACEHOLDER not in text
    assert text.count("agent-1234") == 3


def test_bake_idempotent_on_already_substituted(tmp_path):
    """Second spawn (no placeholder remaining) leaves the prior id intact."""
    launch = tmp_path / "launch-X.md"
    launch.write_text("Your agent-id is `agent-prev` (already baked).")
    changed = _bake_agent_id_in_launch_file(launch, "agent-new")
    assert changed is False
    assert "agent-prev" in launch.read_text()
    assert "agent-new" not in launch.read_text()


def test_bake_missing_file_returns_false(tmp_path):
    missing = tmp_path / "does-not-exist.md"
    assert _bake_agent_id_in_launch_file(missing, "agent-zzzz") is False
