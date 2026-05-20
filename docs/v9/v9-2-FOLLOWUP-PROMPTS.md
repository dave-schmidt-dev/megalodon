# v9.2 — Follow-up Prompts + Adapter Contract

**Status:** SHIPPED 2026-05-18.
**Companion:** `v9-2-TMUX-FLEET.md` (architecture), `v9-1-HARNESS-ADAPTERS.md` (v9.1 baseline contract).

A "follow-up" sends a new prompt to a running lane WITHOUT teardown + manual re-launch. The user types into the dashboard textbox, hits Send, and the lane's tmux pane is respawned in place under a new argv.

The mechanism: the orchestrator calls `tmux respawn-pane -k` on the target pane with a freshly-built argv. The old child dies; the new child takes its place under the same pane id and pipe-pane association — except `respawn-pane -k` actually drops the pipe-pane (PM-3, fixed) so we have to re-pipe explicitly after each respawn.

This document is the per-adapter contract + the lifecycle rules.

## 1 — The Protocol

```python
class HarnessAdapter(Protocol):
    name: ClassVar[str]
    default_model: ClassVar[str]
    supports_autonomous_loop: ClassVar[bool]

    def build_argv(self, prompt, *, model, cwd, output_format="text", extra_env=None):
        """First launch — no prior session id available."""

    def build_followup_argv(self, prompt, *, prior_session_id, model, cwd,
                            output_format="text", extra_env=None):
        """Subsequent prompts — `prior_session_id` may be None or a str."""

    def session_log_dir(self, cwd):
        """Optional — where this adapter writes its session JSONL, if any."""
```

`build_followup_argv` is new in v9.2. Adapters that don't have a CLI-level "resume" concept fall back to `build_argv` (the default mixin handles this).

### 1.1 `_FollowupArgvDefault` mixin

```python
class _FollowupArgvDefault:
    def build_followup_argv(self, prompt, *, prior_session_id, model, cwd,
                            output_format="text", extra_env=None):
        del prior_session_id   # not used in the default fallback
        return self.build_argv(prompt, model=model, cwd=cwd,
                               output_format=output_format, extra_env=extra_env)
```

Adapters opt in by inheriting from `_FollowupArgvDefault`. Currently used by `GeminiAdapter`, `CopilotAdapter`, `CursorAdapter`, `VibeAdapter`. Claude and Codex provide explicit overrides (see §2).

This is a mixin, not a base class, because the adapter set already uses duck-typing — no `HarnessAdapter` inheritance hierarchy. The mixin lets us share the fallback default without forcing every adapter into an inheritance chain.

## 2 — Per-adapter behavior

### 2.1 `ClaudeAdapter`

```python
# build_followup_argv with prior_session_id="abc-123":
["claude", "--print", "--model", "claude-sonnet-4-6",
 "--resume", "abc-123", "follow up prompt"]

# build_followup_argv with prior_session_id=None or "":
["claude", "--print", "--model", "claude-sonnet-4-6", "follow up prompt"]
```

The `--resume <sid>` flag tells Claude Code to continue an existing session by session id. Without it, Claude starts a fresh session. Empty-string `prior_session_id` is treated as `None` — UI edge case: a textbox bound to `session.session_id` will hand `""` when the operator hasn't seeded a session yet.

### 2.2 `CodexAdapter`

```python
# build_followup_argv with prior_session_id="abc-123":
["codex", "exec", "resume", "abc-123", "follow up prompt"]

# build_followup_argv with prior_session_id=None or "":
["codex", "exec", "-m", "gpt-5.5", "-s", "read-only", "--skip-git-repo-check",
 "follow up prompt"]
```

Codex has a different subcommand shape for resume — `codex exec resume <sid> <prompt>` — not a flag. The adapter switches argv shape entirely based on whether a session id is available.

### 2.3 Default fallback adapters

`GeminiAdapter`, `CopilotAdapter`, `CursorAdapter`, `VibeAdapter` all inherit from `_FollowupArgvDefault`. A follow-up reuses `build_argv`, discarding `prior_session_id`. These CLIs do not (yet) have a resume concept; the new pane starts a fresh session.

## 3 — Respawn lifecycle

`FleetSpawner.respawn(lane, argv, env)` executes in strict order:

```
1. tmux respawn-pane -k <argv>   ── replace running child
2. tmux pipe-pane <stream_log>   ── re-attach byte stream
3. tmux display-message -p '#{pane_pipe}'   ── verify pipe took
4. Under subscribers_lock:
     for q in subscribers:
        while not q.empty(): q.get_nowait()
        q.put_nowait(_RESPAWN_SENTINEL)
5. session.argv = argv; session.env = env
```

