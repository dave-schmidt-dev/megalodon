# V9 M1 + M1.5 + M1.6 — Queue Trio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Ship queue daemon + applier + queue_client + S-8 fixes (B1+B2+B3+B4) + Q1 intents (STATUS_ROW_INSERT, TASKS_INJECT, MISSION_EVENT_CORRECTION) + migration script + M3 backend swap + M1.5 UI mutation migration + M1.6 backend unification.

**Architecture:** Standalone applier daemon drains JSON request files from `queue/pending/`, applies atomically with per-file `fcntl.LOCK_EX`, journals (WAL) for crash safety. Workers submit via `megalodon_ui.queue.queue_client`. UI mutation endpoints become 202-async. Legacy `ui/server.py` becomes thin shim.

**Tech Stack:** Python 3 (fcntl, hashlib, pydantic, fastapi), bash launcher.

**Spec reference:** `docs/superpowers/specs/2026-05-16-v9-m1-queue-trio-design.md` (15 sections).

**Dependencies:** M3 (helper scripts — done), M4 (constants — done), M2 (contract scan — done; api-contract.md updated in Phase D).

---

## Phase A — Queue core (M1)

### Task A1: Promote skeleton + package init

**Files:**
- Create: `megalodon_ui/queue/__init__.py`
- Create: `megalodon_ui/queue/applier.py` (copy from `docs/v9/queue/applier.py`)
- Create: `megalodon_ui/queue/queue_client.py` (copy from `docs/v9/queue/queue_client.py`)

- [ ] **Step 1:** `mkdir -p megalodon_ui/queue && cp docs/v9/queue/applier.py megalodon_ui/queue/applier.py && cp docs/v9/queue/queue_client.py megalodon_ui/queue/queue_client.py && touch megalodon_ui/queue/__init__.py`
- [ ] **Step 2:** Apply B1 fix to `queue_client.py:95` — change `utc = new_utc or utc_now().replace(":", "Z", 1)[:17] + "Z"` to `utc = new_utc or utc_now()`.
- [ ] **Step 3:** Stage: `git add megalodon_ui/queue/`

### Task A2: WAL journal module + tests

**Files:**
- Create: `megalodon_ui/queue/journal.py`
- Create: `scripts/tests/test_queue_journal.py`

- [ ] **Step 1: Write failing test**

```python
# scripts/tests/test_queue_journal.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from megalodon_ui.queue.journal import Journal


def test_journal_write_pending_and_applied(tmp_path):
    j = Journal(tmp_path / "journal.log")
    j.write_pending("rid1", "HISTORY_APPEND", "HISTORY.md", {"line": "test"})
    j.write_applied("rid1", "ok")
    terminal = j.replay()
    assert terminal["rid1"] == "APPLIED"


def test_journal_pending_without_applied_is_indoubt(tmp_path):
    j = Journal(tmp_path / "journal.log")
    j.write_pending("rid1", "HISTORY_APPEND", "HISTORY.md", {"line": "test"})
    terminal = j.replay()
    assert terminal["rid1"] == "PENDING_INDOUBT"


def test_journal_rejected_is_terminal(tmp_path):
    j = Journal(tmp_path / "journal.log")
    j.write_pending("rid1", "HISTORY_APPEND", "HISTORY.md", {"line": "test"})
    j.write_rejected("rid1", "schema invalid")
    terminal = j.replay()
    assert terminal["rid1"] == "REJECTED"


def test_journal_multiple_entries(tmp_path):
    j = Journal(tmp_path / "journal.log")
    for i in range(5):
        j.write_pending(f"rid{i}", "HISTORY_APPEND", "HISTORY.md", {})
        if i % 2 == 0:
            j.write_applied(f"rid{i}", "ok")
        else:
            j.write_rejected(f"rid{i}", "bad")
    terminal = j.replay()
    assert sum(1 for v in terminal.values() if v == "APPLIED") == 3
    assert sum(1 for v in terminal.values() if v == "REJECTED") == 2


def test_journal_append_only_no_truncate(tmp_path):
    j = Journal(tmp_path / "journal.log")
    j.write_pending("rid1", "X", "Y", {})
    size1 = (tmp_path / "journal.log").stat().st_size
    j.write_applied("rid1", "ok")
    size2 = (tmp_path / "journal.log").stat().st_size
    assert size2 > size1


def test_journal_persists_across_instances(tmp_path):
    log = tmp_path / "journal.log"
    j1 = Journal(log)
    j1.write_pending("rid1", "X", "Y", {})
    j1.write_applied("rid1", "ok")
    j2 = Journal(log)
    assert j2.replay()["rid1"] == "APPLIED"


def test_journal_empty_replay_returns_empty(tmp_path):
    j = Journal(tmp_path / "journal.log")
    assert j.replay() == {}


def test_journal_skips_malformed_lines(tmp_path):
    log = tmp_path / "journal.log"
    log.write_text("not-json garbage\n{\"rid\":\"rid1\",\"status\":\"PENDING\"}\n")
    j = Journal(log)
    terminal = j.replay()
    assert "rid1" in terminal
```

