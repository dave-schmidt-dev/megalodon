---
title: V9 A9 — Fleet performance ledger (design spec)
status: APPROVED-FOR-PLAN
version: 1.0
utc: 2026-05-17T01:30Z
roadmap-anchor: docs/v9/V9-ROADMAP.md §A9 + Migration plan §3j
---

# V9 A9 — Fleet performance ledger

## 1. Goal

Per V9-ROADMAP A9 + self-contrarian OW-6 split: workers track what they can observe (tasks, walltime, CAS retries, SIGNAL ACK latency); operator tooling parses tokens/cost from Claude Code JSONL session logs externally. Aggregator merges both into a single per-mission performance ledger to inform A3 fleet matrix decisions.

## 2. Locked decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **Two ledger surfaces: worker-side + operator-side** | Per OW-6 — workers can't read own tokens. |
| D2 | **Worker writes per-tick JSON** | `.fleet-ledger/<lane>-tick-<N>-<utc>.json`. Append-only; never edit. |
| D3 | **Operator-side parser** | `scripts/parse_session_tokens.py` walks `~/.claude/projects/<path>/<session>.jsonl` files post-mission. |
| D4 | **Aggregator runs post-mission** | `scripts/aggregate_fleet_perf.py` merges into `runs/<mission-id>/fleet-perf.json`. Not real-time. |
| D5 | **Worker emits via helper, not by-hand** | `scripts/_fleet_tick.py` provides `record_tick(...)` API; workers call once per /loop tick. |
| D6 | **Idempotent ticks** | If `<lane>-tick-N-<utc>.json` exists, skip (no double-write on re-launch). |
| D7 | **Tick numbers monotonic per lane** | Read existing tick files; new tick = max(existing) + 1. |

## 3. Worker-side ledger entry format

`.fleet-ledger/<lane>-tick-<N>-<utc>.json`:
```json
{
  "lane": "AUDIT",
  "agent": "agent-aaaa",
  "tick_number": 5,
  "tick_started_utc": "2026-05-17T00:00:00Z",
  "tick_ended_utc": "2026-05-17T00:00:30Z",
  "walltime_seconds": 30,
  "tasks_completed": ["P5-A"],
  "tasks_claimed": ["P5-B"],
  "cas_retries": 0,
  "repair_injections_received": [],
  "signals_acked": [],
  "signal_ack_latency_seconds": null,
  "phase": "PHASE-RUN"
}
```

All fields optional except `lane`, `agent`, `tick_number`, `tick_started_utc`.

## 4. Worker helper

`scripts/_fleet_tick.py`:
```python
"""V9 A9 worker-side ledger emission."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _next_tick_number(ledger_dir: Path, lane: str) -> int:
    existing = list(ledger_dir.glob(f"{lane}-tick-*.json"))
    if not existing:
        return 1
    nums = []
    for p in existing:
        # filename: <lane>-tick-<N>-<utc>.json
        parts = p.stem.split("-tick-")
        if len(parts) != 2:
            continue
        n = parts[1].split("-", 1)[0]
        try:
            nums.append(int(n))
        except ValueError:
            continue
    return max(nums, default=0) + 1


def record_tick(mission_dir: Path, *, lane: str, agent: str, **fields: Any) -> Path:
    """Write a tick entry. Returns path. Idempotent — skip if same N+UTC exists."""
    ledger_dir = mission_dir / ".fleet-ledger"
    n = fields.pop("tick_number", None) or _next_tick_number(ledger_dir, lane)
    started_utc = fields.pop("tick_started_utc", None) or _utc()
    entry = {
        "lane": lane,
        "agent": agent,
        "tick_number": n,
        "tick_started_utc": started_utc,
        **fields,
    }
    filename = f"{lane}-tick-{n}-{started_utc.replace(':', '-')}.json"
    path = ledger_dir / filename
    if path.exists():
        return path  # Idempotent skip
    _atomic_write(path, json.dumps(entry, indent=2))
    return path
```

## 5. Operator-side parser

