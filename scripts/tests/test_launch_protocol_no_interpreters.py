"""Lint: launch.md must route agent steps through bounded tools only.

Guards the 2026-05-22 tool-surface policy: no python/compound interpreter
invocations in the worker protocol. Fenced 'NEVER …' guard lines are allowed
to NAME a forbidden command (they are prohibitions, not instructions), so we
only scan fenced ```bash blocks for executable invocations.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LAUNCH = REPO / "launch.md"

# Patterns that must not appear as an executable line inside a ```bash block.
FORBIDDEN = [
    re.compile(r"^\s*python3?\s+-[cm]\b"),
    re.compile(r"^\s*python3?\s+-m\b"),
    re.compile(r"^\s*mkdir\s+claims/"),
    re.compile(r"record_tick\s*\("),
    re.compile(r"^\s*curl\b"),
    re.compile(r"&&"),  # no compound chains in fenced agent commands
]


def _bash_block_lines(text: str) -> list[str]:
    lines, in_block = [], False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_block = stripped == "```bash"
            continue
        if in_block:
            lines.append(line)
    return lines


def test_launch_md_has_no_interpreter_invocations():
    text = LAUNCH.read_text(encoding="utf-8")
    offenders = []
    for line in _bash_block_lines(text):
        for pat in FORBIDDEN:
            if pat.search(line):
                offenders.append((pat.pattern, line.strip()))
    assert not offenders, f"forbidden invocations in launch.md bash blocks: {offenders}"


def test_launch_md_references_bounded_tools():
    text = LAUNCH.read_text(encoding="utf-8")
    for tool in [
        "scripts/claim.sh",
        "scripts/queue_submit.py",
        "scripts/run_tests.sh",
        "{{AGENT_ID}}",
    ]:
        assert tool in text, f"launch.md must reference {tool}"


def test_rendered_per_lane_file_is_interpreter_free(tmp_path):
    """Scan the RENDERED launch-<LANE>.md (header + body) agents actually read,
    not just the template (CV-4/CV-7). Confirms the {{AGENT_ID}} placeholder is
    present PRE-bake (spawn.py bakes it later) and no interpreter invocations leak
    in via the gen_lane_launches header.
    """
    import sys

    sys.path.insert(0, str(REPO))
    from scripts.gen_lane_launches import generate_one

    rendered = generate_one("BACKEND", 2, REPO)
    assert "{{AGENT_ID}}" in rendered, "rendered file lost the pre-bake placeholder"
    offenders = []
    for line in _bash_block_lines(rendered):
        for pat in FORBIDDEN:
            if pat.search(line):
                offenders.append((pat.pattern, line.strip()))
    assert not offenders, (
        f"forbidden invocations in rendered launch-BACKEND.md: {offenders}"
    )
