"""Governor canary self-test (Task 2.3) — turn silent non-enforcement loud.

Two complementary layers are tested here (the third, the agent-side runtime
canary in launch-*.md, is prose validated at Task 2.4's manual REPL gate):

  * Layer 1 — policy sentinel: see ``test_governor_policy.py`` for the unit
    deny/allow matrix. This file re-checks the canary helper round-trips through
    the REAL run-dir shim.
  * Layer 2 — ``governor_canary_selftest(mission_dir)``: pipes the sentinel
    PreToolUse event through the run-dir shim EXACTLY as claude will, and raises
    ``GovernorCanaryError`` (loud) if the governor does not actually deny.

Distinct from preflight: preflight proves the hook is REACHABLE; the canary
self-test proves it actually DENIES.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.governor.policy import (
    GOVERNOR_CANARY_TOKEN,
    canary_command,
)
from megalodon_ui.governor.wiring import (
    GovernorCanaryError,
    governor_canary_selftest,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _real_run_dir(tmp_path: Path) -> Path:
    """A run dir whose scripts/ symlinks the real repo scripts/ (new_run.sh:77)."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "scripts").symlink_to(REPO_ROOT / "scripts")
    return run_dir


def _stub_run_dir(tmp_path: Path, hook_body: str, *, executable: bool = True) -> Path:
    """A run dir with a REAL scripts/ dir holding a STUB governor_hook.py.

    Lets us simulate a non-enforcing governor WITHOUT weakening the real shim.
    """
    run_dir = tmp_path / "run"
    scripts = run_dir / "scripts"
    scripts.mkdir(parents=True)
    hook = scripts / "governor_hook.py"
    hook.write_text(hook_body, encoding="utf-8")
    if executable:
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return run_dir


_ALLOW_HOOK = """\
#!/usr/bin/env python3
import json, sys
sys.stdin.read()
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow",
    "permissionDecisionReason": "stub allows everything",
}}))
"""

_MALFORMED_HOOK = """\
#!/usr/bin/env python3
import sys
sys.stdin.read()
print("this is not json at all")
"""

_NONZERO_HOOK = """\
#!/usr/bin/env python3
import sys
sys.stdin.read()
sys.stderr.write("boom\\n")
sys.exit(3)
"""

_WRONG_DENY_HOOK = """\
#!/usr/bin/env python3
import json, sys
sys.stdin.read()
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "denied for some UNRELATED reason",
}}))
"""


# ---------------------------------------------------------------------------
# Self-test PASSES with the real shim
# ---------------------------------------------------------------------------


def test_selftest_passes_with_real_shim(tmp_path: Path):
    """The real run-dir shim denies the sentinel → self-test returns cleanly."""
    run_dir = _real_run_dir(tmp_path)
    hook = REPO_ROOT / "scripts" / "governor_hook.py"
    assert hook.exists() and os.access(hook, os.X_OK), "real hook must be executable"
    governor_canary_selftest(run_dir)  # must not raise


# ---------------------------------------------------------------------------
# Self-test ALARMS when the governor isn't enforcing (stub shims, real shim
# untouched)
# ---------------------------------------------------------------------------


def test_selftest_alarms_on_allow_decision(tmp_path: Path):
    run_dir = _stub_run_dir(tmp_path, _ALLOW_HOOK)
    with pytest.raises(GovernorCanaryError) as exc:
        governor_canary_selftest(run_dir)
    msg = str(exc.value)
    assert "allow" in msg
    assert "not enforcing" in msg.lower()


def test_selftest_alarms_on_malformed_stdout(tmp_path: Path):
    run_dir = _stub_run_dir(tmp_path, _MALFORMED_HOOK)
    with pytest.raises(GovernorCanaryError) as exc:
        governor_canary_selftest(run_dir)
    assert "malformed" in str(exc.value).lower()


def test_selftest_alarms_on_nonzero_exit(tmp_path: Path):
    run_dir = _stub_run_dir(tmp_path, _NONZERO_HOOK)
    with pytest.raises(GovernorCanaryError) as exc:
        governor_canary_selftest(run_dir)
    msg = str(exc.value)
    assert "non-zero" in msg.lower()
    assert "rc=3" in msg


