# P1 — LANE-A audit signal F: STATUS.md seed/regex/applier triple mismatch

- **Agent:** `agent-0fa4`
- **UTC:** 2026-05-19T22-35-08Z
- **Phase:** PHASE-PLAN (unchanged from tick 12)
- **State:** idle (P1-A done, no further `[LANE-A]` in P1; awaiting PHASE-FLIP)
- **Operator messages:** `feedback/AUDIT.md` still does not exist; no new directives to ack.
- **Refines:** tick-12 signal E (which incorrectly hypothesised "STATUS_UPDATE handler missing"). Handler exists; the bug is a *format* mismatch between the seed STATUS.md, the parser regex, and the applier regex.

## Root cause (with code evidence)

Three layers all disagree on the shape of a STATUS.md row:

### Layer 1 — seed STATUS.md (lines 12-18)

```
| LANE-A (AUDIT) | — | unclaimed | — | — |
```

Lane cell content is the long name `LANE-A (AUDIT)`.

### Layer 2 — `parse_status` regex (used by `/api/v1/state` → dashboard)

`megalodon_ui/mission_config/regex_builder.py:73-79`:

```python
r"^\|\s*(?P<lane>[A-Z][A-Z\- ]*?)\s*\|\s*..."
```

The lane character class is `[A-Z\- ]` — it allows uppercase letters, hyphens, and spaces, but **not `(` or `)`**. Against `| LANE-A (AUDIT) |` the engine reaches `LANE-A `, then `\s*\|` needs to find a pipe; instead it sees `(AUDIT)`. The class can't extend to consume `(`, so the whole row fails to match.

**Consequence:** `parse_status` returns `[]` for every row whose lane cell contains parentheses → `status.lanes == []` in `/api/v1/state` → dashboard lane cards render with state `—` / task `—` for every lane.

This is the direct root cause of `BUG-STATUS-NOT-WRITTEN` in TASKS.md (line 58) — not "agents never call status_update" as the bug description hypothesises; the data simply can't round-trip through the parser even if the file held real content.

### Layer 3 — `_apply_status_update` regex (queue applier)

`megalodon_ui/queue/applier.py:404-418`:

```python
def _apply_status_update(self, target, payload, req):
    lane = payload["lane"]                # e.g. "A"
    with AtomicFile(target) as f:
        content = f.read()
        pattern = rf"^\|\s*{re.escape(lane.upper())}\s*\|.*$"
        matches = re.findall(pattern, content, re.MULTILINE | re.IGNORECASE)
        if len(matches) != 1:
            raise ValueError(f"status-row-not-unique:lane={lane}:matches={len(matches)}")
        new_row = (
            f"| {lane.upper():<9} | {payload.get('agent', req['agent'])} "
            f"| {payload['new_state']} | {payload['new_utc']} | {payload['new_notes']} |"
        )
        f.write(content.replace(matches[0], new_row))
```

The applier searches for `^\|\s*A\s*\|` — i.e. a row whose first cell content is **exactly** `A`. The seed has `LANE-A (AUDIT)`. No match → `matches=0` → `ValueError("status-row-not-unique:lane=A:matches=0")` → caught at `applier.py:270`, written to `rejected/` with reason `"apply-failed: status-row-not-unique:..."`.

If the applier ever did successfully write, it would write `| A         | ...` (9-char padded short code) — **a different format from the seed**, but one that both the applier's own regex and `parse_status`'s `[A-Z\- ]*?` lane class could parse. So the format used by writes is internally self-consistent; only the seed is wrong.

## Tick-12 pending-forever anomaly explained

Tick-12 observed `status=pending` with `rejection_reason: null` across 5 one-second polls. The applier should have rejected the request within one drain cycle (~1 s) per `applier.py:270-274`. Three plausible explanations:

1. **Applier not running.** A long-running fleet without restarts can drift if `lifespan` shut down the applier task. Operator should check `ps`/`lsof` for the background applier task.
2. **Drain loop stalled.** `applier.py:215` iterates `self.pending_dir.glob("*.json")` sorted by submitted_utc. If an earlier request raised an unexpected exception, `applier.py:283` re-raises and would crash the drain loop. Subsequent submissions then sit in `pending/` indefinitely.
3. **Drain interval longer than the 5-second polling window.** Tick-12's polling was 5×1 s. If the drain interval is ≥5 s, the rejection would just have appeared after the poll window closed.

The journal in `queue/journal/` (and `queue/rejected/`) is the ground truth. Whoever investigates next should `ls -la queue/{pending,applied,rejected,journal}/` and pick the most recent STATUS_UPDATE rid to see which bucket it landed in.

## Fix recommendation (one-line for BACKEND)

The **smallest** fix is to change the seed `STATUS.md` format to match what the applier writes. Replace each row like:

```
| LANE-A (AUDIT) | — | unclaimed | — | — |
```

with the canonical write-shape:

```
| A         | —     | unclaimed | —                    | —                    |
```

(Or whatever exact spacing `_apply_status_update` produces — the applier uses `{lane.upper():<9}` for column 1; other columns just have single spaces around the pipe.)

This is a one-time edit to STATUS.md (or to whatever seed template the spawner uses). No code change required. **Caveat:** the operator probably *wants* the long form for human readability. If so, the better fix is to change both regexes to accept `LANE-<short>\b[^|]*` in cell 1 — but that's two-file code change vs. a one-file data change.

Either way, MISSION.md exit criteria gate on tests passing, so the integration test in `ui/tests/integration/test_api_endpoints.py` should grow a case asserting `status.lanes` is non-empty after a single STATUS_UPDATE round-trip against the v9.3-shaped seed.

## Cross-references

- `BUG-STATUS-NOT-WRITTEN` (TASKS.md:58) — this finding is the diagnosis.
- Tick-12 signal E (`agent-0fa4-A-P1-idle-2026-05-19T22-15-10Z.md`) — refined: handler exists, format mismatch is the actual bug.
- `BUG-PHASE-INDICATOR-STUCK` (TASKS.md:60) — unrelated but adjacent; suggests the FE store-subscriber issue is separate from `status.lanes` being empty.

## Iteration housekeeping

- No claimable `[LANE-A]` task in PHASE-PLAN; P1-A done.
- No new operator feedback at `feedback/AUDIT.md`.
- Did NOT submit a `status_update` POST this tick — would just deepen the rejected/ pile against the same bug. Will resume status pings once Layer 1 or Layer 2/3 is fixed.
- ScheduleWakeup(300) per launch step 12.
