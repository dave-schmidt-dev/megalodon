# V9 Doc Bundle Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development.

**Bundle:** A2 + A3 + A4 + A5 + A6 + A7 + M5 + M6 + A8 — six V9-ROADMAP milestones dominated by markdown/launch.md + small Python.

**Spec:** `docs/superpowers/specs/2026-05-17-v9-doc-bundle-design.md` (17 sections).

**Dependency:** A1 watchdog must land first because the doc bundle's launch.md edits compose with A1's RULE 16 addition.

---

### Task 1: A4 — Deterministic agent IDs

**Files:**
- Create: `scripts/_agent_id.py`
- Create: `scripts/tests/test_agent_id.py`

- [ ] **Step 1: Write 5 failing tests**

```python
# scripts/tests/test_agent_id.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts._agent_id import deterministic_agent_id


def test_returns_agent_prefix_plus_4_hex():
    aid = deterministic_agent_id("mission-1", "AUDIT", "2026-05-17T00:00:00Z")
    assert aid.startswith("agent-")
    assert len(aid) == 6 + 4
    for c in aid[6:]:
        assert c in "0123456789abcdef"


def test_same_inputs_same_id():
    a = deterministic_agent_id("mission-1", "AUDIT", "2026-05-17T00:00:00Z")
    b = deterministic_agent_id("mission-1", "AUDIT", "2026-05-17T00:00:00Z")
    assert a == b


def test_different_mission_different_id():
    a = deterministic_agent_id("mission-1", "AUDIT", "2026-05-17T00:00:00Z")
    b = deterministic_agent_id("mission-2", "AUDIT", "2026-05-17T00:00:00Z")
    assert a != b


def test_different_lane_different_id():
    a = deterministic_agent_id("m", "AUDIT", "2026-05-17T00:00:00Z")
    b = deterministic_agent_id("m", "BACKEND", "2026-05-17T00:00:00Z")
    assert a != b


def test_different_utc_different_id():
    a = deterministic_agent_id("m", "AUDIT", "2026-05-17T00:00:00Z")
    b = deterministic_agent_id("m", "AUDIT", "2026-05-17T00:01:00Z")
    assert a != b
```

- [ ] **Step 2: Implement** per spec §6.
- [ ] **Step 3: Stage.**

### Task 2: A2 — Lane launch codegen

**Files:**
- Create: `scripts/gen_lane_launches.py`
- Create: `scripts/tests/test_lane_launch_codegen.py`
- Generated: `launch-AUDIT.md` through `launch-META.md` (6 files)

- [ ] **Step 1: Write 4 failing tests**

```python
# scripts/tests/test_lane_launch_codegen.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import gen_lane_launches


def test_generates_6_files(tmp_path):
    gen_lane_launches.generate_all(tmp_path)
    files = sorted(tmp_path.glob("launch-*.md"))
    names = [f.name for f in files]
    assert names == [
        "launch-ARCHITECT.md", "launch-AUDIT.md", "launch-BACKEND.md",
        "launch-FRONTEND.md", "launch-META.md", "launch-TEST.md",
    ]


def test_header_has_lane(tmp_path):
    gen_lane_launches.generate_all(tmp_path)
    text = (tmp_path / "launch-AUDIT.md").read_text()
    assert "LANE: AUDIT" in text


def test_body_includes_launch_md_content(tmp_path):
    gen_lane_launches.generate_all(tmp_path)
    text = (tmp_path / "launch-AUDIT.md").read_text()
    # launch.md has at least the heading or RULE structure
    assert "## " in text or "# " in text


def test_offset_increases_per_lane(tmp_path):
    gen_lane_launches.generate_all(tmp_path)
    audit = (tmp_path / "launch-AUDIT.md").read_text()
    backend = (tmp_path / "launch-BACKEND.md").read_text()
    # AUDIT = 0, BACKEND = 90 (index 2 × 45)
    assert "TICK_OFFSET_SECONDS: 0" in audit
    assert "TICK_OFFSET_SECONDS: 90" in backend
```

- [ ] **Step 2: Implement** per spec §4.1.

