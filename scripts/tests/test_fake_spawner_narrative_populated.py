"""Fake/demo boot self-populates the narrative board.

Regression for the P0 backend bug: the fake-spawner lifespan branch created a
``NarrativeHub`` + empty ``narrative_cache`` and then ``return``ed — it never
started ``run_narrator_scheduler`` / the deterministic ``build_rows`` tick that
lived only in the live branch. A fake/demo boot therefore had a permanently
empty narrative cache and the summary board was blank.

The fix runs one deterministic ``narrator_tick`` at startup (plus the same
watcher-gated scheduler the live branch uses), so ``/api/v1/narrative`` returns
non-empty lanes with the deterministic Goal/Now/Last/state fields populated.
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from megalodon_ui.auth import write_token_atomic
from megalodon_ui.server import make_app

TOKEN = "fake-narrative-token"


@pytest_asyncio.fixture
async def fake_client(tmp_path: Path, monkeypatch) -> AsyncGenerator[tuple, None]:
    """Authenticated client under MEGALODON_FAKE_SPAWNER=1 with a real fixture."""
    monkeypatch.delenv("MEGALODON_LIFESPAN_TEST_MODE", raising=False)
    monkeypatch.delenv("MEGALODON_FAKE_SESSIONS_PATH", raising=False)
    monkeypatch.setenv("MEGALODON_FAKE_SPAWNER", "1")

    # Copy the medium fixture (default config, lanes A-F, canonical TASKS.md).
    import shutil

    fixtures = Path(__file__).resolve().parents[2] / "ui" / "tests" / "fixtures"

    def _ignore(_src, names):
        return [
            n
            for n in names
            if n.endswith(".stream.log") or n in ("tmux.sock", "dashboard.url")
        ]

    dst = tmp_path / "fix-medium"
    shutil.copytree(fixtures / "fix-medium", dst, ignore=_ignore)
    fleet = dst / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    write_token_atomic(fleet / "ui.token", TOKEN)

    app = make_app(mission_dir=dst)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post("/api/v1/auth/exchange", json={"token": TOKEN})
            assert r.status_code == 200, f"auth exchange failed: {r.text}"
            yield client, app, dst


@pytest.mark.asyncio
async def test_fake_boot_populates_narrative_cache(fake_client) -> None:
    """The startup one-shot tick seeds app.state.narrative_cache with lanes."""
    _client, app, _ = fake_client
    cache = app.state.narrative_cache
    assert cache, "fake boot left the narrative cache empty (blank-board bug)"
    # Every cached lane must carry the deterministic board fields.
    for short, row in cache.items():
        for key in ("last", "now", "goal", "state"):
            assert key in row, (
                f"lane {short} missing deterministic field {key!r}: {row}"
            )


@pytest.mark.asyncio
async def test_fake_boot_narrative_endpoint_non_empty(fake_client) -> None:
    """GET /api/v1/narrative returns non-empty lanes on a fresh fake boot."""
    client, _app, _ = fake_client
    r = await client.get("/api/v1/narrative")
    assert r.status_code == 200, f"unexpected status {r.status_code}: {r.text}"
    body = r.json()
    assert "lanes" in body, f"missing 'lanes' key: {body}"
    assert body["lanes"], (
        "/api/v1/narrative returned empty lanes on a fake boot — the demo "
        "board would render blank"
    )


@pytest.mark.asyncio
async def test_fake_boot_starts_scheduler_task(fake_client) -> None:
    """The fake branch wires the same scheduler task the live branch uses."""
    _client, app, _ = fake_client
    task = getattr(app.state, "narrator_scheduler_task", None)
    assert task is not None, "fake branch did not start the narrator scheduler task"
    assert not task.done(), "scheduler task exited immediately"
