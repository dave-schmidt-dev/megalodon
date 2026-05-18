# Megalodon v9.1 — Harness Adapter Reference

> Audience: operators binding a lane to a non-Claude harness CLI.  
> Covers auth, invocation shape, capability surface, and known limitations.

---

## 1. Overview

A **harness adapter** is a thin Python class that knows how to launch one specific
AI CLI (Claude Code, Codex, Gemini, Copilot, cursor-agent, Vibe) in a headless,
non-interactive mode and parse its output into a common `Event` stream.

All adapters implement the `HarnessAdapter` Protocol defined in
`megalodon_ui/harnesses/base.py`. Megalodon's fleet runner holds a reference to
the adapter instance for each lane and calls `build_argv` / `parse_stream_line`
at tick time; it never reaches into adapter internals directly.

### Binding a lane to a specific harness

In the mission config (`.mission-config.yaml`), each lane
carries a `HarnessBinding` stanza inside the lane entry:

```yaml
lanes:
  - name: planner
    harness:
      cli: gemini            # adapter name — one of the six below
      model: gemini-3.1-pro-preview  # optional; adapter default used if absent
      extra_args: []
      auth_env: []
```

The fleet builder resolves `harness = "gemini"` to `GeminiAdapter()`, calls
`adapter.build_argv(prompt, model=model, cwd=cwd, ...)`, and spawns the
resulting `argv` as a subprocess. The binding is validated at preflight; an
unknown adapter name aborts startup.

See `docs/v9/v9-1-MISSION-CONFIG.md` for the full `HarnessBinding` field
reference and `docs/v9/v9-1-PREFLIGHT.md` for startup validation details.

---

## 2. Adapter Contract

Every adapter must satisfy the `HarnessAdapter` Protocol. The full definition
(from `megalodon_ui/harnesses/base.py`):

```python
@runtime_checkable
class HarnessAdapter(Protocol):
    """Contract every harness adapter must satisfy."""

    name: str                             # "claude" | "codex" | "gemini" | …
    default_model: str                    # canonical ID used when none specified
    available_models: tuple[ModelSpec, ...]
    supports_autonomous_loop: bool        # CR-4: True only for ClaudeAdapter in v9.1

    def build_argv(
        self,
        prompt_or_launch_path: str,
        *,
        model: str,
        cwd: pathlib.Path,
        session_id: str | None = None,
        output_format: str = "text",
        extra_env: dict[str, str] | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        """Return (argv, env_overlay) for subprocess.Popen."""
        ...

    def parse_stream_line(self, line: str) -> Event | None:
        """Parse one stdout line; return None for blank/garbage lines."""
        ...

    def session_log_path(
        self, cwd: pathlib.Path, session_id: str
    ) -> pathlib.Path | None:
        """Filesystem path where this harness writes session logs, or None."""
        ...

    def auth_env_keys(self) -> list[str]:
        """Names of env vars this adapter reads for authentication."""
        ...

    def supports(self) -> Capabilities:
        """Return static capability flags for this adapter."""
        ...
```

Supporting dataclasses (`Event`, `Capabilities`, `ModelSpec`) are also in
`base.py`. Key fields:

| Dataclass | Notable fields |
|---|---|
| `Event` | `kind` ("text" / "tool_use" / "tool_result" / "system" / "error"), `text`, `raw` |
| `Capabilities` | `supports_autonomous_loop`, `supports_session_resume`, `supports_stream_json`, `supports_tool_use` |
| `ModelSpec` | `id`, `aliases`, `is_default` |

---

## 3. Autonomous-Loop Matrix (CR-4)

**v9.1 ships configurability for six harnesses but only Claude can self-loop.**

Non-Claude lanes are **MANUAL TICK** in v9.1: the operator (or an external
wrapper script) must re-prompt each tick. The autonomous `/loop` mechanism
depends on Claude Code's internal session-resume and JSONL log capabilities;
replicating that for other CLIs is planned for v9.2.

| Adapter | `supports_autonomous_loop` | Loop mode in v9.1 |
|---|:---:|---|
| `ClaudeAdapter` | **True** | Autonomous (`/loop` supported) |
| `CodexAdapter` | False | Manual tick |
| `GeminiAdapter` | False | Manual tick |
| `CopilotAdapter` | False | Manual tick |
| `CursorAdapter` | False | Manual tick |
| `VibeAdapter` | False | Manual tick |

For the planned tmux-based manual-tick UI that will wrap non-Claude lanes, see
`docs/v9/v9-2-ROADMAP.md`.

---

## 4. Per-Adapter Reference

---

### 4.1 ClaudeAdapter (`claude`)

**Invocation**

