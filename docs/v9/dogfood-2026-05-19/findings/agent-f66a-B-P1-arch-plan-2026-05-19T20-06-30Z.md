# P1-B — ARCHITECT Plan (v9.3 spec for live_repl generalization + external loop driver)

- **Lane:** LANE-B (ARCHITECT)
- **Agent:** `agent-f66a`
- **Task:** `P1-B`
- **Phase:** PHASE 1 — PLAN
- **UTC:** 2026-05-19T20-06-30Z

## Summary

v9.2 shipped `live_repl` and `initial_prompt` as a **Claude-only** path:
`ClaudeAdapter.build_argv(..., live_repl=True)` returns an interactive
`claude --model … --allowedTools …` argv with a curated tool allow-list,
and `FleetSpawner._deliver_initial_prompt` does one `tmux send-keys` to
bootstrap `/loop`. Continuation is then internal to Claude Code (the slash
command schedules its own wake-ups).

v9.3 must close two gaps:

- **(a) Generalize `live_repl`** so a lane bound to Codex / Gemini / Copilot
  / Cursor / Vibe can run in REPL mode without TypeError'ing at spawn. The
  contract today is *implicitly* Claude-only: only `ClaudeAdapter.build_argv`
  accepts the `live_repl` kwarg (`megalodon_ui/harnesses/claude.py:56`), yet
  `spawn.py:257` and `:309` splat `live_repl=True` into whichever adapter the
  config names. A non-Claude lane with `live_repl=True` is a **latent
  TypeError** the schema permits.

- **(b) External loop driver** so non-Claude CLIs (which have no in-session
  `/loop`) can still iterate autonomously. Mechanism: server-side cadence
  timer + `tmux send-keys` of the loop prompt at each tick. The existing
  `_deliver_initial_prompt` already proves the primitive works; v9.3 promotes
  it from a one-shot bootstrap to a recurring driver, gated by a new
  capability flag.

This plan does **not** write the v9.3 spec itself — that is `P2-B`. It
identifies the contract surfaces that move, the per-adapter shape, the
preflight guards needed, and the failure modes to test.

## Evidence

### Current `live_repl` surface (v9.2-shipped)

| File:line | Role |
|---|---|
| `megalodon_ui/mission_config/schema.py:38` | `LaneConfig.live_repl: bool = False` — declared on every lane regardless of harness. |
| `megalodon_ui/mission_config/schema.py:39` | `LaneConfig.initial_prompt: str \| None = None` — same. |
| `megalodon_ui/harnesses/claude.py:56-70` | Only adapter whose `build_argv` accepts `live_repl=True`. Returns `["claude", "--model", model, "--allowedTools", <curated>]` — no `--print`, opens TUI. |
| `megalodon_ui/spawn.py:257`, `:309` | `**({"live_repl": True} if lane_cfg.live_repl else {})` — passed to whatever adapter `adapter_resolver(lane_cfg.harness.cli)` returns. |
| `megalodon_ui/spawn.py:320` | `initial_prompt` copied onto `LaneSession` only when `live_repl=True`. |
| `megalodon_ui/spawn.py:588-611` | `_deliver_initial_prompt` — sleeps `_LIVE_REPL_PROMPT_DELAY_SECONDS`, then **one** `tmux send-keys`. Errors logged, not raised. |
| `megalodon_ui/harnesses/base.py` (Protocol) | `build_argv` signature has **no** `live_repl` parameter — Claude's is an undeclared extension. |

### Non-Claude adapters today

All five (Codex, Gemini, Copilot, Cursor, Vibe) define `build_argv` with the
`HarnessAdapter` Protocol signature only (no `live_repl` kwarg). Three of
them (Codex, Gemini, Cursor) have a usable interactive shape — the same
binary without `-p` / `--print` / `exec` enters a TUI — but the adapter
doesn't expose it. Two (Copilot, Vibe) are Batch-2b experimental and
**spawn-untested** under fleet load (`v9-1-HARNESS-ADAPTERS.md` §7).

### /loop's actual coupling