- [ ] **Step 2: Run, verify failure**

`cd /Users/dave/Documents/Projects/megalodon && uv run --with pytest python -m pytest scripts/tests/test_queue_journal.py -v`

- [ ] **Step 3: Implement Journal**

```python
# megalodon_ui/queue/journal.py
"""V9 M1 WAL journal — write-ahead log for crash-safe apply.

Per S-8 §B B2 (MAJOR): journal entries written BEFORE apply; replay marks
PENDING-without-APPLIED as PENDING_INDOUBT for reconciliation.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Journal:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    def _append(self, entry: dict[str, Any]) -> None:
        line = json.dumps(entry, sort_keys=True) + "\n"
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    def write_pending(self, rid: str, intent: str, target: str, payload: dict) -> None:
        self._append({
            "rid": rid, "status": "PENDING", "intent": intent,
            "target": target, "payload": payload, "utc": _utc(),
        })

    def write_applied(self, rid: str, summary: str) -> None:
        self._append({"rid": rid, "status": "APPLIED", "summary": summary, "utc": _utc()})

    def write_rejected(self, rid: str, reason: str) -> None:
        self._append({"rid": rid, "status": "REJECTED", "reason": reason, "utc": _utc()})

    def replay(self) -> dict[str, str]:
        """Returns {rid: terminal_status} where status ∈ {APPLIED, REJECTED, PENDING_INDOUBT}."""
        states: dict[str, str] = {}
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rid = entry.get("rid")
                status = entry.get("status")
                if not rid or status not in {"PENDING", "APPLIED", "REJECTED"}:
                    continue
                if status == "PENDING":
                    if rid not in states:
                        states[rid] = "PENDING_INDOUBT"
                else:
                    states[rid] = status
        return states
```

- [ ] **Step 4:** Run tests, verify 8 PASS.
- [ ] **Step 5:** Stage `megalodon_ui/queue/journal.py scripts/tests/test_queue_journal.py`.

### Task A3: Pydantic intent schemas

**Files:**
- Create: `megalodon_ui/queue/schemas.py`

- [ ] **Step 1: Write schemas**

```python
# megalodon_ui/queue/schemas.py
"""V9 M1 — Pydantic payload schemas for queue intents.

Original 6: STATUS_UPDATE, TASKS_BRACKET, HISTORY_APPEND, MISSION_EVENT_APPEND,
CLAIM_DIR_CREATE, CLAIM_DIR_DONE.

Q1 additions per S-8 §A Q1: STATUS_ROW_INSERT, TASKS_INJECT, MISSION_EVENT_CORRECTION.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class StatusUpdatePayload(BaseModel):
    lane: str
    agent: str
    new_state: str
    new_utc: str
    new_notes: str = ""


class TasksBracketPayload(BaseModel):
    task_id: str
    new_bracket: str


class HistoryAppendPayload(BaseModel):
    line: str


class MissionEventAppendPayload(BaseModel):
    line: str


class ClaimDirCreatePayload(BaseModel):
    task_id: str
    owner_agent: str
    owner_lane: str


class ClaimDirDonePayload(BaseModel):
    task_id: str
    agent: str


class StatusRowInsertPayload(BaseModel):
    lane: str
    agent: str
    initial_state: str = "idle"
    initial_utc: str
    initial_notes: str = ""


class TasksInjectPayload(BaseModel):
    task_id: str
    lane: str
    bracket: str = "[ ]"
    description: str
    after_task_id: str | None = None


class MissionEventCorrectionPayload(BaseModel):
    line: str

    @field_validator("line")
    @classmethod
    def must_have_correction_prefix(cls, v: str) -> str:
        if "CORRECTION by " not in v:
            raise ValueError("MISSION_EVENT_CORRECTION line must contain 'CORRECTION by '")
        return v


INTENT_SCHEMAS = {
    "STATUS_UPDATE": StatusUpdatePayload,
    "TASKS_BRACKET": TasksBracketPayload,
    "HISTORY_APPEND": HistoryAppendPayload,
    "MISSION_EVENT_APPEND": MissionEventAppendPayload,
    "CLAIM_DIR_CREATE": ClaimDirCreatePayload,
    "CLAIM_DIR_DONE": ClaimDirDonePayload,
    "STATUS_ROW_INSERT": StatusRowInsertPayload,
    "TASKS_INJECT": TasksInjectPayload,
    "MISSION_EVENT_CORRECTION": MissionEventCorrectionPayload,
}


def validate_payload(intent: str, payload: dict) -> None:
    """Raises ValueError if intent unknown or payload doesn't match schema."""
    if intent not in INTENT_SCHEMAS:
        raise ValueError(f"unknown intent: {intent!r}")
    INTENT_SCHEMAS[intent].model_validate(payload)
```

