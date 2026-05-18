# Megalodon v9.2 — Handoff Brief for Next Brainstorming Pass

**Status:** Active brief. Hand this to the next brainstorming session.
**Date created:** 2026-05-17
**Predecessor (superseded):** `docs/superpowers/specs/2026-05-17-megalodon-v9-2-tmux-design.md`
**Contrarian review of predecessor:** `verifications/2026-05-17-contrarian-v9-2-tmux.md` (verdict: `spec-should-be-redone`)
**Original roadmap sketch:** `docs/v9/v9-2-ROADMAP.md`

## 1. Why this brief exists

A full v9.2 design was attempted on 2026-05-17. It passed in-session self-review with patches, then failed external review (GPT-5.5 xhigh, codex) with 10 OW findings invalidating core architectural assumptions. The verdict was `spec-should-be-redone` — not `needs-revision`.

Rather than patch a third draft, this brief hands off the **lessons learned** plus **what's still decided** so the next brainstorming pass can start from a known starting point instead of re-deriving everything.

**Do not treat this brief as a spec.** It is a starting point for brainstorming, not an implementation contract.

## 2. The goal of v9.2 (unchanged)

Decouple lane spawn from operator-facing view so megalodon runs headless on Linux/SSH/containers and surfaces all lane terminals in one browser tab.

- Spawn layer: tmux session per lane (cross-platform, detached, lifecycle-managed by tmux).
- View layer: web UI (one tab, one xterm.js pane per lane).
- Two data taps per lane: structured (pipe-pane → applier) and visual (capture → browser).

The roadmap in `docs/v9/v9-2-ROADMAP.md` still describes the right *intent*. It is the *mechanism* that needs redesign.

## 3. Decisions carried over from 2026-05-17 brainstorming

These were settled deliberately with the user; do NOT re-relitigate without new evidence.

| Decision | Why it stays |
|---|---|
| **Scope: v9.2 = spawn swap + browser grid + stdin + auth, single release** | Operator never sees a regression in ergonomics during transition. |
| **Topology: 127.0.0.1 only, browser on same host** | Auth simplifies dramatically; remote = `ssh -L` is operator's responsibility. |
| **tmux is a hard prerequisite** | No fallback (no AppleScript, no nohup). |
| **Server (Python) owns tmux lifecycle, not bash** | One process to start, one to stop. `scripts/launch_fleet.sh` shrinks to pre-flight checks then `exec`s the server. |
| **Bottom-up phasing (spawn → tap → visual → UI → stdin → polish)** | PM-7 sequential refactor discipline; each phase verifiable independently. |

## 4. Predecessor work confirmed shipped from v9.1

The brainstormer can assume these as fixed contracts:

- `HarnessAdapter` Protocol with `build_argv(...) -> (argv, env_overlay)` (see `megalodon_ui/harnesses/base.py:69-134`).
- `parse_stream_line(line) -> Event | None` per adapter.
- `session_log_path`, `auth_env_keys`, `supports()` Capabilities.
- Concrete adapters: claude, codex, gemini, copilot, cursor, vibe.
- Existing entrypoint accepts `--mission-dir`, `--port`, `--host` only (see `megalodon_ui/__main__.py:22-33`). Any new flags must be additive.

**Confirm v9.1 status before starting:** is it merged? The brief assumes "almost done." If v9.1 reshapes any of the above, the brief's assumptions need updating.

## 5. Architectural lessons from the failed attempt

Read all of these before designing. Each is a concrete trap.

### 5.1 — `capture-pane` is not a PTY byte stream

The failed spec sent `capture-pane -e -p` snapshots straight to xterm.js `term.write()`. xterm.js consumes **PTY-style bytes** (cursor moves, escape sequences, incremental output) — not full-screen frame dumps. Sending repeated snapshots makes the browser append new screen-fulls each tick instead of repainting.

**Implication for the next brainstorm:** the visual stream needs a different mechanism. Candidates worth evaluating:

