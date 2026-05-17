# V9 M2 — PRE-VERIFY Contract Scan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Ship `docs/v9/api-contract.md` (source of truth) + Pydantic schemas + BE startup validation + FE runtime fetch wrapper + `scripts/contract_scan.py` orchestrator + 14 pytest tests.

**Architecture:** Three-pronged enforcement — (a) doc as canonical, (b) BE startup cross-checks, (c) FE runtime instrumentation in test mode. All three must pass for P3 close.

**Tech Stack:** Python 3 (FastAPI, Pydantic, PyYAML), JS ES modules, Playwright TS.

**Spec reference:** `docs/superpowers/specs/2026-05-16-v9-m2-contract-scan-design.md` (18 sections).

**Dependency:** M4 (constants registry) — already shipped.

---

### Task 1: contract_loader skeleton + first tests

**Files:**
- Create: `megalodon_ui/contract_loader.py`
- Create: `scripts/tests/test_contract_loader.py`
- Create: `scripts/tests/fixtures/contracts/minimal.md`
- Create: `scripts/tests/fixtures/contracts/malformed.md`

- [ ] **Step 1: Write failing test**

```python
# scripts/tests/test_contract_loader.py
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


def test_empty_contract_returns_empty_endpoints():
    empty = FIXTURES / "empty.md"
    empty.write_text("# Empty\n\nNo endpoints here.\n", encoding="utf-8")
    contract = load_contract(empty)
    assert contract["endpoints"] == []
    empty.unlink()


def test_parses_sse_events():
    contract = load_contract(FIXTURES / "with_sse.md")
    sse_ep = next(e for e in contract["endpoints"] if e["path"] == "/api/v1/events")
    assert "status-change" in sse_ep["sse_events"]
    assert "heartbeat" in sse_ep["sse_events"]


def test_handles_path_templates():
    contract = load_contract(FIXTURES / "with_template.md")
    paths = [e["path"] for e in contract["endpoints"]]
    assert "/api/v1/findings/{filename}" in paths
```

- [ ] **Step 2: Create fixture contract files**

`scripts/tests/fixtures/contracts/minimal.md`:
```markdown
# Minimal Test Contract

## Endpoints

### GET /api/v1/state

```yaml
method: GET
path: /api/v1/state
response_model: StateResponse
status: 200
content_type: application/json
fe_consumers:
  - ui/static/js/sse.js:56
description: Returns full mission snapshot.
```
```

`scripts/tests/fixtures/contracts/malformed.md`:
```markdown
### GET /api/v1/broken

```yaml
method: GET
  path: /api/v1/broken    # bad indentation — will break YAML
response_model: BrokenModel
```
```

`scripts/tests/fixtures/contracts/with_sse.md`:
```markdown
### GET /api/v1/events

```yaml
method: GET
path: /api/v1/events
response_model: SSEStream
status: 200
content_type: text/event-stream
fe_consumers:
  - ui/static/js/sse.js:152
sse_events:
  - status-change
  - heartbeat
```
```

`scripts/tests/fixtures/contracts/with_template.md`:
```markdown
### GET /api/v1/findings/{filename}

```yaml
method: GET
path: /api/v1/findings/{filename}
response_model: FindingDetail
status: 200
content_type: application/json
```
```

- [ ] **Step 3: Run tests to verify failure**

Run: `cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest --with pyyaml python -m pytest scripts/tests/test_contract_loader.py -v`

Expected: ModuleNotFoundError.

- [ ] **Step 4: Implement contract_loader.py**

```python
"""V9 M2 — parse api-contract.md into structured dict."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_YAML_BLOCK_RE = re.compile(r"^```yaml\s*\n(.*?)\n```\s*$", re.MULTILINE | re.DOTALL)


class ContractParseError(ValueError):
    pass


def load_contract(path: Path) -> dict[str, Any]:
    """Parse api-contract.md → {"endpoints": [...]}."""
    text = path.read_text(encoding="utf-8")
    endpoints: list[dict[str, Any]] = []
    for match in _YAML_BLOCK_RE.finditer(text):
        try:
            block = yaml.safe_load(match.group(1))
        except yaml.YAMLError as e:
            raise ContractParseError(f"YAML parse error in {path}: {e}") from e
        if not isinstance(block, dict):
            continue
        if "method" not in block or "path" not in block:
            continue  # Not an endpoint block; skip
        endpoints.append(block)
    return {"endpoints": endpoints}
```