`scripts/parse_session_tokens.py`:
```python
"""V9 A9 — parse Claude Code session JSONL for tokens + cost.

JSONL format per Claude Code: each line is a JSON envelope with optional
`message.usage.{input_tokens, output_tokens, cache_*_tokens}` fields when
the line is an assistant turn.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Anthropic published pricing as of 2026-05 (per-million tokens)
PRICING = {
    "claude-opus-4-7": {"in": 15.0, "out": 75.0},
    "claude-sonnet-4-6": {"in": 3.0, "out": 15.0},
    "claude-haiku-4-5-20251001": {"in": 0.80, "out": 4.0},
}


def parse(jsonl_path: Path) -> dict:
    total_in = total_out = total_cache_create = total_cache_read = 0
    model_seen: str | None = None
    n_turns = 0
    started_utc = None
    last_utc = None
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                env = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = env.get("timestamp") or env.get("ts")
            if ts:
                if started_utc is None:
                    started_utc = ts
                last_utc = ts
            msg = env.get("message", env)
            usage = msg.get("usage") if isinstance(msg, dict) else None
            if not usage:
                continue
            total_in += usage.get("input_tokens", 0)
            total_out += usage.get("output_tokens", 0)
            total_cache_create += usage.get("cache_creation_input_tokens", 0)
            total_cache_read += usage.get("cache_read_input_tokens", 0)
            model_seen = msg.get("model") or model_seen
            n_turns += 1

    pricing = PRICING.get(model_seen, {"in": 0.0, "out": 0.0})
    cost = (
        (total_in + total_cache_create) * pricing["in"] / 1_000_000
        + total_out * pricing["out"] / 1_000_000
    )
    return {
        "jsonl_path": str(jsonl_path),
        "model": model_seen,
        "turns": n_turns,
        "tokens": {
            "input": total_in,
            "output": total_out,
            "cache_creation": total_cache_create,
            "cache_read": total_cache_read,
        },
        "estimated_cost_usd": round(cost, 4),
        "started_utc": started_utc,
        "last_utc": last_utc,
    }


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("jsonl_path", type=Path, nargs="?")
    p.add_argument("--project-glob", help="e.g., ~/.claude/projects/*/*.jsonl")
    args = p.parse_args(argv)
    if args.jsonl_path:
        print(json.dumps(parse(args.jsonl_path), indent=2))
        return 0
    if args.project_glob:
        import glob, os
        results = []
        for path in glob.glob(os.path.expanduser(args.project_glob)):
            try:
                results.append(parse(Path(path)))
            except Exception as e:
                results.append({"jsonl_path": path, "error": str(e)})
        print(json.dumps(results, indent=2))
        return 0
    p.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

## 6. Aggregator

`scripts/aggregate_fleet_perf.py`:
```python
"""V9 A9 — merge worker ledger + operator session-token parse into per-mission perf.json."""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def aggregate(mission_dir: Path, output: Path) -> dict:
    ledger_dir = mission_dir / ".fleet-ledger"
    by_lane: dict[str, list] = defaultdict(list)
    if ledger_dir.is_dir():
        for path in sorted(ledger_dir.glob("*-tick-*-*.json")):
            try:
                entry = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            by_lane[entry.get("lane", "UNKNOWN")].append(entry)

    summary = {}
    for lane, ticks in by_lane.items():
        summary[lane] = {
            "tick_count": len(ticks),
            "tasks_completed": sum(len(t.get("tasks_completed", [])) for t in ticks),
            "cas_retries": sum(t.get("cas_retries", 0) for t in ticks),
            "repair_injections_received": sum(
                len(t.get("repair_injections_received", [])) for t in ticks
            ),
            "total_walltime_seconds": sum(t.get("walltime_seconds", 0) or 0 for t in ticks),
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"lanes": summary}, indent=2))
    return summary


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mission-dir", required=True, type=Path)
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args(argv)
    out = args.out or args.mission_dir / "fleet-perf.json"
    summary = aggregate(args.mission_dir.resolve(), out)
    print(f"wrote {out}", file=sys.stderr)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

## 7. Test plan

### `scripts/tests/test_fleet_tick.py` — 6 tests
- `test_first_tick_is_n_1`
- `test_tick_increments_per_lane`
- `test_tick_idempotent_same_n_utc`
- `test_atomic_write`
- `test_fields_persisted`
- `test_independent_lanes_independent_counters`

### `scripts/tests/test_parse_session_tokens.py` — 5 tests
- `test_parses_empty_jsonl_returns_zero_tokens`
- `test_sums_input_output_tokens`
- `test_handles_cache_tokens`
- `test_estimates_cost_for_known_model`
- `test_handles_malformed_lines_gracefully`

### `scripts/tests/test_aggregate_fleet_perf.py` — 4 tests
- `test_aggregates_per_lane`
- `test_sums_cas_retries`
- `test_handles_missing_ledger_dir`
- `test_writes_output_file`

**Total:** 15 tests.

## 8. File manifest

### Created
- `scripts/_fleet_tick.py` (~60 LOC)
- `scripts/parse_session_tokens.py` (~80 LOC)
- `scripts/aggregate_fleet_perf.py` (~50 LOC)
- `scripts/tests/test_fleet_tick.py`
- `scripts/tests/test_parse_session_tokens.py`
- `scripts/tests/test_aggregate_fleet_perf.py`

### Modified
- `.gitignore` — add `.fleet-ledger/*` (mission state) with `!scripts/tests/fixtures/**/.fleet-ledger/*` re-include for fixtures
- `launch.md` — add §X.Y A9 note: workers SHOULD call `record_tick(...)` once per /loop tick
- `HISTORY.md` — A9-COMPLETE entry

## 9. Definition of done

- [ ] All 6 new files shipped.
- [ ] 15 tests pass.
- [ ] Smoke: drop a synthetic JSONL into a temp dir; run parse_session_tokens.py → cost reported. Drop a few tick files in `.fleet-ledger/`; aggregator produces sane summary.
- [ ] launch.md A9 note added.
- [ ] HISTORY.md A9-COMPLETE entry.

## 10. Implementation order

1. `_fleet_tick.py` + 6 tests.
2. `parse_session_tokens.py` + 5 tests.
3. `aggregate_fleet_perf.py` + 4 tests.
4. `.gitignore` + launch.md updates.
5. HISTORY.md entry.

## 11. Risks

| Risk | Mitigation |
|------|------------|
| Claude Code JSONL format drifts | Parser handles `env.get("message", env)` fallback; ignores malformed lines. |
| Pricing data goes stale | Documented in `PRICING` dict; operator can update. Cost is "estimated" not authoritative. |
| Ledger directory bloats over mission | `.gitignore`'d; archived to runs/ post-mission per existing archive process. |
| Tick number race when worker re-launches | Idempotent skip (D6); if N is reused, file existence check prevents overwrite. |

## 12. Document control

- Author: orchestrator (Claude)
- Date: 2026-05-17T01:30Z
- Status: APPROVED-FOR-PLAN
- Successor: `docs/superpowers/plans/2026-05-17-v9-a9-fleet-ledger.md`