- `tmux pipe-pane -O 'cat >> file.raw'` writing a *raw byte stream* (which is PTY-shaped), and the SSE endpoint tails that file. Same `pipe-pane` primitive as the structured tap, different sink path.
- A dedicated PTY (via `ptyprocess` or `pexpect`) the server spawns directly, with tmux removed entirely from the visual path. Trade-off: lose tmux's detach/reattach properties.
- `tmux capture-pane -J -p` (the "join wrapped lines" variant) is *still* snapshot-shaped — not a fix.
- WebSocket bidirectional with the raw PTY bytes — but the failed spec explicitly rejected WS; reconsider only with new evidence.

**Verify before deciding:** what exactly `tmux pipe-pane` writes (a raw byte copy of pane output including escapes), and whether xterm.js's `Terminal.write()` can rebuild a coherent display from it. The tmux man page + xterm.js docs are the authorities, not training data.

### 5.2 — Adapters are one-shot, not interactive REPLs

`claude --print`, `codex exec`, `gemini -p` all **exit after producing output**. They are not REPLs. `send-keys` to a dead pane is a no-op.

**Implication:** the stdin proxy as designed cannot meaningfully drive the configured harnesses. Two paths:

- Drop stdin proxy from v9.2 scope. The browser is read-only. Operators use `tmux attach -t lane-X` for interactive intervention.
- Redefine "stdin proxy" to mean "send a follow-up prompt that re-invokes the adapter" — i.e., a chat-style input that *spawns a new invocation*, not a keystroke pipe. This requires the adapter contract to grow a `build_followup_argv` or similar.

Either is defensible. The failed spec chose neither — it just shipped a feature that wouldn't work.

### 5.3 — Global tmux socket = cross-mission collisions

The default tmux server socket is global per-user. `lane-AUDIT` from mission A and `lane-AUDIT` from mission B share a namespace. The failed spec said "one mission per server" without specifying *socket isolation*, so two server processes would silently clobber each other's panes.

**Fix the next brainstorm must specify:** use `tmux -L <socket-name>` or `tmux -S <socket-path>` with a per-mission socket. Every tmux call carries the socket arg. Mission-A's `tmux` invocations cannot see mission-B's sessions. Also fixes the "list-sessions with prefix='lane-' kills other users' sessions" hazard.

### 5.4 — `.fleet/` is not in `.gitignore`

Confirmed against the repo: `.gitignore:55-80` covers `queue/`, `.fleet-ledger/`, and fixture re-includes. **`.fleet/` itself is not ignored.** The token file and stream logs would be commit candidates without an explicit `.gitignore` change. Violates the no-secrets hard rule.

**Action item for the spec:** any v9.2 design must include `.gitignore` updates as a P0 deliverable, not an afterthought.

### 5.5 — Synchronous `subprocess.run` in the FastAPI event loop

The failed spec specified `tmux.py` as a sync wrapper using `subprocess.run`. Then `capture-pane` runs every 500 ms × N lanes inside the asyncio loop that also serves SSE and POSTs. Spawning subprocesses synchronously blocks the loop.

**Fix:** all tmux calls go through `asyncio.create_subprocess_exec` (or `asyncio.to_thread` wrapping `subprocess.run` if the wrapper stays sync). Verify behavior against FastAPI/uvicorn lifespan and asyncio docs — don't assume.

### 5.6 — Auth + shutdown rewrites must propagate everywhere

In the failed spec, the auth model changed in §4 but P4/P5/P6/tests still referenced the old `?t=` query param and `X-Megalodon-Token` header. Shutdown changed to non-destructive in §6 but P6 and test specs still asserted destructive behavior.

**Process lesson:** when a cross-cutting decision changes, the next agent must walk every section + every test name in the prior spec and verify references. The contrarian found this in the second pass; the self-pass didn't.

### 5.7 — Log rotation cannot be a boot-time rename

The applier maintains a saved byte offset per stream-log file. Renaming the file at boot invalidates the offset (either points past EOF or replays old data). The failed spec removed the rotation feature outright — acceptable, but if rotation re-enters v9.2 scope it must be atomic with applier-state reset.

### 5.8 — `tmux pipe-pane` flush semantics are not line-buffered

Spec claimed `pipe-pane` flushes line-buffered. The tmux man page doesn't promise that. High-output lanes (stream-json with thousands of lines/sec on tool-use chains) may buffer in unpredictable ways. The next brainstorm should either benchmark or document the actual buffering behavior and design around it.