- [ ] **Step 5: Run tests, verify pass**

Run: `cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest --with pyyaml python -m pytest scripts/tests/test_contract_loader.py -v`

Expected: 5 PASS.

- [ ] **Step 6: Stage**

```bash
git add megalodon_ui/contract_loader.py scripts/tests/test_contract_loader.py scripts/tests/fixtures/contracts/
```

---

### Task 2: Author docs/v9/api-contract.md (full 11 endpoints)

**Files:**
- Create: `docs/v9/api-contract.md`

- [ ] **Step 1: Read existing factory route handlers to extract actual response shapes**

```bash
grep -nA 15 -E "@app\.(get|post)\(API_" /Users/dave/Documents/Projects/megalodon/megalodon_ui/server.py | head -200
```

Capture for each route: HTTP method, path, the response shape returned (look for `return {...}` patterns).

- [ ] **Step 2: Write `docs/v9/api-contract.md`**

Header:
```markdown
---
title: Megalodon v9 API Contract
status: CANONICAL
version: 1.0
utc: 2026-05-16
owner: orchestrator-Claude (v9); revisit v10
---

# Megalodon v9 API Contract

> Canonical source-of-truth for all FE→BE calls. BE startup validates this.
> FE runtime wrapper validates this in test mode. ANY drift fails P3 verify.
>
> See `docs/superpowers/specs/2026-05-16-v9-m2-contract-scan-design.md`.

## Conventions

- Path templates use `{param}` for path parameters.
- YAML blocks are extracted verbatim by `megalodon_ui.contract_loader`.
- Response shapes documented as Pydantic class skeletons; canonical models in
  `megalodon_ui/schemas.py`.
- SSE event names MUST match `megalodon_ui.constants.SSE_EVENT_TYPES`.

---

## Endpoints
```

Then for each of the 11 endpoints, write a section like:

```markdown
### GET /api/v1/state

\`\`\`yaml
method: GET
path: /api/v1/state
response_model: StateResponse
status: 200
content_type: application/json
fe_consumers:
  - ui/static/js/sse.js:56
description: Returns full mission snapshot for FE bootstrap.
\`\`\`

**Response shape (`StateResponse`):**
\`\`\`python
class StateResponse(BaseModel):
    status: StatusBlock
    tasks: list[TaskBlock]
    history: list[HistoryEntry]
    findings: list[FindingSummary]
    config: ConfigBlock
\`\`\`

---
```

Write all 11 endpoints listed in spec §3.2:
1. GET /api/v1/state
2. GET /api/v1/config
3. GET /api/v1/events (SSE)
4. GET /api/v1/findings
5. GET /api/v1/findings/{filename}
6. POST /api/v1/reclaim
7. POST /api/v1/signal
8. POST /api/v1/challenge
9. POST /api/v1/phase-flip
10. POST /api/v1/mission-status
11. POST /api/v1/inject-task

For each, capture the actual response shape from server.py.

- [ ] **Step 3: Verify contract_loader parses the real file**

```bash
cd /Users/dave/Documents/Projects/megalodon && uv run --with pyyaml python3 -c "
from megalodon_ui.contract_loader import load_contract
from pathlib import Path
c = load_contract(Path('docs/v9/api-contract.md'))
print(f'Loaded {len(c[\"endpoints\"])} endpoints')
for e in c['endpoints']:
    print(f'  {e[\"method\"]} {e[\"path\"]}')
"
```

Expected: 11 endpoints listed.

- [ ] **Step 4: Stage**

```bash
git add docs/v9/api-contract.md
```