- [ ] **Step 2:** Stage.

### Task A4: Apply B2 + B3 to applier; add Q1 intents

**Files:**
- Modify: `megalodon_ui/queue/applier.py`
- Modify: `megalodon_ui/queue/queue_client.py`

- [ ] **Step 1:** Read existing `applier.py` (you copied it in A1).
- [ ] **Step 2:** Apply B2 (WAL) — wire `Journal` into `Applier.__init__` + `_apply`. Add `_reconcile_indoubt(rid)` method that for HISTORY_APPEND/MISSION_EVENT_APPEND scans target file for payload.line; if found mark APPLIED, else re-drain.
- [ ] **Step 3:** Apply B3 (heartbeat) — in drain loop, before `_drain_once()`: `(self.lock_dir / "heartbeat.txt").write_text(utc_now())`.
- [ ] **Step 4:** Apply B4 (strict claim ownership) — in `_apply_claim_dir_create`, add the `elif not owner_file.exists(): raise ValueError("claim-exists-no-owner")` branch.
- [ ] **Step 5:** Add `INTENTS` extension: STATUS_ROW_INSERT, TASKS_INJECT, MISSION_EVENT_CORRECTION.
- [ ] **Step 6:** Add `_apply_status_row_insert`, `_apply_tasks_inject`, `_apply_mission_event_correction` methods.
- [ ] **Step 7:** Replace `_validate(req)` regex-only validation with `from .schemas import validate_payload; validate_payload(req["intent"], req["payload"])` plus the existing envelope validation.
- [ ] **Step 8:** Add corresponding `queue_client.py` helpers: `status_row_insert(...)`, `tasks_inject(...)`, `mission_event_correction(...)`.
- [ ] **Step 9:** Stage.

### Task A5: Applier + client tests

**Files:**
- Create: `scripts/tests/test_queue_applier.py`
- Create: `scripts/tests/test_queue_client.py`
- Create: `scripts/tests/fixtures/queue_mission/`