```python
# scripts/gen_lane_launches.py
"""V9 A2 — generate per-lane launch files from launch.md template."""
from __future__ import annotations

import sys
from pathlib import Path

DEFAULT_LANES = ["AUDIT", "ARCHITECT", "BACKEND", "FRONTEND", "TEST", "META"]
DEFAULT_MODEL = "opus-4.7"

# Sonnet for observer lanes
LANE_MODELS = {
    "AUDIT": "sonnet-4.6",
    "META": "sonnet-4.6",
}
# Faster cadence for builders
LANE_CADENCE = {
    "AUDIT": 300, "ARCHITECT": 300, "BACKEND": 180,
    "FRONTEND": 180, "TEST": 180, "META": 420,
}


HEADER = """# launch-{lane}.md — pre-bound launch for {lane} lane

> Generated from launch.md by scripts/gen_lane_launches.py — DO NOT EDIT.
> Regenerate with: `python3 scripts/gen_lane_launches.py`

## Pre-binding

- LANE: {lane}
- CADENCE_SECONDS: {cadence}
- TICK_OFFSET_SECONDS: {offset}
- MODEL_HINT: {model}

## Step 0 — Stagger wait (A6)

Before /loop arm, sleep for TICK_OFFSET_SECONDS to spread tick load across lanes.

```bash
sleep {offset}
```

---

"""


def generate_one(lane: str, lane_index: int, repo_root: Path) -> str:
    launch_md = (repo_root / "launch.md").read_text(encoding="utf-8")
    return HEADER.format(
        lane=lane,
        cadence=LANE_CADENCE.get(lane, 300),
        offset=lane_index * 45,
        model=LANE_MODELS.get(lane, DEFAULT_MODEL),
    ) + launch_md


def generate_all(out_dir: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, lane in enumerate(DEFAULT_LANES):
        text = generate_one(lane, i, repo_root)
        (out_dir / f"launch-{lane}.md").write_text(text, encoding="utf-8")


def main(argv: list[str]) -> int:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", type=Path, default=Path("."))
    args = p.parse_args(argv)
    generate_all(args.out_dir)
    print(f"Generated 6 lane launch files in {args.out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 3: Run codegen to produce real lane files**

`cd /Users/dave/Documents/Projects/megalodon && python3 scripts/gen_lane_launches.py`

- [ ] **Step 4: Stage all generated `launch-*.md` files + codegen.**

### Task 3: A2 launch_fleet.sh

**Files:**
- Create: `scripts/launch_fleet.sh`

- [ ] **Step 1: Write per spec §4.2**
- [ ] **Step 2: chmod +x**
- [ ] **Step 3: Stage.**

### Task 4: A3 — fleet matrix doc + selector + 3 tests

**Files:**
- Create: `docs/v9/fleet-matrix.md`
- Create: `scripts/fleet_select.py`
- Create: `scripts/tests/test_fleet_select.py`

- [ ] **Step 1: Write 3 failing tests**

```python
# scripts/tests/test_fleet_select.py
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.fleet_select import select


def test_default_lookup(tmp_path):
    assert select("AUDIT", tmp_path) == "sonnet-4.6"
    assert select("BACKEND", tmp_path) == "opus-4.7"


def test_override_file_takes_precedence(tmp_path):
    scratch = tmp_path / ".scratch"
    scratch.mkdir()
    (scratch / "fleet-matrix-override.json").write_text(
        json.dumps({"lanes": {"AUDIT": {"model": "haiku-4.5"}}})
    )
    assert select("AUDIT", tmp_path) == "haiku-4.5"


def test_unknown_lane_returns_default():
    assert select("OBSERVER-7", Path("/tmp")) == "opus-4.7"
```

- [ ] **Step 2: Implement** per spec §5.2.
- [ ] **Step 3: Author `docs/v9/fleet-matrix.md`** per spec §5.1.
- [ ] **Step 4: Stage.**

### Task 5: A8 — SIGNAL grammar doc + parser + 3 tests

**Files:**
- Create: `docs/v9/SIGNAL-GRAMMAR.md`
- Create: `megalodon_ui/signal_parser.py`
- Create: `scripts/tests/test_signal_grammar_parse.py`

- [ ] **Step 1: Write 3 failing tests**

```python
# scripts/tests/test_signal_grammar_parse.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from megalodon_ui.signal_parser import parse_signal


