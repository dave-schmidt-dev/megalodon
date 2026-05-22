# Run — v94-ui-dogfood (2026-05-22)

Harden v9.4 dashboard + clear v9.x backlog + scope v10; each lane validates one dashboard surface against disk.

## Lanes

AUDIT, ARCHITECT, BACKEND, FRONTEND, TEST, META

## What's here

| Path | Purpose |
|------|---------|
| `findings/` | Per-agent finding files (primary output) |
| `signals/` | Inter-lane messages |
| `claims/` | Task claim mutex dirs |
| `queue/` | Queue applier intents + journal |
| `.fleet/` | Stream logs, tokens, applier log |
| `MISSION.md` `STATUS.md` `TASKS.md` `HISTORY.md` | Final mission state |
| `.mission-config.yaml` | Config that drove the spawn |
