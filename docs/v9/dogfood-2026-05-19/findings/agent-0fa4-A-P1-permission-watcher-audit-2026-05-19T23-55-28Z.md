# Finding: PermissionWatcher post-fix audit (BUG-PROMPT-FLICKER)

**Agent:** agent-0fa4
**Lane:** A (AUDIT)
**Phase:** PHASE-PLAN
**Status type:** idle-with-audit (no claimable LANE-A task in current phase)
**UTC:** 2026-05-19T23-55-28Z
**Severity:** INFO (with 2 LOW tail-risks called out)

---

## Why this finding

PHASE-PLAN is still active; the only `[LANE-A]` task here (`P1-A`) is `done`. No
`[LANE-A]` rows in the secondary pool are open. Per launch-AUDIT.md step 12 I am
idle this iteration, but the AUDIT charter (protocol adherence + dead code +
race conditions) maps cleanly onto the most recent BE change shipped tonight:
LANE-C's `BUG-PROMPT-FLICKER` fix at `2026-05-19T23-47-27Z`. So this idle note
doubles as a peer-audit of that fix while the operator considers phase progression.

I reviewed:
- `megalodon_ui/permission_watcher.py` (83 LoC, the post-fix module)
- `scripts/tests/test_permission_watcher.py` (13 tests, all passing per d510's finding)
- `findings/agent-d510-C-P1-bug-prompt-flicker-fix-2026-05-19T23-47-27Z.md`

## What the fix does well

1. **Correct clock choice.** `time.monotonic()` (lines 46, 82) is the right
   primitive for a suppression window. NTP corrections, DST, and wall-clock jumps
   cannot end suppression early or extend it. A `time.time()` implementation
   could have introduced a subtle, hard-to-repro flicker on clock skew.
2. **Per-lane isolation.** `_suppressed_until: dict[str, float]` keyed by
   uppercase lane (line 43, normalized at 54/81) means clearing C cannot mute D.
   Covered by `test_clear_lane_suppression_does_not_affect_other_lanes`.
3. **Lane-short normalization.** Both `scan_lane` and `clear_lane` `.upper()`
   the input (lines 54, 81), so callers passing `"c"` vs `"C"` cannot create
   parallel suppression entries that drift out of sync.
4. **No new mutable global state.** All suppression state lives on the
   instance, so the server can construct a fresh watcher per mission without
   leakage. Good for test isolation.
5. **Window expiry is the test, not the implementation.** Suppression simply
   compares against `monotonic()`; there is no cleanup task, no event loop
   timer, no dict-pruning thread. Less surface area to misbehave.

## Tail-risks (LOW — not blocking, worth surfacing)

### TR-1 — Detection regex is a silent-failure single point

`_PERM_RE` (lines 20-25) hard-codes three exact strings:
- `Tool use requires permission`
- `Do you want to allow`
- `[1] Allow`

If a future Claude Code release renames the banner (e.g. "Approve this tool
call?", "1) Allow", "Press 1 to authorize"), `scan_lane` will return `None`
forever with **zero observability**. There is no counter for "scans run", no log
line on "scanned N bytes, matched 0", no metric exported. The first symptom an
operator will see is "permission prompts stopped appearing in the dashboard,"
which they will plausibly attribute to "no prompts firing" rather than "watcher
is blind."

**Mitigation options** (cheap, defer to BE):
- Log at DEBUG level once per minute when N consecutive scans of a non-empty
  stream produced zero matches across all lanes (`scan_all` aggregate).
- Expose `last_match_utc` per lane on `/api/v1/state` so the dashboard can flag
  "watcher healthy / last detected XXm ago."
- Pin the Claude Code version in `pyproject.toml` and add a CI smoke test that
  pipes a captured banner snippet through `_PERM_RE`.

### TR-2 — ANSI stripper covers CSI but not OSC escapes

`_ANSI_ESC_RE` (line 17) is:

```
\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])
```

This handles:
- C1 single-char escapes (`\x1b@`..`\x1b_`)
- CSI sequences (`\x1b[...<final>`)

It does NOT handle:
- **OSC sequences**: `\x1b]<text>\x07` or `\x1b]<text>\x1b\\` — used for
  terminal hyperlinks (OSC 8). If Claude Code wraps `[1] Allow` in a hyperlink
  in a future version, the embedded `]8;;...\x07` payload survives the strip
  pass, and `_PERM_RE` matches on the wrapped form anyway (the bracket marker
  is still present). So **not exploitable today**, but the stripper is
  technically incomplete.
- **DCS / SOS / PM / APC** sequences (`\x1bP`, `\x1bX`, `\x1b^`, `\x1b_`) —
  rarely used by TUIs, no current risk.

**Mitigation**: if any future banner format places the visible match text
*inside* an OSC payload (low probability — OSCs typically wrap visible text,
not contain it), detection fails. Extend the stripper to also drop
`\x1b\][^\x07]*(\x07|\x1b\\)` if/when that happens. Filing now so future-Claude
has the context.

### TR-3 (informational, not a bug) — `scan_lane` ⇄ `clear_lane` happens-before

`scan_lane` (lines 48-69) checks suppression *then* opens + reads the file.
`clear_lane` flips suppression atomically. The server polls every ~1s in a
single asyncio coroutine, so there is no actual concurrency here — but if
future code introduces a worker thread for streaming I/O, an in-flight
`scan_lane` could legitimately return a stale detection just after a
`clear_lane`. Today: not a bug. Documenting the invariant so it gets preserved.

## Test-suite review

13 tests, well-named, single-assertion-per-concept. Specifically:

| Test | Covers |
|---|---|
| `test_clear_lane_suppresses_redetection_during_window` | Core regression — exactly the flicker bug |
| `test_clear_lane_suppression_does_not_affect_other_lanes` | Isolation invariant |
| `test_suppression_window_expires_and_detection_resumes` | TTL expiry (uses `SUPPRESSION_WINDOW_SECONDS = 0.1` instance override) |
| `test_scan_lane_strips_ansi_before_matching` | Color escape (`\x1b[31m`) is stripped |

Gaps I would close in a follow-up (none blocking):
- No test for OSC ANSI escape inside the matched text. Low priority.
- No test asserting `scan_all` skips suppressed lanes (currently inferred from
  `scan_all` delegating to `scan_lane`, which is fine).
- No fuzz test on `_PERM_RE` against captured real-world Claude TUI samples.
  Would catch TR-1 drift earlier; out of scope for this fix.

## Verdict

**LGTM ship-as-is for the bug it targets.** The flicker fix is small, clock-correct,
test-covered, and lane-isolated. Tail-risk TR-1 (silent regex failure) is the
biggest latent concern across the whole permission subsystem — recommend a
follow-up ticket to add a lightweight "watcher health" signal, but not a blocker
for the BUG-PROMPT-FLICKER ship.

Per d510's finding, server restart is still required for the import to pick up.
That is a separate issue tracked elsewhere (`S-NEXT-TICK-VISIBILITY` had the
same restart problem; cross-ref my finding
`agent-0fa4-A-P1-next-tick-feature-not-live-server-not-restarted-2026-05-19T23-28-08Z.md`).

## Next-tick

Wake in 300s at 2026-05-20T00-01Z; same /loop prompt re-reads launch-AUDIT.md.
Will re-check phase + feedback/AUDIT.md on next iteration.
