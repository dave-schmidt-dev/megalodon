"""P3.7 — coverage for megalodon_ui.preflight.__main__.main().

The preflight CLI entrypoint wires argparse → GOAL validation → auth-env check
(MOCK_CLAUDE=1 bypass) → dir resolution / existing-file refusal → preamble load
→ signal handlers → run_interview → atomic write. The interview REPL itself is
covered by test_preflight_interview.py; here we exercise main()'s own control
flow by injecting a deterministic run_interview so no real Claude subprocess
runs.
"""

from __future__ import annotations

import signal
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from megalodon_ui.mission_config.schema import MissionConfig
from megalodon_ui.preflight import __main__ as preflight_main


_VALID_YAML = textwrap.dedent("""\
    schema_version: 1
    mission:
      id: preflight-main-test
      utc_started: '2026-01-01T00:00:00Z'
      type: software-engineering
      description: ''
    lanes:
      - name: BACKEND
        short: A
        role: ''
        harness:
          cli: claude
          model: claude-opus-4-7
          extra_args: []
          auth_env: []
        cadence_seconds: 300
        tick_offset_seconds: 0
    phases:
      - INIT
      - COMPLETE
""")


def _valid_config() -> MissionConfig:
    return MissionConfig.model_validate(yaml.safe_load(_VALID_YAML))


@pytest.fixture
def _restore_signal_handlers():
    """main() installs SIGINT/SIGTERM handlers; restore originals afterward."""
    orig_int = signal.getsignal(signal.SIGINT)
    orig_term = signal.getsignal(signal.SIGTERM)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, orig_int)
        signal.signal(signal.SIGTERM, orig_term)


def test_main_empty_goal_returns_1(tmp_path, monkeypatch, capsys):
    """A whitespace-only GOAL fails validation (exit 1) before any auth/IO."""
    monkeypatch.setenv("MOCK_CLAUDE", "1")
    rc = preflight_main.main(["   ", "--mission-dir", str(tmp_path)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "GOAL" in err


def test_main_missing_auth_returns_1(tmp_path, monkeypatch, capsys):
    """Without MOCK_CLAUDE and without ANTHROPIC_API_KEY, the auth check exits 1."""
    monkeypatch.delenv("MOCK_CLAUDE", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rc = preflight_main.main(["build a thing", "--mission-dir", str(tmp_path)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "ANTHROPIC_API_KEY" in err


def test_main_existing_config_without_force_returns_1(tmp_path, monkeypatch, capsys):
    """An existing .mission-config.yaml without --force is refused (exit 1)."""
    monkeypatch.setenv("MOCK_CLAUDE", "1")
    (tmp_path / ".mission-config.yaml").write_text("schema_version: 1\n")
    rc = preflight_main.main(["build a thing", "--mission-dir", str(tmp_path)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "already exists" in err


def test_main_happy_path_writes_config(
    tmp_path, monkeypatch, capsys, _restore_signal_handlers
):
    """MOCK_CLAUDE=1 + an approved config from run_interview → atomic write, exit 0.

    Patches the lazily-imported run_interview so main()'s real flow (auth bypass,
    dir resolution, preamble load, signal-handler install, write_atomic) runs end
    to end without a Claude subprocess.
    """
    monkeypatch.setenv("MOCK_CLAUDE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    captured = {}

    def fake_run_interview(goal, preamble, max_refine):
        captured["goal"] = goal
        captured["max_refine"] = max_refine
        return _valid_config(), None

    # main() does `from megalodon_ui.preflight.interview import run_interview`,
    # so patch the name on that module.
    monkeypatch.setattr(
        "megalodon_ui.preflight.interview.run_interview", fake_run_interview
    )

    # README provides a non-empty preamble so _load_preamble's read branch runs.
    (tmp_path / "README.md").write_text("# Context\nsome project notes\n")

    rc = preflight_main.main(
        ["build a backend service", "--mission-dir", str(tmp_path), "--max-refine", "3"]
    )

    assert rc == 0
    out = capsys.readouterr().out
    target = tmp_path / ".mission-config.yaml"
    assert target.exists(), "config file was not written"
    assert "wrote " in out
    # main() forwarded the real GOAL (stripped) + max_refine into run_interview.
    assert captured["goal"] == "build a backend service"
    assert captured["max_refine"] == 3
    # Written YAML round-trips back to the approved config's mission id.
    written = yaml.safe_load(target.read_text())
    assert written["mission"]["id"] == "preflight-main-test"


def test_main_abandoned_interview_returns_1(
    tmp_path, monkeypatch, _restore_signal_handlers
):
    """When run_interview abandons (None config), main() returns 1 and writes a
    snapshot of the last draft rather than a real config."""
    monkeypatch.setenv("MOCK_CLAUDE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def fake_run_interview(goal, preamble, max_refine):
        return None, "schema_version: 1  # draft\n"

    monkeypatch.setattr(
        "megalodon_ui.preflight.interview.run_interview", fake_run_interview
    )

    rc = preflight_main.main(["build a thing", "--mission-dir", str(tmp_path)])

    assert rc == 1
    # No real config written, but an aborted-snapshot of the draft exists.
    assert not (tmp_path / ".mission-config.yaml").exists()
    snapshots = list(tmp_path.glob(".mission-config.yaml.aborted-*"))
    assert snapshots, "expected an aborted-draft snapshot"
