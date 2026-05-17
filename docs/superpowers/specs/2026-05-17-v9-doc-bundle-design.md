---
title: V9 Doc Bundle — A2+A3+A4+A5, A6+A7, M5, M6, A8 (design spec)
status: APPROVED-FOR-PLAN
version: 1.0
utc: 2026-05-17T01:00Z
roadmap-anchor: docs/v9/V9-ROADMAP.md §A2-A8 + Migration plan §3f-§3k
bundle-rationale: All these milestones are dominated by markdown/launch.md edits + small Python/bash; bundling reduces context-switching overhead.
---

# V9 Doc Bundle — pre-flight + cadence + grammar + SIGNAL

## 1. Goal

Ship 6 V9-ROADMAP milestones together since they're dominated by markdown + launch.md edits with small code changes:

- **A2** — Per-lane launch files (launch-AUDIT.md etc.) + scripts/launch_fleet.sh
- **A3** — Fleet matrix doc + scripts/fleet_select.py + ledger schema
- **A4** — Deterministic agent IDs (hash of mission+lane+launch_utc)
- **A5** — Terminal title via ANSI escape
- **A6** — Lane tick-offset staggering (45s gaps)
- **A7** — Per-lane configurable cadence
- **M5** — PRE-CLASSIFY launch.md grammar
- **M6** — INTENT-EXPIRED + reclaim
- **A8** — SIGNAL grammar doc

## 2. Locked decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **Bundle into one spec** | All share the same code/doc surface (launch.md, README.md, docs/v9/, small scripts/). Easier to track + verify cross-references. |
| D2 | **Per-lane launch files (A2) reuse a SHARED template** | Per WR-related concern PW-4 (~600 LOC duplication). Use a Jinja-like substitution model via Python: `scripts/gen_lane_launches.py` reads `launch.md` template + per-lane front matter and emits 6 files. |
| D3 | **Fleet matrix is informational (A3)** | The matrix is a Markdown table + a small Python helper for selection. Operator chooses, not enforced. Performance ledger schema is JSON. |
| D4 | **Deterministic agent IDs (A4) via hashlib.sha1** | `agent-{sha1(mission_id + lane + launch_utc)[:4]}`. Replaces `secrets.token_hex(2)`. Same lane + same mission + same launch UTC → same ID. Re-launch in same mission gets new ID via different launch_utc. |
| D5 | **Terminal title (A5) helper at `scripts/_title.py` or just inline** | `\033]0;<text>\007`. Single-call helper or inline. Inline is simpler. |
| D6 | **A6 staggering as per-lane sleep at launch start** | `launch-LANE.md` includes `sleep <offset_seconds>` step 0 before /loop arm. Offset = (lane_index × 45). |
| D7 | **A7 cadence matrix at `.scratch/cadence-matrix.json`** | `{lane: {phase: cadence_seconds}}`. Workers read on phase-flip + re-arm /loop. |
| D8 | **M5 PRE-CLASSIFY is launch.md text-only** | Doc work. Adds §6.X checklist + 3-cause-class taxonomy. |
| D9 | **M6 INTENT-EXPIRED extends STATUS row Notes grammar** | `intent-declared:<task>@<utc> walltime:<Nm>`. Reclaim helper detects expiry. Mostly launch.md + a small helper. |
| D10 | **A8 SIGNAL grammar at `docs/v9/SIGNAL-GRAMMAR.md`** | ~100 lines. Defines frontmatter, routing, idempotency. launch.md cross-ref. |

## 3. File manifest

