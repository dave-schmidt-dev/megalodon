"""v9.4 T3.3 — approval-rules wired into spawn (PM-8).

Four cases:
1. 2 rules merged: patterns appear in --allowedTools AFTER the static allowlist.
2. No rules file: argv has static allowlist only (regression test).
3. Corrupt rules file: spawn warns and uses static allowlist only.
4. Empty rules list: file with {rules: []} behaves like no-file.

Note: the file format is a raw JSON *list* (not wrapped in {"rules": ...}).
The GET endpoint wraps it; the file itself is the bare list.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from megalodon_ui.harnesses.claude import ClaudeAdapter
from megalodon_ui.mission_config.schema import MissionConfig
from megalodon_ui.spawn import FleetSpawner

SOCKET = Path("/tmp/test-approval-rules.sock")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_live_repl_config(lane_short: str = "A") -> MissionConfig:
    """Single live_repl lane so --allowedTools is relevant."""
    return MissionConfig.model_validate(
        {
            "mission": {
                "id": "test-approval-rules",
                "utc_started": "2026-01-01T00:00:00Z",
            },
            "lanes": [
                {
                    "name": f"LANE{lane_short}",
                    "short": lane_short,
                    "role": "test role",
                    "harness": {"cli": "claude", "model": "claude-opus-4-7"},
                    "cadence_seconds": 300,
                    "tick_offset_seconds": 0,
                    "live_repl": True,
                    "initial_prompt": None,
                }
            ],
            "phases": ["INIT"],
        }
    )


def _make_real_resolver() -> MagicMock:
    """Resolver that returns a real ClaudeAdapter so build_argv is exercised."""
    adapter = ClaudeAdapter()
    # Wrap in a MagicMock so the resolver call itself is trackable, but the
    # adapter methods are the real implementations.
    resolver = MagicMock(return_value=adapter)
    return resolver


def _patch_spawn_success():
    """Context-manager stack: patch tmux calls so spawn completes without error."""
    return (
        patch("megalodon_ui.spawn.tmux.list_sessions", new=AsyncMock(return_value=[])),
        patch("megalodon_ui.spawn.tmux.new_session", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn.tmux.kill_session", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
        patch(
            "megalodon_ui.spawn._discover_session_id", new=AsyncMock(return_value=None)
        ),
        patch("megalodon_ui.spawn.FleetSpawner._start_tail_task", new=AsyncMock()),
    )


def _get_allowed_value(captured_argv: list[str]) -> str:
    """Extract the --allowedTools string from a captured argv list."""
    idx = captured_argv.index("--allowedTools")
    return captured_argv[idx + 1]


# ---------------------------------------------------------------------------
# Case 1: 2 rules merged — both patterns appear after the static allowlist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_rules_merged_into_allowedtools(tmp_path: Path):
    """Two entries in approval-rules.json must appear in --allowedTools value."""
    # Write the rules file (raw JSON list — not wrapped in {rules: ...}).
    rules = [
        {
            "pattern": "Bash(curl -s http://127.0.0.1:8765/*)",
            "added_at_utc": "2026-05-20T00:00:00+00:00",
            "added_by_session": "sess-abc",
        },
        {
            "pattern": "Bash(find:*)",
            "added_at_utc": "2026-05-20T00:01:00+00:00",
            "added_by_session": "sess-abc",
        },
    ]
    fleet_dir = tmp_path / ".fleet"
    fleet_dir.mkdir(parents=True, exist_ok=True)
    (fleet_dir / "approval-rules.json").write_text(json.dumps(rules), encoding="utf-8")

    config = _make_live_repl_config("A")
    resolver = _make_real_resolver()
    spawner = FleetSpawner(tmp_path, config, resolver, SOCKET)

    captured_argv: list[str] = []

    async def capture_new_session(**kwargs):
        captured_argv.extend(kwargs["argv"])
        return 0

    with (
        patch("megalodon_ui.spawn.tmux.list_sessions", new=AsyncMock(return_value=[])),
        patch(
            "megalodon_ui.spawn.tmux.new_session",
            new=AsyncMock(side_effect=capture_new_session),
        ),
        patch("megalodon_ui.spawn.tmux.kill_session", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
        patch(
            "megalodon_ui.spawn._discover_session_id", new=AsyncMock(return_value=None)
        ),
        patch("megalodon_ui.spawn.FleetSpawner._start_tail_task", new=AsyncMock()),
    ):
        await spawner.start_all()

    assert "--allowedTools" in captured_argv, "Expected --allowedTools in spawn argv"
    allowed = _get_allowed_value(captured_argv)

    # Static allowlist entries must still be present.
    assert "Bash(mkdir claims/*)" in allowed, (
        "Static allowlist missing from merged argv"
    )
    assert "Bash(curl -s http://127.0.0.1*)" in allowed, "Static localhost curl missing"
    assert "Read" in allowed, "Static Read tool missing"

    # Both operator-approved patterns must appear AFTER the static allowlist.
    assert "Bash(curl -s http://127.0.0.1:8765/*)" in allowed, (
        f"First approval-rule pattern missing from --allowedTools: {allowed!r}"
    )
    assert "Bash(find:*)" in allowed, (
        f"Second approval-rule pattern missing from --allowedTools: {allowed!r}"
    )

    # Confirm ordering: extra patterns come after the static ones.
    static_end = allowed.rindex("Bash(npm run test*)")
    curl_pos = allowed.index("Bash(curl -s http://127.0.0.1:8765/*)")
    find_pos = allowed.index("Bash(find:*)")
    assert curl_pos > static_end, (
        "First extra pattern must appear after static allowlist"
    )
    assert find_pos > static_end, (
        "Second extra pattern must appear after static allowlist"
    )


# ---------------------------------------------------------------------------
# Case 2: No rules file — static allowlist only (regression)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_rules_file_uses_static_allowlist_only(tmp_path: Path):
    """When no approval-rules.json exists, spawn behaves exactly as before."""
    fleet_dir = tmp_path / ".fleet"
    fleet_dir.mkdir(parents=True, exist_ok=True)
    # Explicitly ensure the file is absent.
    rules_file = fleet_dir / "approval-rules.json"
    rules_file.unlink(missing_ok=True)

    config = _make_live_repl_config("B")
    resolver = _make_real_resolver()
    spawner = FleetSpawner(tmp_path, config, resolver, SOCKET)

    # Capture argv from a no-rules baseline using ClaudeAdapter directly.
    baseline_adapter = ClaudeAdapter()
    baseline_argv, _ = baseline_adapter.build_argv(
        "test role",
        model="claude-opus-4-7",
        cwd=tmp_path,
        live_repl=True,
    )

    captured_argv: list[str] = []

    async def capture_new_session(**kwargs):
        captured_argv.extend(kwargs["argv"])
        return 0

    with (
        patch("megalodon_ui.spawn.tmux.list_sessions", new=AsyncMock(return_value=[])),
        patch(
            "megalodon_ui.spawn.tmux.new_session",
            new=AsyncMock(side_effect=capture_new_session),
        ),
        patch("megalodon_ui.spawn.tmux.kill_session", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
        patch(
            "megalodon_ui.spawn._discover_session_id", new=AsyncMock(return_value=None)
        ),
        patch("megalodon_ui.spawn.FleetSpawner._start_tail_task", new=AsyncMock()),
    ):
        await spawner.start_all()

    assert "--allowedTools" in captured_argv
    allowed = _get_allowed_value(captured_argv)
    baseline_allowed = _get_allowed_value(baseline_argv)

    # With no rules file the allowed string must be identical to the baseline.
    assert allowed == baseline_allowed, (
        f"Expected identical allowlist when no rules file.\n"
        f"  got:      {allowed!r}\n"
        f"  baseline: {baseline_allowed!r}"
    )


# ---------------------------------------------------------------------------
# Case 3: Corrupt rules file — warn and proceed with static allowlist only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_corrupt_rules_file_warns_and_uses_static_allowlist(
    tmp_path: Path, caplog
):
    """Invalid JSON in approval-rules.json triggers WARNING and falls back gracefully."""
    fleet_dir = tmp_path / ".fleet"
    fleet_dir.mkdir(parents=True, exist_ok=True)
    (fleet_dir / "approval-rules.json").write_text(
        "this is not valid json {{{", encoding="utf-8"
    )

    config = _make_live_repl_config("C")
    resolver = _make_real_resolver()
    spawner = FleetSpawner(tmp_path, config, resolver, SOCKET)

    baseline_adapter = ClaudeAdapter()
    baseline_argv, _ = baseline_adapter.build_argv(
        "test role",
        model="claude-opus-4-7",
        cwd=tmp_path,
        live_repl=True,
    )

    captured_argv: list[str] = []

    async def capture_new_session(**kwargs):
        captured_argv.extend(kwargs["argv"])
        return 0

    with caplog.at_level(logging.WARNING, logger="megalodon_ui.spawn"):
        with (
            patch(
                "megalodon_ui.spawn.tmux.list_sessions", new=AsyncMock(return_value=[])
            ),
            patch(
                "megalodon_ui.spawn.tmux.new_session",
                new=AsyncMock(side_effect=capture_new_session),
            ),
            patch(
                "megalodon_ui.spawn.tmux.kill_session", new=AsyncMock(return_value=0)
            ),
            patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
            patch(
                "megalodon_ui.spawn._discover_session_id",
                new=AsyncMock(return_value=None),
            ),
            patch("megalodon_ui.spawn.FleetSpawner._start_tail_task", new=AsyncMock()),
        ):
            await spawner.start_all()

    # A warning must have been logged mentioning the failure.
    assert any("approval-rules" in r.message.lower() for r in caplog.records), (
        f"Expected an approval-rules warning; got: {[r.message for r in caplog.records]}"
    )

    # Spawn must have proceeded — argv captured.
    assert "--allowedTools" in captured_argv
    allowed = _get_allowed_value(captured_argv)
    baseline_allowed = _get_allowed_value(baseline_argv)

    # Must fall back to static allowlist only (no extra patterns appended).
    assert allowed == baseline_allowed, (
        f"Expected static allowlist only after corrupt file.\n"
        f"  got:      {allowed!r}\n"
        f"  baseline: {baseline_allowed!r}"
    )


# ---------------------------------------------------------------------------
# Case 4: Empty rules list — behaves like no-file case
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_rules_list_behaves_like_no_file(tmp_path: Path):
    """A file with an empty JSON list [] produces the static allowlist only."""
    fleet_dir = tmp_path / ".fleet"
    fleet_dir.mkdir(parents=True, exist_ok=True)
    (fleet_dir / "approval-rules.json").write_text("[]", encoding="utf-8")

    config = _make_live_repl_config("D")
    resolver = _make_real_resolver()
    spawner = FleetSpawner(tmp_path, config, resolver, SOCKET)

    baseline_adapter = ClaudeAdapter()
    baseline_argv, _ = baseline_adapter.build_argv(
        "test role",
        model="claude-opus-4-7",
        cwd=tmp_path,
        live_repl=True,
    )

    captured_argv: list[str] = []

    async def capture_new_session(**kwargs):
        captured_argv.extend(kwargs["argv"])
        return 0

    with (
        patch("megalodon_ui.spawn.tmux.list_sessions", new=AsyncMock(return_value=[])),
        patch(
            "megalodon_ui.spawn.tmux.new_session",
            new=AsyncMock(side_effect=capture_new_session),
        ),
        patch("megalodon_ui.spawn.tmux.kill_session", new=AsyncMock(return_value=0)),
        patch("megalodon_ui.spawn.tmux.pipe_pane", new=AsyncMock(return_value=0)),
        patch(
            "megalodon_ui.spawn._discover_session_id", new=AsyncMock(return_value=None)
        ),
        patch("megalodon_ui.spawn.FleetSpawner._start_tail_task", new=AsyncMock()),
    ):
        await spawner.start_all()

    assert "--allowedTools" in captured_argv
    allowed = _get_allowed_value(captured_argv)
    baseline_allowed = _get_allowed_value(baseline_argv)

    assert allowed == baseline_allowed, (
        f"Expected static allowlist only for empty rules list.\n"
        f"  got:      {allowed!r}\n"
        f"  baseline: {baseline_allowed!r}"
    )