---

### Task 3: megalodon_ui/schemas.py — Pydantic models + drift assert

**Files:**
- Create: `megalodon_ui/schemas.py`

- [ ] **Step 1: Write schemas module**

```python
"""V9 M2 — Pydantic response models for contract enforcement.

Top-level response shapes only; inner dicts (tasks/history/findings) stay
loose for v9 — tighten in v10. See spec D6.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from .constants import SSE_EVENT_TYPES


SSEEventName = Literal[
    "status-change", "task-change", "phase-flip", "finding-new",
    "history-append", "claim-create", "claim-done", "signal-new",
    "lagging", "heartbeat", "mission-status", "sync",
]
# Import-time drift assert: schemas.py SSEEventName must match constants.SSE_EVENT_TYPES
_declared = frozenset(SSEEventName.__args__)
_canonical = frozenset(SSE_EVENT_TYPES)
assert _declared == _canonical, (
    f"schemas.py SSEEventName drifted from constants.SSE_EVENT_TYPES: "
    f"missing={_canonical - _declared} extra={_declared - _canonical}"
)


class LaneStatus(BaseModel):
    lane: str
    agent: str | None = None
    state: str
    last_utc: str
    staleness_seconds: float
    is_stale: bool
    notes: str = ""


class StatusBlock(BaseModel):
    lanes: list[LaneStatus]
    current_phase: str


class StateResponse(BaseModel):
    status: StatusBlock
    tasks: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    config: dict[str, Any] = {}


class ConfigResponse(BaseModel):
    mission_dir: str
    port: int
    csrf_token: str | None = None


class FindingSummary(BaseModel):
    filename: str
    lane: str | None = None
    severity: str | None = None
    task_id: str | None = None
    mtime_utc: str


class FindingsListResponse(BaseModel):
    findings: list[FindingSummary]


class FindingDetailResponse(BaseModel):
    filename: str
    body: str
    frontmatter: dict[str, Any] = {}


class ActionResponse(BaseModel):
    """Generic POST-action acknowledgement (reclaim, signal, challenge, etc.)."""
    ok: bool
    message: str = ""
```

- [ ] **Step 2: Verify import-time assert passes**

```bash
cd /Users/dave/Documents/Projects/megalodon && uv run --with pydantic python3 -c "from megalodon_ui import schemas; print('OK')"
```

Expected: `OK`.

- [ ] **Step 3: Stage**

```bash
git add megalodon_ui/schemas.py
```

---

### Task 4: BE startup validation

**Files:**
- Modify: `megalodon_ui/server.py`
- Create: `scripts/tests/test_be_contract_validation.py`

- [ ] **Step 1: Write failing tests**

```python
# scripts/tests/test_be_contract_validation.py
"""V9 M2 BE-side contract validation tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from megalodon_ui.server import _validate_contract  # noqa: E402
from megalodon_ui.contract_loader import load_contract  # noqa: E402
from fastapi import FastAPI  # noqa: E402


def test_validates_passes_when_routes_match(tmp_path):
    contract_md = tmp_path / "contract.md"
    contract_md.write_text(
        '### GET /api/v1/state\n'
        '\n```yaml\n'
        'method: GET\npath: /api/v1/state\nresponse_model: StateResponse\nstatus: 200\n'
        'content_type: application/json\n'
        '```\n',
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
        '### GET /api/v1/bogus\n'
        '\n```yaml\n'
        'method: GET\npath: /api/v1/bogus\nresponse_model: BogusModel\nstatus: 200\n'
        'content_type: application/json\n'
        '```\n',
        encoding="utf-8",
    )
    app = FastAPI()

    with pytest.raises(RuntimeError, match="declared routes not registered"):
        _validate_contract(app, contract_md)
