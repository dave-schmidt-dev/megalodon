---
title: v9.3 — Orchestrator Auto-Loop (Protocol Contract)
status: DRAFT
version: 0.1
utc: 2026-05-19T23-46-02Z
owner: agent-f66a (LANE-B ARCHITECT)
companions:
  - docs/v9/v9-3-HYBRID-DASHBOARD.md
  - docs/v9/api-contract.md
  - docs/v9/v9-2-ROADMAP.md
sibling-task: S-ORCHESTRATOR-AUTO-LOOP (LANE-B+C)
---

# v9.3 — Orchestrator Auto-Loop (LANE-G)

> Design contract for a 7th `/loop`-driven Claude Code session that
> watches the fleet on the operator's behalf. Its job: turn the operator
> from a tick-by-tick approval bottleneck into a high-stakes-decisions-only
> reviewer.
>
> **Lane label: `LANE-G` (Guardian).** Chosen over `LANE-ORCH` to keep the
> single-letter regex invariant (`^[A-Z]{1,4}$`, `permission_watcher.py:55`
> et al.) intact across the fleet.

## §1 Scope

The auto-loop session has **read access to the fleet state** and
**write access to three narrow surfaces**:

1. `POST /api/v1/permission_prompts/{lane}/respond` — auto-approve calls
   whose detected command matches the safelist (§3).
2. `Write` to `feedback/<LANE>.md` — operator-style messages to nudge
   stuck or misbehaving lanes (§4).
3. `PushNotification` to the operator — significant-event alerts (§5).

The auto-loop **does NOT**:

- claim or release tasks (preserves the v9.3 "queue mediates all task
  writes" invariant; the operator still drives phase progression);
- flip phases or change mission status;
- write to TASKS.md, STATUS.md, HISTORY.md, or `.mission-events`;
- modify source code, tests, or design docs;
- send signals to other lanes via `/api/v1/signal` (signals are
  cross-lane work directives — operator-only intent).

This carves a deliberately small surface so a hung or misbehaving
auto-loop cannot corrupt mission state.

## §2 Tick structure

Each iteration is **read-mostly**. Pseudo-code (transliterated to the
actual tool calls the agent will make):

```
1. Read feedback/LANE-G.md (operator instructions targeting the guardian).
2. GET /api/v1/state               → fleet snapshot
3. GET /api/v1/permission_prompts  → pending prompts across all lanes
4. For each pending prompt:
     - parse detected command (§3.1)
     - if command ∈ safelist AND lane is in expected state → auto-approve
     - else → PushNotification(operator) + skip
5. Compute stuck claims (§4) from /api/v1/state:
     - any claim held > STUCK_THRESHOLD with no new finding → feedback nudge
6. Compute significant events since last tick (§5) → PushNotification
7. Write findings/agent-<id>-G-<phase>-tick-<UTC>.md (per-iter heartbeat).
8. ScheduleWakeup(delaySeconds=30, prompt=<same /loop>)
```

**Tick cadence: 30s.** Justification: permission prompts are
operator-blocking, so the auto-loop must beat the operator's eyeball
cadence. 30s is well under the 5-min prompt-cache window
(LANE-G iterations stay cache-warm) and 60× more responsive than the
existing 5-min idle lanes.

**Cold-tick budget: ≤ 8 BE calls.** One `state`, one `permission_prompts`,
plus up to N approval POSTs and M feedback writes. The BE serves this
load trivially.

## §3 Permission auto-approval

### §3.1 Detection format

The BE endpoint `GET /api/v1/permission_prompts` (to be implemented by
LANE-C; see §8 for the spec) returns:

```python
class PermissionPromptsResponse(BaseModel):
    prompts: list[PermissionPrompt]

class PermissionPrompt(BaseModel):
    lane: str                 # "A".."F" (or "G"; ignored — guardian
                              # cannot have permission prompts since it
                              # operates in a sandbox session)
    detected_text: str        # raw permission banner text
    parsed_command: str | None  # best-effort first-line command, e.g.
                              # "find . -name '*.py'" or "git push origin main"
    detected_at_utc: str      # ISO8601 UTC of first detection
    lane_state: str           # mirror of STATUS.md state for context
```

The guardian uses `parsed_command` for safelist matching. `parsed_command`
is **best-effort** and may be `None` for prompts the BE can't unambiguously
parse — those always escalate.

### §3.2 Safelist policy (allowlist, not denylist)

A prompt is auto-approved **only if** all four hold:

1. `parsed_command` is non-null.
2. `parsed_command` matches **at least one** safelist entry (§3.3).
3. `parsed_command` matches **zero** denylist entries (§3.4).
4. `lane_state` is one of `working: <task-id>` or `initialized`
   (no auto-approve for `BLOCKED`, `STALE-RECLAIMED`, or `unclaimed`).

Failsafe direction: an unknown command **escalates to the operator**
(PushNotification, no approval).

### §3.3 Safelist (v9.3 initial set)

```yaml
safelist:
  # Read-only file & dir inspection
  - 'ls(\s+.*)?'
  - 'find\s+\.\s+-name\s+\S+'      # find . -name PATTERN (no -exec)
  - 'find\s+\.\s+-type\s+[fdl]'    # find . -type f/d/l (no -exec)
  - 'cat\s+\S+'
  - 'head(\s+-n\s+\d+)?\s+\S+'
  - 'tail(\s+-n\s+\d+)?\s+\S+'
  - 'wc(\s+-[lwc])?\s+\S+'

  # Read-only text search
  - 'grep(\s+-[a-zA-Z]+)?\s+\S+\s+\S+'
  - 'rg(\s+--?\S+)*\s+\S+'

  # Read-only metadata
  - 'stat\s+\S+'
  - 'file\s+\S+'
  - 'realpath\s+\S+'
  - 'basename\s+\S+'
  - 'dirname\s+\S+'

  # Read-only git
  - 'git\s+status(\s+\S+)*'
  - 'git\s+diff(\s+\S+)*'
  - 'git\s+log(\s+\S+)*'
  - 'git\s+show(\s+\S+)*'
  - 'git\s+branch(\s+\S+)*'
  - 'git\s+rev-parse(\s+\S+)*'
  - 'git\s+ls-files(\s+\S+)*'

  # v9 protocol primitives
  - 'mkdir\s+claims/[A-Za-z0-9_-]+/?'
  - 'rm\s+-rf\s+claims/[A-Za-z0-9_-]+/?'
  - 'rmdir\s+claims/[A-Za-z0-9_-]+/?'

  # Time
  - 'date(\s+-u)?(\s+\+\S+)?'
```

### §3.4 Denylist (hard refuse — never auto-approve)

```yaml
denylist:
  - '.*\b(rm|rmdir)\b(?!\s+-rf\s+claims/).*'   # any rm outside claims/
  - '.*\bgit\s+(push|reset|rebase|checkout|restore|clean)\b.*'
  - '.*\b(curl|wget|ssh|scp|nc|netcat)\b.*'    # network ops (operator-only)
  - '.*\bpython3?\b.*'                          # arbitrary code injection
  - '.*\bnpm\b.*'
  - '.*\buv\s+(?!run\s+--with\s+pytest).*'      # only the canonical pytest cmd
  - '.*\bsudo\b.*'
  - '.*[;&|`$].*'                               # any compound/substitution
```

Order: **denylist wins**. A command matching anything in the denylist is
refused even if it also matches a safelist entry. (Defensive layering;
the regexes shouldn't overlap in practice.)

### §3.5 Rate limit

To bound blast radius if the safelist regex has a bug: **≤ 12
auto-approvals per lane per minute**, **≤ 30 fleet-wide per minute**.
Exceeding either limit pauses auto-approval and PushNotification-escalates
the next prompt to the operator with a "rate-limit tripped" note.

## §4 Stuck-claim detection

A claim is **stuck** when both:

1. Claim directory `claims/<task-id>/` has existed for `> STUCK_THRESHOLD`.
2. No new finding from the owning agent in the same window.

`STUCK_THRESHOLD = 10 minutes` (matches the operator quote in the task
description; configurable via `MissionConfig.stuck_claim_threshold_s`).

When detected, the guardian writes to `feedback/<LANE>.md`:

```markdown
## <UTC> — Stuck-claim nudge (auto-detected by LANE-G)

