# P1 — LANE-A audit signal G: server.py v9.3 endpoints — half-built feature + filter false positives + route regex drift

- **Agent:** `agent-0fa4`
- **UTC:** 2026-05-19T23-04-34Z
- **Phase:** PHASE-PLAN (unchanged; `P1-A` done, awaiting PHASE-FLIP)
- **Operator messages:** `feedback/AUDIT.md` still does not exist; no new directives to ack.
- **Audit target:** uncommitted v9.3 additions in `megalodon_ui/server.py` (S-LIVE-ACTIVITY, S-NEXT-TICK-VISIBILITY).

## Summary

Three concrete signals against the uncommitted v9.3 server changes, ordered by severity:

| # | Severity | Issue | Where |
|---|---|---|---|
| G1 | **HIGH** | `S-NEXT-TICK-VISIBILITY` has no writer. Read + render + test-fixture exist; no production code or launch file emits `.fleet/<short>.next_tick.txt`. Feature will show `null` for every lane in real runs. | `server.py:721-736` vs. `launch-*.md` (no matches) |
| G2 | MEDIUM | `_TUI_BOILERPLATE_RE` filters substring matches against `Sonnet\|Haiku\|Opus`. Any agent output mentioning a model name is dropped from `last_text`. Misleading "last activity" UX. | `server.py:_TUI_BOILERPLATE_RE` (line ~58) |
| G3 | LOW | Route path-param regex `[A-Za-z]{1,4}` accepts shapes the schema rejects (`[A-Z]{1,2}`). Drift. Not a vuln (validated, no path concat), but inconsistent. | `server.py:756` vs. `mission_config/schema.py:29` |

Plus three lower-priority observations (G4-G6) documented below.

## G1 (HIGH) — `S-NEXT-TICK-VISIBILITY` has no writer

### Evidence

- **Read side** (`megalodon_ui/server.py:721-736`):
  ```python
  for lane_cfg in ctx.mission_config.lanes:
      short = (lane_cfg.short or "").upper()
      next_tick_utc: str | None = None
      if short:
          tick_path = ctx.mission_dir / ".fleet" / f"{short}.next_tick.txt"
          try:
              next_tick_utc = tick_path.read_text().strip() or None
          except OSError:
              pass
      mission_lanes.append({..., "next_tick_utc": next_tick_utc})
  ```
- **Render side** (`ui/static/pages/dashboard.js:364-367`):
  ```js
  // S-NEXT-TICK-VISIBILITY: build next_tick lookup keyed by lane name
  ...
  if (ml?.name) nextTickByLane[ml.name] = ml.next_tick_utc || null;
  ```
- **Test side** (`scripts/tests/test_back_compat_shape.py:151`):
  ```python
  (fleet_dir / "A.next_tick.txt").write_text("2026-05-19T23:00Z")
  ```
  The test creates the file itself. It does not exercise any production writer.
- **Writer side**:
  ```
  $ grep -rn "next_tick.txt\|next_tick_utc" megalodon_ui/ scripts/ launch-*.md
  → only the reader, the test fixture, and this finding.
  ```
  No `Path(...).write_text(...)` against `.fleet/*.next_tick.txt` exists anywhere in the tree, and no `launch-*.md` instructs any agent to write one.

### Impact

The S-NEXT-TICK-VISIBILITY task description (`TASKS.md:45`) says:
> per-lane `.fleet/<short>.next_tick.txt` file **written by the agent each iteration**

But the launch templates (which are the only mechanism by which agents learn the protocol) never mention `next_tick.txt`. In production, no agent writes it; the dashboard countdown will be `null` for every lane until either (a) launch templates grow a write step or (b) the harness wraps `ScheduleWakeup` server-side and writes the file itself (no such wrapper exists; `ScheduleWakeup` is a client-side Claude tool).

The integration test passes because it writes the fixture manually — classic "test green, feature dark" scenario.

### Fix

Two options:

1. **Per-launch-file instruction.** Add to every `launch-*.md` per-iteration workflow step (just before/after `ScheduleWakeup`):
   > Before calling `ScheduleWakeup(delaySeconds=N, ...)`, compute `(now + N).strftime(...)` and `Write` the result to `.fleet/<your-short>.next_tick.txt`. Use the canonical UTC format `YYYY-MM-DDTHH:MM:SSZ`.

   Pro: pure protocol change, no code. Con: agents may forget; bypassable; race against concurrent reads (see G4).

2. **Server-mediated write.** Add `POST /api/v1/lane/{short}/next_tick` body `{"next_tick_utc": "..."}` that writes atomically through the queue (tmp + rename) and add a launch step "POST your next_tick before calling ScheduleWakeup". Pro: atomic, observable, enforceable. Con: more code; another queue endpoint to maintain.

LANE-C currently holds the S-NEXT-TICK-VISIBILITY claim (`agent-d510 @ 2026-05-19T22:14:40Z`); BACKEND should pick one before marking the task done. **MISSION.md exit criterion #2 requires tests to pass — the existing test passes trivially; a stronger test should write *through the writer of record* and then read.**

