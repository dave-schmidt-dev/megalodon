# V9 SIGNAL Grammar

SIGNALs are findings-class artifacts that carry cross-agent or operator-facing
directives. v9 codifies what run-2 evolved organically (SIG-ORCH-1 ..
SIG-ORCH-6).

## Frontmatter (required)

```yaml
---
signal-type: <SIG-ORCH-N | SIG-LANE-X | WATCHDOG-ALERT | OPERATOR-DIRECTIVE>
addressed-to: <operator | all-lanes | <SPECIFIC-LANE>>
severity: <TIER-1 | TIER-2 | MAJOR | MINOR | INFO>
utc: <ISO-8601-UTC>
related-findings:
  - <path/to/finding-1.md>
expected-ack: <one-line description of what ACK looks like>
agent: <source-agent-id-or-name>
idempotency-key: <sha1-of-signal-content>
---
```

The single load-bearing field is `signal-type`. The parser at
`megalodon_ui/signal_parser.py:parse_signal` returns the frontmatter dict iff
that key is present.

## Routing

| signal-type        | Source              | Targets                       |
|--------------------|---------------------|-------------------------------|
| SIG-ORCH-N         | orchestrator-Claude | all-lanes or specific         |
| SIG-LANE-X         | worker              | peer lane (cross-lane handoff) |
| WATCHDOG-ALERT     | watchdog            | operator                      |
| OPERATOR-DIRECTIVE | operator            | all-lanes or specific         |

## Idempotency

`idempotency-key` lets workers detect re-issued SIGNALs and skip
double-processing. SIGNALs with identical `idempotency-key` + `addressed-to`
are no-ops on re-read. Compute the key as the SHA1 of the canonical signal
body (excluding `utc` and `agent`) so re-issuance preserves identity.

## File naming

`findings/<signal-type>-<NNN>-<topic>-<utc>.md` — e.g.,
`findings/SIG-ORCH-001-queue-required-2026-05-16T18-43Z.md` or
`findings/watchdog-ALERT-AUDIT-2026-05-17T00-30Z.md`.

## ACK convention

ACKing a SIGNAL means:

1. Mention the SIGNAL filename in the ACK'er's next tick STATUS Notes OR
   finding.
2. State what action (if any) was taken.
3. If action deferred, state when.

ACK MUST cite evidence per RULE 4 (`path:line` or `path:section`).

## Parser semantics (megalodon_ui/signal_parser.py)

- Reads the frontmatter delimited by `---\n` ... `---\n` at file start.
- Returns the parsed dict iff `signal-type` is present.
- Returns `None` on missing frontmatter, non-dict frontmatter, malformed
  YAML, or absent `signal-type` key.
- Never raises (OSError, YAMLError are swallowed).

## Cross-references

- `launch.md` — workers MUST address operator-routed SIGNALs within the cadence
  budget of their lane.
- `docs/v9/V9-ROADMAP.md` §A8 — origin story.
- `scripts/start_watchdog.sh` + RULE 16 — WATCHDOG-ALERT emitter.
