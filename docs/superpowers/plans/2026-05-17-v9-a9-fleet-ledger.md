# V9 A9 — Fleet Performance Ledger Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development.

**Goal:** Worker tick ledger + operator JSONL parser + aggregator.

**Spec:** `docs/superpowers/specs/2026-05-17-v9-a9-fleet-ledger-design.md` (12 sections).

---

### Task 1: `_fleet_tick.py` + 6 tests

**Files:**
- Create: `scripts/_fleet_tick.py`
- Create: `scripts/tests/test_fleet_tick.py`

- [ ] **Step 1: Write 6 failing tests**

```python
# scripts/tests/test_fleet_tick.py
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts._fleet_tick import record_tick, _next_tick_number


def test_first_tick_is_n_1(tmp_path):
    path = record_tick(tmp_path, lane="AUDIT", agent="agent-aaaa")
    data = json.loads(path.read_text())
    assert data["tick_number"] == 1


def test_tick_increments_per_lane(tmp_path):
    record_tick(tmp_path, lane="AUDIT", agent="a", tick_started_utc="2026-01-01T00:00:00Z")
    record_tick(tmp_path, lane="AUDIT", agent="a", tick_started_utc="2026-01-01T00:01:00Z")
    n = _next_tick_number(tmp_path / ".fleet-ledger", "AUDIT")
    assert n == 3


def test_tick_idempotent_same_n_utc(tmp_path):
    p1 = record_tick(tmp_path, lane="AUDIT", agent="a",
                     tick_number=1, tick_started_utc="2026-01-01T00:00:00Z",
                     custom_field="first")
    p2 = record_tick(tmp_path, lane="AUDIT", agent="a",
                     tick_number=1, tick_started_utc="2026-01-01T00:00:00Z",
                     custom_field="second")
    assert p1 == p2
    data = json.loads(p1.read_text())
    assert data["custom_field"] == "first"  # First write wins


def test_atomic_write(tmp_path):
    path = record_tick(tmp_path, lane="AUDIT", agent="a",
                       tick_started_utc="2026-01-01T00:00:00Z")
    # No .tmp leftover
    tmp_leftover = list((tmp_path / ".fleet-ledger").glob("*.tmp"))
    assert tmp_leftover == []


def test_fields_persisted(tmp_path):
    path = record_tick(tmp_path, lane="AUDIT", agent="agent-x",
                       tick_started_utc="2026-01-01T00:00:00Z",
                       walltime_seconds=30, tasks_completed=["P5-A"], cas_retries=2)
    data = json.loads(path.read_text())
    assert data["walltime_seconds"] == 30
    assert data["tasks_completed"] == ["P5-A"]
    assert data["cas_retries"] == 2


def test_independent_lanes_independent_counters(tmp_path):
    record_tick(tmp_path, lane="AUDIT", agent="a", tick_started_utc="2026-01-01T00:00:00Z")
    record_tick(tmp_path, lane="AUDIT", agent="a", tick_started_utc="2026-01-01T00:01:00Z")
    record_tick(tmp_path, lane="BACKEND", agent="b", tick_started_utc="2026-01-01T00:02:00Z")
    n_audit = _next_tick_number(tmp_path / ".fleet-ledger", "AUDIT")
    n_backend = _next_tick_number(tmp_path / ".fleet-ledger", "BACKEND")
    assert n_audit == 3
    assert n_backend == 2
```

- [ ] **Step 2: Implement** per spec §4.
- [ ] **Step 3: Stage.**

### Task 2: `parse_session_tokens.py` + 5 tests

**Files:**
- Create: `scripts/parse_session_tokens.py`
- Create: `scripts/tests/test_parse_session_tokens.py`

- [ ] **Step 1: Write 5 failing tests**

```python
# scripts/tests/test_parse_session_tokens.py
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.parse_session_tokens import parse


def test_parses_empty_jsonl(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text("")
    result = parse(p)
    assert result["tokens"]["input"] == 0
    assert result["tokens"]["output"] == 0


def test_sums_input_output_tokens(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text(
        json.dumps({"message": {"usage": {"input_tokens": 100, "output_tokens": 50}, "model": "claude-opus-4-7"}}) + "\n"
        + json.dumps({"message": {"usage": {"input_tokens": 200, "output_tokens": 75}, "model": "claude-opus-4-7"}}) + "\n"
    )
    result = parse(p)
    assert result["tokens"]["input"] == 300
    assert result["tokens"]["output"] == 125


def test_handles_cache_tokens(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text(json.dumps({"message": {"usage": {
        "input_tokens": 10, "output_tokens": 5,
        "cache_creation_input_tokens": 100,
        "cache_read_input_tokens": 200,
    }, "model": "claude-opus-4-7"}}) + "\n")
    result = parse(p)
    assert result["tokens"]["cache_creation"] == 100
    assert result["tokens"]["cache_read"] == 200


def test_estimates_cost_for_known_model(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text(json.dumps({"message": {"usage": {
        "input_tokens": 1_000_000, "output_tokens": 1_000_000
    }, "model": "claude-opus-4-7"}}) + "\n")
    result = parse(p)
    # opus pricing: 15/M in + 75/M out = 90 USD
    assert result["estimated_cost_usd"] == 90.0


def test_handles_malformed_lines(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text("not-json garbage\n" + json.dumps({"message": {"usage": {"input_tokens": 5, "output_tokens": 3}}}) + "\n")
    result = parse(p)
    assert result["tokens"]["input"] == 5
```