```

- [ ] **Step 2: Verify failure**

Run: `cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest --with pyyaml --with fastapi python -m pytest scripts/tests/test_be_contract_validation.py -v`

Expected: ImportError on `_validate_contract`.

- [ ] **Step 3: Add _validate_contract + hook into make_app**

In `megalodon_ui/server.py`, add (after imports, before `make_app`):

```python
def _validate_contract(app: FastAPI, contract_path: Path) -> None:
    """V9 M2 — assert declared routes match registered routes.

    Raises RuntimeError if a contract-declared route isn't registered.
    Warns (non-fatal) if a registered route isn't declared.
    """
    from .contract_loader import load_contract
    import warnings

    if not contract_path.exists():
        warnings.warn(f"api-contract.md not found at {contract_path} — skipping validation")
        return

    contract = load_contract(contract_path)
    registered: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if path and methods and path.startswith("/api/v1/"):
            for method in methods:
                registered.add((method, path))

    declared = {(e["method"], e["path"]) for e in contract["endpoints"]}
    introspect_path = "/api/v1/__contract_introspect__"
    registered_filtered = {r for r in registered if not r[1].endswith("__contract_introspect__")}

    missing = declared - registered_filtered
    if missing:
        raise RuntimeError(
            f"BE contract violation: declared routes not registered: {missing}"
        )
    extras = registered_filtered - declared
    if extras:
        warnings.warn(f"Routes registered but not in contract: {extras}")
```

In `make_app(...)`, after all `@app.get/post` decorations, before return:

```python
    # V9 M2 — contract validation (opt-in via env var until contract.md is complete)
    import os
    if os.environ.get("M9_VALIDATE_CONTRACT") == "1":
        contract_path = Path(__file__).resolve().parents[1] / "docs" / "v9" / "api-contract.md"
        _validate_contract(app, contract_path)

    return app
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest --with pyyaml --with fastapi python -m pytest scripts/tests/test_be_contract_validation.py -v`

Expected: 2 PASS.

- [ ] **Step 5: Smoke validation against real contract**

```bash
cd /Users/dave/Documents/Projects/megalodon && \
    M9_VALIDATE_CONTRACT=1 uv run --with fastapi --with "uvicorn[standard]" --with sse-starlette --with pyyaml --with pydantic \
    python -m megalodon_ui --mission-dir scripts/tests/fixtures/minimal_mission --port 8089 &
sleep 2
curl -s http://localhost:8089/api/v1/state > /dev/null && echo OK || echo FAIL
pkill -f "python -m megalodon_ui --mission-dir scripts/tests/fixtures"
```

Expected: `OK`. If validation fails (RuntimeError on startup → server doesn't bind → curl fails), iterate on api-contract.md until factory routes match.

- [ ] **Step 6: Stage**

```bash
git add megalodon_ui/server.py scripts/tests/test_be_contract_validation.py
```

---

### Task 5: BE introspection endpoint

**Files:**
- Modify: `megalodon_ui/server.py`

- [ ] **Step 1: Add endpoint**

In `make_app(...)` after the other `@app.get` decorations:

```python
    @app.get("/api/v1/__contract_introspect__")
    async def contract_introspect():
        """V9 M2 — list registered routes for contract scan."""
        return {
            "registered": sorted([
                [next(iter(r.methods), "GET"), r.path]
                for r in app.routes
                if hasattr(r, "path") and r.path.startswith("/api/v1/")
                and not r.path.endswith("__contract_introspect__")
            ]),
        }
```

- [ ] **Step 2: Smoke test**

```bash
cd /Users/dave/Documents/Projects/megalodon && \
    uv run --with fastapi --with "uvicorn[standard]" --with sse-starlette --with pyyaml --with pydantic \
    python -m megalodon_ui --mission-dir scripts/tests/fixtures/minimal_mission --port 8089 &