### 3.1 Why re-pipe (PM-3)

`tmux respawn-pane -k` creates a new `pane_id`, dropping the prior `pipe-pane` association. Without step 2, the stream log silently stops growing — the dashboard sees no new bytes despite a running new child. Step 3 verifies the pipe actually took; fail loud rather than silently dead.

### 3.2 Why drain-then-push (CV-12 + PM-7)

The sentinel byte chunk

```python
_RESPAWN_SENTINEL: bytes = b"\x1bc\xe2\x9f\xb3 restarting\xe2\x80\xa6\r\n"
#                          ^^^^  ESC c (terminal-clear)
#                              ^^^^^^^^^^^^^^^^^^^^^^^^^^^ UTF-8: ⟳ restarting…\r\n
```

is what the operator sees the instant Send fires: the terminal clears and the line `⟳ restarting…` appears. Then the next harness output overwrites it.

Subscriber queues are bounded (`maxsize=8`) with `drop-oldest` overflow semantics. If a subscriber is slow (a backgrounded tab, a paused devtools breakpoint), a producer racing into the queue between our `put_nowait` and the consumer's `get` could evict the sentinel before any subscriber sees it. The drain-then-push pattern inside one `subscribers_lock` critical section makes the queue's contents transition atomically:

```
[stale...]  →  []  →  [sentinel]
```

A subscriber's first `get()` after a respawn is guaranteed to be the sentinel. PM-7 test pins this property (`scripts/tests/test_respawn_sentinel_survives_backpressure.py`).

### 3.3 Why a SINGLE sentinel chunk

We pin both the bytes AND the boundary: one `put_nowait` call → one chunk. xterm.js parses ESC sequences across `term.write()` calls correctly, but on the SSE wire we want the `\x1bc` and the `⟳ restarting…\r\n` to land in the *same* base64 event. The dashboard's Send-button debounce reads exactly two bytes of the first incoming chunk and checks for `\x1bc`. If we ever split the sentinel across two SSE events, that detection breaks.

## 4 — Endpoint contract

```
POST /api/v1/lane/{lane}/followup
Cookie: mui_session=<sid>
Content-Type: application/json

{
  "prompt": "your follow-up prompt",
  "model": "claude-opus-4-7"   // optional; defaults to lane.harness.model
}

→ 202 Accepted
   {"lane": "A", "status": "respawned"}
```

Error paths:

| Status | Cause |
| ------ | ----- |
| 401 | Missing or invalid `mui_session` cookie |
| 404 | Unknown lane (no LaneSession for that short) |
| 404 | Spawner not initialized (test-mode lifespan) |
| 422 | `prompt` missing or whitespace-only |

The endpoint returns 202 IMMEDIATELY — the new session id is discovered asynchronously by `FleetSpawner.respawn` and persisted to `<mission>/.fleet/<short>.session.txt` (CV-5). Polling `GET /api/v1/lane/{lane}/state` will show the running flag back to `true` once the new child is up.

## 5 — Test coverage matrix

| Concern | Test file | Layer |
| ------- | --------- | ----- |
| Adapter argv for Claude resume | `scripts/tests/test_followup_claude.py` | unit |
| Adapter argv for Codex resume | `scripts/tests/test_followup_codex.py` | unit |
| Adapter argv for fallback adapters | `scripts/tests/test_followup_gemini.py` (representative) | unit |
| Endpoint wiring + auth | `ui/tests/integration/test_followup_endpoint.py` | integration |
| `respawn()` calls re-pipe + sentinel | `scripts/tests/test_respawn_unit.py` | unit |
| Sentinel survives backpressure | `scripts/tests/test_respawn_sentinel_survives_backpressure.py` | unit (PM-7) |
| Real-tmux re-pipe verified | `scripts/tests/test_followup_pipe_pane_preserved.py` | integration (`@pytest.mark.isolated`, CI Linux) |
| Browser-level CV-12 e2e | `ui/tests/e2e/followup.spec.ts` | Playwright (`test.fixme` pending fake-spawner mode) |

The Playwright spec is `fixme` because `chromium-v92-dashboard` runs with `MEGALODON_LIFESPAN_TEST_MODE=1`, leaving `app.state.spawner = None`. A fake-spawner test mode (proposed, post-v9.2) or a real-tmux Playwright project on CI Linux is needed to land it. Until then, the contract is fully covered at unit + integration level.
