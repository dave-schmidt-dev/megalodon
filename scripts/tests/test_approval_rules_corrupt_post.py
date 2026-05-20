"""v9.4 ship-time gap-fill — POST /api/v1/approval-rules self-heals a corrupt file.

Existing coverage (test_approval_rules_endpoints.py):
  - GET with corrupt file → {} with warning (tested)
  - POST with clean file → 201 (tested)

Missing coverage:
  - POST when approval-rules.json is ALREADY CORRUPT at the moment of the POST.

Why this matters
----------------
In production a file could be corrupted by a disk error, a kill-9 during a
prior write (before os.replace), or a botched manual edit. The operator's next
action will be to open the dashboard and try to save a new rule. If POST does
not self-heal, the operator is permanently locked out of approval-rules (every
subsequent POST would re-read the corrupt content and fail).

Expected behaviour
------------------
_read_approval_rules() treats corrupt JSON as an empty list, so POST:
  1. reads corrupt file → treats as []
  2. appends new entry → list of 1
  3. writes fresh, valid JSON via _write_approval_rules() (atomic rename)
  4. returns 201 with the new entry

A subsequent GET must return the new rule (proving the corrupt file was
overwritten, not left in place).
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from megalodon_ui.auth import write_token_atomic
from megalodon_ui.server import make_app


TOKEN = "corrupt-post-test-token"
SESSION = "heal-session-abc"
PATTERN = "Bash(heal:*)"


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def corrupt_ar_client(tmp_path: Path, monkeypatch) -> AsyncGenerator[tuple, None]:
    """Authenticated httpx client whose .fleet/approval-rules.json is corrupt at startup."""
    monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")

    fleet = tmp_path / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    write_token_atomic(fleet / "ui.token", TOKEN)

    (tmp_path / "STATUS.md").write_text("# Status\n")
    (tmp_path / "TASKS.md").write_text("# Tasks\n")
    (tmp_path / "HISTORY.md").write_text("# History\n")

    # Seed a corrupt approval-rules.json BEFORE the server starts.
    rules_path = fleet / "approval-rules.json"
    rules_path.write_text("not valid json {{{", encoding="utf-8")

    app = make_app(mission_dir=tmp_path)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            exch = await client.post("/api/v1/auth/exchange", json={"token": TOKEN})
            assert exch.status_code == 200, f"auth failed: {exch.text}"

            config_r = await client.get("/api/v1/config")
            csrf_token = config_r.json().get("csrf_token", "")

            yield client, csrf_token, tmp_path


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_self_heals_corrupt_rules_file(corrupt_ar_client):
    """POST /api/v1/approval-rules with a corrupt file pre-existing → 201, file healed.

    Steps:
    1. Confirm GET returns {rules: []} (corrupt file treated as empty).
    2. POST a new rule → must return 201 (not 500).
    3. GET again → must return the new rule (corrupt file was overwritten).
    4. The rules file must now contain valid JSON.
    """
    client, csrf_token, mission_dir = corrupt_ar_client
    rules_path = mission_dir / ".fleet" / "approval-rules.json"

    # Verify corrupt file is in place.
    assert rules_path.exists(), "rules file should have been seeded"
    raw = rules_path.read_text(encoding="utf-8")
    assert "not valid json" in raw, "pre-condition: file should still be corrupt"

    # Step 1: GET should treat corrupt file as empty (already covered; verify here too).
    get_before = await client.get("/api/v1/approval-rules")
    assert get_before.status_code == 200, get_before.text
    assert get_before.json() == {"rules": []}, (
        f"corrupt file should return empty rules: {get_before.json()}"
    )

    # Step 2: POST a new rule while file is still corrupt.
    post_r = await client.post(
        "/api/v1/approval-rules",
        json={"pattern": PATTERN, "added_by_session": SESSION},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert post_r.status_code == 201, (
        f"POST on corrupt file should return 201 (self-heal), got {post_r.status_code}: {post_r.text}"
    )
    entry = post_r.json()
    assert entry["pattern"] == PATTERN
    assert entry["added_by_session"] == SESSION

    # Step 3: GET must now return the new rule.
    get_after = await client.get("/api/v1/approval-rules")
    assert get_after.status_code == 200, get_after.text
    rules = get_after.json()["rules"]
    assert len(rules) == 1, (
        f"expected 1 rule after self-heal, got {len(rules)}: {rules}"
    )
    assert rules[0]["pattern"] == PATTERN

    # Step 4: The file itself must now be valid JSON (not corrupt any more).
    import json

    healed_text = rules_path.read_text(encoding="utf-8")
    parsed = json.loads(healed_text)  # raises if still corrupt
    assert isinstance(parsed, list)
    assert len(parsed) == 1
    assert parsed[0]["pattern"] == PATTERN
