"""v9.4 T3.2 — teardown cleanup tests for approval-rules.json and inject logs.

Tests both CLI (shutdown.py) and HTTP (DELETE /api/v1/fleet) teardown paths to
ensure they clean:
  1. approval-rules.json (literal filename)
  2. inject-log-*.jsonl files (glob pattern for daily rotation)

And verify the teardown is idempotent and doesn't touch other .fleet/ files.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.auth import write_token_atomic
from megalodon_ui.mission_config.schema import MissionConfig
from megalodon_ui.server import make_app
from megalodon_ui import shutdown

TOKEN = "teardown-test-token"
LANE_SHORT = "A"


def _make_mission_config() -> MissionConfig:
    """Create a minimal mission config for testing."""
    return MissionConfig.model_validate(
        {
            "mission": {"id": "test-teardown", "utc_started": "2026-01-01T00:00:00Z"},
            "lanes": [
                {
                    "name": "AUDIT",
                    "short": LANE_SHORT,
                    "role": "auditor",
                    "harness": {"cli": "claude", "model": "claude-sonnet-4-6"},
                    "cadence_seconds": 300,
                    "tick_offset_seconds": 0,
                }
            ],
            "phases": ["INIT"],
        }
    )


def _setup_mission(tmp_path: Path) -> None:
    """Create minimal required mission directory structure."""
    fleet = tmp_path / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    write_token_atomic(fleet / "ui.token", TOKEN)
    (tmp_path / "STATUS.md").write_text("# Status\n")
    (tmp_path / "TASKS.md").write_text("# Tasks\n")
    (tmp_path / "HISTORY.md").write_text("# History\n")
    (tmp_path / "findings").mkdir(exist_ok=True)
    (tmp_path / "signals").mkdir(exist_ok=True)


# ===========================================================================
# CLI Teardown Tests (via shutdown.py)
# ===========================================================================


@pytest.mark.asyncio
async def test_cli_teardown_cleans_approval_rules(tmp_path: Path):
    """CLI teardown removes approval-rules.json."""
    _setup_mission(tmp_path)
    fleet = tmp_path / ".fleet"

    # Create the approval-rules.json file
    approval_rules_file = fleet / "approval-rules.json"
    approval_rules_file.write_text('{"rules": []}')
    assert approval_rules_file.exists()

    # Run shutdown
    rc = await shutdown._run(tmp_path)
    assert rc == 0

    # Verify it's gone
    assert not approval_rules_file.exists()


@pytest.mark.asyncio
async def test_cli_teardown_cleans_inject_logs(tmp_path: Path):
    """CLI teardown removes all inject-log-*.jsonl files."""
    _setup_mission(tmp_path)
    fleet = tmp_path / ".fleet"

    # Create multiple dated inject log files
    log1 = fleet / "inject-log-2026-05-20.jsonl"
    log2 = fleet / "inject-log-2026-05-19.jsonl"
    log3 = fleet / "inject-log-2026-05-18.jsonl"
    log1.write_text('{"event": "test1"}\n')
    log2.write_text('{"event": "test2"}\n')
    log3.write_text('{"event": "test3"}\n')

    assert log1.exists()
    assert log2.exists()
    assert log3.exists()

    # Run shutdown
    rc = await shutdown._run(tmp_path)
    assert rc == 0

    # Verify all are gone
    assert not log1.exists()
    assert not log2.exists()
    assert not log3.exists()


@pytest.mark.asyncio
async def test_cli_teardown_cleans_both_artifacts(tmp_path: Path):
    """CLI teardown removes both approval-rules.json and inject-log-*.jsonl."""
    _setup_mission(tmp_path)
    fleet = tmp_path / ".fleet"

    # Create both types of artifacts
    approval_rules_file = fleet / "approval-rules.json"
    approval_rules_file.write_text('{"rules": []}')
    log1 = fleet / "inject-log-2026-05-20.jsonl"
    log2 = fleet / "inject-log-2026-05-19.jsonl"
    log1.write_text('{"event": "test1"}\n')
    log2.write_text('{"event": "test2"}\n')

    # Create an old-style artifact to verify it's also cleaned
    ui_token = fleet / "ui.token"
    ui_token.write_text("old-token")

    assert approval_rules_file.exists()
    assert log1.exists()
    assert log2.exists()
    assert ui_token.exists()

    # Run shutdown
    rc = await shutdown._run(tmp_path)
    assert rc == 0

    # Verify all artifacts are gone
    assert not approval_rules_file.exists()
    assert not log1.exists()
    assert not log2.exists()
    assert not ui_token.exists()


@pytest.mark.asyncio
async def test_cli_teardown_idempotent(tmp_path: Path):
    """CLI teardown is idempotent — calling twice doesn't raise."""
    _setup_mission(tmp_path)
    fleet = tmp_path / ".fleet"

    # Create artifacts
    approval_rules_file = fleet / "approval-rules.json"
    approval_rules_file.write_text('{"rules": []}')
    log1 = fleet / "inject-log-2026-05-20.jsonl"
    log1.write_text('{"event": "test1"}\n')

    # Run shutdown twice
    rc1 = await shutdown._run(tmp_path)
    assert rc1 == 0
    rc2 = await shutdown._run(tmp_path)  # Should not raise
    assert rc2 == 0