### Created
- `docs/v9/fleet-matrix.md` (~150 lines) — A3
- `docs/v9/SIGNAL-GRAMMAR.md` (~120 lines) — A8
- `launch-AUDIT.md`, `launch-ARCHITECT.md`, `launch-BACKEND.md`, `launch-FRONTEND.md`, `launch-TEST.md`, `launch-META.md` — A2 (generated)
- `scripts/gen_lane_launches.py` (~80 LOC) — A2 codegen
- `scripts/launch_fleet.sh` (~30 LOC) — A2 launcher
- `scripts/fleet_select.py` (~60 LOC) — A3 selector
- `scripts/_agent_id.py` (~20 LOC) — A4 helper
- `scripts/_intent_expired.py` (~60 LOC) — M6 reclaim logic
- `scripts/tests/test_agent_id.py` (5 tests) — A4
- `scripts/tests/test_intent_expired.py` (8 tests) — M6
- `scripts/tests/test_lane_launch_codegen.py` (4 tests) — A2
- `scripts/tests/test_fleet_select.py` (3 tests) — A3
- `scripts/tests/test_signal_grammar_parse.py` (3 tests) — A8 parser tests
- `megalodon_ui/signal_parser.py` (~60 LOC) — A8 parse SIGNAL frontmatter from finding files

### Modified
- `launch.md` — extensive: M5 PRE-CLASSIFY (~50 lines), M6 INTENT-EXPIRED (~20 lines), A5 ANSI title (~5 lines), A8 SIGNAL cross-ref (~5 lines), generic A2/A6/A7 hooks
- `README.md` — v9 launch ceremony additions
- `HISTORY.md` — DOC-BUNDLE-COMPLETE entry
- `megalodon_ui/queue/queue_client.py` — A4 hook in `_request_id` to use deterministic ID if provided

## 4. A2 — Per-lane launch files

### 4.1 Template approach

`launch.md` stays the canonical full launch script. `scripts/gen_lane_launches.py` does substitution:

```python
LANE_HEADER = """# launch-{lane}.md — pre-bound launch for {lane} lane

Generated from launch.md by scripts/gen_lane_launches.py. DO NOT EDIT.

## Pre-binding

- LANE: {lane}
- CADENCE_SECONDS: {cadence}
- TICK_OFFSET_SECONDS: {offset}
- MODEL_HINT: {model}

"""

def generate(lane: str, cadence: int, offset: int, model: str) -> str:
    base = (REPO_ROOT / "launch.md").read_text(encoding="utf-8")
    # Prepend lane-bound header; let body inherit common rules.
    return LANE_HEADER.format(lane=lane, cadence=cadence, offset=offset, model=model) + base
```

Lanes from `DEFAULT_LANES = ["AUDIT", "ARCHITECT", "BACKEND", "FRONTEND", "TEST", "META"]`. Defaults: cadence=300, offset=lane_index×45, model="opus-4.7".

CLI: `python3 scripts/gen_lane_launches.py [--out-dir .]`.

### 4.2 `scripts/launch_fleet.sh`

```bash
#!/usr/bin/env bash
# V9 A2 — open 6 terminals with lane-bound launch files.
set -euo pipefail
MISSION_DIR="${1:-$PWD}"
for lane in AUDIT ARCHITECT BACKEND FRONTEND TEST META; do
    echo "Launching $lane (terminal won't actually open in headless mode; print invocation):"
    echo "    cd $MISSION_DIR && claude --model opus-4.7 \"read launch-${lane}.md\""
done
```

On macOS, replace echo with `osascript` to open new Terminal windows; on Linux, `gnome-terminal --tab`. For the v9 ship, echo-only is sufficient — operator copies commands.

## 5. A3 — Fleet matrix

### 5.1 `docs/v9/fleet-matrix.md`

```markdown
# Megalodon v9 — Fleet Matrix

Provider/model assignments per lane. Operator-overridable.

## Defaults

| Lane | Model | Why | Cadence |
|------|-------|-----|---------|
| AUDIT | sonnet-4.6 | Observation/synthesis — cheap model OK | 5m |
| ARCHITECT | opus-4.7 | Heavy reasoning, SPEC drafting | 5m |
| BACKEND | opus-4.7 | Code work | 3m |
| FRONTEND | opus-4.7 | Code work | 3m |
| TEST | opus-4.7 | Code work + e2e debugging | 3m |
| META | sonnet-4.6 | Observation | 7m |

## Provider order (per operator 2026-05-16T19:50Z)

1. Claude (highest usage available)
2. Codex (second)
3. Gemini (third, 24h cooldown)
4. Mistral / Cursor (lower-tier tasks)

## Per-run overrides

Edit `.scratch/fleet-matrix-override.json` to override per mission:

```json
{
  "lanes": {"AUDIT": {"model": "haiku-4.5"}}
}
```
```

