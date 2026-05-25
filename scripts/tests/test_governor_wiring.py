"""Tests for the governor wiring helper + spawn-path kill-switch (Task 2.2).

Covers:
  * ``governor_settings_path()`` resolves the committed settings file.
  * ``governor_enabled(mission_config)`` reads the mission kill-switch (default True).
  * ``preflight_governor(mission_dir)`` fails LOUD with a specific error when the
    settings file is missing or the run-dir hook is missing/non-executable, and
    returns cleanly when both resolve.
  * kill-switch + preflight interaction: a disabled governor must not block spawn.

NOTE (Task 2.4): the runtime precedence guarantee — the ``permissions.deny`` floor
beats a hook ``allow`` — is a real Claude Code behavior that requires a live
``claude`` REPL. It is covered by Task 2.4's integration e2e, NOT here.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.governor.wiring import (
    GovernorPreflightError,
    governor_enabled,
    governor_kwargs,
    governor_settings_path,
    preflight_governor,
)
from megalodon_ui.mission_config.default_v9_0_shape import synthesize

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# governor_settings_path
# ---------------------------------------------------------------------------


def test_governor_settings_path_resolves_committed_file():
    p = governor_settings_path()
    assert p.is_absolute()
    assert p == REPO_ROOT / ".claude" / "governor-settings.json"
    assert p.exists(), "committed governor-settings.json must exist"
    # Valid JSON with the PreToolUse hook + deny floor.
    data = json.loads(p.read_text(encoding="utf-8"))
    assert "PreToolUse" in data["hooks"]
    assert any("governor-settings.json" in d for d in data["permissions"]["deny"])


# ---------------------------------------------------------------------------
# governor_enabled (kill-switch, default True)
# ---------------------------------------------------------------------------


def test_governor_enabled_default_true():
    cfg = synthesize(Path("/tmp"))
    assert governor_enabled(cfg) is True


def test_governor_enabled_false_when_flag_off():
    cfg = synthesize(Path("/tmp"))
    cfg.governor_enabled = False
    assert governor_enabled(cfg) is False


# ---------------------------------------------------------------------------
# governor_kwargs — the single-source gating helper
# ---------------------------------------------------------------------------


class _Harness:
    def __init__(self, cli: str):
        self.cli = cli


class _Lane:
    def __init__(self, cli: str):
        self.harness = _Harness(cli)


def test_governor_kwargs_enabled_claude_returns_settings():
    cfg = synthesize(Path("/tmp"))  # governor_enabled defaults True
    kw = governor_kwargs(cfg, _Lane("claude"))
    assert kw == {"governor_settings": governor_settings_path()}


def test_governor_kwargs_reuses_precomputed_settings_path():
    cfg = synthesize(Path("/tmp"))
    precomputed = Path("/precomputed/governor-settings.json")
    kw = governor_kwargs(cfg, _Lane("claude"), settings_path=precomputed)
    assert kw == {"governor_settings": precomputed}


def test_governor_kwargs_disabled_returns_empty():
    cfg = synthesize(Path("/tmp"))
    cfg.governor_enabled = False
    # Even with a precomputed path, a disabled governor yields no kwarg.
    assert governor_kwargs(cfg, _Lane("claude"), settings_path=Path("/x")) == {}


def test_governor_kwargs_non_claude_returns_empty():
    cfg = synthesize(Path("/tmp"))
    for cli in ("codex", "gemini", "copilot", "cursor", "vibe"):
        assert governor_kwargs(cfg, _Lane(cli)) == {}, cli


# ---------------------------------------------------------------------------
# preflight_governor — success
# ---------------------------------------------------------------------------


def _make_run_dir_with_symlink(tmp_path: Path) -> Path:
    """A run dir whose scripts/ symlinks the real repo scripts/ (as new_run.sh does)."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "scripts").symlink_to(REPO_ROOT / "scripts")
    return run_dir


def test_preflight_success(tmp_path: Path):
    run_dir = _make_run_dir_with_symlink(tmp_path)
    # Real hook must exist + be executable in the repo for the symlink to resolve.
    hook = REPO_ROOT / "scripts" / "governor_hook.py"
    assert hook.exists() and os.access(hook, os.X_OK), "hook must be executable"
    preflight_governor(run_dir)  # must not raise


# ---------------------------------------------------------------------------
# preflight_governor — fail loud
# ---------------------------------------------------------------------------


def test_preflight_fails_when_settings_missing(tmp_path: Path, monkeypatch):
    run_dir = _make_run_dir_with_symlink(tmp_path)
    # Point the settings path at a nonexistent file.
    missing = tmp_path / "nope" / "governor-settings.json"
    monkeypatch.setattr(
        "megalodon_ui.governor.wiring.governor_settings_path", lambda: missing
    )
    with pytest.raises(GovernorPreflightError) as exc:
        preflight_governor(run_dir)
    msg = str(exc.value)
    assert "governor-settings.json" in msg
    assert str(missing) in msg