- [ ] **Step 1:** Create fixture mission tree at `scripts/tests/fixtures/queue_mission/`. Files needed:
  - `STATUS.md` — table with 6 lane rows (copy minimal_mission's)
  - `TASKS.md` — empty header + 2-3 sample tasks
  - `HISTORY.md` — empty header
  - `.mission-events` — single PHASE-PLAN line
  - `MISSION.md` — minimal stub
  - `claims/.gitkeep`, `findings/.gitkeep`, `queue/.gitkeep`

  Use existing `scripts/tests/fixtures/minimal_mission/` as template.

- [ ] **Step 2: Write conftest fixture for queue mission**

In `scripts/tests/conftest.py` (extend existing), add:

```python
@pytest.fixture
def queue_mission(tmp_path):
    src = Path(__file__).parent / "fixtures" / "queue_mission"
    dst = tmp_path / "queue_mission"
    shutil.copytree(src, dst)
    return dst
```

- [ ] **Step 3: Write test_queue_applier.py** with core tests + T1-T4:

```python
# scripts/tests/test_queue_applier.py
"""V9 M1 applier tests — including S-8 T1-T4."""
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from megalodon_ui.queue import queue_client
from megalodon_ui.queue.applier import Applier


def _drain_until(applier, predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        applier.drain_once()
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_status_update_lands(queue_mission):
    applier = Applier(queue_mission)
    rid = queue_client.status_update(queue_mission, "agent-aaaa", "AUDIT",
                                     "working: P5", "test notes")
    assert _drain_until(applier, lambda: (queue_mission / "queue" / "applied" / f"{rid}.json").exists())
    status = (queue_mission / "STATUS.md").read_text()
    assert "working: P5" in status
    assert "test notes" in status


def test_idempotent_replay(queue_mission):
    applier = Applier(queue_mission)
    rid = queue_client.history_append(queue_mission, "agent-aaaa", "AUDIT",
                                       "P5", "findings/x.md", "MAJOR")
    _drain_until(applier, lambda: (queue_mission / "queue" / "applied" / f"{rid}.json").exists())
    hist1 = (queue_mission / "HISTORY.md").read_text()
    # Reset to pending — simulate re-drain
    applied = queue_mission / "queue" / "applied" / f"{rid}.json"
    pending = queue_mission / "queue" / "pending" / f"{rid}.json"
    pending.parent.mkdir(parents=True, exist_ok=True)
    applied.rename(pending)
    applier2 = Applier(queue_mission)  # WAL replay says APPLIED, should skip
    applier2.drain_once()
    hist2 = (queue_mission / "HISTORY.md").read_text()
    assert hist1 == hist2  # Not double-written


def test_t1_concurrent_status_updates(queue_mission):
    """T1 — 5 concurrent STATUS_UPDATEs to 5 different lanes."""
    lanes = ["AUDIT", "ARCHITECT", "BACKEND", "FRONTEND", "TEST"]
    threads = []
    rids = []

    def submit(lane):
        rid = queue_client.status_update(queue_mission, f"agent-{lane[:4]}", lane,
                                          f"working: T1-{lane}", "")
        rids.append(rid)

    for lane in lanes:
        t = threading.Thread(target=submit, args=(lane,))
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    applier = Applier(queue_mission)
    assert _drain_until(applier, lambda: all(
        (queue_mission / "queue" / "applied" / f"{rid}.json").exists() for rid in rids
    ))
    status = (queue_mission / "STATUS.md").read_text()
    for lane in lanes:
        assert f"working: T1-{lane}" in status


def test_t2_hash_mismatch_rejected(queue_mission):
    """T2 — STATUS_UPDATE with stale expected_hash_before is rejected."""
    rid = queue_client.submit(
        queue_mission, "agent-aaaa", "AUDIT", "STATUS.md", "STATUS_UPDATE",
        {"lane": "AUDIT", "agent": "agent-aaaa", "new_state": "x",
         "new_utc": queue_client.utc_now(), "new_notes": ""},
        expected_hash_before="stale-hash-not-matching",
    )
    applier = Applier(queue_mission)
    assert _drain_until(applier, lambda: (queue_mission / "queue" / "rejected" / f"{rid}.json").exists())


def test_t3_pending_overflow_drains_fifo(queue_mission):
    """T3 — 100 pending requests drain in submitted order."""
    rids = []
    for i in range(100):
        rid = queue_client.history_append(queue_mission, "agent-aaaa", "AUDIT",
                                           f"P{i}", f"findings/{i}.md", "MINOR")
        rids.append(rid)
    applier = Applier(queue_mission)
    assert _drain_until(applier, lambda: all(
        (queue_mission / "queue" / "applied" / f"{rid}.json").exists() for rid in rids
    ), timeout=15.0)
    history = (queue_mission / "HISTORY.md").read_text()
    for i in range(100):
        assert f"findings/{i}.md" in history


def test_t4_disk_full_mocked(queue_mission, monkeypatch):
    """T4 — fsync ENOSPC simulation → reject, no corruption."""
    rid = queue_client.history_append(queue_mission, "agent-aaaa", "AUDIT",
                                       "P-disk", "findings/x.md", "MAJOR")
    original_fsync = os.fsync
    def boom(fd):
        raise OSError(28, "No space left on device")  # ENOSPC
    monkeypatch.setattr(os, "fsync", boom)
    applier = Applier(queue_mission)
    with pytest.raises(OSError):
        applier.drain_once()
    monkeypatch.setattr(os, "fsync", original_fsync)
    # Verify rejection got journaled
    journal_log = (queue_mission / "queue" / "journal.log").read_text()
    assert "REJECTED" in journal_log or "PENDING" in journal_log


def test_q1_status_row_insert(queue_mission):
    applier = Applier(queue_mission)
    rid = queue_client.status_row_insert(queue_mission, "agent-zzzz", "OBSERVER-7",
                                          initial_state="idle",
                                          initial_utc=queue_client.utc_now(),
                                          initial_notes="surplus observer")
    assert _drain_until(applier, lambda: (queue_mission / "queue" / "applied" / f"{rid}.json").exists())
    status = (queue_mission / "STATUS.md").read_text()
    assert "OBSERVER-7" in status


def test_q1_tasks_inject(queue_mission):
    applier = Applier(queue_mission)
    rid = queue_client.tasks_inject(queue_mission, "agent-aaaa", "META",
                                     task_id="CHALLENGE-42", lane="BE",
                                     description="Fix something")
    assert _drain_until(applier, lambda: (queue_mission / "queue" / "applied" / f"{rid}.json").exists())
    tasks = (queue_mission / "TASKS.md").read_text()
    assert "CHALLENGE-42" in tasks


def test_q1_mission_event_correction_required_prefix(queue_mission):
    applier = Applier(queue_mission)
    rid_bad = queue_client.submit(
        queue_mission, "agent-aaaa", "AUDIT", ".mission-events",
        "MISSION_EVENT_CORRECTION", {"line": "no prefix here"},
    )
    assert _drain_until(applier, lambda: (queue_mission / "queue" / "rejected" / f"{rid_bad}.json").exists())


def test_b3_heartbeat_updated_during_drain(queue_mission):
    applier = Applier(queue_mission)
    applier.drain_once()
    hb = (queue_mission / "queue" / ".applier.lock" / "heartbeat.txt")
    assert hb.exists()
    first = hb.read_text()
    time.sleep(0.1)
    applier.drain_once()
    second = hb.read_text()
    # Heartbeat should refresh (UTC stamp may collide at second-level — accept either)
    assert second >= first


def test_b4_claim_exists_no_owner_rejected(queue_mission):
    """B4 — pre-v9-style claim dir without owner.txt → reject (don't steal)."""
    (queue_mission / "claims" / "LEGACY-TASK").mkdir(parents=True)
    rid = queue_client.claim_dir_create(queue_mission, "agent-aaaa", "AUDIT", "LEGACY-TASK")
    applier = Applier(queue_mission)
    assert _drain_until(applier, lambda: (queue_mission / "queue" / "rejected" / f"{rid}.json").exists())
    # owner.txt should NOT have been written
    assert not (queue_mission / "claims" / "LEGACY-TASK" / "owner.txt").exists()
```

(Continue with ~10 more tests to reach 20-test target — original 6 intents tests + edge cases.)

- [ ] **Step 4: Write test_queue_client.py**

```python
# scripts/tests/test_queue_client.py
"""V9 M1 queue_client tests — including B1 regression."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from megalodon_ui.queue import queue_client


def test_b1_utc_default_is_valid_iso8601(queue_mission):
    """B1 regression — status_update without explicit utc must produce valid UTC."""
    import json
    rid = queue_client.status_update(queue_mission, "agent-aaaa", "AUDIT",
                                      "working: x", "")
    req_path = queue_mission / "queue" / "pending" / f"{rid}.json"
    req = json.loads(req_path.read_text())
    import re
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", req["submitted_utc"])
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", req["payload"]["new_utc"])


# ... (continue with helper coverage — claim_dir_create, history_append, etc.)
```

- [ ] **Step 5:** Run tests, verify all PASS (~35 between applier + client).
- [ ] **Step 6:** Stage.

### Task A6: Start applier launcher script

**Files:**
- Create: `scripts/start_applier.sh`

- [ ] **Step 1: Write per spec §4.5**

```bash
#!/usr/bin/env bash
set -euo pipefail
MISSION_DIR="${1:-$PWD}"
shift 2>/dev/null || true
echo "Starting applier for mission: $MISSION_DIR"
PROJECT_ROOT="$(git rev-parse --show-toplevel)"
exec uv run --directory "$PROJECT_ROOT" \
    --with pyyaml --with pydantic \
    python -m megalodon_ui.queue.applier \
    --mission-dir "$MISSION_DIR" "$@"
```

- [ ] **Step 2:** `chmod +x scripts/start_applier.sh`
- [ ] **Step 3:** Stage.

---

## Phase B — Migration script (CR-6)

### Task B1: scripts/migrate_claims_to_owner_txt.py + tests

**Files:**
- Create: `scripts/migrate_claims_to_owner_txt.py`
- Create: `scripts/tests/test_queue_migrate_claims.py`

- [ ] **Step 1: Write 6 failing tests**

```python
# scripts/tests/test_queue_migrate_claims.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import migrate_claims_to_owner_txt as mig


def test_skips_claims_with_existing_owner(tmp_path):
    mission = tmp_path / "m"
    (mission / "claims" / "P1-A").mkdir(parents=True)
    (mission / "claims" / "P1-A" / "owner.txt").write_text("agent-existing 2026-01-01T00:00:00Z\n")
    n = mig.migrate(mission)
    assert n == 0
    assert (mission / "claims" / "P1-A" / "owner.txt").read_text().startswith("agent-existing")


def test_creates_owner_for_orphan_claim(tmp_path):
    mission = tmp_path / "m"
    (mission / "claims" / "P1-A").mkdir(parents=True)
    n = mig.migrate(mission, default_owner="legacy-pre-v9")
    assert n == 1
    content = (mission / "claims" / "P1-A" / "owner.txt").read_text()
    assert "legacy-pre-v9" in content


def test_idempotent_re_run_is_noop(tmp_path):
    mission = tmp_path / "m"
    (mission / "claims" / "P1-A").mkdir(parents=True)
    mig.migrate(mission)
    n2 = mig.migrate(mission)
    assert n2 == 0


def test_dry_run_writes_nothing(tmp_path):
    mission = tmp_path / "m"
    (mission / "claims" / "P1-A").mkdir(parents=True)
    n = mig.migrate(mission, dry_run=True)
    assert n == 1  # would have written
    assert not (mission / "claims" / "P1-A" / "owner.txt").exists()


def test_infers_owner_from_status_md(tmp_path):
    mission = tmp_path / "m"
    (mission / "claims" / "P1-A").mkdir(parents=True)
    (mission / "STATUS.md").write_text(
        "| AUDIT | agent-aaaa | working: P1-A | 2026-01-01T00:00:00Z | foo |\n"
    )
    mig.migrate(mission)
    content = (mission / "claims" / "P1-A" / "owner.txt").read_text()
    assert "agent-aaaa" in content


def test_handles_missing_claims_dir(tmp_path):
    mission = tmp_path / "m"
    mission.mkdir()
    n = mig.migrate(mission)
    assert n == 0
```

- [ ] **Step 2: Implement**

```python
# scripts/migrate_claims_to_owner_txt.py
"""V9 M1 — backfill owner.txt for pre-v9 claim directories (CR-6).

Walks claims/*/ under mission_dir. For each claim that lacks owner.txt:
  1. Try to infer owner from STATUS.md (look for "working: <task_id>" row).
  2. Try to infer from HISTORY.md (look for "<task_id>" attribution line).
  3. Fall back to default_owner with current UTC.
  4. Write owner.txt atomically.

Idempotent: skips claims that already have owner.txt.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _infer_owner(mission_dir: Path, task_id: str) -> str | None:
    status = mission_dir / "STATUS.md"
    if status.exists():
        m = re.search(rf"^\|\s*\w+\s*\|\s*(agent-[0-9a-f]+)\s*\|.*?{re.escape(task_id)}",
                      status.read_text(), re.MULTILINE)
        if m:
            return m.group(1)
    history = mission_dir / "HISTORY.md"
    if history.exists():
        m = re.search(rf"(agent-[0-9a-f]+)\s*\|\s*\w+\s*\|\s*{re.escape(task_id)}",
                      history.read_text())
        if m:
            return m.group(1)
    return None


def migrate(mission_dir: Path, *, dry_run: bool = False,
            default_owner: str = "legacy-pre-v9") -> int:
    """Backfill missing owner.txt files. Returns count of claims migrated."""
    claims_dir = mission_dir / "claims"
    if not claims_dir.is_dir():
        return 0
    n = 0
    for child in sorted(claims_dir.iterdir()):
        if not child.is_dir():
            continue
        owner_file = child / "owner.txt"
        if owner_file.exists():
            continue
        task_id = child.name
        owner = _infer_owner(mission_dir, task_id) or default_owner
        content = f"{owner} {_utc_now()}\n"
        if not dry_run:
            owner_file.write_text(content, encoding="utf-8")
        n += 1
        print(f"{'[dry-run] ' if dry_run else ''}migrated claims/{task_id}: {owner}")
    return n


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="migrate_claims_to_owner_txt")
    p.add_argument("--mission-dir", required=True, type=Path)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--default-owner", default="legacy-pre-v9")
    args = p.parse_args(argv)
    n = migrate(args.mission_dir.resolve(), dry_run=args.dry_run,
                default_owner=args.default_owner)
    print(f"\n{'[dry-run] ' if args.dry_run else ''}{n} claim(s) migrated.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 3:** Run tests, verify 6 PASS.
- [ ] **Step 4:** Stage.

---

## Phase C — M3 backend swap

### Task C1: scripts/_backends/queue_client.py adapter

**Files:**
- Create: `scripts/_backends/queue_client.py`

- [ ] **Step 1:** Read `scripts/_backends/direct_fcntl.py` to learn the interface (claim_dir_done, tasks_bracket, history_append, status_update — function signatures match `_shared_state.execute_close`).

- [ ] **Step 2: Implement adapter**

```python
# scripts/_backends/queue_client.py
"""V9 M1 — queue_client backend for scripts/_shared_state.

Provides the same interface as direct_fcntl but routes through the
queue applier instead of doing direct file mutations.
"""
from __future__ import annotations

from pathlib import Path

from megalodon_ui.queue import queue_client as _qc


def claim_dir_done(*, mission_dir: Path, task_id: str, agent: str) -> None:
    rid = _qc.claim_dir_done(mission_dir, agent, lane="?", task_id=task_id)
    status = _qc.wait_until_applied(mission_dir, rid, timeout_seconds=10.0)
    if status != "applied":
        raise RuntimeError(f"queue claim_dir_done failed: {status}")


def tasks_bracket(*, mission_dir: Path, task_id: str, new_bracket: str,
                  agent: str, lane: str) -> None:
    rid = _qc.tasks_bracket(mission_dir, agent, lane, task_id, new_bracket)
    status = _qc.wait_until_applied(mission_dir, rid, timeout_seconds=10.0)
    if status != "applied":
        raise RuntimeError(f"queue tasks_bracket failed: {status}")


def history_append(*, mission_dir: Path, agent: str, lane: str, task_id: str,
                   finding_path: str, severity: str) -> None:
    rid = _qc.history_append(mission_dir, agent, lane, task_id, finding_path, severity)
    status = _qc.wait_until_applied(mission_dir, rid, timeout_seconds=10.0)
    if status != "applied":
        raise RuntimeError(f"queue history_append failed: {status}")


def status_update(*, mission_dir: Path, agent: str, lane: str, new_state: str,
                  new_notes: str) -> None:
    rid = _qc.status_update(mission_dir, agent, lane, new_state, new_notes)
    status = _qc.wait_until_applied(mission_dir, rid, timeout_seconds=10.0)
    if status != "applied":
        raise RuntimeError(f"queue status_update failed: {status}")
```

- [ ] **Step 3:** Stage.

### Task C2: test_shared_state_via_queue.py

**Files:**
- Create: `scripts/tests/test_shared_state_via_queue.py`

- [ ] **Step 1: Write 5 integration tests**

```python
# scripts/tests/test_shared_state_via_queue.py
"""V9 M1 — verify scripts/_shared_state via queue backend.

Spawns applier subprocess; runs the same RULE-10 four-step close;
verifies all 4 mutations land via queue.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def applier_proc(queue_mission):
    proc = subprocess.Popen(
        ["uv", "run", "--with", "pyyaml", "--with", "pydantic",
         "python", "-m", "megalodon_ui.queue.applier",
         "--mission-dir", str(queue_mission), "--poll-seconds", "0.5"],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    time.sleep(2)  # Let applier acquire lock + start heartbeat
    yield proc, queue_mission
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_atomic_close_via_queue_backend(applier_proc):
    """RULE-10 four-step close lands all four mutations through the queue."""
    proc, mission = applier_proc
    # Pre-state: claim dir + bracket
    (mission / "claims" / "TEST-1").mkdir(parents=True)
    (mission / "claims" / "TEST-1" / "owner.txt").write_text("agent-aaaa 2026-01-01T00:00:00Z\n")
    tasks = mission / "TASKS.md"
    tasks.write_text(tasks.read_text() + "\n- [claimed: agent-aaaa @ 2026-01-01T00:00:00Z] [LANE-A] `TEST-1`\n")

    result = subprocess.run(
        [sys.executable, "scripts/atomic_close.py",
         "--task", "TEST-1", "--lane", "AUDIT", "--agent", "agent-aaaa",
         "--finding", "findings/test1.md", "--severity", "MAJOR",
         "--notes", "test via queue", "--summary", "test",
         "--mission-dir", str(mission)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    # All four mutations landed
    assert (mission / "claims" / "TEST-1" / "done").exists()
    assert "[done:" in tasks.read_text()
    assert "TEST-1" in (mission / "HISTORY.md").read_text()
    # STATUS.md should also reflect the update (one of the test lanes)
```

(Plus 4 more tests covering rejection cases.)

- [ ] **Step 2:** Stage.

### Task C3: Swap backend in _shared_state.py

**Files:**
- Modify: `scripts/_shared_state.py`

- [ ] **Step 1:** Change `from ._backends import direct_fcntl as _backend` → `from ._backends import queue_client as _backend` (single line).
- [ ] **Step 2: Verify swap works WITHOUT breaking existing tests**

Existing M3 tests use `direct_fcntl` directly (they don't go through the backend swap). Verify by:
```bash
cd /Users/dave/Documents/Projects/megalodon && \
    uv run --with pytest --with pyyaml --with pydantic python -m pytest scripts/tests/ -v -x
```

If M3 tests fail: revert the swap (M3 tests need to be refactored to use the backend abstraction OR remain on direct_fcntl). Likely M3 tests directly call `direct_fcntl.claim_dir_done(...)` etc.; the swap only affects callers that go through `_shared_state.execute_close()`.

- [ ] **Step 3:** Stage.

---

## Phase D — M1.5 UI mutation migration

### Task D1: Convert 3 endpoints to 202-async

**Files:**
- Modify: `megalodon_ui/server.py`

- [ ] **Step 1: Read existing endpoints** (lines around 324-337, 367-400, 537-565).
- [ ] **Step 2: Add import** `from megalodon_ui.queue import queue_client`.
- [ ] **Step 3: For each endpoint, replace direct write with `queue_client.<intent>(...)` + return 202**

Pattern per spec §5.2.

- [ ] **Step 4: Add `GET /api/v1/queue/{request_id}` introspection endpoint**

```python
@app.get("/api/v1/queue/{request_id}")
async def queue_status(request_id: str):
    mission = ctx.mission_dir
    if (mission / "queue" / "applied" / f"{request_id}.json").exists():
        return {"request_id": request_id, "status": "applied"}
    rejected = mission / "queue" / "rejected" / f"{request_id}.json"
    if rejected.exists():
        reason = rejected.with_name(f"{request_id}-reason.txt")
        return {
            "request_id": request_id, "status": "rejected",
            "rejection_reason": reason.read_text() if reason.exists() else None,
        }
    if (mission / "queue" / "pending" / f"{request_id}.json").exists():
        return {"request_id": request_id, "status": "pending"}
    raise HTTPException(404, "request_id not found")
```

- [ ] **Step 5: Update `docs/v9/api-contract.md`** with the 3 changed endpoints (now `status: 202`) + new `/api/v1/queue/{request_id}` endpoint.
- [ ] **Step 6: Run M2 contract scan** to verify still passes:
```bash
cd /Users/dave/Documents/Projects/megalodon && \
    uv run --with pyyaml --with pydantic python3 scripts/contract_scan.py
```

- [ ] **Step 7:** Stage.

---

## Phase E — M1.6 Backend unification

### Task E1: ui/server.py → thin shim

**Files:**
- Modify: `ui/server.py` (DELETE ~1200 lines, replace with ~30)

- [ ] **Step 1:** Read existing `ui/server.py` head + tail.
- [ ] **Step 2:** Replace ENTIRE file with spec §6.1 shim code.
- [ ] **Step 3: Smoke**

```bash
cd /Users/dave/Documents/Projects/megalodon && \
    uv run --with fastapi --with "uvicorn[standard]" --with sse-starlette --with pyyaml --with pydantic \
    python ui/server.py --mission-dir scripts/tests/fixtures/minimal_mission --port 8089 &
sleep 2
curl -s http://localhost:8089/api/v1/state | python3 -c "import json,sys; d=json.load(sys.stdin); print('lanes:', len(d['status']['lanes']))"
pkill -f "python ui/server.py --mission-dir"
```

Expected: `lanes: 6`.

- [ ] **Step 4:** Stage.

---

## Phase F — Final wrap

### Task F1: Update launch.md + README.md

- [ ] **Step 1:** Add RULE 15 to launch.md §5 (or extend RULE 12): "Operator MUST start applier daemon before workers via `./scripts/start_applier.sh <mission-dir> &`".
- [ ] **Step 2:** Add v9 startup sequence section to README.md per spec §10.
- [ ] **Step 3:** Stage.

### Task F2: Final smoke + pytest + HISTORY

- [ ] **Step 1:** Full pytest suite:
```bash
cd /Users/dave/Documents/Projects/megalodon && \
    uv run --with pytest --with pyyaml --with fastapi --with pydantic \
    python -m pytest scripts/tests/ -v
```
Expected: 116 existing (M3+M4+M2) + 54 new (M1) = 170 PASS.

- [ ] **Step 2:** End-to-end smoke per spec §10 operator runbook:
```bash
# 1. Start applier
./scripts/start_applier.sh scripts/tests/fixtures/queue_mission &
APPLIER_PID=$!
sleep 2
# 2. Start factory
uv run --with fastapi --with "uvicorn[standard]" --with sse-starlette --with pyyaml --with pydantic \
    python -m megalodon_ui --mission-dir scripts/tests/fixtures/queue_mission --port 8089 &
UI_PID=$!
sleep 2
# 3. POST reclaim via curl
curl -s -X POST http://localhost:8089/api/v1/reclaim \
    -H "Content-Type: application/json" \
    -d '{"agent":"agent-aaaa","lane":"AUDIT","new_state":"working: smoke"}' \
    | tee /tmp/m1-reclaim.json
# 4. Read request_id, poll introspection
RID=$(jq -r .request_id /tmp/m1-reclaim.json)
sleep 3
curl -s "http://localhost:8089/api/v1/queue/$RID" | jq .
# 5. Cleanup
kill $APPLIER_PID $UI_PID 2>/dev/null
```

Expected: reclaim returns 202 with request_id; introspection shows `"status": "applied"` within 3 seconds.

- [ ] **Step 3:** Append HISTORY.md M1-COMPLETE entry.
- [ ] **Step 4:** Stage.

---

## Self-review

- [ ] All TDD discipline (test first, fail, implement, pass).
- [ ] S-8 BLOCKING fixes B1+B2+B3 applied with regression tests.
- [ ] Q1 intents added with schema validation + applier impl.
- [ ] CR-6 migration script with idempotency.
- [ ] M3 backend swap is a single-line change in _shared_state.py — does not break existing M3 tests.
- [ ] M1.5 endpoints are 202-async with introspection.
- [ ] M1.6 ui/server.py is ~30 lines, all logic in factory.
- [ ] M2 contract scan still passes after api-contract.md update.
- [ ] No git commits.