### 5.2 `scripts/fleet_select.py`

```python
"""V9 A3 — select model for a given lane."""
import json
from pathlib import Path

DEFAULTS = {
    "AUDIT": "sonnet-4.6", "ARCHITECT": "opus-4.7",
    "BACKEND": "opus-4.7", "FRONTEND": "opus-4.7",
    "TEST": "opus-4.7", "META": "sonnet-4.6",
}

def select(lane: str, mission_dir: Path) -> str:
    override = mission_dir / ".scratch" / "fleet-matrix-override.json"
    if override.exists():
        data = json.loads(override.read_text())
        return data.get("lanes", {}).get(lane, {}).get("model", DEFAULTS.get(lane, "opus-4.7"))
    return DEFAULTS.get(lane, "opus-4.7")
```

## 6. A4 — Deterministic agent IDs

`scripts/_agent_id.py`:
```python
"""V9 A4 — deterministic agent IDs from (mission, lane, launch_utc)."""
import hashlib


def deterministic_agent_id(mission_id: str, lane: str, launch_utc: str) -> str:
    """Returns agent-XXXX, where XXXX is first 4 hex chars of sha1."""
    seed = f"{mission_id}|{lane}|{launch_utc}".encode("utf-8")
    return "agent-" + hashlib.sha1(seed).hexdigest()[:4]
```

launch.md step 2 changes from `secrets.token_hex(2)` → `deterministic_agent_id(...)`.

## 7. A5 — Terminal title

In launch.md heartbeat step, add:
```python
import sys
def set_terminal_title(lane: str, agent: str, phase: str) -> None:
    sys.stdout.write(f"\033]0;{lane}:{agent}:{phase}\007")
    sys.stdout.flush()
```

Inline in launch.md as a 1-liner pattern, not a helper file.

## 8. A6 — Lane staggering

Per-lane launch file (generated by A2) has step 0: `sleep <offset>` where offset = lane_index × 45.

## 9. A7 — Per-lane cadence

Cadence comes from per-lane launch file (generated by A2) OR `.scratch/cadence-matrix.json` if present. Workers re-arm /loop on phase-flip per launch.md guidance.

`.scratch/cadence-matrix.json` schema:
```json
{
  "lanes": {
    "AUDIT": {"PHASE-PLAN": 420, "PHASE-BUILD": 300, "PHASE-RUN": 60},
    "BACKEND": {"PHASE-PLAN": 600, "PHASE-BUILD": 180, "PHASE-RUN": 120}
  }
}
```

## 10. M5 — PRE-CLASSIFY launch.md grammar

Insert into launch.md §6 (or wherever observation discipline lives):

```markdown
## §6.X PRE-CLASSIFY INVARIANTS (V9 M5)

Before classifying any artifact (finding, claim, mission state), run the
following discipline:

### Step 1 — Liveness check

```bash
stat -f "%m %z" <path>
```

If size growing across 2 ticks → "in-flight, do not classify yet."

### Step 2 — Wait for completion signal

One of:
- `done` marker file exists
- mtime stable for >60 seconds
- finding written with frontmatter

### Step 3 — PRE-CLASSIFY checklist (META-OBS-18)

- (a) Liveness check passed (Step 1+2)
- (b) Baseline-invariants check — does this match known patterns?
- (c) Uniformity check — if N items fail same way, suspect upstream invariant, not per-item bug.
- (d) Lane-bias check — am I over-attributing to my lane's known classification bias?

### Step 4 — Three cause classes (META-OBS-34)

Classify the root cause:
1. **INFRASTRUCTURE-FAILURE** — cron, network, OS resource.
2. **BEHAVIORAL** — worker logic, model output.
3. **APPLICATION-LAYER-DISCIPLINE** — protocol grammar drift, RULE violation.

**Most consensus errors come from misattributing application-layer-discipline
as infrastructure or behavioral.**

### Step 5 — Convergence-can-be-wrong (META-OBS-35)

N-LANE consensus is **necessary but not sufficient** for empirical-fact claims.
Normative-protocol claims are more reliable than empirical-fact claims.

Operator SIGNAL is the ground-truth override path when consensus is wrong.
```

