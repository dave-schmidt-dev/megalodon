"""Dynamic regex pattern builder for MissionConfig.

PM-8: Phase header regex must sort phases length-descending before alternation
so that a longer phase name like ``PHASE-AUDIT-EXTENDED`` is tried before the
shorter ``PHASE-A``.  Without this, the alternation engine would match
``PHASE-A`` inside ``PHASE-AUDIT-EXTENDED`` and capture the wrong group.
"""

from __future__ import annotations

import re

from .schema import MissionConfig


def build_lane_re(config: MissionConfig) -> re.Pattern:
    """`^(LANE1|LANE2|...)$` — alternation of config.lanes[*].name."""
    names = "|".join(re.escape(lane.name) for lane in config.lanes)
    return re.compile(rf"^({names})$")


def build_lane_short_charclass(config: MissionConfig) -> str:
    """Returns the *string form* of a regex character class for lane short codes.

    For ≤26 lanes (all 1-char codes A..Z): returns ``[A-F]``-style range when
    codes are a contiguous prefix A,B,C,..., else explicit set like ``[ACFG]``.
    For >26 lanes (some 2-char codes): returns alternation ``(A|B|...|AA|AB)`` —
    char class can't span 2-char tokens. Sort 1-char codes before 2-char; within
    each group, declaration order.
    """
    shorts = [lane.short for lane in config.lanes]
    one_char = [s for s in shorts if len(s) == 1]
    two_char = [s for s in shorts if len(s) == 2]

    if two_char:
        # Alternation: 1-char codes first (declaration order), then 2-char
        all_ordered = one_char + two_char
        return "(" + "|".join(all_ordered) + ")"

    # All 1-char: check for contiguous prefix A, B, C, ...
    if not one_char:
        return "[]"  # degenerate; shouldn't happen per schema

    sorted_codes = sorted(one_char)
    is_contiguous = all(
        ord(sorted_codes[i]) == ord("A") + i for i in range(len(sorted_codes))
    )
    if is_contiguous:
        last = sorted_codes[-1]
        return f"[A-{last}]"
    else:
        return "[" + "".join(sorted_codes) + "]"


def build_task_line_re(config: MissionConfig) -> re.Pattern:
    """Per server.py:131 shape, but ``(?P<lane>...)`` uses build_lane_short_charclass
    output. Compiled with re.MULTILINE."""
    charclass = build_lane_short_charclass(config)
    pattern = (
        r"^\s*-\s*\[(?P<state_block>[^\]]*)\]\s*\[LANE-(?P<lane>"
        + charclass
        + r")\]\s*"
        r"`(?P<task_id>[^`]+)`\s*(?:[—-]\s*(?P<description>.*))?$"
    )
    return re.compile(pattern, re.MULTILINE)


def build_status_row_re(config: MissionConfig) -> re.Pattern:
    """Per server.py:64 shape. The lane group keeps the v9.0 pattern
    ``[A-Z][A-Z\\- ]*?`` — config doesn't change this because it captures the
    *long* lane name from a markdown table row and downstream parsing already
    handles whitespace/dash. Compiled with re.MULTILINE."""
    pattern = (
        r"^\|\s*(?P<lane>[A-Z][A-Z\- ]*?)\s*\|\s*"
        r"(?P<agent>[^|]+?)\s*\|\s*"
        r"(?P<state>[^|]+?)\s*\|\s*"
        r"(?P<last_utc>[^|]+?)\s*\|\s*"
        r"(?P<notes>.*?)\s*\|\s*$"
    )
    return re.compile(pattern, re.MULTILINE)


def build_task_id_re(config: MissionConfig) -> re.Pattern:
    """`^(<config.task_id_patterns.patterns alternation>)$`.

    Each pattern's leading ``^`` and trailing ``$`` (if present) are stripped
    before alternation to avoid ``^(^a$|^b$)$`` malformation.
    """
    stripped = []
    for p in config.task_id_patterns.patterns:
        core = p
        if core.startswith("^"):
            core = core[1:]
        if core.endswith("$"):
            core = core[:-1]
        stripped.append(core)
    combined = "|".join(stripped)
    return re.compile(rf"^({combined})$")


def build_phase_header_re(config: MissionConfig) -> re.Pattern:
    """`^##\\s+(?P<phase>PHASE1|PHASE2|...)$` — PM-8 sorts phases
    length-descending so ``PHASE-A`` doesn't pre-empt ``PHASE-AUDIT``.
    Compiled with re.MULTILINE."""
    phases_sorted = sorted(config.phases, key=lambda p: len(p), reverse=True)
    # Phase names are constrained by schema to ^[A-Z][A-Z0-9_-]*$ — no regex
    # metacharacters that need escaping in an alternation context.
    alternation = "|".join(phases_sorted)
    pattern = rf"^##\s+(?P<phase>{alternation})$"
    return re.compile(pattern, re.MULTILINE)
