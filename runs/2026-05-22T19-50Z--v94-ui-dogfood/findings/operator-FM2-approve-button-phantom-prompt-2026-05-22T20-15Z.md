# FINDING — "Approve" button broken: phantom prompts from append-only stream log

- **Lane:** operator (reported live during dogfood)
- **Severity:** high (failure-mode #2 regression — operator cannot clear prompts; whole fleet stalled on approvals)
- **Surface:** approval / `megalodon_ui/permission_watcher.py` + `permission_prompts/{lane}/respond`
- **Status:** fixed in this run (capture-pane confirmation gate)

## Symptom

Clicking "approve" on the dashboard did nothing useful. Server accepted each
click (`POST /api/v1/permission_prompts/A/respond → 202`), but the agent pane
showed five stray `❯ 1` lines at the REPL's MAIN input — the agent even
narrated: *"I see the user sent '1' a few times … carries no new instruction."*
Meanwhile real prompts stayed unanswered. All 6 lanes ended up blocked.

## Root cause (regression chain)

`PermissionWatcher._scan_once` tails the **append-only** pipe-pane stream log
(`{short}.stream.log`), ANSI-strips it, and surfaces the LAST
`"Do you want to proceed?"` match. But an append-only log never forgets: when a
prompt is answered, the Claude REPL erases it from the **screen** via CSI
sequences — the bytes remain in the log. So a resolved marker keeps matching.

The v9.3.5 `TAIL_BYTES` bump (4096 → 32768, to stop prompts scrolling out of
the tail — failure-mode #1) made a resolved marker linger ~8 min instead of
~30s. The 5 s `CLEAR_SUPPRESSION_SECONDS` re-flash window (tuned for the 4 KB
era) expires long before the stale marker leaves the 32 KB tail, so the
dashboard re-surfaces a **phantom** prompt. The operator approves it; `1`+Enter
lands at the (now-idle) REPL main input as stray chat input.

**The fix for failure-mode #1 caused the failure-mode-#2 regression.**

## Fix

Make `tmux capture-pane -p -t lane-<short>` the **authoritative detection
source**, falling back to the stream-log tail only when no live socket exists
(unit tests / no fleet). The live screen is correct in both directions a
blocking prompt is always on it; an answered one is always gone — so it fixes
**both** the phantom (FM-2) and the scroll-out blindness (FM-1) at once.

A first attempt used capture-pane only as a *confirmation gate* on top of
stream-log detection. That fixed phantoms but NOT scroll-out: when blocked
agents repainted a thinking spinner for ~19 min, the marker scrolled out of the
32 KB stream-log tail, detection found nothing, and the gate never ran — the
dashboard showed zero prompts while all 6 lanes were genuinely blocked
(reproduced live this run). Hence capture-pane had to be primary, not a gate.

Injectable `capture_fn` for tests. Regression tests: stale-log-marker-not-
on-pane → suppressed; live-pane marker with scrolled-out log → surfaced;
no-socket → stream-log fallback. All 24 watcher tests green.

## Follow-up (v10 / backlog)

- Consider making `capture-pane` the *sole* detection source (drop the
  stream-log + `_suppressed` machinery) once a live-tmux test fixture exists.
- `CLEAR_SUPPRESSION_SECONDS` is now largely redundant; revisit in v10.
- The respond endpoint should verify a prompt is live *before* send-keys
  (defence in depth) rather than relying solely on the watcher's view.