def test_preflight_fails_when_settings_invalid_json(tmp_path: Path, monkeypatch):
    run_dir = _make_run_dir_with_symlink(tmp_path)
    bad = tmp_path / "bad-settings.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(
        "megalodon_ui.governor.wiring.governor_settings_path", lambda: bad
    )
    with pytest.raises(GovernorPreflightError) as exc:
        preflight_governor(run_dir)
    assert "valid JSON" in str(exc.value)


def test_preflight_fails_when_scripts_symlink_missing(tmp_path: Path):
    # Run dir with NO scripts/ symlink — simulates a broken new_run.sh scaffold.
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    with pytest.raises(GovernorPreflightError) as exc:
        preflight_governor(run_dir)
    msg = str(exc.value)
    assert "governor_hook.py" in msg
    assert "scripts/" in msg
    assert "new_run.sh" in msg  # error names the fix


def test_preflight_fails_when_hook_not_executable(tmp_path: Path):
    # Run dir with a real scripts/ dir but a non-executable hook.
    run_dir = tmp_path / "run"
    scripts = run_dir / "scripts"
    scripts.mkdir(parents=True)
    hook = scripts / "governor_hook.py"
    hook.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    hook.chmod(0o644)  # not executable
    with pytest.raises(GovernorPreflightError) as exc:
        preflight_governor(run_dir)
    assert "executable" in str(exc.value)


# ---------------------------------------------------------------------------
# kill-switch + preflight interaction (spawn-layer decision)
# ---------------------------------------------------------------------------


def _live_repl_config(*, governor_enabled_flag: bool):
    from megalodon_ui.mission_config.schema import MissionConfig

    return MissionConfig.model_validate(
        {
            "mission": {"id": "gov-killswitch", "utc_started": "2026-01-01T00:00:00Z"},
            "lanes": [
                {
                    "name": "LANEA",
                    "short": "A",
                    "role": "test role",
                    "harness": {"cli": "claude", "model": "claude-opus-4-7"},
                    "live_repl": True,
                }
            ],
            "phases": ["INIT"],
            "governor_enabled": governor_enabled_flag,
        }
    )


def _run_dir_with_symlink(tmp_path: Path) -> Path:
    """Run dir with a working scripts/ symlink so preflight passes when enabled."""
    (tmp_path / "scripts").symlink_to(REPO_ROOT / "scripts")
    return tmp_path


async def _capture_spawn_argv(tmp_path: Path, *, governor_enabled_flag: bool):
    from unittest.mock import AsyncMock, MagicMock, patch

    from megalodon_ui.harnesses.claude import ClaudeAdapter
    from megalodon_ui.spawn import FleetSpawner

    run_dir = _run_dir_with_symlink(tmp_path)
    config = _live_repl_config(governor_enabled_flag=governor_enabled_flag)
    resolver = MagicMock(return_value=ClaudeAdapter())
    spawner = FleetSpawner(run_dir, config, resolver, Path("/tmp/gov-killswitch.sock"))

    captured: list[str] = []

    async def capture_new_session(**kwargs):
        captured.extend(kwargs["argv"])
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
    return captured


@pytest.mark.asyncio
async def test_killswitch_on_spawn_passes_settings(tmp_path: Path):
    """Default (governor_enabled=True) → spawn argv carries --settings <path>,
    and the --allowedTools allowlist is still present (additive)."""
    argv = await _capture_spawn_argv(tmp_path, governor_enabled_flag=True)
    assert "--settings" in argv
    assert argv[argv.index("--settings") + 1] == str(governor_settings_path())
    assert "--allowedTools" in argv  # allowlist untouched


@pytest.mark.asyncio
async def test_killswitch_off_spawn_omits_settings(tmp_path: Path):
    """governor_enabled=False → spawn argv has NO --settings (governor_settings=None)."""
    argv = await _capture_spawn_argv(tmp_path, governor_enabled_flag=False)
    assert "--settings" not in argv
    assert "--allowedTools" in argv  # allowlist still present


def test_killswitch_disabled_skips_preflight(tmp_path: Path):
    """A disabled governor must not block spawn on a missing hook.

    The spawn layer only calls preflight_governor when governor_enabled is True;
    here we assert the decision predicate so the spawn wiring can guard on it.
    """
    cfg = synthesize(Path("/tmp"))
    cfg.governor_enabled = False
    assert governor_enabled(cfg) is False
    # The run dir is broken (no symlink), but because the governor is disabled
    # the spawn layer never calls preflight — so a broken hook is irrelevant.
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    # Sanity: preflight WOULD fail if called, proving the skip matters.
    with pytest.raises(GovernorPreflightError):
        preflight_governor(run_dir)