### 5.9 — Numeric constants need justification

The failed spec asserted 500 ms cadence, 200×50 pane, queue depth 4, 50 ms debounce, 500 MB watchdog, 86400 cookie max-age, 32-byte token. Without measurement or threat-model basis, these are agent fingerprints. Either drop them, derive them, or label them "initial values pending tuning."

### 5.10 — Disk-full handling needs a real error channel

The failed spec promised "fatal clean shutdown on disk full." But the writer is `tmux pipe-pane | cat >> file` — the Python server gets no `errno` from a shell-side write failure. Either:

- Use `pipe-pane` with no shell wrapper (`tmux pipe-pane -t … -- /path/to/file` form, if it exists — verify); the server polls file stat for growth.
- Server tails its own copy of the log via Python file I/O; disk full is caught locally.
- Accept that disk-full is OS-level death; remove the promise.

## 6. Hard requirements the next spec must satisfy

| Requirement | Source |
|---|---|
| No secrets in URL bar, history, bookmarks, referer | OW-1 from self-pass; CLAUDE.md no-secrets hard rule |
| `.fleet/` added to `.gitignore` as P0 | §5.4 above |
| Per-mission tmux socket — never the default | §5.3 above |
| Async-safe subprocess calls inside the event loop | §5.5 above |
| Single, internally consistent auth model — every endpoint, every test | §5.6 above |
| Single, internally consistent shutdown semantics — code + tests aligned | §5.6 above |
| Visual stream protocol that xterm.js can actually consume | §5.1 above |
| Stdin / interactive model that matches what one-shot adapters can do | §5.2 above |
| No new flags on the entrypoint that aren't documented in `__main__.py:22-33` | §4 above |
| Rotating file logging via `scripts/_logging.py` pattern, not handwave "log warning" | Contrarian WR-8 |

## 7. Open architectural questions the next brainstorm must answer

The starting questions in priority order. Do not begin §1 of a new spec until at least Q1–Q4 are settled.

1. **Visual stream protocol.** Raw `pipe-pane` byte log → xterm.js, or PTY-direct, or something else? What does the byte format actually look like? Does xterm.js handle resize escapes from a detached tmux pane sensibly?
2. **Stdin model.** Drop (read-only browser) vs. redefine as "send follow-up prompt that spawns a new adapter invocation" vs. defer to v9.3?
3. **Per-mission tmux socket name.** `mission-<hash>` derived from path? `mission-<port>`? Stored where (so the operator can `tmux -L <name> attach` manually)?
4. **Auth model — one design, top to bottom.** Cookie via hash-fragment exchange is plausible but must be specified once and propagated through every endpoint, every flow diagram, every test. Or pick a simpler model (e.g., `Authorization: Bearer` with bootstrap loading token from sessionStorage set by a one-time POST) and stick to it.
5. **Shutdown semantics — pick one default.** Non-destructive (sessions persist across server restart) OR destructive (clean teardown). Don't have both as defaults conditional on flags; one default + explicit opt-in to the other. Then audit every test and code path.
6. **Logging architecture.** Reuse `scripts/_logging.py` (RotatingFileHandler to `/tmp/megalodon.log`, per CLAUDE.md), or stand up a new sibling for `megalodon_ui/`?
7. **Lane count assumption.** Always-6 (v9.0/v9.1 default) hardcoded vs. driven by `config.lanes[*]`. If the v9.1 mission_config supports variable N, the v9.2 spec must use N consistently.
8. **Numeric constants.** Defaults + tuning rationale or benchmark, for: capture cadence (or whatever replaces it), pane dimensions (or dynamic resize), per-subscriber buffer depth, debounce window, log size thresholds, cookie/token TTLs.

## 8. External assumptions the next spec must verify

Do not trust training data on any of these. Confirm against current authoritative docs.

