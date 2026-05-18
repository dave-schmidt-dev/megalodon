# Megalodon v9.1 — Pre-flight CLI

> Bootstrap a `.mission-config.yaml` by letting Claude interview you about your
> goals. Claude proposes a config, you refine it, then approve — or abandon and
> recover the draft later.

---

## 1. Overview

Pre-flight is an interactive CLI that spawns Claude as an interviewer. You
describe what you want to build in plain English; Claude drafts a
`.mission-config.yaml` conforming to the v9.1 schema; you iterate until the
draft looks right, then approve it. The file is written atomically to disk.

**When to use pre-flight:**

- You are starting a new mission from scratch and want Claude to do the
  structural thinking for you.
- You have some prior context (a README or tasks.md) that should shape the
  lane design, phases, and cadence.

**When _not_ to use pre-flight:**

- You want a blank template to fill in by hand: run
  `python -m megalodon_ui.mission_config init` instead — it writes the default
  v9.0 shape with all required fields and sensible defaults.
- You already know exactly what you want: hand-author from the schema reference
  at [`v9-1-MISSION-CONFIG.md`](v9-1-MISSION-CONFIG.md). Pre-flight is not a
  shortcut; it is a collaborative drafting tool.

---

## 2. CLI Surface

```
python -m megalodon_ui.preflight <GOAL> [--mission-dir PATH]
                                         [--context-dir PATH]
                                         [--max-refine N]
                                         [--force]
```

### Positional argument

| Argument | Required | Description |
|---|---|---|
| `<GOAL>` | yes | Mission goal in natural language. Quote multi-word strings. Example: `"Build a Python CLI for auditing ML paper citations."` |

### Options

