"""P5.2 — GET /api/v1/config exposes `v92_dashboard` for the v9.2 page.

The dashboard-v92.js client decides whether to render the xterm grid based
on this flag. The flag is server-runtime state (env-var or CLI), not a
MissionConfig declaration, so v9.0 missions stay v9.0 without YAML edits.

Discriminator: `MEGALODON_V92_DASHBOARD=1` env var at server start.

  - Unset / empty / "0" / "false" -> `v92_dashboard: False`
  - "1" / "true" (case-insensitive) -> `v92_dashboard: True`

The dashboard page reads only the boolean — no other parsing needed
client-side.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from megalodon_ui.server import AppConfig, make_app


_APP_CONFIG = AppConfig(csrf_token="test-csrf", poll_interval_seconds=0.05)


@pytest.mark.asyncio
async def test_v92_dashboard_field_present_default_false(queue_mission, monkeypatch):
    """Without the env var, /api/v1/config returns v92_dashboard: False."""
    monkeypatch.delenv("MEGALODON_V92_DASHBOARD", raising=False)
    app = make_app(mission_dir=queue_mission, config=_APP_CONFIG)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/config")
    assert r.status_code == 200, r.text
    data = r.json()
    assert "v92_dashboard" in data, "v92_dashboard key missing from /api/v1/config"
    assert data["v92_dashboard"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["1", "true", "True", "TRUE"])
async def test_v92_dashboard_true_when_env_set(value, queue_mission, monkeypatch):
    """Truthy env var values flip v92_dashboard to True."""
    monkeypatch.setenv("MEGALODON_V92_DASHBOARD", value)
    app = make_app(mission_dir=queue_mission, config=_APP_CONFIG)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/config")
    data = r.json()
    assert data["v92_dashboard"] is True, (
        f"MEGALODON_V92_DASHBOARD={value!r} should yield True, got {data['v92_dashboard']!r}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["0", "false", "False", "", "no", "off"])
async def test_v92_dashboard_false_for_explicit_false_values(value, queue_mission, monkeypatch):
    """Falsy env var values keep v92_dashboard False."""
    monkeypatch.setenv("MEGALODON_V92_DASHBOARD", value)
    app = make_app(mission_dir=queue_mission, config=_APP_CONFIG)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/config")
    data = r.json()
    assert data["v92_dashboard"] is False, (
        f"MEGALODON_V92_DASHBOARD={value!r} should yield False, got {data['v92_dashboard']!r}"
    )
