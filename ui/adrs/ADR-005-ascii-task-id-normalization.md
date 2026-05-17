# ADR-005 — ASCII task-id normalization (5-source BLOCKING quorum)

- **Status:** Accepted
- **UTC:** 2026-05-16T15:54Z
- **Authored by:** agent-aa79 (ARCHITECT, P3-B)
- **Quorum evidence:** ARCHITECT P2.5-B §10, BACKEND P2-C→B C3, FRONTEND STATUS @ 15:40Z, META STATUS @ 15:46Z (CH-2), SIG-ORCH#2 + SIG-ORCH#3 (HISTORY @ 15:38Z + @ 15:40Z)

## Context

The v7 protocol's task-id convention uses Unicode RIGHTWARDS ARROW (`→`, U+2192) for directed challenge/verify pairings: `P2-C→B`, `P2-A→F`, `P4-B→E`, etc. This is human-readable but creates **filesystem, URL, JSON, and terminal encoding hazards**.

Empirical evidence observed during this run:

- BACKEND attempted `mkdir claims/P2-CtoB` (ASCII transliteration), then `mkdir claims/P2-C→B` (canonical). **Both directories now exist for the same logical task** — two locks for one claim.
- ARCHITECT (this lane) created `claims/P2-B-A` (hyphen) before learning the canonical form, then `claims/P2-B→A`. Duplicate locks again.
- FRONTEND observed and flagged the inconsistency in STATUS notes ("ghost claim dir `P2-B-A` (non-canonical) suggests Unicode-arrow encoding pain").
- META aggregated the convergence: CH-2 has **5 independent sources** identifying this as a defect.

Per TIER 2 §"Severity escalation," 2+ independent lanes' Pass-1 findings on the same artifact = MAJOR → BLOCKING. We have 5. This is unambiguously BLOCKING-class.

## Decision

**Codify ASCII task-ids in the UI's API surface, normalize in both directions, surface inconsistencies visually.**

### API normalization

- All task-id-bearing endpoints **accept both forms** in request bodies, path params, and query strings:
  - `P2-C→B` (Unicode arrow, U+2192)
  - `P2-C-to-B` (ASCII hyphen-to-hyphen)
  - URL-encoded `P2-C%E2%86%92B` (UTF-8 encoded arrow)
- Server **canonicalizes internally to** `P2-C-to-B` (the ASCII form).
- **JSON responses always return the canonical ASCII form.**

### Filesystem normalization (UI side)

The UI **does not modify existing `claims/` directories** (workers own those). But:

- The `Task` model includes `dup_claim_dirs: string[]` listing every observed variant.
- Tasks with `dup_claim_dirs.length > 1` render with a **red "duplicate locks" chip** in the UI.
- A CROSS-tool `ui/tools/normalize-claims.py` is planned (claimable as S-pool task) for **operator-driven** consolidation — never automatic.

### v8 protocol recommendation (handoff to AUDIT)

`docs/v8-changeset.md` should mandate ASCII task-ids from v8 forward:

- `P2-C→B` → `P2-C-to-B` everywhere in TASKS.md, MISSION.md task matrix, examples in README.md.
- v8 workers create claim directories using ASCII form only.
- v7-compat: workers reading v7 TASKS.md may encounter Unicode form; they should normalize on read for their own `mkdir` calls.

## Why this couldn't wait for v8

The UI ships in this mission. If the UI doesn't normalize, it can't faithfully render the filesystem state — it will see `P2-C→B` and `P2-CtoB` as separate tasks and confuse the operator. Normalization is **load-bearing for correctness**, not a polish item.

## Implementation specifics

### Canonical conversion function

```python
import unicodedata

ARROW_GLYPHS = {"→", "->", "to"}  # observed variants
ARROW_REPLACEMENT = "-to-"

def canonicalize_task_id(raw: str) -> str:
    # Decompose then re-encode any combining-character oddities
    s = unicodedata.normalize("NFKC", raw.strip())
    # Replace U+2192 and common transliterations with ASCII canonical
    s = s.replace("→", "-to-")
    # Collapse repeated separators that may result
    while "--" in s:
        s = s.replace("--", "-")
    s = s.strip("-")
    return s
```

### Filesystem discovery (`dup_claim_dirs`)

```python
def discover_claim_dirs(canonical_id: str, claims_root: Path) -> list[str]:
    """
    Returns sorted list of claim-dir names whose canonical form matches.
    """
    matches = []
    for entry in claims_root.iterdir():
        if not entry.is_dir():
            continue
        if canonicalize_task_id(entry.name) == canonical_id:
            matches.append(entry.name)
    return sorted(matches)
```

### UI surfacing

```html
<div class="task-card" data-testid="task-{{ task.id }}">
  <h3>{{ task.id }}</h3>
  {% if task.dup_claim_dirs|length > 1 %}
  <span class="chip chip-red" data-testid="task-{{ task.id }}-dup-claims"
        aria-label="Duplicate claim directories detected">
    Duplicate locks: {{ task.dup_claim_dirs|join(', ') }}
  </span>
  {% endif %}
</div>
```

## Consequences

**Positive:**
- Operator sees one task per logical task, not multiple confusing entries.
- URL space stays clean (`/tasks/P2-C-to-B` instead of percent-encoded mess).
- ASCII task-ids in filenames make `grep` / `find` / shell completion painless.
- Aligns the UI with the v8 protocol direction.

**Negative:**
- One more normalization layer to test. Mitigation: TEST `T-R2-b canonical-claim` test ID already specified.
- Cannot retroactively fix existing duplicate claim directories without operator action. Mitigation: CROSS-tool + UI chip; operator stays in control.
- If a worker writes a *third* variant we haven't seen (e.g., `P2-C_to_B`), it won't be normalized until we add it to the conversion function. Mitigation: regex-based fallback that converts any non-alphanumeric run between lane codes to `-to-`.

## What this ADR does NOT do

- It does **not** automatically delete duplicate `claims/<variant>` directories. That's destructive; operator chooses.
- It does **not** modify TASKS.md to canonicalize existing entries. That's AUDIT's `docs/v8-changeset.md` territory.
- It does **not** rewrite finding filenames. Those are immutable per RULE 10.

## References

- ARCHITECT P2.5-B §10: `findings/agent-aa79-B-P2.5-arch-plan-v2-2026-05-16T15-46Z.md`
- BACKEND P2-C→B C3: `findings/agent-8318-C-P2-challenge-of-architect-2026-05-16T15-38Z.md:56-71`
- HISTORY @ 15:38Z SIG-ORCH#2: file-collision concurrency-architecture
- HISTORY @ 15:40Z SIG-ORCH#3: output-format-standardization
- META capstone tracking (CH-2 BLOCKING-quorum aggregation)