```
claude --print --model <id> "<prompt>"
claude --print --output-format stream-json --model <id> "<prompt>"
```

The `--output-format stream-json` flag is appended only when `output_format="stream-json"` is passed to `build_argv`; default is plain text (`--print` alone).

**Auth**

| Env var | Notes |
|---|---|
| `ANTHROPIC_API_KEY` | Primary. Already expected in caller's environment; `env_overlay` is always `{}`. |

Interactive alternative: `claude setup-token` (not managed by Megalodon).

**Models**

| Model ID | Aliases | Default |
|---|---|:---:|
| `claude-opus-4-7` | `opus` | Yes |
| `claude-sonnet-4-6` | `sonnet` | |
| `claude-haiku-4-5-20251001` | `haiku` | |
| `claude-opus-4-6` | — | |
| `claude-opus-4-5-20251101` | — | |
| `claude-sonnet-4-5-20250929` | — | |

**Session log**

`~/.claude/projects/<sanitized-cwd>/<session_id>.jsonl`

Sanitisation: strip leading `/`, replace `/` with `-`, collapse leading dashes.
Example: `/Users/dave/projects/foo` → `Users-dave-projects-foo`.

**Stream format**

`parse_stream_line` attempts JSON parse first (detects `{` prefix). Recognised
shapes: `{"type": "text", "text": "…"}` and `{"content": "…"}`. Falls back to a
plain-text `Event` if JSON parse fails or the type is unrecognised. System-level
JSON objects (no text payload) produce `Event(kind="system")`.

**Capabilities**

| Flag | Value |
|---|:---:|
| `supports_autonomous_loop` | True |
| `supports_session_resume` | True |
| `supports_stream_json` | True |
| `supports_tool_use` | True |

---

### 4.2 CodexAdapter (`codex`)

**Invocation**

```
codex exec -m <id> -s read-only --skip-git-repo-check "<prompt>"
```

`-s read-only` runs in sandboxed read-only mode; remove or change to `read-write`
if the lane needs write access (operator responsibility). `--skip-git-repo-check`
suppresses the git-repo prompt in headless contexts.

`output_format="stream-json"` falls back silently to the text shape — Codex
v0.130.0 has no dedicated JSON-stream flag. This is a known v9.1 limitation.

**Auth**

| Env var | Notes |
|---|---|
| `CODEX_API_KEY` | Primary. |

Interactive alternative: `codex login`.

**Models**

| Model ID | Default |
|---|:---:|
| `gpt-5.5` | Yes |
| `gpt-5.4` | |
| `gpt-5.4-mini` | |
| `gpt-5.3-codex` | |
| `gpt-5.3-codex-spark` | Pro subscription only |
| `gpt-5.2` | legacy |

**Session log**

`~/.codex/sessions/<session_id>/` — a directory; Codex writes multiple files
inside it. `session_log_path` returns the directory path.

**Stream format**

`parse_stream_line` attempts JSON parse on lines starting with `{`. Extracts
`text` or `content` fields. Falls back to plain-text `Event` otherwise.

**Capabilities**

| Flag | Value |
|---|:---:|
| `supports_autonomous_loop` | False |
| `supports_session_resume` | True |
| `supports_stream_json` | False |
| `supports_tool_use` | True |

---

### 4.3 GeminiAdapter (`gemini`)

**Invocation**

```
gemini -p "<prompt>" -m <id> --approval-mode plan
gemini -p "<prompt>" -m <id> --approval-mode yolo
```

`--approval-mode plan` is the default (safe, read-oriented). Passing
`output_format="write"` or `output_format="yolo"` to `build_argv` switches to
`--approval-mode yolo` (allows writes and destructive operations). All other
`output_format` values use `plan`.

**Auth**

| Env var | Notes |
|---|---|
| `GEMINI_API_KEY` | Primary. |

Alternative: Google OAuth flow (not managed by Megalodon).

**Models**

| Model ID | Default |
|---|:---:|
| `gemini-3.1-pro-preview` | Yes |
| `gemini-3-flash-preview` | |
| `gemini-3.1-flash-lite-preview` | |
| `gemini-2.5-pro` | |
| `gemini-2.5-flash` | |
| `gemini-2.5-flash-lite` | |
| `gemma-4-31b-it` | |
| `gemma-4-26b-a4b-it` | |

Note: the `preview` suffix is required for the v3.x models; earlier drafts
omitted it (corrected per operator confirmation 2026-05-17).

**Session log**

`~/.gemini/history/<cwd.name>/` — a directory keyed to the project directory
name (not the full path). `session_id` is not used in the path.

**Stream format**

Plain text only. `parse_stream_line` wraps each non-blank line as
`Event(kind="text")`. No JSON parsing.