`claims/<task-id>/` has been held for <N> minutes by `<agent-id>` with
no new finding since `<last-finding-utc>`. What's blocking? Reply with
a finding or release the claim (`rm -rf claims/<task-id>/`).
```

**De-dup**: the guardian tracks
`{lane: last_nudge_utc_for_<task-id>}` and re-nudges at most every
`STUCK_THRESHOLD / 2` minutes per (lane, task-id) pair.

**Escalation**: if a single (lane, task-id) accrues ≥ 3 nudges without
a finding, the guardian PushNotifications the operator instead of
nudging again.

## §5 PushNotification triggers

The guardian generates **at most one PushNotification per significant
event**. Triggers:

| Event | Detection | Body |
|---|---|---|
| Phase boundary | `mission.phase` differs from prior tick | `"PHASE-FLIP: <prev> → <next> at <UTC>"` |
| Task completed | new `[done: ...]` in TASKS.md since last tick | `"DONE: <task-id> by <agent-id>"` |
| Lane stuck (3rd nudge) | §4 escalation criterion | `"STUCK: lane <X> on <task-id> for <Nm>, nudges exhausted"` |
| Permission rate-limit tripped | §3.5 | `"RATE-LIMIT: auto-approval paused for lane <X>"` |
| Unrecognized prompt | §3 escalation | `"PROMPT: lane <X> needs operator review: <first-line of detected_text>"` |
| Mission status change | `mission.status` differs from prior tick | `"MISSION: <prev> → <next>"` |
| Mission COMPLETE | `mission.status == "COMPLETE"` | `"MISSION COMPLETE: <UTC>"` |

Notifications **never** include the full detected text (may contain
secrets-adjacent content); only the parsed command first line or a
fixed-shape summary.

## §6 Security model

The guardian session **has a different `--allowedTools` set** than the
worker lanes. Specifically:

- **Permitted**: `Read`, `Write` (only to `feedback/`, `findings/`),
  `Bash` (only `date`, single-`curl` to `127.0.0.1:8765`), `Glob`,
  `Grep`, `ScheduleWakeup`, `PushNotification`.
- **Forbidden**: `Edit`, any compound bash, any non-localhost network
  op, any python3, any phase-flip / mission-status / inject-task /
  signal / reclaim endpoint.

The auto-loop's launch file (`launch-GUARDIAN.md`, new in v9.3) hard-codes
these and re-asserts them every iteration.

**Token bootstrap**: the guardian reads `.fleet/ui.token` once at
session start and exchanges it via `POST /api/v1/auth/exchange` for the
cookie that authorizes every subsequent call — exact same pattern as
the worker lanes.

## §7 Failure modes & defenses

| Failure | Defense |
|---|---|
| Guardian crashes / hangs | Operator dashboard surfaces "LANE-G last tick: <Nm ago>" the same way every other lane does; operator-driven reclaim works on LANE-G identically. |
| Guardian over-approves due to regex bug | §3.5 rate limit caps blast radius; denylist (§3.4) wins over safelist; operator can disable the auto-loop by `touch .fleet/GUARDIAN.disabled` (checked at tick start). |
| BE `/api/v1/permission_prompts` returns stale data | Guardian compares `detected_at_utc` to current time; prompts older than 30s are escalated, not auto-approved (assumes lane TUI cleared the prompt before BE re-scanned). |
| Two guardian sessions race (operator restarted) | Singleton lock: `.fleet/GUARDIAN.lock` written with PID + start UTC; new sessions exit if lock is fresh (< 2× tick cadence). |
| Auto-loop writes to wrong lane | `feedback/<LANE>.md` is single-writer per lane (the operator and the guardian); guardian's writes are signed with `LANE-G` prefix in each message so origin is unambiguous. |

## §8 BE contract additions (LANE-C work)

Two endpoints must exist for the guardian to function. Both are
**new in v9.3** and belong in `api-contract.md`:

### §8.1 GET /api/v1/permission_prompts

```yaml
method: GET
path: /api/v1/permission_prompts
response_model: PermissionPromptsResponse
status: 200
content_type: application/json
fe_consumers:
  - ui/static/pages/dashboard.js:656
description: List currently-detected permission prompts across all worker lanes.
```

Source of truth: `PermissionWatcher.scan_all()` over the worker lane
short-codes from `MissionConfig.lanes`. Cached for ≤ 1s server-side to
amortize stream-log reads when both the dashboard and the guardian
poll concurrently.

### §8.2 POST /api/v1/permission_prompts/{lane}/respond

```yaml
method: POST
path: /api/v1/permission_prompts/{lane}/respond
response_model: ActionResponse
status: 200
content_type: application/json
fe_consumers:
  - ui/static/pages/dashboard.js:669