- `tmux pipe-pane` exact behavior: `-O` flag semantics, with-shell vs. without-shell forms, buffering, error reporting.
- `tmux capture-pane` output format with `-e`, `-p`, `-J` — what is actually emitted, how it relates to PTY bytes.
- `tmux new-session -d -x N -y M` initial size behavior on subsequent `capture-pane`.
- `tmux remain-on-exit on` interaction with `kill-session` (does session linger after kill?).
- FastAPI lifespan ordering relative to uvicorn's socket bind and the first request.
- `EventSource` cookie behavior on initial connect AND reconnect (`withCredentials`).
- `asyncio.Queue.put_nowait` + `get_nowait` race semantics under contention.
- `os.open(O_CREAT|O_EXCL, mode)` umask interaction on macOS vs. Linux.
- GitHub Ubuntu 24.04 runner image — does it ship tmux? (Per the contrarian: no.)

## 9. Sequencing relative to v9.1

User states v9.1 is "almost done" as of 2026-05-17. Concrete next-session checklist:

1. Confirm v9.1 is merged. If not, the assumptions in §4 may need updating from the actual delivered contract.
2. Check `megalodon_ui/__main__.py` for any new flags v9.1 added (the brief was written against `--mission-dir / --port / --host`).
3. Check `megalodon_ui/server.py` for the actual FastAPI app shape v9.1 leaves behind.
4. Re-read this brief.
5. Begin brainstorming with §7 questions, one at a time per the brainstorming skill.
6. Produce a new design doc, run the self-contrarian pass + external contrarian dispatch (per `~/.agent/prompts/contrarian.md`).

## 10. What NOT to inherit from the prior spec

The superseded spec at `docs/superpowers/specs/2026-05-17-megalodon-v9-2-tmux-design.md` contains the architectural mistakes catalogued above. Read it for context if useful, but do not copy:

- Its visual-stream design (capture-pane → xterm.js).
- Its stdin proxy design (send-keys to one-shot adapter pane).
- Its auth design (mixed cookie + header + query token across endpoints).
- Its shutdown semantics (conflicting defaults).
- Its test fixture stub (`StubAdapter` had import errors, model name mismatches).
- Its default-socket tmux usage.
- Its disk-full handling claim.
- Its `--mission` and `--shutdown` flags (the entrypoint doesn't have them).

Sections that *are* still useful as reference (not as final design):

- §1 Problem statement (the goal is unchanged).
- §3 Component-impact table for `scripts/launch_fleet.sh` (the deletions to plan).
- §6 Server-startup port-bind ordering (correct sequence, even if other parts of the spec failed).
- §11 Predecessor-work table (v9.1 contracts list).
- §12 Terminology notes (avoid re-deriving terms).

## 11. References

- Superseded spec: `docs/superpowers/specs/2026-05-17-megalodon-v9-2-tmux-design.md`
- External contrarian review: `verifications/2026-05-17-contrarian-v9-2-tmux.md`
- Self-pass summary (transient): `/tmp/contrarian-2026-05-17/self-pass-summary.md`
- Original roadmap sketch: `docs/v9/v9-2-ROADMAP.md`
- Existing entrypoint: `megalodon_ui/__main__.py:1-50`
- Existing logging helper to reuse: `scripts/_logging.py:1-24`
- Existing harness Protocol: `megalodon_ui/harnesses/base.py:69-134`
- Existing launch driver to replace: `scripts/launch_fleet.sh`
- Existing `.gitignore` (needs `.fleet/` added): `.gitignore:55-80`

## 12. Honest assessment from the prior session

The 2026-05-17 attempt produced a spec that read well section-by-section and passed in-session self-review with patches, then failed external review with structural problems the in-session review couldn't see. Two patterns to watch:

- **Cross-section consistency does not survive incremental edits.** When OW-1 (auth) and OW-4 (shutdown) were fixed in §4 and §6, the references in P4/P5/P6 and the test names were not updated. The next brainstorm should treat any cross-cutting change as a global rename + audit, not a section-local fix.
- **External assumptions weren't verified.** The visual-stream design assumed `capture-pane` produces something xterm.js consumes. It doesn't. A 5-minute check of the xterm.js docs would have caught it. Verify every external dependency claim before writing it into the design.

Hand this brief to the next agent with the expectation that they will produce a better spec than the prior attempt — not because they're smarter, but because they have these lessons in front of them.