def test_selftest_alarms_on_deny_for_wrong_reason(tmp_path: Path):
    """A deny that is NOT the canary deny must still alarm (enforcement unproven)."""
    run_dir = _stub_run_dir(tmp_path, _WRONG_DENY_HOOK)
    with pytest.raises(GovernorCanaryError) as exc:
        governor_canary_selftest(run_dir)
    assert "canary" in str(exc.value).lower()


def test_selftest_alarms_when_hook_missing(tmp_path: Path):
    """No scripts/ symlink at all → the hook cannot execute → alarm."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    with pytest.raises(GovernorCanaryError):
        governor_canary_selftest(run_dir)


# ---------------------------------------------------------------------------
# The probe round-trips through the real shim as the canary deny
# ---------------------------------------------------------------------------


def test_real_shim_denies_canary_event(tmp_path: Path):
    """Manual proof in-test: feed the canary event to the real shim, see deny."""
    import subprocess

    run_dir = _real_run_dir(tmp_path)
    shim = run_dir / "scripts" / "governor_hook.py"
    event = json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": canary_command()},
            "cwd": str(run_dir),
        }
    )
    proc = subprocess.run(
        [str(shim)],
        input=event,
        capture_output=True,
        text=True,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(run_dir)},
        timeout=15,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    assert "canary" in out["permissionDecisionReason"].lower()
    assert GOVERNOR_CANARY_TOKEN  # token is the single source of truth


# ---------------------------------------------------------------------------
# Spawn integration — start_all runs the self-test (proceeds / aborts loudly)
# ---------------------------------------------------------------------------


def _live_repl_config(*, governor_enabled_flag: bool = True):
    from megalodon_ui.mission_config.schema import MissionConfig

    return MissionConfig.model_validate(
        {
            "mission": {"id": "gov-canary", "utc_started": "2026-01-01T00:00:00Z"},
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


async def _run_start_all(run_dir: Path, config):
    from unittest.mock import AsyncMock, MagicMock, patch

    from megalodon_ui.harnesses.claude import ClaudeAdapter
    from megalodon_ui.spawn import FleetSpawner

    resolver = MagicMock(return_value=ClaudeAdapter())
    spawner = FleetSpawner(run_dir, config, resolver, run_dir / ".fleet" / "tmux.sock")

    spawned: list[str] = []

    async def capture_new_session(**kwargs):
        spawned.append(kwargs.get("name", "?"))
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
    return spawned


@pytest.mark.asyncio
async def test_spawn_runs_canary_and_proceeds(tmp_path: Path):
    """Governor enabled + real shim → self-test passes, lanes spawn.

    Also asserts the canary self-test was actually INVOKED during start_all
    (a spy wrapping the real function), so removing the canary call from
    start_all fails here instead of silently passing because spawning still
    succeeds.
    """
    from unittest.mock import patch

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "scripts").symlink_to(REPO_ROOT / "scripts")
    with patch(
        "megalodon_ui.spawn.governor_canary_selftest",
        wraps=governor_canary_selftest,
    ) as canary_spy:
        spawned = await _run_start_all(
            run_dir, _live_repl_config(governor_enabled_flag=True)
        )
    assert spawned, "lanes should spawn when the governor is enforcing"
    assert canary_spy.called, (
        "start_all must invoke governor_canary_selftest before spawning lanes"
    )


@pytest.mark.asyncio
async def test_spawn_aborts_loudly_when_governor_not_enforcing(tmp_path: Path):
    """A non-enforcing (stub-allow) governor → start_all raises, NO lane spawns."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from megalodon_ui.harnesses.claude import ClaudeAdapter
    from megalodon_ui.spawn import FleetSpawner

    run_dir = _stub_run_dir(tmp_path, _ALLOW_HOOK)
    config = _live_repl_config(governor_enabled_flag=True)
    resolver = MagicMock(return_value=ClaudeAdapter())
    spawner = FleetSpawner(run_dir, config, resolver, run_dir / ".fleet" / "tmux.sock")

    spawned: list[str] = []

    async def capture_new_session(**kwargs):
        spawned.append(kwargs.get("name", "?"))
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
        with pytest.raises(GovernorCanaryError):
            await spawner.start_all()
    assert spawned == [], "no lane may spawn once the canary self-test fails"
