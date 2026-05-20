"""v9.4 Task 3.1 — POST/GET/DELETE /api/v1/approval-rules endpoint tests.

Covers:
- POST then GET → entry appears
- POST same pattern twice → only 1 entry (dedup; first call 201, second 200)
- POST + DELETE → entry gone
- DELETE non-existent → 404
- CSRF missing → 403 on POST and DELETE
- CSRF mismatch → 403 on POST and DELETE
- File missing → GET returns {rules: []}
- Corrupt file → GET returns {rules: []} with warning logged (no 500)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from megalodon_ui.auth import write_token_atomic
from megalodon_ui.server import make_app


TOKEN = "approval-rules-test-token"
SESSION = "test-session-abc"
PATTERN = "Bash(npm run *)"


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def ar_client(tmp_path: Path, monkeypatch) -> AsyncGenerator[tuple, None]:
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
            # Authenticate and grab CSRF token
            exch = await client.post("/api/v1/auth/exchange", json={"token": TOKEN})
            assert exch.status_code == 200, f"auth failed: {exch.text}"

            config_r = await client.get("/api/v1/config")
            csrf_token = config_r.json().get("csrf_token", "")

            yield client, csrf_token, tmp_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rules_file(mission_dir: Path) -> Path:
    return mission_dir / ".fleet" / "approval-rules.json"


async def _post_rule(client, csrf_token, pattern=PATTERN, session=SESSION):
    return await client.post(
        "/api/v1/approval-rules",
        json={"pattern": pattern, "added_by_session": session},
        headers={"X-CSRF-Token": csrf_token},
    )


async def _get_rules(client):
    return await client.get("/api/v1/approval-rules")


async def _delete_rule(client, csrf_token, pattern=PATTERN):
    return await client.delete(
        f"/api/v1/approval-rules?pattern={pattern}",
        headers={"X-CSRF-Token": csrf_token},
    )


# ---------------------------------------------------------------------------
# POST then GET → entry appears
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_then_get(ar_client):
    client, csrf_token, _ = ar_client
    post_r = await _post_rule(client, csrf_token)
    assert post_r.status_code == 201, post_r.text
    entry = post_r.json()
    assert entry["pattern"] == PATTERN
    assert entry["added_by_session"] == SESSION
    assert "added_at_utc" in entry

    get_r = await _get_rules(client)
    assert get_r.status_code == 200, get_r.text
    rules = get_r.json()["rules"]
    assert len(rules) == 1
    assert rules[0]["pattern"] == PATTERN


# ---------------------------------------------------------------------------
# POST same pattern twice → only 1 entry (dedup)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_dedup(ar_client):
    client, csrf_token, _ = ar_client
    r1 = await _post_rule(client, csrf_token)
    assert r1.status_code == 201, r1.text

    r2 = await _post_rule(client, csrf_token)
    assert r2.status_code == 200, r2.text
    # Second response must be the same entry
    assert r2.json()["pattern"] == PATTERN

    get_r = await _get_rules(client)
    rules = get_r.json()["rules"]
    assert len(rules) == 1, f"expected 1 rule, got {len(rules)}: {rules}"


# ---------------------------------------------------------------------------
# POST + DELETE → entry gone
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_delete_gone(ar_client):
    client, csrf_token, _ = ar_client
    await _post_rule(client, csrf_token)

    del_r = await _delete_rule(client, csrf_token)
    assert del_r.status_code == 204, del_r.text

    get_r = await _get_rules(client)
    assert get_r.json()["rules"] == []


# ---------------------------------------------------------------------------
# DELETE non-existent → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_nonexistent_returns_404(ar_client):
    client, csrf_token, _ = ar_client
    del_r = await _delete_rule(client, csrf_token, pattern="nonexistent-pattern")
    assert del_r.status_code == 404, del_r.text


# ---------------------------------------------------------------------------
# CSRF missing → 403 on POST and DELETE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_missing_csrf_returns_403(ar_client):
    client, _, _ = ar_client
    r = await client.post(
        "/api/v1/approval-rules",
        json={"pattern": PATTERN, "added_by_session": SESSION},
        # No X-CSRF-Token header
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_delete_missing_csrf_returns_403(ar_client):
    client, csrf_token, _ = ar_client
    # First add a rule so DELETE would succeed if CSRF were valid
    await _post_rule(client, csrf_token)
    r = await client.delete(f"/api/v1/approval-rules?pattern={PATTERN}")
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# CSRF mismatch → 403 on POST and DELETE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_csrf_mismatch_returns_403(ar_client):
    client, _, _ = ar_client
    r = await client.post(
        "/api/v1/approval-rules",
        json={"pattern": PATTERN, "added_by_session": SESSION},
        headers={"X-CSRF-Token": "wrong-token"},
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_delete_csrf_mismatch_returns_403(ar_client):
    client, csrf_token, _ = ar_client
    await _post_rule(client, csrf_token)
    r = await client.delete(
        f"/api/v1/approval-rules?pattern={PATTERN}",
        headers={"X-CSRF-Token": "wrong-token"},
    )
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# File missing → GET returns {rules: []}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_file_missing_returns_empty(ar_client):
    client, _, mission_dir = ar_client
    rules_path = _rules_file(mission_dir)
    # Ensure no file exists
    rules_path.unlink(missing_ok=True)

    get_r = await _get_rules(client)
    assert get_r.status_code == 200, get_r.text
    assert get_r.json() == {"rules": []}


# ---------------------------------------------------------------------------
# Corrupt file → GET returns {rules: []} with warning logged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_corrupt_file_returns_empty_with_warning(ar_client, caplog):
    client, _, mission_dir = ar_client
    rules_path = _rules_file(mission_dir)
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_path.write_text("this is not valid json {{{", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="megalodon_ui.server"):
        get_r = await _get_rules(client)

    assert get_r.status_code == 200, get_r.text
    assert get_r.json() == {"rules": []}
    # A warning must have been logged
    assert any("corrupt" in r.message.lower() for r in caplog.records), (
        f"expected a 'corrupt' warning, got records: {[r.message for r in caplog.records]}"
    )


# ---------------------------------------------------------------------------
# Atomic write: .fleet/approval-rules.json.tmp is cleaned up
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_no_tmp_file_left(ar_client):
    client, csrf_token, mission_dir = ar_client
    await _post_rule(client, csrf_token)
    tmp_path = _rules_file(mission_dir).with_suffix(".json.tmp")
    assert not tmp_path.exists(), f".tmp file was not cleaned up: {tmp_path}"
