"""V9 M2 — PRE-VERIFY contract scan orchestrator.

Runs three checks and emits a JSON report to stdout:

  1. BE startup: spawn `python -m megalodon_ui` with M9_VALIDATE_CONTRACT=1 and
     confirm it boots (the make_app validator crashes on contract drift).
  2. Routes declared vs registered: cross-check the contract doc against the
     factory's /api/v1/__contract_introspect__ output.
  3. FE runtime: invoke the playwright contract-trace spec; parse the
     M9_CONTRACT_CALLS_{BEGIN,END} sentinels out of stdout; compare each
     fetched URL (normalized to the contract template) against the declared
     endpoints. Any undeclared call fails the scan.

Exit codes:
  0 = pass (or --soft)
  1 = drift detected (strict mode)
  2 = BE failed to start
  3 = playwright failed

Spec: docs/superpowers/specs/2026-05-16-v9-m2-contract-scan-design.md §8.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO_ROOT / "docs" / "v9" / "api-contract.md"
DEFAULT_MISSION_DIR = REPO_ROOT / "scripts" / "tests" / "fixtures" / "minimal_mission"
DEFAULT_PORT = 8089


def _normalize_path(url: str) -> str:
    """Normalize concrete paths to contract templates.

    - Strip query string.
    - /api/v1/findings/<anything-non-empty> → /api/v1/findings/{filename}
    - All other paths pass through unchanged.
    """
    url = url.split("?", 1)[0]
    if url.startswith("/api/v1/findings/") and len(url) > len("/api/v1/findings/"):
        return "/api/v1/findings/{filename}"
    return url


def _start_be(mission_dir: Path, port: int) -> subprocess.Popen:
    """Spawn `python -m megalodon_ui` with contract validation enabled."""
    env = {**os.environ, "M9_VALIDATE_CONTRACT": "1"}
    cmd = [
        "uv", "run",
        "--with", "fastapi",
        "--with", "uvicorn[standard]",
        "--with", "sse-starlette",
        "--with", "pyyaml",
        "--with", "pydantic",
        "python", "-m", "megalodon_ui",
        "--mission-dir", str(mission_dir),
        "--port", str(port),
    ]
    return subprocess.Popen(
        cmd, cwd=REPO_ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def _wait_be_ready(port: int, timeout: float = 30.0) -> bool:
    import urllib.error
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(
                f"http://localhost:{port}/api/v1/__contract_introspect__",
                timeout=1,
            )
            return True
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.3)
    return False


def _run_be_check(port: int) -> tuple[dict[str, Any], list[list[str]]]:
    """Returns (contract_dict, registered_routes)."""
    sys.path.insert(0, str(REPO_ROOT))
    from megalodon_ui.contract_loader import load_contract
    contract = load_contract(CONTRACT_PATH)

    import urllib.request
    with urllib.request.urlopen(
        f"http://localhost:{port}/api/v1/__contract_introspect__"
    ) as r:
        registered = json.loads(r.read())["registered"]
    return contract, registered


def _run_fe_check() -> list[dict[str, Any]]:
    """Run playwright contract-trace spec; parse fetched URLs from stdout."""
    cmd = [
        str(REPO_ROOT / "scripts" / "run_e2e.sh"),
        "--grep", "M2 contract-trace",
        "--reporter", "list",
    ]
    result = subprocess.run(
        cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=180,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise RuntimeError(f"playwright contract-trace failed (rc={result.returncode})")
    match = re.search(
        r"M9_CONTRACT_CALLS_BEGIN(.+?)M9_CONTRACT_CALLS_END",
        result.stdout,
        re.DOTALL,
    )
    if not match:
        raise RuntimeError("no contract calls captured from playwright output")
    return json.loads(match.group(1))


def _compare(
    contract: dict[str, Any],
    registered: list[list[str]],
    fetched: list[dict[str, Any]],
) -> dict[str, Any]:
    declared = {(e["method"], e["path"]) for e in contract["endpoints"]}
    reg = {(r[0], r[1]) for r in registered}

    undocumented: list[str] = []
    for call in fetched:
        normalized = _normalize_path(call["url"])
        if not normalized.startswith("/api/v1/"):
            continue
        if normalized.endswith("__contract_introspect__"):
            continue
        key = (call["method"], normalized)
        if key not in declared:
            undocumented.append(f'{call["method"]} {normalized}')

    # De-duplicate while preserving order.
    seen: set[str] = set()
    undocumented_unique: list[str] = []
    for u in undocumented:
        if u not in seen:
            seen.add(u)
            undocumented_unique.append(u)

    contracts: list[dict[str, str]] = []
    for method, path in sorted(declared):
        status = "ok" if (method, path) in reg else "missing"
        contracts.append({"endpoint": f"{method} {path}", "status": status})

    untested = sorted(reg - declared)
    schema_mismatches: list[str] = []  # v9 deferred per spec D6
    pass_ = (
        not undocumented_unique
        and not any(c["status"] != "ok" for c in contracts)
    )

    return {
        "pass": pass_,
        "contracts": contracts,
        "undocumented_fetches": undocumented_unique,
        "schema_mismatches": schema_mismatches,
        "untested_be_routes": [f"{m} {p}" for m, p in untested],
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="contract_scan")
    parser.add_argument("--soft", action="store_true",
                        help="Report findings but always exit 0")
    parser.add_argument("--mission-dir", default=str(DEFAULT_MISSION_DIR))
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)

    start = time.time()
    proc = _start_be(Path(args.mission_dir), args.port)
    try:
        if not _wait_be_ready(args.port):
            try:
                stderr_text = proc.stderr.read().decode("utf-8") if proc.stderr else ""
            except Exception:
                stderr_text = ""
            print(json.dumps({
                "pass": False,
                "error": "BE failed to start",
                "stderr": stderr_text[-2000:],
            }, indent=2))
            return 2

        contract, registered = _run_be_check(args.port)
        try:
            fetched = _run_fe_check()
        except Exception as e:
            print(json.dumps({"pass": False, "error": str(e)}, indent=2))
            return 3

        result = _compare(contract, registered, fetched)
        result["duration_seconds"] = round(time.time() - start, 2)
        print(json.dumps(result, indent=2))
        if args.soft:
            return 0
        return 0 if result["pass"] else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
