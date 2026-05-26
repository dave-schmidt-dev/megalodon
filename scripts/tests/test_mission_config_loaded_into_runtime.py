"""CR-3 / Task 1.3 — MissionConfig loaded into runtime via make_app.

Verifies that GET /api/v1/config returns lanes that reflect the actual
.mission-config.yaml in the mission directory, not the v9.0 synthesis default.

Parametrized over:
  - minimal_3_lane: 3-lane YAML fixture (ALPHA, BETA, GAMMA) with shorts A, B, C.
  - minimal_custom_phases: 1-lane YAML fixture (ALPHA) with short A.

Note: no 6-lane or 8-lane YAML config fixtures exist in
scripts/tests/fixtures/configs/ as of Task 1.3; Task 1.4 grep audit will
identify if additional fixtures are needed. The two available fixtures are
sufficient to demonstrate that lane count and lane shorts are driven by the
actual mission config, not the 6-lane v9.0 default.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

try:
    from megalodon_ui.server import make_app

    _BACKEND_AVAILABLE = True
except ImportError:
    make_app = None  # type: ignore[assignment]
    _BACKEND_AVAILABLE = False


CONFIGS_DIR = Path(__file__).parent / "fixtures" / "configs"

# Each entry: (fixture_subdir, expected_lane_count, expected_short_codes)
FIXTURE_PARAMS = [
    pytest.param(
        "minimal_3_lane",
        3,
        {"A", "B", "C"},
        id="3-lane",
    ),
    pytest.param(
        "minimal_custom_phases",
        1,
        {"A"},
        id="1-lane-custom-phases",
    ),
]


@pytest.fixture
def config_mission(tmp_path: Path, request):
    """Build a minimal runnable mission dir from a configs fixture.

    Copies the .mission-config.yaml into a tmp dir that also has a minimal
    STATUS.md so parse_status does not fail.
    """
    fixture_name = request.param
    src = CONFIGS_DIR / fixture_name
    dest = tmp_path / fixture_name
    dest.mkdir()
    shutil.copy(src / ".mission-config.yaml", dest / ".mission-config.yaml")
    # Minimal STATUS.md so the server does not 500.
    (dest / "STATUS.md").write_text(
        "# Status\n| Lane | Agent | State | Last UTC | Notes |\n|---|---|---|---|---|\n"
    )
    (dest / "TASKS.md").write_text("# Tasks\n")
    return dest



def _auth(app, client) -> None:
    """Attach a valid mui_session cookie — every /api/** call is now gated."""
    client.cookies.set("mui_session", app.state.megalodon.session_store.create())

@pytest.mark.asyncio
@pytest.mark.skipif(not _BACKEND_AVAILABLE, reason="megalodon_ui.server not available")
@pytest.mark.parametrize(
    "config_mission,expected_lane_count,expected_shorts",
    [
        ("minimal_3_lane", 3, {"A", "B", "C"}),
        ("minimal_custom_phases", 1, {"A"}),
    ],
    indirect=["config_mission"],
)
async def test_config_endpoint_reflects_mission_config(
    config_mission: Path,
    expected_lane_count: int,
    expected_shorts: set,
):
    """GET /api/v1/config returns lanes matching the fixture's .mission-config.yaml."""
    from httpx import AsyncClient, ASGITransport

    app = make_app(mission_dir=config_mission)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            _auth(app, client)
            r = await client.get("/api/v1/config")

    assert r.status_code == 200, f"unexpected status {r.status_code}: {r.text}"
    body = r.json()

    lanes = body.get("lanes", [])
    assert len(lanes) == expected_lane_count, (
        f"expected {expected_lane_count} lanes, got {len(lanes)}: {lanes}"
    )

    actual_shorts = {lane["short"] for lane in lanes}
    assert actual_shorts == expected_shorts, (
        f"expected shorts {expected_shorts}, got {actual_shorts}"
    )


@pytest.mark.asyncio
@pytest.mark.skipif(not _BACKEND_AVAILABLE, reason="megalodon_ui.server not available")
async def test_default_v9_lane_count_not_leaked_for_3_lane_mission(tmp_path: Path):
    """CR-3 regression: a 3-lane mission must not return the v9.0 default 6 lanes."""
    from httpx import AsyncClient, ASGITransport

    src = CONFIGS_DIR / "minimal_3_lane"
    dest = tmp_path / "mission"
    dest.mkdir()
    shutil.copy(src / ".mission-config.yaml", dest / ".mission-config.yaml")
    (dest / "STATUS.md").write_text(
        "# Status\n| Lane | Agent | State | Last UTC | Notes |\n|---|---|---|---|---|\n"
    )
    (dest / "TASKS.md").write_text("# Tasks\n")

    app = make_app(mission_dir=dest)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            _auth(app, client)
            r = await client.get("/api/v1/config")

    body = r.json()
    assert len(body["lanes"]) == 3, (
        f"expected 3 lanes (not the v9.0 default 6), got {len(body['lanes'])}"
    )
    # v9.0 default has AUDIT, ARCHITECT, BACKEND, FRONTEND, TEST, META;
    # this fixture has ALPHA, BETA, GAMMA — confirm the v9.0 names are absent.
    lane_names = {lane["name"] for lane in body["lanes"]}
    assert "AUDIT" not in lane_names, (
        "v9.0 default lane AUDIT leaked into 3-lane mission"
    )
