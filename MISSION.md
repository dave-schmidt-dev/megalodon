# Mission

**Mission ID:** &lt;UTC-date&gt;--&lt;short-slug&gt;
**Started:** &lt;UTC timestamp&gt;
**Deliverable date:** &lt;date if applicable&gt;
**Status:** template — replace with actual mission before deployment

---

## Source project

- **Path:** `<PROJECT_ROOT>/<source-project-name>/` (absolute path to the source project)
- **Description:** &lt;one paragraph: what the project is, what's being reviewed/built/audited&gt;

## Scope

&lt;What this run will accomplish. Be specific about artifacts in scope and out-of-scope.&gt;

Example: "Multi-angle independent review of the tribunal-facing rebuttal draft at reports/justin/aj-rebuttal-draft-*.md, the counsel-implications companion, methodology docs, and exhibits. Out of scope: re-loading source data; modifying any okx_case file."

## Lanes (configurable per mission)

Edit this table to define lanes. Defaults below are for multi-angle review missions; adapt for implementation, brainstorming, audit, etc. Then update STATUS.md to match.

| Code | Lane | Stance | Typical defects to find |
|---|---|---|---|
| A | LOGIC | Inference auditor | Claim→evidence chain breaks, internal inconsistency, unstated premises, temporal-precedence inversions, definitional drift |
| B | PROSE | Tribunal-register editor | Writing-standard violations, hedging/assertion miscalibration, ambiguity, defined-term drift, register breaks |
| C | SQL | Query-fidelity auditor | Row-count mismatches, ORDER BY drift, join cardinality errors, query-vs-result divergence |
| D | MATH | Quantitative auditor | Arithmetic, percentages, methodology, threshold-policy compliance, float-precision artifacts |
| E | LEGAL | Opposing-counsel mindset | Attack surfaces, evidentiary admissibility, uncited bridges, factual overreach, framing risk |

**Alternative lane sets** (for different mission types):
- Implementation: ARCHITECT / CODER / TESTER / REVIEWER / DOCS
- Brainstorming: EXPLORER / SKEPTIC / SYNTHESIZER / DEVIL / SCRIBE
- Security review: THREAT-MODELER / CODE-AUDITOR / DEPENDENCY-AUDITOR / RUNTIME / DOCS
- Data audit: SCHEMA / SAMPLING / DISTRIBUTION / OUTLIER / METADATA

## Cadence

**Loop cadence:** 3 minutes (cron `*/3 * * * *` or dynamic `delaySeconds: 180`)

Override only if mission characteristics warrant:
- Deep audits with 60+ min tasks: 10m
- Rapid iteration / small tasks: 2m
- Idle / monitoring only: 20-30m

## Useful pointers in source project

(List files / paths workers should know about — drafts, specs, standards, existing verifications, etc.)

- **Drafts:** `<paths>`
- **Standards:** `<paths>`
- **Methodology docs:** `<paths>`
- **Existing verifications (Pass-2 only):** `<paths>`
- **Read-only databases:** `<command to open them safely>`

## Hard constraints (mission-specific)

- READ-ONLY on `<source project path>`
- No writes outside `<PROJECT_ROOT>/` (this Megalodon project directory)
- (Add any project-specific restrictions: e.g., do not run loaders, do not invoke build scripts, etc.)

## Permissions update

If workers will read paths outside `<PROJECT_ROOT>/` (this Megalodon directory), add Read allows to `.claude/settings.json`:
```
"Read(<SOURCE_PROJECT_ABSOLUTE_PATH>/**)"
```

## Deliverable

&lt;What the orchestrator/user will do with this run's findings. E.g., manually promote selected findings to source-project verifications/, compile a remediation memo, generate a delivery package, etc.&gt;

## Mission-specific subagent guidance

(Optional: any task types that benefit from specific subagent dispatch patterns)

---

## Pre-deployment checklist

- [ ] Source project path filled in
- [ ] Scope clear (in/out)
- [ ] Lanes defined (matches STATUS.md rows)
- [ ] Cadence decided
- [ ] Pointers listed
- [ ] Hard constraints stated
- [ ] `.claude/settings.json` Read allows added for source project
- [ ] TASKS.md seeded with initial lane-tagged tasks
- [ ] README.md Mission status changed from IDLE to ACTIVE
