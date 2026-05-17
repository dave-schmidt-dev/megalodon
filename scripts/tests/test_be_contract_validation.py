"""V9 M2 BE-side contract validation tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from fastapi import FastAPI  # noqa: E402

from megalodon_ui.server import _validate_contract  # noqa: E402


def test_validates_passes_when_routes_match(tmp_path):
    contract_md = tmp_path / "contract.md"
    contract_md.write_text(
        "### GET /api/v1/state\n"
        "\n```yaml\n"
        "method: GET\npath: /api/v1/state\nresponse_model: StateResponse\nstatus: 200\n"
        "content_type: application/json\n"
        "```\n",
        encoding="utf-8",
    )
    app = FastAPI()

    @app.get("/api/v1/state")
    async def state_route():
        return {}

    _validate_contract(app, contract_md)  # Should not raise.


def test_validates_fails_when_route_declared_but_missing(tmp_path):
    contract_md = tmp_path / "contract.md"
    contract_md.write_text(
        "### GET /api/v1/bogus\n"
        "\n```yaml\n"
        "method: GET\npath: /api/v1/bogus\nresponse_model: BogusModel\nstatus: 200\n"
        "content_type: application/json\n"
        "```\n",
        encoding="utf-8",
    )
    app = FastAPI()

    with pytest.raises(RuntimeError, match="declared routes not registered"):
        _validate_contract(app, contract_md)