sleep 2
curl -s http://localhost:8089/api/v1/__contract_introspect__ | python3 -m json.tool
pkill -f "python -m megalodon_ui --mission-dir scripts/tests/fixtures"
```

Expected: JSON with `registered` list of 11+ routes.

- [ ] **Step 3: Stage**

```bash
git add megalodon_ui/server.py
```

---

### Task 6: FE contract-trace wrapper

**Files:**
- Create: `ui/static/js/contract-trace.js`
- Modify: `ui/static/index.html`

- [ ] **Step 1: Write wrapper**

`ui/static/js/contract-trace.js`:
```javascript
// V9 M2 — runtime fetch wrapper for contract scan instrumentation.
// Active only when window.__M9_CONTRACT_TRACE__ === true.
// In production (flag unset), this script is a no-op.

(function () {
  if (typeof window === "undefined" || !window.__M9_CONTRACT_TRACE__) return;

  const calls = [];
  const originalFetch = window.fetch.bind(window);
  const OriginalEventSource = window.EventSource;

  window.fetch = async function (input, init) {
    const url = typeof input === "string" ? input : input.url;
    const method =
      (init && init.method) ||
      (typeof input === "object" && input.method) ||
      "GET";
    calls.push({ kind: "fetch", method, url, ts: Date.now() });
    return originalFetch(input, init);
  };

  window.EventSource = function (url, options) {
    calls.push({ kind: "eventsource", method: "GET", url, ts: Date.now() });
    return new OriginalEventSource(url, options);
  };

  window.__M9_CONTRACT_CALLS__ = calls;
  console.info("[M9] contract-trace active");
})();
```

- [ ] **Step 2: Add script tag to index.html**

Add to `ui/static/index.html` `<head>` (BEFORE any other script tags so the wrapper installs first):

```html
    <script src="/static/js/contract-trace.js"></script>
```

- [ ] **Step 3: Verify wrapper is no-op when flag unset**

```bash
cd /Users/dave/Documents/Projects/megalodon && \
    uv run --with fastapi --with "uvicorn[standard]" --with sse-starlette --with pyyaml --with pydantic \
    python -m megalodon_ui --mission-dir scripts/tests/fixtures/minimal_mission --port 8089 &
sleep 2
curl -s http://localhost:8089/static/js/contract-trace.js | head -5
curl -s http://localhost:8089/static/index.html | grep contract-trace
pkill -f "python -m megalodon_ui --mission-dir scripts/tests/fixtures"
```

Expected: script content shown + script tag present in HTML.

- [ ] **Step 4: Stage**

```bash
git add ui/static/js/contract-trace.js ui/static/index.html
```

---

### Task 7: Playwright contract-trace spec

**Files:**
- Create: `ui/tests/e2e/contract-trace.spec.ts`

- [ ] **Step 1: Write spec**

```typescript
import { test, expect } from '@playwright/test';

test('M2 contract-trace — walks SPA, dumps fetched URLs', async ({ page }) => {
  await page.addInitScript(() => { (window as any).__M9_CONTRACT_TRACE__ = true; });
  await page.goto('/static/index.html');

  // Wait for dashboard to render (proves /api/v1/state fired).
  await page.waitForSelector('[data-testid^="lane-row-"]', { timeout: 15000 });

  // Visit other SPA routes that trigger their own fetches.
  await page.goto('/static/index.html#/findings');
  await page.waitForLoadState('networkidle');
  await page.goto('/static/index.html#/mission');
  await page.waitForLoadState('networkidle');

  // Let SSE settle.
  await page.waitForTimeout(2000);

  const calls = await page.evaluate(() => (window as any).__M9_CONTRACT_CALLS__);
  expect(Array.isArray(calls)).toBe(true);
  expect(calls.length).toBeGreaterThan(0);

  // Emit calls to stdout for contract_scan.py to parse.
  console.log('M9_CONTRACT_CALLS_BEGIN' + JSON.stringify(calls) + 'M9_CONTRACT_CALLS_END');
});
```

- [ ] **Step 2: Smoke run**

```bash
cd /Users/dave/Documents/Projects/megalodon && \
    ./scripts/run_e2e.sh --grep "M2 contract-trace" --reporter list 2>&1 | tee /tmp/m2-trace.log