@pytest.mark.asyncio
async def test_cli_teardown_preserves_other_files(tmp_path: Path):
    """CLI teardown leaves non-artifact .fleet/ files alone."""
    _setup_mission(tmp_path)
    fleet = tmp_path / ".fleet"

    # Create an artifact to clean
    approval_rules_file = fleet / "approval-rules.json"
    approval_rules_file.write_text('{"rules": []}')

    # Create a non-artifact file
    other_file = fleet / "other.txt"
    other_file.write_text("should remain")

    assert approval_rules_file.exists()
    assert other_file.exists()

    # Run shutdown
    rc = await shutdown._run(tmp_path)
    assert rc == 0

    # Verify artifact is gone but other file remains
    assert not approval_rules_file.exists()
    assert other_file.exists()
    assert other_file.read_text() == "should remain"


# ===========================================================================
# HTTP Teardown Tests (via DELETE /api/v1/fleet)
# ===========================================================================


@pytest.mark.asyncio
async def test_http_teardown_cleans_approval_rules(tmp_path: Path, monkeypatch):
    """HTTP DELETE /api/v1/fleet removes approval-rules.json."""
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    _setup_mission(tmp_path)
    fleet = tmp_path / ".fleet"

    # Create the approval-rules.json file
    approval_rules_file = fleet / "approval-rules.json"
    approval_rules_file.write_text('{"rules": []}')
    assert approval_rules_file.exists()

    app = make_app(mission_dir=tmp_path, port=8080)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            # POST to auth first to get a session cookie
            auth_resp = await client.post(
                "/api/v1/auth/exchange",
                json={"token": TOKEN},
            )
            assert auth_resp.status_code == 200
            # DELETE /api/v1/fleet is now CSRF-protected; attach the token.
            client.headers["X-CSRF-Token"] = app.state.megalodon.csrf_token

            # Call DELETE /api/v1/fleet with the session cookie
            delete_resp = await client.delete(
                "/api/v1/fleet",
            )
            assert delete_resp.status_code == 200

    # Verify it's gone
    assert not approval_rules_file.exists()


@pytest.mark.asyncio
async def test_http_teardown_cleans_inject_logs(tmp_path: Path, monkeypatch):
    """HTTP DELETE /api/v1/fleet removes all inject-log-*.jsonl files."""
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    _setup_mission(tmp_path)
    fleet = tmp_path / ".fleet"

    # Create multiple dated inject log files
    log1 = fleet / "inject-log-2026-05-20.jsonl"
    log2 = fleet / "inject-log-2026-05-19.jsonl"
    log3 = fleet / "inject-log-2026-05-18.jsonl"
    log1.write_text('{"event": "test1"}\n')
    log2.write_text('{"event": "test2"}\n')
    log3.write_text('{"event": "test3"}\n')

    assert log1.exists()
    assert log2.exists()
    assert log3.exists()

    app = make_app(mission_dir=tmp_path, port=8080)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            # Auth first
            auth_resp = await client.post(
                "/api/v1/auth/exchange",
                json={"token": TOKEN},
            )
            assert auth_resp.status_code == 200
            # DELETE /api/v1/fleet is now CSRF-protected; attach the token.
            client.headers["X-CSRF-Token"] = app.state.megalodon.csrf_token

            # Call DELETE
            delete_resp = await client.delete(
                "/api/v1/fleet",
            )
            assert delete_resp.status_code == 200

    # Verify all logs are gone
    assert not log1.exists()
    assert not log2.exists()
    assert not log3.exists()


