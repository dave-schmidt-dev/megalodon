"""P7.2 — `megalodon_ui/shutdown.py` standalone CLI tests.

Plan §6.7 + Task 7.2: a small ``python -m megalodon_ui.shutdown
--mission-dir <path>`` entrypoint that:

1. Resolves ``<mission>/.fleet/tmux.sock``.
2. Runs ``tmux -S <sock> kill-server`` (best-effort — server may already be
   gone). The CLI does NOT shell out; it imports and awaits
   ``megalodon_ui.tmux.kill_server`` so tests can mock cleanly.
3. Unlinks ``.fleet/ui.token``, ``.fleet/tmux.sock``, ``.fleet/dashboard.url``
   (``missing_ok=True`` — idempotent across repeated invocations).
4. Exits 0 on success.
5. Exits non-zero ONLY if ``--mission-dir`` is missing or doesn't point at a
   directory (operator error, not a runtime condition).

Used by operator-facing scripts and by tests that need to scrub a mission
between runs without invoking the live server's destructive DELETE endpoint.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch


def _seed_mission(tmp_path: Path) -> Path:
    mission = tmp_path / "m"
    fleet = mission / ".fleet"
    fleet.mkdir(parents=True)
    (fleet / "ui.token").write_text("secret\n")
    (fleet / "tmux.sock").write_bytes(b"")
    (fleet / "dashboard.url").write_text("http://127.0.0.1:8080/#t=secret\n")
    return mission


def test_shutdown_runs_kill_server_and_unlinks_files(tmp_path: Path):
    from megalodon_ui import shutdown as sd

    mission = _seed_mission(tmp_path)

    with patch(
        "megalodon_ui.shutdown.tmux.kill_server",
        new=AsyncMock(return_value=0),
    ) as ks:
        rc = sd.main(["--mission-dir", str(mission)])

    assert rc == 0
    ks.assert_awaited_once_with(mission / ".fleet" / "tmux.sock")
    assert not (mission / ".fleet" / "ui.token").exists()
    assert not (mission / ".fleet" / "tmux.sock").exists()
    assert not (mission / ".fleet" / "dashboard.url").exists()


def test_shutdown_idempotent_on_already_clean_mission(tmp_path: Path):
    """Re-running on a clean mission must exit 0 — operators chain it after `kill -9`."""
    from megalodon_ui import shutdown as sd

    mission = tmp_path / "m"
    (mission / ".fleet").mkdir(parents=True)
    # No files seeded.

    with patch(
        "megalodon_ui.shutdown.tmux.kill_server",
        new=AsyncMock(return_value=1),
    ):
        rc = sd.main(["--mission-dir", str(mission)])

    assert rc == 0


def test_shutdown_unlinks_dashboard_url_explicitly(tmp_path: Path):
    """Regression: CV-11 dashboard.url must be unlinked alongside the other two."""
    from megalodon_ui import shutdown as sd

    mission = _seed_mission(tmp_path)
    assert (mission / ".fleet" / "dashboard.url").exists()

    with patch(
        "megalodon_ui.shutdown.tmux.kill_server",
        new=AsyncMock(return_value=0),
    ):
        rc = sd.main(["--mission-dir", str(mission)])
    assert rc == 0
    assert not (mission / ".fleet" / "dashboard.url").exists()


def test_shutdown_tolerates_kill_server_nonzero_rc(tmp_path: Path):
    """Server already gone (non-zero rc) — still unlink files and exit 0."""
    from megalodon_ui import shutdown as sd

    mission = _seed_mission(tmp_path)
    with patch(
        "megalodon_ui.shutdown.tmux.kill_server",
        new=AsyncMock(return_value=1),
    ):
        rc = sd.main(["--mission-dir", str(mission)])
    assert rc == 0
    assert not (mission / ".fleet" / "ui.token").exists()


def test_shutdown_missing_mission_dir_returns_nonzero(tmp_path: Path):
    from megalodon_ui import shutdown as sd

    nonexistent = tmp_path / "does-not-exist"
    rc = sd.main(["--mission-dir", str(nonexistent)])
    assert rc != 0


def test_shutdown_mission_dir_is_file_returns_nonzero(tmp_path: Path):
    from megalodon_ui import shutdown as sd

    not_a_dir = tmp_path / "f"
    not_a_dir.write_text("oops")
    rc = sd.main(["--mission-dir", str(not_a_dir)])
    assert rc != 0