## G2 (MEDIUM) — `_TUI_BOILERPLATE_RE` model-name false positives

`megalodon_ui/server.py:_TUI_BOILERPLATE_RE`:

```python
r"ctx:\s*\d"
r"|Sonnet|Haiku|Opus"
r"|session:\s*\d"
...
```

`Sonnet|Haiku|Opus` is a bare substring match (no word boundaries, no anchors). Any meaningful agent line that names a model is filtered out of `last_text`:

- `"Use Sonnet for this since it's cheap"` → filtered (boilerplate).
- `"Opus is rate-limited, retrying in 30s"` → filtered.
- `"Haiku finished the lint pass"` → filtered.

The intent is clearly to filter the Claude TUI footer line like `"claude-sonnet-4-6 · ctx: 12k/200k"`. But the `ctx:\s*\d` clause **alone** already matches that footer (it always contains `ctx:`). The model-name disjuncts are redundant for the footer and harmful elsewhere.

### Fix

Drop the `Sonnet|Haiku|Opus` alternation. Or, if a defensive filter is wanted, anchor to TUI-footer shape: `r"\b(?:Sonnet|Haiku|Opus)\b\s*\d"` (require a digit nearby, as in the footer).

## G3 (LOW) — route path-param regex drift

`API_LANE_ACTIVITY = "/api/v1/lane/{short}/activity_summary"`. The handler validates:

```python
if not re.fullmatch(r"[A-Za-z]{1,4}", short):
    raise HTTPException(status_code=422, detail="invalid lane short code")
```

But `mission_config/schema.py:29` constrains valid shorts upstream:

```python
short: Annotated[str, StringConstraints(pattern=r"^[A-Z]{1,2}$")] | None = None
```

So a request like `GET /api/v1/lane/abcd/activity_summary` passes the handler's regex (`[A-Za-z]{1,4}`) and then 404s on the missing log file — wastes a stat() and gives a confusing 404 instead of an upstream 422. Not a security issue (no path concat with user input that isn't validated), but the inconsistency invites future drift.

### Fix

Tighten the handler to mirror schema: `re.fullmatch(r"[A-Z]{1,2}", short.upper())`. Or load the allow-list dynamically from `ctx.mission_config.lanes` and 422 anything not present.

## G4 (LOW) — concurrent-write race on `.fleet/<short>.next_tick.txt`

Whichever writer ends up implementing G1's option 1, **non-atomic `Path.write_text(...)` opens-truncates-writes-closes**. The `/api/v1/state` reader can observe a zero-byte file mid-write; `read_text().strip() or None` then returns `None`, and the dashboard countdown blanks momentarily.

Not corruption — just UX flicker. Fix: `tmp_path = path.with_suffix(".txt.tmp"); tmp_path.write_text(...); tmp_path.replace(path)`. Atomic on the same filesystem on POSIX. Document it as part of the launch-file instruction.

## G5 (LOW) — hard-coded macOS path filter

`_TUI_BOILERPLATE_RE` includes `r"|^\s*/Users/"` — strips lines starting with a macOS home path. On Linux fleets (CI, future hosts) this filter does nothing, and Linux paths like `/home/...` will leak through `last_text` and into the dashboard. Either generalise to `r"|^\s*/(?:Users|home|root)/"` or, better, redact paths via a separate cleanup pass on `last_text`.

## G6 (LOW) — magic-number status thresholds

`status = "active" if age < 30 else ("idle" if age < 300 else "blocked")` — bare seconds. The poll interval is config-driven (`ctx.config.poll_interval_seconds`). These thresholds should be derived from that (e.g. `< 3 * poll_interval = active`) or at minimum named constants alongside `_STREAM_TAIL_BYTES`.

## Cross-references

- TASKS.md:45 — `S-NEXT-TICK-VISIBILITY` task description (claimed by agent-d510, LANE-C, since 22:14:40Z — currently 50+ minutes held; would be flagged "stuck" by the proposed orchestrator-auto-loop's 10-minute threshold).
- TASKS.md:47 — `S-LIVE-ACTIVITY` task description (the source spec for `_parse_stream_tail`).
- Prior finding: `agent-0fa4-A-P1-status-applier-seed-mismatch-2026-05-19T22-35-08Z.md` (STATUS.md seed/regex/applier triple mismatch — orthogonal, still unresolved as of this tick).
- MISSION.md exit criterion #2 — tests must pass. The existing test for `S-NEXT-TICK-VISIBILITY` is a tautology (test writes the file the test then reads); BACKEND should strengthen it with an end-to-end write-then-read path.

## Iteration housekeeping

- No claimable `[LANE-A]` task in PHASE-PLAN (P1-A still done).
- No new operator feedback at `feedback/AUDIT.md`.
- Did NOT submit a `status_update` POST this tick — G-prior seed-mismatch bug still unfixed (the applier would reject this update too).
- `ScheduleWakeup(300)` per launch step 12.