## 11. M6 — INTENT-EXPIRED

Add to STATUS row Notes optional field grammar:
```
intent-declared: <task-id> @ <utc> walltime: <Nm>
```

Expiry threshold = `max(12 min, declared_walltime + 5 min)`.

Workers MUST emit periodic heartbeat-ACK every 5 min during long walltime.

`scripts/_intent_expired.py` (helper for reclaim logic):
```python
"""V9 M6 — intent-expired detection + cross-lane reclaim eligibility."""
import re
from datetime import datetime, timezone, timedelta

_INTENT_RE = re.compile(
    r"intent-declared:\s*(?P<task>[A-Z0-9_-]+)\s*@\s*(?P<utc>\S+)"
    r"(?:\s*walltime:\s*(?P<walltime>\d+)m)?"
)


def parse_intent(notes: str) -> dict | None:
    m = _INTENT_RE.search(notes)
    if not m:
        return None
    return {
        "task_id": m["task"],
        "declared_utc": m["utc"],
        "walltime_minutes": int(m["walltime"]) if m["walltime"] else 12,
    }


def is_expired(intent: dict, now: datetime | None = None) -> bool:
    declared = datetime.strptime(intent["declared_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    threshold = timedelta(minutes=max(12, intent["walltime_minutes"] + 5))
    now = now or datetime.now(timezone.utc)
    return (now - declared) > threshold
```

launch.md §6 also gains:
```markdown
## §6.Y INTENT-EXPIRED + cross-lane reclaim (V9 M6)

When declaring intent to claim a REPAIR (e.g., "BE will claim REPAIR-5 on next tick"):

1. Stamp intent in STATUS row Notes:
   `intent-declared: REPAIR-5 @ <utc> walltime: 20m`

2. Within walltime+5min, either materialize claim or expiry occurs.

3. **Long-walltime work MUST emit heartbeat-ACK every 5 min** (STATUS row Last UTC refresh).
   Missing 2 consecutive heartbeats triggers expiry regardless of walltime.

4. After expiry, peers (per task-assignment matrix) may claim freely without RULE-6 ceremony.

5. **HEAL stale-row escalation:** observer lane that detects an expired REPAIR with HEAL
   pressure files SIGNAL to operator (not auto-reclaim — observer lanes can't do code work).
```

## 12. A8 — SIGNAL grammar

`docs/v9/SIGNAL-GRAMMAR.md`:

```markdown
# V9 SIGNAL Grammar

SIGNALs are findings-class artifacts that carry cross-agent or operator-facing
directives. v9 codifies what run-2 evolved organically (SIG-ORCH-1 .. SIG-ORCH-6).

## Frontmatter (required)

```yaml
---
signal-type: <SIG-ORCH-N | SIG-LANE-X | WATCHDOG-ALERT | OPERATOR-DIRECTIVE>
addressed-to: <operator | all-lanes | <SPECIFIC-LANE>>
severity: <TIER-1 | TIER-2 | MAJOR | MINOR | INFO>
utc: <ISO-8601-UTC>
related-findings:
  - <path/to/finding-1.md>
