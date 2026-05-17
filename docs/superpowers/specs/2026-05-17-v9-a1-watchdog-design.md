---
title: V9 A1 — Watchdog daemon (design spec)
status: APPROVED-FOR-PLAN
version: 1.0
utc: 2026-05-17T00:30Z
roadmap-anchor: docs/v9/V9-ROADMAP.md §A1 + Migration plan §3e
---

# V9 A1 — Watchdog daemon

## 1. Goal

Detect crashed / silent / hung worker sessions and notify the operator via SIGNAL finding. Reduces operator-babysitting burden during long missions.

## 2. Locked decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **SIGNAL-only — NEVER auto-respawn** | Per V9-ROADMAP A1 + self-contrarian OW-4. Auto-respawn during legitimate deep-thinking would be worse than the problem. Operator decides whether to act. |
| D2 | **3-signal detection: process death + STATUS stale + JSONL stale** | Per V9-ROADMAP. Context-window monitoring deferred to v10 (requires Claude Code telemetry that doesn't exist). |
| D3 | **Standalone Python daemon, separate process** | Like the applier. `python3 -m megalodon_ui.watchdog --mission-dir PATH`. Operator launches alongside applier. |
| D4 | **State at `.scratch/watchdog/state.json`** | Per-lane health. Mission-scoped (mission's own .scratch/). Cleared on watchdog start. |
| D5 | **SIGNAL findings via existing finding-write convention** | Write to `findings/watchdog-ALERT-<lane>-<utc>.md` with frontmatter `signal-type: WATCHDOG-ALERT`. No new infrastructure. |
| D6 | **Polls every 60s** | Per V9-ROADMAP. Low overhead; latency acceptable for "did the worker just die?" detection. |
| D7 | **De-duplicate alerts** | Same lane in same state shouldn't generate alerts every 60s. Once an alert fires, suppress for that lane until state changes (recovery or new failure type). |
| D8 | **Operator-side tooling: `scripts/start_watchdog.sh`** | Matches `scripts/start_applier.sh` pattern. |

## 3. Detection signals

### S1 — Process death
Per-lane: lookup PID from a `~/.megalodon-pids/<lane>.pid` file (operator/launcher writes this on session start). If file exists but PID is dead → ALERT-CRASHED.

If PID file doesn't exist, watchdog skips that lane (can't observe).

### S2 — STATUS row stale
Read `STATUS.md`. For each lane row, compute `now - last_utc`. Threshold = `max(15 min, 3 × loop_cadence_seconds)`. Default cadence = 300s (5min), so default threshold = 15min. If stale → ALERT-STATUS-STALE.

### S3 — JSONL session log stale
For each lane with a known PID (from S1), find the session JSONL log at `~/.claude/projects/<path-encoded>/<session-id>.jsonl`. If mtime is >5 min old AND STATUS row mtime is newer (suggesting STATUS heartbeat is being written but no tool/text activity) → ALERT-HUNG.

If session JSONL can't be located (path-encoding mismatch, etc.), skip.

## 4. File layout

```
megalodon_ui/watchdog/
├── __init__.py
├── __main__.py             # CLI entry
├── daemon.py               # Main loop + signal handlers
├── detectors.py            # S1, S2, S3 implementations
└── alerts.py               # Write SIGNAL findings + dedup state

scripts/
├── start_watchdog.sh       # Operator-friendly launcher

scripts/tests/
├── test_watchdog_detectors.py    # S1-S3 unit tests
└── test_watchdog_alerts.py       # alert dedup + finding write
```

## 5. State file

`<mission_dir>/.scratch/watchdog/state.json`:
```json
{
  "started_utc": "2026-05-17T00:30:00Z",
  "last_poll_utc": "2026-05-17T00:31:00Z",
  "lanes": {
    "AUDIT": {"last_alert_type": null, "last_alert_utc": null, "pid": 12345, "status": "ok"},
    "BACKEND": {"last_alert_type": "CRASHED", "last_alert_utc": "2026-05-17T00:30:30Z", "pid": null, "status": "alerted"}
  }
}
```

Written atomically (tmp + rename) at end of each poll cycle.

## 6. Alert dedup logic

For each detector + lane:
1. Detect current state (ok / CRASHED / STATUS-STALE / HUNG).
2. If state == "ok": clear any prior alert state for this lane.
3. If state != "ok" and `last_alert_type != current_state`: write SIGNAL finding + update state.
4. If state != "ok" and `last_alert_type == current_state`: no-op (suppress duplicate).

## 7. SIGNAL finding format

`findings/watchdog-ALERT-<lane>-<utc>.md`:
```markdown
---
signal-type: WATCHDOG-ALERT
addressed-to: operator
severity: TIER-1
lane: <LANE>
alert-type: CRASHED|STATUS-STALE|HUNG
utc: 2026-05-17T00:30:30Z
agent: watchdog
expected-ack: operator decides — restart, signal worker, or dismiss
---

# Watchdog alert: <LANE> lane <alert-type>

**Detected at:** 2026-05-17T00:30:30Z (poll #N)

**Signal:** <human-readable description>

**Suggested action:** <suggested-action-based-on-alert-type>

**Evidence:**
- <fact 1 from detector>
- <fact 2>

This is an automated notification. The watchdog will NOT auto-respawn or
take any other action. Operator decision required.
```

## 8. CLI

```
python3 -m megalodon_ui.watchdog --mission-dir PATH [--poll-seconds 60] [--cadence-seconds 300] [--debug]
```

Foreground process. SIGTERM/SIGINT → graceful exit (final state write, exit 0).

## 9. Test plan

- `test_detector_process_alive_returns_ok`
- `test_detector_process_dead_returns_crashed`
- `test_detector_status_stale_above_threshold`
- `test_detector_status_fresh_returns_ok`
- `test_detector_jsonl_stale_with_fresh_status_returns_hung`
- `test_detector_jsonl_missing_skips_silently`
- `test_alert_writes_finding_with_frontmatter`
- `test_alert_dedup_suppresses_duplicate`
- `test_alert_clears_on_recovery_state_ok`
- `test_state_file_persisted_atomically`
- `test_daemon_full_cycle_smoke` (integration)

Total: ~11 tests.

## 10. Definition of done

- [ ] `megalodon_ui/watchdog/{__init__,__main__,daemon,detectors,alerts}.py` shipped.
- [ ] `scripts/start_watchdog.sh` operator launcher.
- [ ] All 11 tests pass.
- [ ] Smoke: spawn watchdog against a fixture mission with intentionally stale STATUS row → alert finding lands.
- [ ] launch.md update: operator startup sequence mentions watchdog as optional.
- [ ] HISTORY.md A1-COMPLETE entry.

## 11. Implementation order

1. `detectors.py` + `test_watchdog_detectors.py` (6 tests).
2. `alerts.py` + `test_watchdog_alerts.py` (4 tests).
3. `daemon.py` (main loop) + `__main__.py` (CLI).
4. `scripts/start_watchdog.sh`.
5. Integration smoke (1 test).
6. launch.md + README.md updates.
7. HISTORY.md entry.

## 12. Risks

| Risk | Mitigation |
|------|------------|
| Path-encoding mismatch for Claude Code JSONL log location | Skip silently per spec §3. Don't crash on missing log. |
| PID file missing for lanes operator didn't write it for | Skip that lane (can't observe). |
| False-positive alerts during legitimate deep thinking | D7 dedup limits noise; D1 (no auto-action) limits damage. Operator filter via finding-severity. |
| Watchdog process itself crashes | Operator-launched; watchdog daemon failure is operator-visible (no findings appearing). Document in README. |
| Concurrency with applier writing to findings/ | findings/ is single-writer-per-file; collision risk near zero. |

## 13. Document control

- Author: orchestrator (Claude)
- Date: 2026-05-17T00:30Z
- Status: APPROVED-FOR-PLAN
- Predecessor: V9-ROADMAP §A1
- Successor: `docs/superpowers/plans/2026-05-17-v9-a1-watchdog.md`