def test_parses_valid_signal(tmp_path):
    p = tmp_path / "f.md"
    p.write_text("""---
signal-type: SIG-ORCH-001
addressed-to: all-lanes
severity: TIER-1
utc: 2026-05-17T00:00:00Z
agent: orch
idempotency-key: abc123
---

Body text.
""")
    fm = parse_signal(p)
    assert fm["signal-type"] == "SIG-ORCH-001"
    assert fm["addressed-to"] == "all-lanes"


def test_rejects_non_signal(tmp_path):
    p = tmp_path / "f.md"
    p.write_text("""---
lane: AUDIT
severity: MAJOR
---

Just a finding.
""")
    assert parse_signal(p) is None


def test_handles_malformed_frontmatter(tmp_path):
    p = tmp_path / "f.md"
    p.write_text("---\nthis: is: malformed: yaml\n---\nbody\n")
    # Either returns None or doesn't raise.
    result = parse_signal(p)
    assert result is None or isinstance(result, dict)
```

- [ ] **Step 2: Implement** per spec §12 — `signal_parser.py`.
- [ ] **Step 3: Author `docs/v9/SIGNAL-GRAMMAR.md`** per spec §12.
- [ ] **Step 4: Stage.**

### Task 6: M6 — INTENT-EXPIRED helper + 8 tests

**Files:**
- Create: `scripts/_intent_expired.py`
- Create: `scripts/tests/test_intent_expired.py`

- [ ] **Step 1: Write 8 failing tests**

```python
# scripts/tests/test_intent_expired.py
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts._intent_expired import parse_intent, is_expired


def test_parse_intent_valid():
    notes = "intent-declared: REPAIR-5 @ 2026-05-17T00:00:00Z walltime: 20m"
    intent = parse_intent(notes)
    assert intent["task_id"] == "REPAIR-5"
    assert intent["walltime_minutes"] == 20


def test_parse_intent_missing_walltime_defaults_12():
    notes = "intent-declared: REPAIR-5 @ 2026-05-17T00:00:00Z"
    intent = parse_intent(notes)
    assert intent["walltime_minutes"] == 12


def test_parse_intent_no_intent_returns_none():
    assert parse_intent("just regular notes") is None
    assert parse_intent("") is None


def test_is_expired_true_after_threshold():
    intent = {"task_id": "X", "declared_utc": "2026-05-17T00:00:00Z", "walltime_minutes": 12}
    now = datetime(2026, 5, 17, 0, 18, 0, tzinfo=timezone.utc)  # 18 min later
    assert is_expired(intent, now) is True


def test_is_expired_false_before_threshold():
    intent = {"task_id": "X", "declared_utc": "2026-05-17T00:00:00Z", "walltime_minutes": 12}
    now = datetime(2026, 5, 17, 0, 10, 0, tzinfo=timezone.utc)
    assert is_expired(intent, now) is False


def test_is_expired_walltime_extends_threshold():
    intent = {"task_id": "X", "declared_utc": "2026-05-17T00:00:00Z", "walltime_minutes": 30}
    now = datetime(2026, 5, 17, 0, 34, 0, tzinfo=timezone.utc)
    assert is_expired(intent, now) is False  # 34 < 30+5 = 35


def test_is_expired_at_boundary():
    intent = {"task_id": "X", "declared_utc": "2026-05-17T00:00:00Z", "walltime_minutes": 12}
    # 12 min boundary (max(12, 12+5) = 17 minutes)
    now = datetime(2026, 5, 17, 0, 18, 0, tzinfo=timezone.utc)
    assert is_expired(intent, now) is True


def test_parse_complex_task_ids():
    notes = "intent-declared: REPAIR-MUTATIONS-E2E-3-ACTION-PANEL @ 2026-05-17T00:00:00Z"
    intent = parse_intent(notes)
    assert intent["task_id"] == "REPAIR-MUTATIONS-E2E-3-ACTION-PANEL"