expected-ack: <one-line description of what ACK looks like>
agent: <source-agent-id-or-name>
idempotency-key: <sha1-of-signal-content>
---
```

## Routing

| signal-type | source | targets |
|-------------|--------|---------|
| SIG-ORCH-N | orchestrator-Claude | all-lanes or specific |
| SIG-LANE-X | worker | peer lane (cross-lane handoff) |
| WATCHDOG-ALERT | watchdog | operator |
| OPERATOR-DIRECTIVE | operator | all-lanes or specific |

## Idempotency

`idempotency-key` lets workers detect re-issued SIGNALs and skip double-processing.
SIGNALs with identical idempotency-key + addressed-to are no-ops on re-read.

## File naming

`findings/<signal-type>-<NNN>-<topic>-<utc>.md` — e.g.,
`findings/SIG-ORCH-001-queue-required-2026-05-16T18-43Z.md`.

## ACK convention

ACKing a SIGNAL means:
1. Mention the SIGNAL filename in the ACK'er's next tick STATUS Notes OR finding.
2. State what action (if any) was taken.
3. If action deferred, state when.
```

`megalodon_ui/signal_parser.py`:
```python
"""V9 A8 — parse SIGNAL frontmatter from finding files."""
import re
from pathlib import Path
from typing import Any

import yaml

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_signal(path: Path) -> dict[str, Any] | None:
    """Return parsed frontmatter dict if file is a SIGNAL, else None."""
    text = path.read_text(encoding="utf-8")
    m = _FM_RE.match(text)
    if not m:
        return None
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None
    if not isinstance(fm, dict):
        return None
    if "signal-type" not in fm:
        return None
    return fm
```

## 13. Test plan

| Module | Tests | Count |
|--------|-------|-------|
| `test_agent_id.py` | A4 determinism, same inputs same output, different inputs different output | 5 |
| `test_intent_expired.py` | M6 parse, parse-missing, expiry true/false at boundary, walltime override | 8 |
| `test_lane_launch_codegen.py` | A2 generates 6 files, header has lane, body has launch.md content | 4 |
| `test_fleet_select.py` | A3 default lookup, override file, unknown lane fallback | 3 |
| `test_signal_grammar_parse.py` | A8 parse valid signal, reject non-signal, handle malformed | 3 |
| **Total** | | **23** |

## 14. Definition of done

- [ ] All 14 new files created.
- [ ] launch.md updated with §6.X (M5), §6.Y (M6), A5 ANSI title pattern, A8 SIGNAL cross-ref.
- [ ] 6 lane launch files generated.
- [ ] README.md updated with v9 ceremony additions.
- [ ] All 23 tests pass.
- [ ] HISTORY.md DOC-BUNDLE-COMPLETE entry.

## 15. Implementation order

1. A4 agent_id helper + tests (5 tests).
2. A2 gen_lane_launches.py + tests + 6 launch files generated (4 tests).
3. A2 launch_fleet.sh.
4. A3 fleet-matrix.md + fleet_select.py + tests (3 tests).
5. A8 SIGNAL-GRAMMAR.md + signal_parser.py + tests (3 tests).
6. M6 _intent_expired.py + tests (8 tests).
7. launch.md edits: M5 §6.X, M6 §6.Y, A5 title pattern, A4 deterministic ID hook, A8 SIGNAL cross-ref.
8. README.md update.
9. HISTORY.md append.

## 16. Risks

| Risk | Mitigation |
|------|------------|
| Lane launch codegen produces stale files if launch.md changes | Codegen is idempotent — re-run on launch.md change. Document in launch.md. |
| Determinic IDs collide for legitimate reasons (re-launch same mission/lane same UTC) | Vanishingly improbable (UTC has second granularity); collision would just produce same ID = re-attach to old session. |
| INTENT-EXPIRED reclaim logic too aggressive | Conservative threshold (max(12min, walltime+5min)); heartbeat-ACK requirement gives long work an escape valve. |
| SIGNAL grammar adoption slow | Documentation-only — workers can use freely; backwards-compatible with run-2 ad-hoc SIGNALs. |

## 17. Document control

- Author: orchestrator (Claude)
- Date: 2026-05-17T01:00Z
- Status: APPROVED-FOR-PLAN
- Bundle covers: A2+A3+A4+A5, A6+A7, M5, M6, A8
- Successor: `docs/superpowers/plans/2026-05-17-v9-doc-bundle.md`
