"""Read-only parser for v9.0 HISTORY.md format variants (CV-10, CV-12).

# SUNSET: when .archive/* HISTORY.md formats are all migrated to canonical
# v9.1 format (scripts/_backends/_history_format.format_history_line).

This module exists for back-compat with archived missions only. New writes
must use format_history_line. The 4 variants documented below describe the
shapes encountered in v9.0 production missions.

Variant 1 — LANE-{short} prefix (real v9.0 run-2 drift):
    ``{utc} | {agent} | LANE-{short} | {task_id} | {finding} | {severity}``
    Example: ``2026-05-16T17:38Z | agent-fec0 | LANE-B | P1-B | findings/x.md | DELTA``
    Some agents prefixed the short lane code with "LANE-" rather than using the
    bare single-letter code.

Variant 2 — bare short code, no notes suffix (real v9.0 run-2 drift):
    ``{utc} | {agent} | {short} | {task_id} | {finding} | {severity}``
    Example: ``2026-05-16T17:39Z | agent-dcbc | A | P1-A | findings/x.md | DELTA``
    Early completion lines used bare lane codes but never included the
    ``({notes})`` suffix that v9.1 canonical format requires.

Variant 3 — pipe-spacing drift (synthesized, CV-10):
    Same 6 fields as Variant 2 but with inconsistent whitespace around pipe
    delimiters — some agents emitted no spaces, others added extras.
    Example: ``2026-05-16T17:39Z |agent-dcbc|A|P1-A|findings/x.md|DELTA``

Variant 4 — frontmatter-style YAML-like prefix (synthesized, CV-10):
    ``--- utc: {utc} agent: {agent} lane: {short} task_id: {tid} finding: {path} severity: {sev} ---``
    Early tooling prototypes emitted inline YAML headers rather than the
    pipe-delimited format.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HistoryEntry:
    """A single parsed history line from any legacy or canonical format."""

    utc: str
    agent: str
    lane_short: str  # always normalized to 1-char code; LANE-X stripped, LANE_LONG_TO_SHORT applied
    task_id: str
    finding_path: str
    severity: str
    notes: str = ""  # may be empty if variant lacked the (notes) suffix
    variant: int = 0  # 1-4, indicates which legacy variant matched


# LANE-{short} prefix used in v9.0 run-2 by some agents (Variant 1).
# These are the single-letter codes; the LANE- prefix itself is stripped.
_VALID_SHORT_CODES = {"A", "B", "C", "D", "E", "F"}

# Regex pieces shared across variants.
_UTC_RE = r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?Z)"
_AGENT_RE = r"([\w][\w\-]*)"
_FINDING_RE = r"([^\|]+?)"
_SEVERITY_RE = r"(\w[\w\-]*)"
_TASK_ID_RE = r"([\w][\w\.\-]*(?:\s*\([^)]+\))?)"  # allow parens in task IDs like "P5-RUN... (SUPERSEDES @21:40Z)"
_NOTES_RE = r"\(([^)]*)\)"

# --- Variant 1: LANE-{short} prefix, no notes ---
# e.g. "2026-05-16T17:38Z | agent-fec0 | LANE-B | P1-B | findings/x.md | DELTA"
_V1_RE = re.compile(
    r"^\s*"
    + _UTC_RE
    + r"\s*\|\s*"
    + _AGENT_RE
    + r"\s*\|\s*LANE-([A-Fa-f])\s*\|\s*"
    + r"([\w][\w\.\-]*)"  # task_id (simple, no parens)
    + r"\s*\|\s*"
    + _FINDING_RE
    + r"\|\s*"
    + _SEVERITY_RE
    + r"\s*$"
)

# --- Variant 2: bare short code, no notes ---
# e.g. "2026-05-16T17:39Z | agent-dcbc | A | P1-A | findings/x.md | DELTA"
# Strict: pipes must be surrounded by at least one space on each side (well-spaced).
# Uses " | " separators to distinguish from Variant 3 (spacing drift).
_V2_RE = re.compile(
    r"^\s*"
    + _UTC_RE
    + r" \| "  # space-pipe-space (well-spaced)
    + _AGENT_RE
    + r" \| ([A-Fa-f]) \| "  # space-pipe-space lane space-pipe-space
    + r"([\w][\w\.\-]*)"  # task_id
    + r" \| "
    + _FINDING_RE
    + r"\| "  # finding ends before pipe; there may be no trailing space before pipe
    + _SEVERITY_RE
    + r"\s*$"
)

# --- Variant 3: pipe-spacing drift (whitespace-tolerant pipe split) ---
# Same 6 fields but pipes may have zero or many spaces around them.
# We reuse a flexible splitter rather than a single monolithic regex.
_V3_UTC_START = re.compile(r"^\s*\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")

# --- Variant 4: frontmatter YAML-style ---
# e.g. "--- utc: 2026-... agent: agent-x lane: A task_id: T finding: path severity: SEV ---"
_V4_RE = re.compile(
    r"^---\s+"
    r"utc:\s*(\S+)\s+"
    r"agent:\s*(\S+)\s+"
    r"lane:\s*([A-Fa-f])\s+"
    r"task_id:\s*(\S+)\s+"
    r"finding:\s*(\S+)\s+"
    r"severity:\s*(\S+)\s*"
    r"---\s*$"
)


def _normalize_lane(code: str) -> str:
    """Return upper-cased single-letter lane code."""
    return code.upper()


def _strip_notes(severity_field: str) -> tuple[str, str]:
    """Split ``SEVERITY (notes text)`` into (severity, notes).

    Returns (severity, "") when no parenthesised suffix is present.
    """
    m = re.match(r"^(\w[\w\-]*)\s*\(([^)]*)\)\s*$", severity_field.strip())
    if m:
        return m.group(1), m.group(2)
    return severity_field.strip(), ""


def _parse_variant_1(line: str) -> HistoryEntry | None:
    """LANE-{short} prefix, bare severity (Variant 1)."""
    m = _V1_RE.match(line)
    if not m:
        return None
    utc, agent, short, task_id, finding, severity_raw = m.groups()
    severity, notes = _strip_notes(severity_raw)
    return HistoryEntry(
        utc=utc.strip(),
        agent=agent.strip(),
        lane_short=_normalize_lane(short),
        task_id=task_id.strip(),
        finding_path=finding.strip(),
        severity=severity,
        notes=notes,
        variant=1,
    )


def _parse_variant_2(line: str) -> HistoryEntry | None:
    """Bare single-letter lane code, no notes suffix (Variant 2)."""
    m = _V2_RE.match(line)
    if not m:
        return None
    utc, agent, short, task_id, finding, severity_raw = m.groups()
    severity, notes = _strip_notes(severity_raw)
    return HistoryEntry(
        utc=utc.strip(),
        agent=agent.strip(),
        lane_short=_normalize_lane(short),
        task_id=task_id.strip(),
        finding_path=finding.strip(),
        severity=severity,
        notes=notes,
        variant=2,
    )


def _parse_variant_3(line: str) -> HistoryEntry | None:
    """Pipe-spacing drift: 6 fields, whitespace-flexible (Variant 3)."""
    # Must start with a timestamp; skip if it looks like V1/V2 (those have spaces around pipes).
    if not _V3_UTC_START.match(line):
        return None

    # Split on pipes, strip each field.
    parts = [p.strip() for p in line.split("|")]
    if len(parts) != 6:
        return None

    utc_raw, agent_raw, lane_raw, task_id_raw, finding_raw, severity_raw = parts

    # Validate UTC looks right.
    if not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", utc_raw):
        return None

    # Lane must be a bare single letter or LANE-X; reject if it has spaces that
    # would already have matched V1/V2.  We only take it here if the original line
    # had *no* surrounding spaces for at least one pipe (whitespace drift).
    # Detect drift: the original line has a pipe with no space on either side OR
    # a pipe immediately after a non-space.
    if not re.search(r"\S\||\|\S", line):
        # All pipes are properly spaced — let V1/V2 handle it; avoid double-match.
        return None

    # Lane normalisation: strip LANE- prefix if present.
    lane_raw = lane_raw.strip()
    if re.match(r"^LANE-([A-Fa-f])$", lane_raw, re.IGNORECASE):
        short = lane_raw[-1].upper()
    elif re.match(r"^[A-Fa-f]$", lane_raw):
        short = lane_raw.upper()
    else:
        return None

    if not agent_raw or not task_id_raw or not finding_raw or not severity_raw:
        return None

    severity, notes = _strip_notes(severity_raw)
    return HistoryEntry(
        utc=utc_raw,
        agent=agent_raw,
        lane_short=short,
        task_id=task_id_raw,
        finding_path=finding_raw,
        severity=severity,
        notes=notes,
        variant=3,
    )


def _parse_variant_4(line: str) -> HistoryEntry | None:
    """Frontmatter YAML-style prefix (Variant 4)."""
    m = _V4_RE.match(line.strip())
    if not m:
        return None
    utc, agent, lane, task_id, finding, severity_raw = m.groups()
    severity, notes = _strip_notes(severity_raw)
    return HistoryEntry(
        utc=utc,
        agent=agent,
        lane_short=_normalize_lane(lane),
        task_id=task_id,
        finding_path=finding,
        severity=severity,
        notes=notes,
        variant=4,
    )


def parse_line(line: str) -> HistoryEntry | None:
    """Try each variant in order; return the first match or None.

    Order: Variant 4 (frontmatter, structurally distinct) → Variant 1 (LANE- prefix)
    → Variant 2 (bare short, well-spaced) → Variant 3 (spacing drift fallback).
    Most-specific shapes first to avoid false matches.
    """
    for parser in (_parse_variant_4, _parse_variant_1, _parse_variant_2, _parse_variant_3):
        entry = parser(line)
        if entry is not None:
            return entry
    return None


def parse_file(path: Path) -> list[HistoryEntry]:
    """Parse a HISTORY.md file and return all matched entries.

    Skips blank lines and unmatchable lines silently — the parser is best-effort
    for archived data; corrupted lines just drop.

    Args:
        path: Path to the HISTORY.md (or similar) file to parse.

    Returns:
        List of :class:`HistoryEntry` objects in file order.
    """
    entries: list[HistoryEntry] = []
    text = path.read_text(encoding="utf-8")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        entry = parse_line(line)
        if entry is not None:
            entries.append(entry)
    return entries