grep -c "M9_CONTRACT_CALLS_BEGIN" /tmp/m2-trace.log
```

Expected: 1 (one matching line, calls JSON present).

- [ ] **Step 3: Stage**

```bash
git add ui/tests/e2e/contract-trace.spec.ts
```

---

### Task 8: scripts/contract_scan.py orchestrator + tests

**Files:**
- Create: `scripts/contract_scan.py`
- Create: `scripts/tests/test_contract_scan.py`

- [ ] **Step 1: Write failing test skeleton**

```python
# scripts/tests/test_contract_scan.py
"""V9 M2 contract_scan tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import contract_scan  # noqa: E402


def test_normalize_path_strips_uuid():
    assert contract_scan._normalize_path("/api/v1/findings/abc12345") == \
        "/api/v1/findings/{filename}"


def test_normalize_path_passes_static_path():
    assert contract_scan._normalize_path("/api/v1/state") == "/api/v1/state"


def test_compare_pass(tmp_path):
    contract = {"endpoints": [
        {"method": "GET", "path": "/api/v1/state"},
        {"method": "POST", "path": "/api/v1/reclaim"},
    ]}
    registered = [["GET", "/api/v1/state"], ["POST", "/api/v1/reclaim"]]
    fetched = [{"method": "GET", "url": "/api/v1/state"},
               {"method": "POST", "url": "/api/v1/reclaim"}]
    result = contract_scan._compare(contract, registered, fetched)
    assert result["pass"] is True
    assert result["undocumented_fetches"] == []


def test_compare_undocumented_fetch_fails():
    contract = {"endpoints": [{"method": "GET", "path": "/api/v1/state"}]}
    registered = [["GET", "/api/v1/state"]]
    fetched = [{"method": "GET", "url": "/api/v1/state"},
               {"method": "GET", "url": "/api/v1/secret"}]
    result = contract_scan._compare(contract, registered, fetched)
    assert result["pass"] is False
    assert "/api/v1/secret" in str(result["undocumented_fetches"])


def test_soft_mode_returns_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(contract_scan, "_run_be_check", lambda *a, **k: ({"endpoints": []}, []))
    monkeypatch.setattr(contract_scan, "_run_fe_check", lambda *a, **k: [])
    rc = contract_scan.main(["--soft"])
    assert rc == 0
```

- [ ] **Step 2: Implement contract_scan.py**

```python
"""V9 M2 — PRE-VERIFY contract scan orchestrator.

Runs three checks (BE startup, route introspect, FE runtime trace) and
emits a JSON report to stdout.

Exit codes:
  0 = pass (or --soft)
  1 = drift detected (strict mode)
  2 = BE failed to start
  3 = playwright failed
