"""v9.4 Task 3.5 — GET /api/v1/approval-rules/extract endpoint tests.

Covers:
- curl URL command → scheme://host:port/* pattern
- find command → Bash(find:*)
- compound command (&&) → null
- redirect command → null
- empty command → null
- missing query param → null
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncGenerator
from urllib.parse import quote

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from megalodon_ui.auth import write_token_atomic
from megalodon_ui.server import make_app


TOKEN = "extract-endpoint-test-token"


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def extract_client(
    tmp_path: Path, monkeypatch
) -> AsyncGenerator[AsyncClient, None]:
    """Authenticated httpx client pointing at a fresh mission dir."""
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")

    fleet = tmp_path / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    write_token_atomic(fleet / "ui.token", TOKEN)

    (tmp_path / "STATUS.md").write_text("# Status\n")
    (tmp_path / "TASKS.md").write_text("# Tasks\n")
    (tmp_path / "HISTORY.md").write_text("# History\n")

    app = make_app(mission_dir=tmp_path)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            exch = await client.post("/api/v1/auth/exchange", json={"token": TOKEN})
            assert exch.status_code == 200, f"auth failed: {exch.text}"
            yield client


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _extract(client: AsyncClient, command: str):
    """GET /api/v1/approval-rules/extract?command=<encoded>."""
    resp = await client.get(f"/api/v1/approval-rules/extract?command={quote(command)}")
    assert resp.status_code == 200, f"unexpected status {resp.status_code}: {resp.text}"
    return resp.json()


# ---------------------------------------------------------------------------
# Case 1: curl URL → scheme://host:port/* pattern
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_curl_url_returns_host_wildcard(extract_client):
    """curl -s http://127.0.0.1:8765/api/v1/foo → Bash(curl -s http://127.0.0.1:8765/*)."""
    body = await _extract(extract_client, "curl -s http://127.0.0.1:8765/api/v1/foo")
    assert body["pattern"] == "Bash(curl -s http://127.0.0.1:8765/*)"


# ---------------------------------------------------------------------------
# Case 2: find . -name x → Bash(find:*)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_returns_bash_find_wildcard(extract_client):
    """find . -name x → Bash(find:*)."""
    body = await _extract(extract_client, "find . -name x")
    assert body["pattern"] == "Bash(find:*)"


# ---------------------------------------------------------------------------
# Case 3: compound command with && → null
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compound_and_returns_null(extract_client):
    """git status && npm test → null (compound)."""
    body = await _extract(extract_client, "git status && npm test")
    assert body["pattern"] is None


# ---------------------------------------------------------------------------
# Case 4: redirect → null
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redirect_returns_null(extract_client):
    """echo x > out.txt → null (redirect)."""
    body = await _extract(extract_client, "echo x > out.txt")
    assert body["pattern"] is None


# ---------------------------------------------------------------------------
# Case 5: empty command → null
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_command_returns_null(extract_client):
    """Empty string → null."""
    resp = await extract_client.get("/api/v1/approval-rules/extract?command=")
    assert resp.status_code == 200
    assert resp.json()["pattern"] is None


# ---------------------------------------------------------------------------
# Case 6: missing query param → null
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_param_returns_null(extract_client):
    """No command param → null."""
    resp = await extract_client.get("/api/v1/approval-rules/extract")
    assert resp.status_code == 200
    assert resp.json()["pattern"] is None


# ---------------------------------------------------------------------------
# Case 7: simple program like pytest → Bash(pytest:*)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pytest_returns_generic_pattern(extract_client):
    """pytest scripts/tests/ -v → Bash(pytest:*)."""
    body = await _extract(extract_client, "pytest scripts/tests/ -v")
    assert body["pattern"] == "Bash(pytest:*)"