`/loop` is a Claude Code **slash command** — it runs inside the TUI and
re-arms itself via `ScheduleWakeup`. The fleet runtime touches it exactly
once, by sending the bootstrap prompt that contains `/loop` (e.g. the text
in `launch-ARCHITECT.md` referenced by `initial_prompt`). After that, the
TUI owns the loop. For CLIs without an equivalent slash command, the
runtime must supply the iteration trigger itself.

### Test gap

`scripts/tests/test_harness_claude.py` has no test for the `live_repl=True`
branch of `build_argv`; the v9.3 default config
(`mission_config/default_v9_3_live_repl.py:60-95`) is the only thing that
exercises it, and only at config-load time. No regression test guards
against the TypeError described above.

## Recommendations — v9.3 spec contents (to be authored in `P2-B`)

### A. Promote `live_repl` to a first-class capability

1. **Add Protocol method `build_repl_argv`** in
   `megalodon_ui/harnesses/base.py`:

   ```python
   def build_repl_argv(
       self, *, model: str, cwd: pathlib.Path,
       extra_env: dict[str, str] | None = None,
   ) -> tuple[list[str], dict[str, str]]:
       """Argv that launches the harness in interactive REPL mode.

       Adapters without a REPL shape MUST raise NotImplementedError.
       """
   ```

   Rationale: `build_argv` carries a non-interactive prompt argument that
   makes no sense for REPL. Keeping them separate avoids the v9.2 hack of
   ignoring `prompt_or_launch_path` when `live_repl=True`.

2. **Add capability flag** `supports_interactive_repl: bool` to
   `Capabilities`. Claude = `True`; everyone else = `False` until verified
   per-adapter (start `False`, opt in deliberately).

3. **Preflight validator** in `LaneConfig`: cross-field check rejecting
   `live_repl=True` when the resolved adapter has
   `supports_interactive_repl=False`. Error names the lane, the adapter,
   and links to this doc. Fixes the latent TypeError surfaced above.

4. **Tool-gating contract.** Claude's `--allowedTools` is CLI-specific.
   The spec defines an **abstract** allow-list (mkdir-claims, rm-claims,
   read, write, edit, grep, glob, wakeup, task-mgmt) and each adapter
   implementing `build_repl_argv` translates it to its own flags:
   - Codex: `--allow-tool` enumeration (verify CLI syntax against
     v0.130.0 docs before P2-B).
   - Gemini: `--approval-mode plan` blanket + manual review (no per-tool
     flag exists in v3.1).
   - Cursor / Copilot / Vibe: re-research at P2-B time; flag as TBD.

5. **Drop the kwarg hack** in `spawn.py:257,309`. The new flow:
   `argv, env = adapter.build_repl_argv(model=…, cwd=…)` when
   `lane_cfg.live_repl=True`, else `adapter.build_argv(prompt, …)`.

### B. External loop driver for CLIs without `/loop`

1. **New module** `megalodon_ui/loop_driver.py`. Single class
   `ExternalLoopDriver(socket, lane_session, cadence_seconds, prompt)`.
   Responsibilities:
   - On `start()`, sleep `cadence_seconds`, send the loop prompt via
     `tmux.send_keys(socket, session.name, prompt)`, then loop.
   - On send-keys rc!=0, log + back off (do not crash the spawner — same
     posture as `_deliver_initial_prompt`).
   - Write `<mission>/.fleet/<short>.next_tick.txt` with the next-tick
     UTC each iteration. **Reuses** the file produced by the
     `S-NEXT-TICK-VISIBILITY` secondary task — single source of truth.
   - Cancellable via `asyncio.Task.cancel()` on lane teardown.

2. **Capability flag** `supports_external_loop_driver: bool`. Default
   `False`. Adapters opt in once their REPL accepts send-keys at idle
   without ANSI-state corruption (Codex / Gemini verified manually as
   part of P2-B research). Claude **does not** need the external driver
   — `/loop` is the in-session equivalent.