@pytest.mark.asyncio
async def test_http_teardown_cleans_both_artifacts(tmp_path: Path, monkeypatch):
    """HTTP DELETE /api/v1/fleet removes both approval-rules.json and inject logs."""
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    _setup_mission(tmp_path)
    fleet = tmp_path / ".fleet"

    # Create both types of artifacts
    approval_rules_file = fleet / "approval-rules.json"
    approval_rules_file.write_text('{"rules": []}')
    log1 = fleet / "inject-log-2026-05-20.jsonl"
    log2 = fleet / "inject-log-2026-05-19.jsonl"
    log1.write_text('{"event": "test1"}\n')
    log2.write_text('{"event": "test2"}\n')

    # Create an old-style artifact (but preserve ui.token so auth works)
    dashboard_url = fleet / "dashboard.url"
    dashboard_url.write_text("http://localhost:8080")

    assert approval_rules_file.exists()
    assert log1.exists()
    assert log2.exists()
    assert dashboard_url.exists()

    app = make_app(mission_dir=tmp_path, port=8080)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            # Auth
            auth_resp = await client.post(
                "/api/v1/auth/exchange",
                json={"token": TOKEN},
            )
            assert auth_resp.status_code == 200
            # DELETE /api/v1/fleet is now CSRF-protected; attach the token.
            client.headers["X-CSRF-Token"] = app.state.megalodon.csrf_token

            # Delete
            delete_resp = await client.delete(
                "/api/v1/fleet",
            )
            assert delete_resp.status_code == 200

    # Verify all artifacts are gone
    assert not approval_rules_file.exists()
    assert not log1.exists()
    assert not log2.exists()
    assert not dashboard_url.exists()


@pytest.mark.asyncio
async def test_http_teardown_idempotent(tmp_path: Path, monkeypatch):
    """HTTP DELETE /api/v1/fleet is idempotent."""
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    _setup_mission(tmp_path)
    fleet = tmp_path / ".fleet"

    # Create artifacts
    approval_rules_file = fleet / "approval-rules.json"
    approval_rules_file.write_text('{"rules": []}')
    log1 = fleet / "inject-log-2026-05-20.jsonl"
    log1.write_text('{"event": "test1"}\n')

    app = make_app(mission_dir=tmp_path, port=8080)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            # Auth
            auth_resp = await client.post(
                "/api/v1/auth/exchange",
                json={"token": TOKEN},
            )
            assert auth_resp.status_code == 200
            # DELETE /api/v1/fleet is now CSRF-protected; attach the token.
            client.headers["X-CSRF-Token"] = app.state.megalodon.csrf_token

            # First delete
            delete_resp1 = await client.delete(
                "/api/v1/fleet",
            )
            assert delete_resp1.status_code == 200

    # Verify files are gone
    assert not approval_rules_file.exists()
    assert not log1.exists()

    # Verify CLI shutdown also succeeds on already-clean mission
    rc = await shutdown._run(tmp_path)
    assert rc == 0


@pytest.mark.asyncio
async def test_http_teardown_preserves_other_files(tmp_path: Path, monkeypatch):
    """HTTP DELETE /api/v1/fleet leaves non-artifact files alone."""
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
    _setup_mission(tmp_path)
    fleet = tmp_path / ".fleet"

    # Create an artifact to clean
    approval_rules_file = fleet / "approval-rules.json"
    approval_rules_file.write_text('{"rules": []}')

    # Create a non-artifact file
    other_file = fleet / "other.txt"
    other_file.write_text("should remain")

    assert approval_rules_file.exists()
    assert other_file.exists()

    app = make_app(mission_dir=tmp_path, port=8080)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            # Auth
            auth_resp = await client.post(
                "/api/v1/auth/exchange",
                json={"token": TOKEN},
            )
            assert auth_resp.status_code == 200
            # DELETE /api/v1/fleet is now CSRF-protected; attach the token.
            client.headers["X-CSRF-Token"] = app.state.megalodon.csrf_token

            # Delete
            delete_resp = await client.delete(
                "/api/v1/fleet",
            )
            assert delete_resp.status_code == 200

    # Verify artifact is gone but other file remains
    assert not approval_rules_file.exists()
    assert other_file.exists()
    assert other_file.read_text() == "should remain"
