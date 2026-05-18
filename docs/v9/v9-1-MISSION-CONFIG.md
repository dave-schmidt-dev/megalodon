---
title: Megalodon v9.1 — Mission Config Reference
version: 1.0
status: AUTHORITATIVE
utc: 2026-05-17
---

# Mission Config Reference (`v9.1`)

## Overview

`.mission-config.yaml` is the operator-authored protocol shape file that lives in the root of a mission directory. It declares the lane fleet (names, roles, harness bindings, cadences), the phase sequence, task-ID grammar, and orchestrator identity for a single Megalodon mission. The orchestrator reads it once at `make_app()` startup and uses it to route ticks, generate per-lane launch files, validate task IDs, and drive the UI header.

When no `.mission-config.yaml` is present, v9.1 synthesizes a back-compat `MissionConfig` automatically from the directory name, `MISSION.md` frontmatter (for `utc_started`), and the v9.0 default lane/phase/harness shape. This means existing v9.0 missions require zero changes to run under v9.1. Custom missions — research, writing, analysis, or any non-software workflow — require an explicit config because the v9.0 defaults (AUDIT/ARCHITECT/BACKEND/FRONTEND/TEST/META + software-engineering phases) are wrong for them.

---

## Quick start

Three paths to a working config:

### `init` — scaffold from v9.0 defaults

```
python -m megalodon_ui.mission_config init [--mission-dir PATH] [--force]
```

Writes a `.mission-config.yaml` in `--mission-dir` (default: `.`) populated with the v9.0 back-compat shape: six lanes, ten phases, Claude harness bindings, and the strict v9.0 task-ID patterns. Use this as a starting template for software-engineering missions or as a baseline to cut down for other mission types.

`--force` overwrites an existing file. The write is atomic (temp file + `os.replace`).

### `preflight` — Claude-interviewed config

```
python -m megalodon_ui.preflight "<goal>"
```

Claude interviews you about your mission goal, lane requirements, and workflow, then proposes a complete `.mission-config.yaml`. Best path for non-software missions where the right lane decomposition isn't obvious. See `docs/v9/v9-1-PREFLIGHT.md` for the full preflight workflow.

### Hand-author from schema

Write `.mission-config.yaml` directly using the schema reference below and the examples at the end of this document. Validate with:

```
python -m megalodon_ui.mission_config validate <path-to-file>
```

Prints `OK: N lanes, M phases` on success, or a Pydantic validation error describing the first failure.

---

## Schema reference

### Top-level `MissionConfig`

| Field | Type | Default | Description |
|---|---|---|---|
| `schema_version` | `int` | `1` | Format version. Must be `1` for v9.1. |
| `mission` | `MissionInfo` | required | Mission identity block. |
| `lanes` | `list[LaneConfig]` | required | Ordered lane fleet. Min 1 lane. Names must be unique. |
| `phases` | `list[str]` | required | Ordered phase sequence. Min 1 phase. Names must be unique. Each name must match `^[A-Z][A-Z0-9_-]*$`. |
| `task_id_patterns` | `TaskIdPattern` | loose default | Regex patterns for valid task IDs. |
| `orchestrator_pseudo_lane` | `str` | `"ORCHESTRATOR"` | Pseudo-lane name for orchestrator-filed events. Must match `^[A-Z][A-Z0-9_-]*$`, max 20 chars. |
| `task_sections` | `list[str]` | `["PHASE-PLAN", "OPERATOR-ACCEPTANCE"]` | Section headings the orchestrator scans in TASKS.md. Each string: 1–80 chars. |
| `harness_rebinding_reserved` | `dict` | `{}` | Reserved for future harness hot-swap. Do not populate. |

### `MissionInfo`

| Field | Type | Constraint | Description |
|---|---|---|---|
| `id` | `str` | 1–80 chars | Mission identifier. Used in deterministic agent-IDs (M3) and the UI header. Typically the mission directory name. |
| `utc_started` | `str` | `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$` | ISO 8601 UTC timestamp of mission start. |
| `type` | `str` | none | Mission type label, e.g. `"software-engineering"`, `"research"`, `"writing"`. Informational; not validated against an enum. |
| `description` | `str` | none | Free-form description. Shown in UI header. |