**Capabilities**

| Flag | Value |
|---|:---:|
| `supports_autonomous_loop` | False |
| `supports_session_resume` | False |
| `supports_stream_json` | False |
| `supports_tool_use` | True |

---

### 4.4 CopilotAdapter (`copilot`)

> **EXPERIMENTAL** — Batch 2b. Best-effort v9.1 support. Smoke tests auto-skip
> if `copilot` CLI is absent. Report spawn issues as v9.2 findings.

**Invocation**

```
copilot -p "<prompt>" --model <id> --allow-all-tools --no-ask-user
```

`--allow-all-tools` grants full tool access; `--no-ask-user` suppresses
interactive confirmation prompts required in headless contexts.

`output_format="stream-json"` falls back silently to the text shape — Copilot
v1.0.48 has no JSON-stream output mode.

**Auth**

| Env var | Notes |
|---|---|
| `COPILOT_GITHUB_TOKEN` | Primary. |

Alternative: `gh auth` interactive flow (not managed by Megalodon).

**Models** (multi-provider via GitHub Copilot subscription)

| Model ID | Default |
|---|:---:|
| `claude-sonnet-4.6` | Yes |
| `claude-opus-4.7` | |
| `gpt-5.2` | |
| `gpt-5.4` | |

**Session log**

`~/.copilot/session-state/<session_id>/` — a directory.

**Stream format**

Plain text only. `parse_stream_line` wraps each non-blank line as
`Event(kind="text")`. No JSON parsing.

**Capabilities**

| Flag | Value |
|---|:---:|
| `supports_autonomous_loop` | False |
| `supports_session_resume` | False |
| `supports_stream_json` | False |
| `supports_tool_use` | True |

---

### 4.5 CursorAdapter (`cursor`)

> **EXPERIMENTAL** — Batch 2b. Best-effort v9.1 support. Smoke tests auto-skip
> if `cursor-agent` CLI is absent. Report spawn issues as v9.2 findings.

**Invocation**

```
cursor-agent -p --model <id> --force --trust "<prompt>"
```

Important: the binary is `cursor-agent`, not `cursor`. `-p` enters headless
prompt mode; `--force` skips confirmation gates; `--trust` marks the working
directory as trusted.

`output_format="stream-json"` falls back silently to the text shape —
`cursor-agent` v2026.05.16 has no dedicated JSON-stream flag.

**Auth**

| Env var | Notes |
|---|---|
| `CURSOR_API_KEY` | Primary. |

Alternative: `cursor-agent login` interactive flow.

**Models** (partial list — adapter ships 10 of 40+)

| Model ID | Default |
|---|:---:|
| `auto` | Yes |
| `composer-2-fast` | |
| `composer-2` | |
| `gpt-5.5-high` | |
| `gpt-5.4-high` | |
| `gpt-5.3-codex-xhigh` | |
| `claude-opus-4-7-thinking-high` | |
| `claude-4.6-opus-high-thinking` | |
| `sonnet-4-thinking` | |
| `kimi-k2.5` | |

**Session log**

`~/.cursor/chats/<session_id>/` — a directory; cursor-agent writes chat files
inside it.

**Stream format**

Plain text only. `parse_stream_line` wraps each non-blank line as
`Event(kind="text")`. No JSON parsing.

**Capabilities**

| Flag | Value |
|---|:---:|
| `supports_autonomous_loop` | False |
| `supports_session_resume` | True |
| `supports_stream_json` | False |
| `supports_tool_use` | True |

---

### 4.6 VibeAdapter (`vibe`)

> **EXPERIMENTAL** — Batch 2b. Best-effort v9.1 support. Smoke tests auto-skip
> if `vibe` CLI is absent. Report spawn issues as v9.2 findings.

**Invocation**

```
vibe --prompt "<prompt>" --agent auto-approve --output json
```

`--agent auto-approve` runs headless without interactive confirmations.
`--output json` requests JSON output (the only adapter in Batch 2b that supports
this natively).

**No `--model` flag.** The `model` argument passed to `build_argv` is silently
ignored. Model selection is done via `~/.vibe/config.toml`:

```toml
active_model = "mistral-medium-3.5"
```

Operators must set the desired model in that file before invoking the lane.

**Auth**

| Env var | Notes |
|---|---|
| `MISTRAL_API_KEY` | Required. No interactive fallback. |

**Models**

| Model ID | Default |
|---|:---:|
| `mistral-medium-3.5` | Yes (config.toml) |
| `mistral-large-2` | |
| `codestral-25.08` | |
| `devstral-2-large` | |
| `devstral-2-small` | |

**Session log**

`~/.vibe/sessions/<session_id>/` — a directory.

