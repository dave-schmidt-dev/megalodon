# Status board

One row per lane. Lanes self-claim via tick activity (writing to claims/).

States: `unclaimed | initialized | working: <task-id> | idle | BLOCKED | STALE-RECLAIMED`

Current phase: `PHASE-PLAN`

---

| Lane | Agent | State | Last tick | Current task |
|---|---|---|---|---|
| A         | agent-0fa4 | idle | 2026-05-20T01-11-07Z | no new finding this tick (deliberate: nothing material changed). LANE-C did refresh STATUS at 01:06:53Z (9-min lag after task/done — G-3 empirics sharpened but no new bug). D still stuck 73+ min. HIGH/MEDIUM unactioned. Extending cadence to 1200s matching B. Next wake 01-31Z. |
| B         | agent-f66a | idle | 2026-05-20T01-06-07Z | heartbeat: cadence bump 600s -> 1200s (stable-blocker awaiting operator action; per ScheduleWakeup docs); discipline-propagation-lag pattern has 3rd datapoint (LANE-C status_update still skipped 41min after BUG-STATUS-NOT-WRITTEN fix shipped) |
| C         | agent-d510 | idle | 2026-05-20T01:06:53Z | P2-C shipped: CV-9 stream reader + pipe_pane wiring + 9 tests. Awaiting P3 task. |
| D         | agent-07c5 | working: P2-D | 2026-05-19T23-58Z | wiring 4 deferred Playwright specs |
| E         | agent-db2a | idle | 2026-05-20T00-14-27Z | P1-E done: 84-test audit suite run, 59 pass/25 fail, 4 findings filed, signal sent to LANE-D |
| F         | agent-d55b | idle | 2026-05-20T00-51-14Z | Iteration 11: LANE-A flagged MEDIUM D-stuck (52+ min no heartbeat). LANE-C P2-C done (00:34Z) but status row not refreshed. LANE-A/B sync to 10m cadence. |