### `LaneConfig`

| Field | Type | Constraint | Default | Description |
|---|---|---|---|---|
| `name` | `str` | `^[A-Z][A-Z0-9_-]*$`, max 20 chars | required | Lane identifier. Must be unique across all lanes. |
| `short` | `str \| None` | `^[A-Z]{1,2}$` | auto-assigned | One- or two-letter short code. Auto-assigned in declaration order (A, B, C … Z, AA, AB …) if omitted. Must be unique. |
| `role` | `str` | none | `""` | Human-readable role description. Embedded in the per-lane launch file header. |
| `harness` | `HarnessBinding` | required | — | CLI binding for this lane's runner. See [HarnessBinding details](#harnessbinding-details). |
| `cadence_seconds` | `int` | 30–3600 | `300` | Tick interval in seconds for this lane's `/loop`. |
| `tick_offset_seconds` | `int` | 0–600 | `0` | Stagger offset from the epoch before first tick fires (A6 staggering). |

### `TaskIdPattern`

| Field | Type | Constraint | Default | Description |
|---|---|---|---|---|
| `patterns` | `list[str]` | each must be a valid Python regex | required | One or more regex strings. A task ID is valid if it matches ANY pattern. |
| `description` | `str` | none | `""` | Human note explaining the ID grammar. Not parsed. |

All patterns are compiled at validation time (`re.compile`). An invalid regex raises a `ValidationError` immediately.