3. **Mission config addition**:
   ```yaml
   lanes:
     - name: PLANNER
       harness: { cli: gemini, model: gemini-3.1-pro-preview }
       live_repl: true
       initial_prompt: "<bootstrap prompt that does the first iteration>"
       loop_driver:
         mode: external   # 'internal' | 'external' | 'none'
         cadence_seconds: 300
         prompt: "Do one iteration. Read launch-PLANNER.md."
   ```
   `mode: internal` reserved for Claude (uses /loop). `mode: none` is
   today's behavior (manual tick). Adapter capability gates which modes
   the schema accepts per lane.

4. **Session-continuity caveat (document, do not solve in v9.3).**
   Claude's `/loop` preserves cache because it stays in the same
   process. External driver tick = "type into the REPL" — each tick
   relies on the adapter's session-resume semantics:
   - Codex: has `exec resume`, but the REPL is a different mode (no
     resume across REPL ticks; cache survives by virtue of staying in
     the same process).
   - Gemini / Copilot / Cursor / Vibe: no resume. Each tick is treated
     by the CLI as continuing the same REPL session — context window is
     the limiting factor.

   Spec calls this out as a known limitation for v9.3, and lists token
   accounting as a v9.4 follow-up tied to `S-LIVE-ACTIVITY`.

### C. Test plan for `P2-B` deliverables

| Test | Where | Type |
|---|---|---|
| `test_build_repl_argv_claude` — verify Claude shape unchanged | `scripts/tests/test_harness_claude.py` | unit |
| `test_build_repl_argv_not_supported` — non-Claude adapters raise `NotImplementedError` | per-adapter unit test | unit |
| `test_live_repl_rejected_for_incapable_adapter` — preflight rejects `live_repl=True` + Gemini lane | `scripts/tests/test_mission_config.py` | unit |
| `test_external_loop_driver_sends_at_cadence` — fake socket, freezegun, assert N send-keys in N×cadence | `scripts/tests/test_loop_driver.py` (new) | unit |
| `test_external_loop_driver_writes_next_tick_file` — verify `.fleet/<short>.next_tick.txt` updated each tick | same | unit |
| `test_spawn_external_loop_lane` — end-to-end with `tmux_real` fixture, Codex REPL stub, two ticks | `scripts/tests/test_spawn_loop_driver.py` (new) | integration, `@pytest.mark.isolated` |

All must run under the v9.3 test command in MISSION.md §exit criteria.

## Coordination with other lanes

- **LANE-A (AUDIT)** flagged in `agent-0fa4-A-P1-audit-plan-…md` that
  `subscribers_lock` is dead until CV-9 lands. The external loop driver
  *does not* touch that lock; my recommendation is the loop driver writes
  `.next_tick.txt` and lets LANE-C's stream-reader pick it up as a
  separate concern.
- **LANE-C (BACKEND)** is doing `P1-C` (CV-9 stream-reader). The external
  loop driver writes the same `.fleet/<short>.next_tick.txt` that
  secondary task `S-NEXT-TICK-VISIBILITY` expects — we should pick **one**
  writer. Recommend: the loop driver is the writer; the secondary task's
  BE endpoint is the *reader*. ARCHITECT to confirm in P2-B; BACKEND to
  ratify in `P3-B-to-C`.
- **LANE-D (FRONTEND)** — the `loop_driver.mode` field and per-lane
  `next_tick_utc` belong on the lane card per `S-LANE-CARD-DETAILS`.
  Coordinate on the `S-HYBRID-DASHBOARD` joint task in PHASE 2.
- **LANE-E (TEST)** — the new integration test for `live_repl` (task
  `P2-E`) should target the **Claude** adapter; the external-driver test
  in §C above targets non-Claude. Two separate harnesses, two tests, both
  required.

## Next steps (for me)

- After PHASE-FLIP to BUILD, claim `P2-B` and write
  `docs/v9/v9-3-DESIGN.md` covering §A + §B above, plus the Protocol diff
  against `harnesses/base.py` and the schema diff against
  `mission_config/schema.py`.
- If `S-HYBRID-DASHBOARD` becomes the operator's next priority (it's
  joint LANE-B+D), draft the design half before BUILD opens so FRONTEND
  has a spec to implement against.
- Until PHASE-FLIP: idle-tick. No other PHASE-PLAN tasks are open for
  LANE-B.