- [ ] **Step 2: Implement** per spec §5.
- [ ] **Step 3: Stage.**

### Task 3: `aggregate_fleet_perf.py` + 4 tests

**Files:**
- Create: `scripts/aggregate_fleet_perf.py`
- Create: `scripts/tests/test_aggregate_fleet_perf.py`

- [ ] **Step 1: Write 4 failing tests**

```python
# scripts/tests/test_aggregate_fleet_perf.py
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.aggregate_fleet_perf import aggregate


def test_aggregates_per_lane(tmp_path):
    led = tmp_path / ".fleet-ledger"
    led.mkdir()
    (led / "AUDIT-tick-1-2026-01-01T00-00-00Z.json").write_text(
        json.dumps({"lane": "AUDIT", "tick_number": 1, "tasks_completed": ["P1"], "cas_retries": 0})
    )
    (led / "AUDIT-tick-2-2026-01-01T00-01-00Z.json").write_text(
        json.dumps({"lane": "AUDIT", "tick_number": 2, "tasks_completed": ["P2", "P3"], "cas_retries": 1})
    )
    out = tmp_path / "fleet-perf.json"
    summary = aggregate(tmp_path, out)
    assert summary["AUDIT"]["tick_count"] == 2
    assert summary["AUDIT"]["tasks_completed"] == 3
    assert summary["AUDIT"]["cas_retries"] == 1


def test_sums_cas_retries(tmp_path):
    led = tmp_path / ".fleet-ledger"
    led.mkdir()
    for i, retries in enumerate([3, 5, 2]):
        (led / f"BACKEND-tick-{i+1}-2026-01-01T00-0{i}-00Z.json").write_text(
            json.dumps({"lane": "BACKEND", "tick_number": i+1, "cas_retries": retries})
        )
    out = tmp_path / "fleet-perf.json"
    summary = aggregate(tmp_path, out)
    assert summary["BACKEND"]["cas_retries"] == 10


def test_handles_missing_ledger_dir(tmp_path):
    out = tmp_path / "fleet-perf.json"
    summary = aggregate(tmp_path, out)
    assert summary == {}


def test_writes_output_file(tmp_path):
    (tmp_path / ".fleet-ledger").mkdir()
    out = tmp_path / "fleet-perf.json"
    aggregate(tmp_path, out)
    assert out.exists()
    assert json.loads(out.read_text()) == {"lanes": {}}
```

- [ ] **Step 2: Implement** per spec §6.
- [ ] **Step 3: Stage.**

### Task 4: .gitignore + launch.md + HISTORY.md

- [ ] **Step 1: Add to .gitignore**

```
# v9 A9 fleet ledger (mission state)
.fleet-ledger/*
!.fleet-ledger/.gitkeep
!scripts/tests/fixtures/**/.fleet-ledger/
!scripts/tests/fixtures/**/.fleet-ledger/**
```

- [ ] **Step 2: Add to launch.md** §X (or near tick heartbeat section):

```markdown
## §X.Y Fleet ledger (V9 A9)

Workers SHOULD call `scripts._fleet_tick.record_tick(mission_dir, lane=LANE, agent=AGENT, ...)` once per /loop tick. Captures tasks completed, CAS retries, REPAIR injections received, SIGNAL ACK latency. Operator runs `scripts/aggregate_fleet_perf.py --mission-dir <m>` post-mission to merge with token data from `scripts/parse_session_tokens.py`.

Optional but useful — feeds A3 fleet matrix decisions for next mission.
```

- [ ] **Step 3: HISTORY.md A9-COMPLETE entry:**

```markdown
## 2026-05-17T~02:00Z — V9 A9 COMPLETE — fleet performance ledger

V9-ROADMAP Migration plan §3j shipped.

**Created:**
- `scripts/_fleet_tick.py` — worker-side per-tick ledger entry helper.
- `scripts/parse_session_tokens.py` — operator-side parser for Claude Code JSONL session logs (tokens, model, estimated cost).
- `scripts/aggregate_fleet_perf.py` — merges worker ledger entries into `<mission>/fleet-perf.json`.
- `scripts/tests/test_{fleet_tick,parse_session_tokens,aggregate_fleet_perf}.py` — 15 tests.

**Modified:**
- `.gitignore` — `.fleet-ledger/*` mission state ignored with fixture re-include.
- `launch.md` — workers SHOULD call `record_tick(...)` per tick.

**Tests:** 15 new (6+5+4), all PASS.

**Operator workflow (post-mission):**
1. `python3 scripts/parse_session_tokens.py --project-glob '~/.claude/projects/*megalodon*/*.jsonl'` — get token + cost totals per session.
2. `python3 scripts/aggregate_fleet_perf.py --mission-dir <mission>` — merge worker tick data.
3. Combine into next-mission A3 fleet-matrix adjustments.
```

- [ ] **Step 4: Stage all.**

---

## Self-review

- [ ] All 15 tests have actual bodies.
- [ ] Tick ledger idempotent (D6).
- [ ] JSONL parser handles malformed lines.
- [ ] Aggregator handles missing ledger dir.
- [ ] No git commits.