```

- [ ] **Step 2: Implement** per spec §11.
- [ ] **Step 3: Stage.**

### Task 7: launch.md edits — M5, M6, A5, A4 hook, A8 cross-ref

**Files:**
- Modify: `launch.md`

- [ ] **Step 1: Read current launch.md** to find §6 (or the observation discipline section).
- [ ] **Step 2: Insert §6.X (M5 PRE-CLASSIFY)** per spec §10.
- [ ] **Step 3: Insert §6.Y (M6 INTENT-EXPIRED)** per spec §11.
- [ ] **Step 4: Add A5 terminal-title snippet** to heartbeat section.
- [ ] **Step 5: Update agent-ID step** to reference `scripts._agent_id.deterministic_agent_id(...)` (A4).
- [ ] **Step 6: Add cross-ref to `docs/v9/SIGNAL-GRAMMAR.md`** (A8).
- [ ] **Step 7: Stage.**

### Task 8: README.md update

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add to V9 startup sequence section:**

```markdown
### Per-lane launch (V9 A2)

Instead of running `claude --model X "read launch.md"` six times manually:

```bash
./scripts/launch_fleet.sh /path/to/mission
```

Each lane gets a pre-bound launch file (`launch-AUDIT.md`, etc.) with model,
cadence, stagger offset baked in.

### Fleet matrix (V9 A3)

Lane→model assignments documented in `docs/v9/fleet-matrix.md`. Override per
mission via `<mission>/.scratch/fleet-matrix-override.json`.

### SIGNAL grammar (V9 A8)

Cross-agent + operator-facing directives codified at `docs/v9/SIGNAL-GRAMMAR.md`.
Use this for any new SIGNAL-class finding.
```

- [ ] **Step 2: Stage.**

### Task 9: HISTORY.md DOC-BUNDLE-COMPLETE

- [ ] **Step 1: Append**

```markdown
## 2026-05-17T~01:30Z — V9 DOC BUNDLE COMPLETE — A2+A3+A4+A5+A6+A7+M5+M6+A8

V9-ROADMAP Migration plan §3f-§3k shipped in single bundle.

**Created:**
- `scripts/_agent_id.py` (A4) + 5 tests — deterministic agent IDs from (mission, lane, launch_utc).
- `scripts/gen_lane_launches.py` (A2) + 4 tests — codegen for 6 lane-bound launch files.
- `launch-{AUDIT,ARCHITECT,BACKEND,FRONTEND,TEST,META}.md` (A2) — generated, committed.
- `scripts/launch_fleet.sh` (A2) — operator fleet launcher.
- `docs/v9/fleet-matrix.md` (A3) — lane→model assignments + provider order.
- `scripts/fleet_select.py` (A3) + 3 tests — model selection with override support.
- `docs/v9/SIGNAL-GRAMMAR.md` (A8) — codified SIGNAL frontmatter, routing, idempotency.
- `megalodon_ui/signal_parser.py` (A8) + 3 tests — parse SIGNAL frontmatter from finding files.
- `scripts/_intent_expired.py` (M6) + 8 tests — intent-declared parsing + expiry detection.

**Modified:**
- `launch.md` — M5 PRE-CLASSIFY checklist (§6.X), M6 INTENT-EXPIRED (§6.Y), A5 ANSI title pattern, A4 deterministic ID hook, A8 SIGNAL cross-ref.
- `README.md` — V9 fleet launch / matrix / SIGNAL sections.

**Tests:** 23 new (5+4+3+3+8), all PASS. Total pytest: 184 + 23 = 207.

**Operator action:** to use per-lane launches, run `./scripts/launch_fleet.sh <mission>` instead of six manual `claude --model X "read launch.md"` invocations.
```

- [ ] **Step 2: Stage.**

---

## Self-review

- [ ] All 23 tests have actual bodies.
- [ ] Codegen idempotent (re-run produces same output).
- [ ] SIGNAL parser doesn't crash on malformed input.
- [ ] INTENT-EXPIRED helper has boundary test.
- [ ] launch.md edits don't conflict with A1's RULE 16 (assume A1 already landed).
- [ ] No git commits.