The schema default factory (used when `task_id_patterns` is omitted) is the **loose pattern** `^[A-Z][A-Za-z0-9\-\.]*$` — any uppercase-initial alphanumeric string with hyphens and dots. The v9.0 back-compat shape uses a strict enumerated pattern instead; see [Task ID grammar](#task-id-grammar).

---

## HarnessBinding details

Each `LaneConfig` carries one `HarnessBinding` that controls which CLI runner executes that lane's ticks.

| Field | Type | Options / Constraint | Default | Description |
|---|---|---|---|---|
| `cli` | `Literal` | `claude`, `codex`, `gemini`, `copilot`, `cursor`, `vibe` | required | Which CLI binary to invoke. |
| `model` | `str` | provider-specific model ID | required | Model string passed via the CLI's model flag. |
| `extra_args` | `list[str]` | — | `[]` | Additional CLI args appended verbatim. Use sparingly; adapter may not support all flags. |
| `auth_env` | `list[str]` | — | `[]` | Environment variable names that must be set before the lane is spawned. The launcher checks for them and aborts with a clear error if any are missing. |

**Example binding for a Gemini lane:**

```yaml
harness:
  cli: gemini
  model: gemini-2.5-pro-preview
  extra_args: []
  auth_env:
    - GEMINI_API_KEY
```

For adapter-specific flags, model name lists, and auth patterns, see `docs/v9/v9-1-HARNESS-ADAPTERS.md`.

---

## Task ID grammar {#task-id-grammar}

Task IDs become filesystem paths (`claims/<task_id>/`). The orchestrator enforces two independent validation layers:

**Layer 1 — operator pattern match.** The task ID must match at least one regex in `task_id_patterns.patterns`. The operator controls this layer entirely.

**Layer 2 — path-traversal guard (CR-3, PM-6).** Applied AFTER the pattern match, unconditionally. The following characters are always forbidden regardless of what the operator's pattern allows:

- `/` — directory separator
- `\` — Windows path separator
- `..` — parent-directory traversal
- null byte (`\x00`)

This is a safety floor the operator cannot weaken. If a task ID passes the operator's pattern but contains any of these, it is rejected with a `ValueError` that names the specific character and links to this section.

### Default patterns

**Back-compat v9.0 shape** (written by `init`, used when no config is present):

```
^(P\d+(\.\d+)?(-[A-F](-to-[A-F])?)?|P\d+-RUN-[A-Z0-9_-]+|REPAIR-[A-Z0-9_-]+|OPERATOR-[A-Z_-]+|S-\d+|TEST-\d+|CHALLENGE-[A-Z0-9_-]+)$
```

This covers: `P1`, `P2.5`, `P2-A`, `P2-A-to-F`, `P5-RUN-MUTATIONS-E2E`, `REPAIR-SSE-HANDLER`, `OPERATOR-ACCEPTANCE-REQUEST`, `S-8`, `TEST-42`, `CHALLENGE-ARCH-001`.

**Schema default (loose, for custom missions):**

```
^[A-Z][A-Za-z0-9\-\.]*$
```

Any string starting with an uppercase letter, followed by alphanumerics, hyphens, or dots. Suitable as a starting point; tighten to match your actual ID grammar.

### Custom patterns

For a research mission using paper-style IDs (`LIT-001`, `ANA-7`, `SYN-12`):

```yaml
task_id_patterns:
  patterns:
    - "^(LIT|ANA|SYN|WRT)-\\d+$"
  description: "Research mission IDs: LIT-NNN, ANA-NNN, SYN-NNN, WRT-NNN"
```

Patterns are standard Python `re` syntax. Always anchor with `^` and `$` to avoid partial matches.

---

## MISSION.md vs `.mission-config.yaml` boundary (WR-6) {#mission-md-boundary}

`MISSION.md` is the mission narrative — the operator's prose description of what they are trying to accomplish, the problem context, acceptance criteria, and any human-readable constraints. It is the *what*. The orchestrator and workers read it to understand intent, but it is not a protocol artifact.

`.mission-config.yaml` is the protocol shape — the *how* the orchestrator and fleet are structured: which lanes exist, which harnesses run them, what phase sequence to follow, and how task IDs are validated. These two files are fully independent. `MISSION.md` does not reference the config; the config does not reference `MISSION.md` content. The one exception is the back-compat synthesis path: when no config file is present, `default_v9_0_shape.synthesize()` reads `MISSION.md` frontmatter to extract `utc_started` (falling back to the file's mtime, then `.mission-events` mtime, then `now()`). In all other circumstances, the two files are orthogonal.

---

## Architecture identity (PW-5) {#architecture-identity}

The orchestrator is always Claude. This is non-negotiable in v9.1.

The `harness` binding on a `LaneConfig` controls which CLI runner executes **that lane's ticks** — it does not affect the orchestrator. A mission may have lanes bound to `codex`, `gemini`, or other providers; the orchestrator that coordinates them, runs preflight, and synthesizes mission config still runs as Claude.

The preflight CLI (`python -m megalodon_ui.preflight`) uses Claude as its reasoning engine regardless of which harnesses are declared in the resulting config. If you set all six lanes to `gemini`, the preflight conversation and config proposal still happen in Claude. Workers in those lanes then execute as Gemini.

Do not set `orchestrator_pseudo_lane` to a lane that exists in `lanes`. That field names a pseudo-lane for orchestrator-filed events in STATUS.md — it is not a real runner. In the v9.0 back-compat shape it is `META` (matching the META lane); in custom configs it defaults to `ORCHESTRATOR`.

---

## Migration from v9.0

### Scenario A: keep v9.0 mission running as-is

Do nothing. When v9.1 starts up against a mission directory with no `.mission-config.yaml`, it calls `default_v9_0_shape.synthesize()` and proceeds with the full v9.0 lane/phase/harness shape. Your existing STATUS.md, TASKS.md, claims, and HISTORY.md are unaffected.

### Scenario B: customize lanes or phases, keep v9.0 harness binding

1. Run `python -m megalodon_ui.mission_config init` from the mission directory. This writes `.mission-config.yaml` with the v9.0 defaults as a starting point.
2. Edit the YAML: rename lanes, remove unused lanes, adjust phases, change cadences, set `tick_offset_seconds` for staggering.
3. Validate: `python -m megalodon_ui.mission_config validate .mission-config.yaml`
4. Restart the orchestrator (`make_app()` reads config at startup; there is no hot-reload in v9.1).

### Scenario C: non-software mission

1. Run `python -m megalodon_ui.preflight "<your goal>"` — Claude will interview you and propose a config.
2. Review the proposed YAML, edit if needed.
3. Save as `.mission-config.yaml` in your mission directory.
4. Validate and start.

Alternatively, hand-author from the examples below and validate before starting.

---

## Examples

### Example 1: software-engineering mission (v9.0 default shape)

This is exactly what `init` produces for a mission directory named `my-project` started on 2026-05-17.

```yaml
schema_version: 1
mission:
  id: my-project
  utc_started: "2026-05-17T09:00:00Z"
  type: software-engineering
  description: "Backend API + frontend dashboard rewrite"
lanes:
  - name: AUDIT
    short: A
    role: "Cross-lane observer; files findings and SIGNALs"
    harness:
      cli: claude
      model: claude-sonnet-4-6
      extra_args: []
      auth_env: []
    cadence_seconds: 300
    tick_offset_seconds: 0
  - name: ARCHITECT
    short: B
    role: "Spec authorship, design decisions, SPEC-FIRST HEAL addenda"
    harness:
      cli: claude
      model: claude-opus-4-7
      extra_args: []
      auth_env: []
    cadence_seconds: 300
    tick_offset_seconds: 45
  - name: BACKEND
    short: C
    role: "Python/API implementation"
    harness:
      cli: claude
      model: claude-opus-4-7
      extra_args: []
      auth_env: []
    cadence_seconds: 180
    tick_offset_seconds: 90
  - name: FRONTEND
    short: D
    role: "JS/HTML/CSS implementation"
    harness:
      cli: claude
      model: claude-opus-4-7
      extra_args: []
      auth_env: []
    cadence_seconds: 180
    tick_offset_seconds: 135
  - name: TEST
    short: E
    role: "Playwright e2e + pytest integration"
    harness:
      cli: claude
      model: claude-opus-4-7
      extra_args: []
      auth_env: []
    cadence_seconds: 180
    tick_offset_seconds: 180
  - name: META
    short: F
    role: "Orchestrator heartbeat, TASKS/HISTORY synthesis"
    harness:
      cli: claude
      model: claude-sonnet-4-6
      extra_args: []
      auth_env: []
    cadence_seconds: 420
    tick_offset_seconds: 225
phases:
  - INIT
  - PHASE-PLAN
  - PHASE-CHALLENGE
  - PHASE-BUILD
  - PHASE-VERIFY
  - PHASE-RUN
  - PHASE-HEAL
  - PHASE-OPERATOR-ACCEPTANCE
  - DRAINING
  - COMPLETE
task_id_patterns:
  patterns:
    - "^(P\\d+(\\.\\d+)?(-[A-F](-to-[A-F])?)?|P\\d+-RUN-[A-Z0-9_-]+|REPAIR-[A-Z0-9_-]+|OPERATOR-[A-Z_-]+|S-\\d+|TEST-\\d+|CHALLENGE-[A-Z0-9_-]+)$"
  description: "v9.0 software-engineering task IDs"
orchestrator_pseudo_lane: META
task_sections:
  - "PHASE 1 — PLAN"
  - "OPERATOR-ACCEPTANCE TASKS"
```

### Example 2: research mission

Four lanes covering a literature-to-synthesis research workflow. Uses paper-style task IDs.

```yaml
schema_version: 1
mission:
  id: market-structure-research
  utc_started: "2026-05-17T10:00:00Z"
  type: research
  description: "Systematic literature review and analysis of market microstructure papers"
lanes:
  - name: LITERATURE
    short: L
    role: "Search, retrieve, and annotate source papers"
    harness:
      cli: claude
      model: claude-opus-4-7
      extra_args: []
      auth_env: []
    cadence_seconds: 600
    tick_offset_seconds: 0
  - name: ANALYSIS
    short: A
    role: "Statistical and methodological analysis of retrieved papers"
    harness:
      cli: claude
      model: claude-opus-4-7
      extra_args: []
      auth_env: []
    cadence_seconds: 600
    tick_offset_seconds: 150
  - name: SYNTHESIS
    short: S
    role: "Cross-paper synthesis, contradiction resolution, gap identification"
    harness:
      cli: claude
      model: claude-opus-4-7
      extra_args: []
      auth_env: []
    cadence_seconds: 600
    tick_offset_seconds: 300
  - name: WRITE
    short: W
    role: "Draft production: sections, citations, figures"
    harness:
      cli: claude
      model: claude-opus-4-7
      extra_args: []
      auth_env: []
    cadence_seconds: 900
    tick_offset_seconds: 450
phases:
  - SCOPING
  - RETRIEVAL
  - ANALYSIS
  - SYNTHESIS
  - DRAFTING
  - REVIEW
  - COMPLETE
task_id_patterns:
  patterns:
    - "^(LIT|ANA|SYN|WRT)-\\d{3}$"
  description: "Research task IDs: LIT-NNN, ANA-NNN, SYN-NNN, WRT-NNN (zero-padded to 3 digits)"
orchestrator_pseudo_lane: ORCHESTRATOR
task_sections:
  - "RETRIEVAL-PLAN"
  - "OPERATOR-ACCEPTANCE"
```

### Example 3: writing mission

A four-lane writing arc from research through fact-checking.

```yaml
schema_version: 1
mission:
  id: technical-report-q2
  utc_started: "2026-05-17T11:00:00Z"
  type: writing
  description: "Q2 technical report: infrastructure performance and recommendations"
lanes:
  - name: RESEARCH
    short: R
    role: "Source gathering, data extraction, background research"
    harness:
      cli: claude
      model: claude-sonnet-4-6
      extra_args: []
      auth_env: []
    cadence_seconds: 600
    tick_offset_seconds: 0
  - name: DRAFT
    short: D
    role: "First-draft authorship, structure, prose"
    harness:
      cli: claude
      model: claude-opus-4-7
      extra_args: []
      auth_env: []
    cadence_seconds: 600
    tick_offset_seconds: 150
  - name: EDIT
    short: E
    role: "Copyediting, style consistency, readability"
    harness:
      cli: claude
      model: claude-sonnet-4-6
      extra_args: []
      auth_env: []
    cadence_seconds: 900
    tick_offset_seconds: 300
  - name: FACTCHECK
    short: F
    role: "Claim verification, citation accuracy, numeric checks"
    harness:
      cli: claude
      model: claude-opus-4-7
      extra_args: []
      auth_env: []
    cadence_seconds: 900
    tick_offset_seconds: 450
phases:
  - RESEARCH
  - OUTLINE
  - DRAFTING
  - EDITING
  - FACT-CHECKING
  - FINAL-REVIEW
  - COMPLETE
task_id_patterns:
  patterns:
    - "^(RSC|DFT|EDT|FCK)-[A-Z0-9_-]+$"
  description: "Writing mission IDs: RSC-*, DFT-*, EDT-*, FCK-*"
orchestrator_pseudo_lane: ORCHESTRATOR
task_sections:
  - "OUTLINE-PLAN"
  - "OPERATOR-ACCEPTANCE"
```

---

## Config reload semantics (CV-8)

The config is read **once** at `make_app()` startup. There is no live reload in v9.1. The supported workflow for config changes is edit-then-restart:

1. Stop the orchestrator (SIGINT or `make stop`).
2. Edit `.mission-config.yaml`.
3. Validate: `python -m megalodon_ui.mission_config validate .mission-config.yaml`
4. Restart.

`kill -HUP <pid>` sending `SIGHUP` to trigger a config reload without full restart is planned for a future version (deferred from v9.1). The signal handler stub exists but the reload logic is not implemented; sending SIGHUP in v9.1 has no effect.

Do not edit the config while the orchestrator is running. The orchestrator holds lane metadata, phase state, and the task-ID validator in memory from the initial load; mid-flight edits create an inconsistency between the file and the in-memory state.

---

## Document control

- **Status**: AUTHORITATIVE for v9.1 mission config behavior.
- **Cross-references**: `docs/v9/v9-1-HARNESS-ADAPTERS.md` (per-CLI binding details), `docs/v9/v9-1-PREFLIGHT.md` (preflight interview workflow), `docs/v9/V9-ROADMAP.md` (architectural decisions).
- **Error cross-reference**: PM-6 errors in `megalodon_ui/mission_config/schema.py` link to `#task-id-grammar`.
- **PW-5 cross-reference**: [Architecture identity](#architecture-identity) addresses the PW-5 concern from the v9 self-contrarian review.
