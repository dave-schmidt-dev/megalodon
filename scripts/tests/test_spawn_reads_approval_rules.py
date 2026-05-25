"""Task 3.3 — spawn no longer reads approval-rules; the governor is the sole consumer.

Before Task 3.3, FleetSpawner loaded ``.fleet/approval-rules.json`` and plumbed the
operator patterns into a ``--allowedTools`` allowlist (filtered through the now-removed
``_is_unbounded_tool``). That whole path is gone: the governor's ``policy.decide`` reads
approval-rules directly as an audited allow-override. The migration safety net for that
behavior lives in ``test_approval_rules_migration_audit.py``.

These tests now assert the NEW reality at the spawn boundary, regardless of the
approval-rules file's state:
  * spawn's build_argv argv carries ``--settings`` (governor wiring intact), and
  * NEVER carries ``--allowedTools`` (no allowlist), and
  * the spawner never passes ``extra_allowed_tools`` to build_argv.

Note: the file format is a raw JSON *list* (not wrapped in {"rules": ...}).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from megalodon_ui.harnesses.claude import ClaudeAdapter
from megalodon_ui.mission_config.schema import MissionConfig
from megalodon_ui.spawn import FleetSpawner

SOCKET = Path("/tmp/test-approval-rules.sock")
REPO_ROOT = Path(__file__).resolve().parents[2]


def _link_scripts(run_dir: Path) -> None:
    """Mirror new_run.sh: symlink scripts/ into the run dir so the governor
    preflight (Task 2.2, default-on) resolves the hook and spawn proceeds.

    These tests run the real ClaudeAdapter with the default governor (enabled),
    so each spawn argv carries --settings — which is exactly what we assert.
    """
    link = run_dir / "scripts"
    if not link.exists():
        link.symlink_to(REPO_ROOT / "scripts")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_live_repl_config(lane_short: str = "A") -> MissionConfig:
    """Single live_repl lane."""
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
    resolver = MagicMock(return_value=adapter)
    return resolver


async def _run_spawn_capture_argv(spawner: FleetSpawner) -> list[str]:
    """Run start_all with tmux patched out, capturing the spawned argv."""
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
    return captured_argv


def _assert_governed_no_allowlist(argv: list[str]) -> None:
    """The spawned live_repl argv carries the governor --settings flag and has
    NO --allowedTools / no extra_allowed_tools plumbing."""
    assert "--settings" in argv, f"governor --settings missing from argv: {argv!r}"
    assert "--allowedTools" not in argv, (
        f"--allowedTools must be gone (Task 3.3): {argv!r}"
    )
    # The bare live_repl argv shape: claude --model <id> --settings <path>.
    assert argv[:3] == ["claude", "--model", "claude-opus-4-7"], argv
    assert "--print" not in argv  # live_repl has no --print


# ---------------------------------------------------------------------------
# Approval-rules file present (bounded + unbounded) — spawn ignores it entirely.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_ignores_approval_rules_file(tmp_path: Path):
    """A populated approval-rules.json (bounded AND unbounded patterns) does NOT
    flow into the spawn argv — spawn no longer reads it; the governor does."""
    rules = [
        {"pattern": "Bash(curl -s http://127.0.0.1:8765/*)"},
        {"pattern": "Bash(find:*)"},
        {"pattern": "Bash(scripts/custom_tool.sh:*)"},
        {"pattern": "Read"},
    ]
    fleet_dir = tmp_path / ".fleet"
    fleet_dir.mkdir(parents=True, exist_ok=True)
    _link_scripts(tmp_path)
    (fleet_dir / "approval-rules.json").write_text(json.dumps(rules), encoding="utf-8")

    spawner = FleetSpawner(
        tmp_path, _make_live_repl_config("A"), _make_real_resolver(), SOCKET
    )
    argv = await _run_spawn_capture_argv(spawner)

    _assert_governed_no_allowlist(argv)
    # None of the operator patterns leak into argv (no allowlist at all).
    joined = " ".join(argv)
    for pat in ["curl", "find", "custom_tool.sh"]:
        assert pat not in joined, f"approval-rule pattern leaked into argv: {pat}"


# ---------------------------------------------------------------------------
# No approval-rules file — identical governed argv (no regression, no read).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_no_rules_file_same_governed_argv(tmp_path: Path):
    """Absent approval-rules.json: spawn argv is still governed, still no allowlist."""
    fleet_dir = tmp_path / ".fleet"
    fleet_dir.mkdir(parents=True, exist_ok=True)
    _link_scripts(tmp_path)
    (fleet_dir / "approval-rules.json").unlink(missing_ok=True)

    spawner = FleetSpawner(
        tmp_path, _make_live_repl_config("B"), _make_real_resolver(), SOCKET
    )
    argv = await _run_spawn_capture_argv(spawner)

    _assert_governed_no_allowlist(argv)


# ---------------------------------------------------------------------------
# Corrupt approval-rules file — spawn unaffected (it doesn't read the file).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_corrupt_rules_file_does_not_affect_argv(tmp_path: Path):
    """Invalid JSON no longer matters at the spawn boundary — spawn never reads it,
    so there is no warning and no fallback; the argv is governed with no allowlist."""
    fleet_dir = tmp_path / ".fleet"
    fleet_dir.mkdir(parents=True, exist_ok=True)
    _link_scripts(tmp_path)
    (fleet_dir / "approval-rules.json").write_text(
        "this is not valid json {{{", encoding="utf-8"
    )

    spawner = FleetSpawner(
        tmp_path, _make_live_repl_config("C"), _make_real_resolver(), SOCKET
    )
    argv = await _run_spawn_capture_argv(spawner)

    _assert_governed_no_allowlist(argv)


# ---------------------------------------------------------------------------
# The loader symbol is gone (regression guard for the removal).
# ---------------------------------------------------------------------------


def test_spawn_loader_symbol_removed():
    """Task 3.3 deleted _load_approval_rule_patterns / _PATTERN_RE from spawn."""
    import megalodon_ui.spawn as spawn_mod

    assert not hasattr(spawn_mod, "_load_approval_rule_patterns")
    assert not hasattr(spawn_mod, "_PATTERN_RE")
