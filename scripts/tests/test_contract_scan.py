"""V9 M2 contract_scan tests."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import contract_scan  # noqa: E402


def test_normalize_path_strips_finding_filename():
    assert (
        contract_scan._normalize_path("/api/v1/findings/abc12345.md")
        == "/api/v1/findings/{filename}"
    )


def test_normalize_path_passes_static_path():
    assert contract_scan._normalize_path("/api/v1/state") == "/api/v1/state"


def test_normalize_path_keeps_findings_list_root():
    # Trailing slash or empty suffix should not be normalized to detail.
    assert contract_scan._normalize_path("/api/v1/findings") == "/api/v1/findings"


def test_compare_pass():
    contract = {
        "endpoints": [
            {"method": "GET", "path": "/api/v1/state"},
            {"method": "POST", "path": "/api/v1/reclaim"},
        ]
    }
    registered = [["GET", "/api/v1/state"], ["POST", "/api/v1/reclaim"]]
    fetched = [
        {"method": "GET", "url": "/api/v1/state"},
        {"method": "POST", "url": "/api/v1/reclaim"},
    ]
    result = contract_scan._compare(contract, registered, fetched)
    assert result["pass"] is True
    assert result["undocumented_fetches"] == []


def test_compare_undocumented_fetch_fails():
    contract = {"endpoints": [{"method": "GET", "path": "/api/v1/state"}]}
    registered = [["GET", "/api/v1/state"]]
    fetched = [
        {"method": "GET", "url": "/api/v1/state"},
        {"method": "GET", "url": "/api/v1/secret"},
    ]
    result = contract_scan._compare(contract, registered, fetched)
    assert result["pass"] is False
    assert any("/api/v1/secret" in u for u in result["undocumented_fetches"])