**Stream format**

`parse_stream_line` attempts JSON parse first (lines starting with `{`). Extracts
`text` field; falls back to `str(parsed)` if absent. Falls back to plain-text
`Event` if JSON parse fails. This is the only Batch 2b adapter with
`supports_stream_json=True`.

**Capabilities**

| Flag | Value |
|---|:---:|
| `supports_autonomous_loop` | False |
| `supports_session_resume` | False |
| `supports_stream_json` | True |
| `supports_tool_use` | False |

---

## 5. WR-3 Watchdog Limitation

The WR-3 watchdog uses three stale-detection signals:

| Signal | Mechanism | Applies to |
|---|---|---|
| S1 | Process-alive check (PID in fleet ledger) | All lanes |
| S2 | STATUS row staleness (fleet-ledger DB) | All lanes |
| S3 | JSONL log staleness (`~/.claude/projects/`) | **Claude only** |

S3 works by polling the Claude JSONL session log for new writes. Non-Claude
CLIs do not write JSONL to `~/.claude/projects/`, so S3 is skipped for those
lanes. At lane startup, the fleet runner prints:

```
S3 detector skipped for lane <NAME> (cli=<X>); WR-3 known limitation in v9.1
```

S1 and S2 provide baseline watchdog coverage for all six harnesses. S3
coverage for non-Claude lanes is out of scope for v9.1.

---

## 6. Zombie Reaping (PW-3)

Spawned harness subprocesses are tracked in the fleet ledger
(`.fleet-ledger/`), which records per-lane PIDs alongside STATUS rows. On
operator shutdown:

1. All tracked PIDs receive SIGTERM.
2. After the configured grace period, any surviving PIDs receive SIGKILL.

v9.1 does not introduce a new zombie-reaping mechanism beyond v9.0; the
existing subprocess lifecycle management handles all six harnesses uniformly
because they are all spawned via `subprocess.Popen`.

---

## 7. Batch 2b Experimental Designation (CV-5)

Copilot, Cursor, and Vibe ship as experimental in v9.1 because:

- The research phase (2026-05-17) verified install paths, CLI shapes, and
  auth flows for all three.
- However, **concurrent spawning under fleet load has not been stress-tested**
  in v9.1. The Batch 2a adapters (Claude, Codex, Gemini) have full smoke-test
  suites with process-spawn assertions; Batch 2b smoke tests use
  `unittest.mock.patch` and auto-skip if the CLI binary is absent.

Operators using Batch 2b harnesses should:

- Treat spawn failures and stream-parse edge cases as expected findings.
- File issues against the v9.2 milestone for any persistent quirks.
- Not use Batch 2b harnesses for production fleet workloads in v9.1.

See `docs/v9/v9-2-ROADMAP.md` for planned stabilisation work.

---

## 8. Adding a New Adapter

To implement a 7th harness adapter:

1. **Create the module.** Add
   `megalodon_ui/harnesses/<name>.py` with a class (e.g. `FooAdapter`)
   implementing all methods of the `HarnessAdapter` Protocol from `base.py`.
   The class does not need to subclass anything — Protocol compliance is
   structural (duck typing). Set `supports_autonomous_loop = False` unless the
   new harness genuinely supports it (see step 5).

2. **Register the adapter.** Add an entry to the adapter registry
   (wherever `"claude"` / `"codex"` / etc. are mapped to their classes) so
   `harness = "foo"` resolves at fleet build time.

3. **Write tests.** Add
   `scripts/tests/test_harness_<name>.py`. Minimum test coverage:
   - `test_name` — verify `adapter.name == "<name>"`.
   - `test_default_model` — verify `adapter.default_model` is in `available_models`.
   - `test_build_argv_basic` — verify argv shape for a plain-text invocation.
   - `test_build_argv_stream_json` — verify stream-json branch (or document skip).
   - `test_parse_stream_line_text` — verify plain-text line parsing.
   - `test_supports_capabilities` — verify `adapter.supports()` returns a
     `Capabilities` instance with correct flag values.

   Batch 2a adapters (Claude, Codex, Gemini) have all six tests with live
   process spawning. Batch 2b adapters (Copilot, Cursor, Vibe) use 4 tests with
   mocked spawning and `pytest.importorskip` / CLI-present guards.

4. **Update this document.** Add a section under §4, add a row to the CR-4
   matrix in §3, and note the batch designation.

5. **If autonomous loop is supported.** Set `supports_autonomous_loop = True`
   in the adapter class, update the §3 matrix, and verify S3 watchdog
   compatibility (the harness must write a JSONL session log in a predictable
   path, or a new S4 detector must be added). Cross-reference CR-4 in the PR.