| Flag | Default | Description |
|---|---|---|
| `--mission-dir PATH` | `.` (current directory) | Directory where `.mission-config.yaml` will be written once approved. |
| `--context-dir PATH` | same as `--mission-dir` | Directory from which `README.md` and `tasks.md` (or `TASKS.md`) are read to provide project context to Claude. Useful when bootstrapping a config for an existing codebase. |
| `--max-refine N` | `10` | Maximum number of refinement iterations before the REPL forces an approve-or-abandon decision. PM-5 safeguard. See [Section 5](#5-max-refine-flag-pm-5). |
| `--force` | off | Overwrite an existing `.mission-config.yaml`. Without this flag, pre-flight exits 1 immediately if the target file already exists. |

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Operator approved; `.mission-config.yaml` written successfully. |
| `1` | Operator abandoned, validation failure, auth error, or target exists without `--force`. |
| `130` | Process interrupted by SIGINT (Ctrl-C). Draft snapshot written if available. |

---

## 3. Workflow

### Step-by-step

**3.1 Run the CLI.**

```
python -m megalodon_ui.preflight "Build a research review tool for ML papers" \
    --mission-dir ./my-mission \
    --max-refine 5
```

**3.2 Auth check.**

Pre-flight verifies `ANTHROPIC_API_KEY` is set before doing anything else. If
it is missing, the process exits 1 immediately. See [Section 9](#9-auth-requirement).

**3.3 Context loading.**

Pre-flight reads `README.md` and `tasks.md` (falling back to `TASKS.md`) from
the context directory. Each file is silently truncated to 50 KB. If neither
file exists, pre-flight still runs — Claude just has less project context.

The contents are embedded verbatim in the prompt preamble so Claude understands
the existing codebase when proposing lanes, phases, and cadence.

**3.4 Initial proposal.**

Pre-flight invokes Claude (via `ClaudeAdapter`, model `claude-opus-4-7`) with:

- The operator's goal.
- The context preamble (if any).
- A schema cheat-sheet covering every required and optional field.
- Strict output rules: raw YAML only, no prose, no markdown fences.

Claude responds with a complete `.mission-config.yaml` draft. Pre-flight
validates the response against the `MissionConfig` Pydantic schema. If
validation fails, the error is fed back to Claude automatically (this counts
against `--max-refine`).

**3.5 Operator REPL.**

The draft is printed. The operator types one of three things:

| Input | Effect |
|---|---|
| `approve` | Writes the YAML atomically and exits 0. |
| `abandon` | Saves the draft to `.mission-config.yaml.aborted-<utc>` and exits 1. |
| Any other text | Treated as a free-form revision request. Pre-flight re-invokes Claude with the current draft plus the feedback. The iteration counter increments. |

Revision requests can be as concise as "add a SECURITY lane" or as detailed as
a multi-line description of what should change. Claude sees the full current
draft and the request simultaneously.

**3.6 Atomic write.**

On `approve`, the YAML is written to a `.tmp` file, then renamed into place via
`os.replace` for atomicity. The `.tmp` file is cleaned up on any exception. The
final path is printed:

```
wrote /path/to/my-mission/.mission-config.yaml
```

---

## 4. Example Session

The following is a reproducible walkthrough using real flags. The YAML is
abridged for readability.

```
$ python -m megalodon_ui.preflight "Build a research review tool for ML papers" \
    --mission-dir . --max-refine 3

[pre-flight] reading context from ./README.md + ./tasks.md
[pre-flight] spawning Claude (model: claude-opus-4-7)

--- Current .mission-config.yaml draft ---
schema_version: 1
mission:
  id: ml-paper-review
  utc_started: '2026-05-17T00:00:00Z'
  type: software-engineering
  description: Research review tool for ML papers
lanes:
  - name: LITERATURE
    short: L
    role: Literature search and ingestion
    harness:
      cli: claude
      model: claude-opus-4-7
      extra_args: []
      auth_env: []
    cadence_seconds: 300
    tick_offset_seconds: 0
  - name: ANALYSIS
    short: A
    role: Paper analysis and summarisation
    harness:
      cli: claude
      model: claude-opus-4-7
      extra_args: []
      auth_env: []
    cadence_seconds: 300
    tick_offset_seconds: 60
phases:
  - INIT
  - INGEST
  - REVIEW
  - COMPLETE
orchestrator_pseudo_lane: ORCHESTRATOR
task_sections:
  - PHASE-PLAN
  - OPERATOR-ACCEPTANCE
------------------------------------------
approve / abandon / <revision request>: add a SECURITY lane that audits paper claims for risks
[pre-flight] revising (1/3)...

--- Current .mission-config.yaml draft ---
schema_version: 1
mission:
  id: ml-paper-review
  utc_started: '2026-05-17T00:00:00Z'
  type: software-engineering
  description: Research review tool for ML papers
lanes:
  - name: LITERATURE
    short: L
    role: Literature search and ingestion
    harness:
      cli: claude
      model: claude-opus-4-7
      extra_args: []
      auth_env: []
    cadence_seconds: 300
    tick_offset_seconds: 0
  - name: ANALYSIS
    short: A
    role: Paper analysis and summarisation
    harness:
      cli: claude
      model: claude-opus-4-7
      extra_args: []
      auth_env: []
    cadence_seconds: 300
    tick_offset_seconds: 60
  - name: SECURITY
    short: S
    role: Audits paper claims for methodological and factual risks
    harness:
      cli: claude
      model: claude-opus-4-7
      extra_args: []
      auth_env: []
    cadence_seconds: 600
    tick_offset_seconds: 120
phases:
  - INIT
  - INGEST
  - REVIEW
  - COMPLETE
orchestrator_pseudo_lane: ORCHESTRATOR
task_sections:
  - PHASE-PLAN
  - OPERATOR-ACCEPTANCE
------------------------------------------
approve / abandon / <revision request>: approve
wrote ./.mission-config.yaml
```

---

## 5. --max-refine Flag (PM-5)

The `--max-refine` cap (default 10) exists to prevent pathological
back-and-forth where operator and Claude iterate indefinitely on a request that
cannot be satisfied within the schema constraints.

**Behaviour at the cap:**

When `refine_count` reaches `max_refine`, the REPL switches to a restricted
prompt:

```
Max refinements (N) reached. You must approve or abandon this draft.
Type 'approve' to accept or 'abandon' to exit:
```

At this point only `approve` and `abandon` are accepted. Any other input is
rejected with "Please type 'approve' or 'abandon'." — Claude is not invoked
again.

**Choosing a value:**

- The default of 10 is generous. Most configs converge in 2-4 rounds.
- For automated or CI contexts pass `--max-refine 1` to require the first
  proposal to be accepted or rejected with no iteration.
- If you hit the cap and the config is still not right, abandon, re-run with a
  more specific goal, and use `--context-dir` to give Claude more to work with.

Note that YAML validation failures also consume iterations from this budget. If
Claude repeatedly returns malformed YAML and exhausts the cap, pre-flight
treats the session as an abandon. See [Section 10](#10-troubleshooting).

---

## 6. SIGINT Recovery (Ctrl-C)

If you press Ctrl-C (or the process receives SIGTERM) during an active
session:

1. The signal handler reads the most recent validated draft from memory.
2. It writes a snapshot to:
   ```
   <mission_dir>/.mission-config.yaml.aborted-<utc>
   ```
   where `<utc>` is an ISO-8601 timestamp such as `20260517T142305Z`.
3. Any in-progress `.mission-config.yaml.tmp` file is deleted.
4. The process exits with code 130 (the POSIX convention for SIGINT
   termination).

Example output on interrupt:

```
^C
Interrupted — draft snapshot written to ./.mission-config.yaml.aborted-20260517T142305Z
```

If interrupted before the first proposal arrives, no snapshot is written
because there is no draft yet.

To resume: copy or rename the `.aborted-*` file, edit by hand if needed, or
re-run pre-flight with `--force`.

Aborted snapshots accumulate in `--mission-dir` and are not cleaned up
automatically.

---

## 7. Opt-out Path

Pre-flight is entirely optional. Two alternatives get you to a valid
`.mission-config.yaml` without it:

**Template approach (recommended for most hand-edits):**

```
python -m megalodon_ui.mission_config init
```

Writes a default v9.0 skeleton to `./.mission-config.yaml` with all required
fields and sensible defaults. Edit the file directly. Use `--mission-dir` to
target a different directory.

**Full hand-author:**

Consult the complete schema reference at
[`v9-1-MISSION-CONFIG.md`](v9-1-MISSION-CONFIG.md) and author the file from
scratch. This gives you the most control and is the right choice when you
already have a precise config in mind or are migrating from another
orchestration system.

Both alternatives produce files that are fully compatible with the Megalodon
v9.1 runtime. Pre-flight produces no output that cannot be replicated by hand.

---

## 8. PW-5: Claude-as-Orchestrator Hard Requirement

Pre-flight is Claude-only. There is no `--orchestrator-cli` flag and no way to
route the pre-flight interaction through a non-Claude model.

This is a hard constraint of v9.1 architecture: the orchestrator role — which
includes running pre-flight, applying the task queue, and running the watchdog
— is always Claude. Even if every lane in the resulting config binds to a
different harness (Codex, Gemini, Cursor, etc.), the orchestrator itself must
be Claude.

See [`v9-1-MISSION-CONFIG.md#architecture-identity`](v9-1-MISSION-CONFIG.md#architecture-identity)
for the full rationale.

**Practical implication:** `ANTHROPIC_API_KEY` is required even if you intend
to run zero Claude lanes in production. The key is used by the orchestrator
only; non-Claude lane harnesses use their own auth env vars. See
[`v9-1-HARNESS-ADAPTERS.md`](v9-1-HARNESS-ADAPTERS.md).

---

## 9. Auth Requirement

Pre-flight fails immediately if `ANTHROPIC_API_KEY` is not set in the
environment:

```
Pre-flight requires ANTHROPIC_API_KEY (orchestrator is Claude). Set it and re-run.
```

Exit code: `1`.

**How to set the key:**

Option A — inline for a single run:

```bash
ANTHROPIC_API_KEY=sk-ant-... python -m megalodon_ui.preflight "My goal"
```

Option B — export in your shell profile or `.env`:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Option C — use `claude setup-token` if the Claude CLI is installed, then
export the value it writes.

**Test bypass:** Set `MOCK_CLAUDE=1` in the environment to skip the auth check
in automated tests. This does not add a `--mock-claude` CLI flag; it is a
test-only escape hatch and should never be used in production.

---

## 10. Troubleshooting

**"Claude returned unparseable YAML."**

Pre-flight automatically retries: the validation error is included verbatim in
the next prompt so Claude can self-correct. This retry counts against
`--max-refine`. If Claude fails to produce valid YAML after exhausting the
refinement budget, the session is treated as an abandon and a draft snapshot is
written. To diagnose, inspect the snapshot: it may contain markdown fences or
prose that the validator rejected. Re-run with a more constrained goal or
report the raw output as a bug.

**"I want to start over."**

Type `abandon` at any prompt. A snapshot is written. Then re-run pre-flight
with a revised goal string. Use `--force` if the previous aborted snapshot or
an earlier failed run left a `.mission-config.yaml` in the target directory.

**"I lost my aborted draft."**

Aborted snapshots are written to:

```
<mission_dir>/.mission-config.yaml.aborted-<utc>
```

They accumulate and are never deleted automatically. List them:

```bash
ls -lt /path/to/mission-dir/.mission-config.yaml.aborted-*
```

Pick the most recent one. Delete old snapshots manually:

```bash
rm /path/to/mission-dir/.mission-config.yaml.aborted-*
```

**"Pre-flight exits immediately with exit 1 and no output."**

The goal string is empty after stripping whitespace. Quote the goal argument:

```bash
python -m megalodon_ui.preflight "My non-empty goal"
```

**"Target file exists error."**

Pass `--force` to overwrite, or move the existing file first to keep it.

**"I need to use a different context than the mission directory."**

Pass `--context-dir /path/to/existing/project` with
`--mission-dir /path/to/new/location`. Standard pattern when bootstrapping a
config for an existing codebase.

**"The REPL is not interactive — I am running in a script."**

EOF on stdin is treated as `abandon`. For fully automated scenarios, prefer
`mission_config init` + programmatic editing over pre-flight.

---

## See Also

- [`v9-1-MISSION-CONFIG.md`](v9-1-MISSION-CONFIG.md) — Full schema reference,
  field-by-field documentation, and architecture identity (PW-5).
- [`v9-1-HARNESS-ADAPTERS.md`](v9-1-HARNESS-ADAPTERS.md) — Configuring
  individual lane harnesses (Claude, Codex, Gemini, Copilot, Cursor, Vibe).
- [`v9-2-ROADMAP.md`](v9-2-ROADMAP.md) — Planned changes to pre-flight in
  future releases, including multi-orchestrator support and diff-mode
  refinement.