description: 'Respond to a lane permission prompt; body {"action": "approve"|"deny", "source": "operator"|"guardian"}.'
```

Approval mechanism: server sends `"1\n"` (Allow) or `"2\n"` (Deny) to the
target lane's tmux pane via `send-keys`. **Then** calls
`PermissionWatcher.clear_lane(lane)` to start the existing 5s
suppression window (`permission_watcher.py:75`). The `source` field is
recorded in `findings/<agent>-G-<phase>-tick-<UTC>.md` for audit.

**Auth**: same cookie-exchange as the existing v9.3 queue endpoints. The
guardian uses its own token issued at fleet-startup; the operator
dashboard uses the regular operator token.

## §9 Test plan (LANE-E)

Five tests are required before the guardian ships:

1. **`test_safelist_allows_canonical_commands`** — assert each safelist
   entry in §3.3 matches at least one realistic example command and zero
   denylist entries.
2. **`test_denylist_wins_over_safelist`** — construct a parsed_command
   that matches both lists; assert auto-approve refuses.
3. **`test_rate_limit_per_lane`** — fire 13 approve-eligible prompts at
   one lane in 60s; assert the 13th escalates instead of approving.
4. **`test_stuck_claim_detection`** — write a fake claim directory with
   mtime > STUCK_THRESHOLD, no finding from owner; assert the guardian's
   feedback writer produces the expected message and de-dup tracker
   records it.
5. **`test_singleton_lock`** — start a second guardian; assert it exits
   non-zero when `.fleet/GUARDIAN.lock` is fresh.

All five fit under the mission's existing exit-criterion command
(no new test infra needed).

## §10 Open questions

1. **LANE-G vs. out-of-band process.** The task description suggests
   either "7th Claude Code session" or "out-of-band". This design
   assumes 7th Claude session, because it gives the guardian the same
   tooling (`Read`, `Write`, `Bash`, `ScheduleWakeup`, `PushNotification`)
   as worker lanes for free. An out-of-band script (e.g. systemd timer
   calling a python script) is simpler but loses the LLM's safelist
   adjudication of edge-case commands. **Recommend: LANE-G as 7th
   Claude session, opus-4-7 model.** Cost is one extra
   `claude` REPL running at 30s cadence; trivially affordable.
2. **Should LANE-G also write `signals/`?** Cross-lane signals are
   currently operator-only intent (per §1). I lean **no** for v9.3:
   the guardian's `feedback/` nudges are enough; signals can come in
   v9.4 once the guardian has a track record. Operator decision.
3. **Auto-approval audit log.** Findings in `findings/agent-<id>-G-*.md`
   capture every approval, but should we also append to a dedicated
   `.fleet/GUARDIAN.audit.log` for grep-friendly review?
   **Recommend: yes**, NDJSON, one line per approve/deny; rotation
   via `RotatingFileHandler` (1MB / 2 backups), matching project
   convention.

## §11 Rollout

Phased to avoid surprising the operator:

1. **v9.3 ship** — BE endpoints (§8) only. Dashboard uses them; no guardian
   yet.
2. **v9.3.1** — Guardian session launches with `--dry-run` (logs would-be
   approvals to audit log but doesn't actually POST `/respond`). Operator
   reviews the audit log for a full mission run.
3. **v9.3.2** — Drop `--dry-run`. Auto-approval live. Rate limit + denylist
   are the only safety net.
4. **v9.4** — Operator-tunable safelist via `MissionConfig`
   (`guardian.safelist`, `guardian.denylist`). Until v9.4 the lists are
   compiled-in.

## §12 Cross-doc references

- v9.2 ROADMAP §"Task 1.6 CV-9" — orthogonal (stream-reader is LANE-C
  P2-C); guardian reads the same `.fleet/<short>.stream.log` files but
  via the BE not directly.
- v9.3 HYBRID-DASHBOARD §1 — the operator's UI for permission prompts.
  Guardian and dashboard read the same `/api/v1/permission_prompts`
  endpoint; the dashboard surfaces what guardian-the-policy chose to
  escalate.
- v9.1 MISSION-CONFIG — `MissionConfig` gains
  `guardian.{enabled,tick_cadence_s,stuck_claim_threshold_s}` in v9.4
  (no schema change in v9.3 — guardian is compiled-in).
