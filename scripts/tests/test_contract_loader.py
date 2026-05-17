"""V9 M2 contract_loader tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "contracts"
sys.path.insert(0, str(REPO_ROOT))

from megalodon_ui.contract_loader import load_contract, ContractParseError  # noqa: E402


def test_parses_minimal_contract():
    contract = load_contract(FIXTURES / "minimal.md")
    assert len(contract["endpoints"]) == 1
    ep = contract["endpoints"][0]
    assert ep["method"] == "GET"
    assert ep["path"] == "/api/v1/state"
    assert ep["response_model"] == "StateResponse"
    assert ep["status"] == 200


def test_rejects_malformed_yaml():
    with pytest.raises(ContractParseError):
        load_contract(FIXTURES / "malformed.md")


def test_empty_contract_returns_empty_endpoints(tmp_path):
    empty = tmp_path / "empty.md"
    empty.write_text("# Empty\n\nNo endpoints here.\n", encoding="utf-8")
    contract = load_contract(empty)
    assert contract["endpoints"] == []


def test_parses_sse_events():
    contract = load_contract(FIXTURES / "with_sse.md")
    sse_ep = next(e for e in contract["endpoints"] if e["path"] == "/api/v1/events")
    assert "status-change" in sse_ep["sse_events"]
    assert "heartbeat" in sse_ep["sse_events"]


def test_handles_path_templates():
    contract = load_contract(FIXTURES / "with_template.md")
    paths = [e["path"] for e in contract["endpoints"]]
    assert "/api/v1/findings/{filename}" in paths
