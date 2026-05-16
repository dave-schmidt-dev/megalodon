# Status board

One row per lane. Workers self-claim a lane by editing the row (see `README.md` → "Lane assignment").

States: `unclaimed | initialized | working: <task-id> | idle | BLOCKED | PEER-REVIEWER | LANE-X-PEER-REVIEWER | STALE-RECLAIMED`

| Lane | Agent | State | Last UTC | Notes |
|---|---|---|---|---|
| LOGIC | unclaimed | — | — | |
| PROSE | unclaimed | — | — | |
| SQL   | unclaimed | — | — | |
| MATH  | unclaimed | — | — | |
| LEGAL | unclaimed | — | — | |

> Edit these lane rows to match the lanes defined in `MISSION.md` for this deployment. Default lanes above match the multi-angle review mission profile.

**Self-claim:** find the first row with `Agent = unclaimed`. Replace with your session ID (`agent-<4-hex>` from `python -c "import secrets; print('agent-'+secrets.token_hex(2))"`). Set State to `initialized`, write current UTC. Race-resolve on next tick: earlier UTC wins; loser re-claims next available lane.

---

## Surplus / observer rows

(append below as agents come online beyond the lane count)