"""
from __future__ import annotations

import argparse
import json
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

_UUID_RE = re.compile(r"/[a-f0-9-]{8,}")
_FILENAME_RE = re.compile(r"/[\w.\-]+\.md")


def _normalize_path(url: str) -> str:
    """Normalize concrete paths to contract templates.

    /api/v1/findings/abc12345.md → /api/v1/findings/{filename}
    /api/v1/findings/abc12345    → /api/v1/findings/{filename}
    """
    # Strip query string.
    url = url.split("?", 1)[0]
    if url.startswith("/api/v1/findings/") and url != "/api/v1/findings/":
        return "/api/v1/findings/{filename}"
    return url


def _start_be(mission_dir: Path, port: int) -> subprocess.Popen:
    """Spawn `python -m megalodon_ui` with contract validation enabled."""
    env = {"M9_VALIDATE_CONTRACT": "1", **dict(__import__("os").environ)}
    cmd = [
        "uv", "run",
        "--with", "fastapi", "--with", "uvicorn[standard]",
        "--with", "sse-starlette", "--with", "pyyaml", "--with", "pydantic",
        "python", "-m", "megalodon_ui",
        "--mission-dir", str(mission_dir), "--port", str(port),
    ]
    return subprocess.Popen(cmd, cwd=REPO_ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _wait_be_ready(port: int, timeout: float = 10.0) -> bool:
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://localhost:{port}/api/v1/__contract_introspect__",
                                   timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def _run_be_check(port: int) -> tuple[dict[str, Any], list[list[str]]]:
    """Returns (contract_dict, registered_routes)."""
    sys.path.insert(0, str(REPO_ROOT))
    from megalodon_ui.contract_loader import load_contract
    contract = load_contract(CONTRACT_PATH)

    import urllib.request
    with urllib.request.urlopen(f"http://localhost:{port}/api/v1/__contract_introspect__") as r:
        registered = json.loads(r.read())["registered"]
    return contract, registered


def _run_fe_check(port: int) -> list[dict[str, Any]]:
    """Run playwright contract-trace spec; parse fetched URLs from stdout."""
    cmd = [str(REPO_ROOT / "scripts" / "run_e2e.sh"),
           "--grep", "M2 contract-trace", "--reporter", "list"]
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                            text=True, timeout=120)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise RuntimeError("playwright contract-trace failed")
    m = re.search(r"M9_CONTRACT_CALLS_BEGIN(.+?)M9_CONTRACT_CALLS_END", result.stdout)
    if not m:
        raise RuntimeError("no contract calls captured from playwright output")
    return json.loads(m.group(1))


def _compare(contract: dict[str, Any], registered: list[list[str]],
             fetched: list[dict[str, Any]]) -> dict[str, Any]:
    declared = {(e["method"], e["path"]) for e in contract["endpoints"]}
    reg = {(r[0], r[1]) for r in registered}

    undocumented: list[str] = []
    for call in fetched:
        normalized = _normalize_path(call["url"])
        if not normalized.startswith("/api/v1/"):
            continue
        key = (call["method"], normalized)
        if key not in declared:
            undocumented.append(f'{call["method"]} {normalized}')

    contracts = []
    for method, path in sorted(declared):
        if (method, path) in reg:
            contracts.append({"endpoint": f"{method} {path}", "status": "ok"})
        else:
            contracts.append({"endpoint": f"{method} {path}", "status": "missing"})

    untested = sorted(reg - declared)
    schema_mismatches: list[str] = []  # v9 deferred per spec D6
    pass_ = not undocumented and not any(c["status"] != "ok" for c in contracts)

    return {
        "pass": pass_,
        "contracts": contracts,
        "undocumented_fetches": undocumented,
        "schema_mismatches": schema_mismatches,
        "untested_be_routes": [f"{m} {p}" for m, p in untested],
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="contract_scan")
    parser.add_argument("--soft", action="store_true")
    parser.add_argument("--mission-dir", default=str(DEFAULT_MISSION_DIR))
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)

    start = time.time()
    proc = _start_be(Path(args.mission_dir), args.port)
    try:
        if not _wait_be_ready(args.port):
            stderr = proc.stderr.read().decode("utf-8") if proc.stderr else ""
            print(json.dumps({"pass": False, "error": "BE failed to start", "stderr": stderr[:1000]},
                             indent=2))
            return 2

        contract, registered = _run_be_check(args.port)
        try:
            fetched = _run_fe_check(args.port)
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
```

- [ ] **Step 3: Run tests, verify pass**

Run: `cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest python -m pytest scripts/tests/test_contract_scan.py -v`

Expected: 5 PASS.

- [ ] **Step 4: Stage**

```bash
git add scripts/contract_scan.py scripts/tests/test_contract_scan.py
```

---

### Task 9: End-to-end smoke (positive)

- [ ] **Step 1: Run full contract scan against live factory + minimal mission**

```bash
cd /Users/dave/Documents/Projects/megalodon && \
    uv run --with pyyaml --with pydantic python3 scripts/contract_scan.py 2>&1 | tee /tmp/m2-positive.log
```

Expected: JSON output with `"pass": true`, `undocumented_fetches: []`, `schema_mismatches: []`.

If `pass: false`: iterate on `docs/v9/api-contract.md` (add missing endpoint or fix path template) and re-run. Iterate until clean.

- [ ] **Step 2: Capture stdout for HISTORY**

```bash
cp /tmp/m2-positive.log /tmp/m2-baseline.json
```

---

### Task 10: End-to-end smoke (negative — drift detection)

- [ ] **Step 1: Comment out one endpoint in api-contract.md temporarily**

Edit `docs/v9/api-contract.md`: wrap the `### GET /api/v1/findings` block in HTML comment so it's hidden from loader.

- [ ] **Step 2: Re-run contract scan**

```bash
cd /Users/dave/Documents/Projects/megalodon && \
    uv run --with pyyaml --with pydantic python3 scripts/contract_scan.py 2>&1 | tee /tmp/m2-negative.log
echo "Exit code: $?"
```

Expected: exit 1, `pass: false`, `undocumented_fetches` includes `GET /api/v1/findings`.

- [ ] **Step 3: Revert api-contract.md edit**

Re-run positive smoke from Task 9 step 1 → confirm `pass: true` again.

---

### Task 11: Full pytest suite + final stage

- [ ] **Step 1: Full pytest run**

```bash
cd /Users/dave/Documents/Projects/megalodon && \
    uv run --with pytest --with pyyaml --with fastapi --with pydantic \
    python -m pytest scripts/tests/ -v
```

Expected: 102 existing + 14 new M2 = 116 PASS.

- [ ] **Step 2: HISTORY.md entry**

Append:
```markdown
## 2026-05-16T~22:30Z — V9 M2 COMPLETE — PRE-VERIFY contract scan

V9-ROADMAP Migration plan §3c shipped (post-CR-3 + CR-7 source-of-truth + runtime-instrumentation pivot).

**Created:**
- `docs/v9/api-contract.md` — 11 factory `/api/v1/*` endpoints declared as canonical (TIER-1 spec).
- `megalodon_ui/contract_loader.py` — parses api-contract.md into structured dict.
- `megalodon_ui/schemas.py` — Pydantic response models + import-time SSE drift assert vs constants.SSE_EVENT_TYPES.
- `ui/static/js/contract-trace.js` — runtime fetch + EventSource wrapper (test-mode only, no-op in production).
- `ui/tests/e2e/contract-trace.spec.ts` — playwright spec that walks SPA + dumps fetched URLs.
- `scripts/contract_scan.py` — CLI orchestrator (BE start + introspect + FE trace + diff).
- `scripts/tests/test_contract_loader.py` (5 tests), `test_be_contract_validation.py` (2 tests), `test_contract_scan.py` (5 tests).
- `scripts/tests/fixtures/contracts/` — fixture contracts for loader tests.

**Modified:**
- `megalodon_ui/server.py` — `_validate_contract()` opt-in via `M9_VALIDATE_CONTRACT=1`, `/api/v1/__contract_introspect__` endpoint.
- `ui/static/index.html` — loads `contract-trace.js` first in `<head>`.

**Tests:** 116 pytest total (102 existing + 14 new), all PASS.

**Smoke validated:** positive scan passes (all 11 contracts ok, no undocumented fetches); negative scan (one endpoint commented out) correctly exits 1 with the missing endpoint reported in `undocumented_fetches`.

**Operator action:** to use contract scan in P3 verify, invoke `python3 scripts/contract_scan.py` after factory boots. Exit 0 = pass; exit 1 = drift. JSON output captures details.
```

- [ ] **Step 3: Stage HISTORY**

```bash
git add HISTORY.md
```

---

## Self-review

- [ ] All 14 tests have actual test bodies (no TODO).
- [ ] BE validation hook is opt-in (M9_VALIDATE_CONTRACT=1) until contract.md complete — won't break dev.
- [ ] FE wrapper is no-op without `window.__M9_CONTRACT_TRACE__` flag — won't impact production.
- [ ] Path normalization handles `/api/v1/findings/{filename}` template per CR-3 normalization rule.
- [ ] Introspect endpoint excluded from validation (double-underscore convention).
- [ ] No git commits per operator policy.
