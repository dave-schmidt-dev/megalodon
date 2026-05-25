"""Task 3.3 migration-audit (PM-3 / SR-4) — the critical safety test.

Proves that real, previously-operator-approved patterns still ALLOW what they
used to, now that the consumer of ``.fleet/approval-rules.json`` is the governor
(``policy.decide``) instead of the removed ``--allowedTools`` allowlist.

Corpus: the actual seeded patterns from the archived v94 dogfood approval-rules
file, ``.archive/2026-05-22T19-50Z--v94-ui-dogfood/.fleet/approval-rules.json``
(read at test time). For each pattern we write the file into a tmp
``project_dir/.fleet/`` and call ``decide`` with a representative command, then
assert the result is ``allow`` — whether via default-allow (bounded commands like
``cat``/``ls``/``find`` with no -exec) OR via the operator ``allow-override`` (for
heads the governor would otherwise deny, e.g. ``pytest``/``uv``).

It ALSO asserts the hard floor holds: even with a permissive matching rule
present, a floor deny (root-destructive, privilege, secret-read) is NOT
overridable. This is the migration safety net — assertions are real and specific.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from megalodon_ui.governor import policy

REPO_ROOT = Path(__file__).resolve().parents[2]
ARCHIVED_RULES = (
    REPO_ROOT
    / ".archive"
    / "2026-05-22T19-50Z--v94-ui-dogfood"
    / ".fleet"
    / "approval-rules.json"
)
LANE = "A"


def _write_rules(project_dir: Path, patterns: list[str]) -> None:
    fleet = project_dir / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    (fleet / "approval-rules.json").write_text(
        json.dumps([{"pattern": p} for p in patterns]), encoding="utf-8"
    )


def _load_archived_patterns() -> list[str]:
    raw = json.loads(ARCHIVED_RULES.read_text(encoding="utf-8"))
    return [e["pattern"] for e in raw if isinstance(e, dict) and "pattern" in e]


# Representative command (or native-tool args) for each archived pattern.
# Each entry: pattern -> (tool_name, tool_input).
# Sourced from .archive/2026-05-22T19-50Z--v94-ui-dogfood/.fleet/approval-rules.json
_REPRESENTATIVE: dict[str, tuple[str, dict]] = {
    "Bash(scripts/poll.py:*)": ("Bash", {"command": "scripts/poll.py --status"}),
    "Bash(scripts/atomic_close.py:*)": (
        "Bash",
        {"command": "scripts/atomic_close.py T-1"},
    ),
    "Bash(scripts/run_e2e.sh:*)": ("Bash", {"command": "scripts/run_e2e.sh"}),
    "Bash(pytest:*)": ("Bash", {"command": "pytest -q tests/"}),
    "Bash(uv:*)": ("Bash", {"command": "uv run pytest"}),
    "Bash(find:*)": ("Bash", {"command": "find . -name '*.py'"}),
    "Bash(grep:*)": ("Bash", {"command": "grep -rn TODO ."}),
    "Bash(cat:*)": ("Bash", {"command": "cat README.md"}),
    "Bash(ls:*)": ("Bash", {"command": "ls -la"}),
    "Bash(head:*)": ("Bash", {"command": "head -20 README.md"}),
    "Bash(tail:*)": ("Bash", {"command": "tail -20 README.md"}),
    "Bash(wc:*)": ("Bash", {"command": "wc -l README.md"}),
    "Read": ("Read", {"file_path": "README.md"}),
    "Grep": ("Grep", {"pattern": "foo", "path": "."}),
    "Glob": ("Glob", {"pattern": "**/*.py"}),
}


def test_archived_corpus_matches_representatives():
    """Guard: every archived pattern has a representative command (and vice
    versa) so a future change to the archived file fails loudly here rather
    than silently skipping coverage."""
    archived = set(_load_archived_patterns())
    represented = set(_REPRESENTATIVE)
    assert archived == represented, (
        f"archived corpus drifted from representatives.\n"
        f"  in archive only: {sorted(archived - represented)}\n"
        f"  in test only:    {sorted(represented - archived)}"
    )


@pytest.mark.parametrize("pattern", list(_REPRESENTATIVE))
def test_archived_pattern_still_allows(pattern: str, tmp_path: Path):
    """Each previously-approved pattern still resolves to ALLOW under the
    governor — proving the migration from --allowedTools to the allow-override
    preserves what operators had approved."""
    # Make the path-based representatives resolve inside project_dir so Read /
    # path heuristics don't deny on scope.
    _write_rules(tmp_path, _load_archived_patterns())
    (tmp_path / "README.md").write_text("hi\n", encoding="utf-8")

    tool_name, tool_input = _REPRESENTATIVE[pattern]
    # Resolve relative path args against the tmp project_dir.
    tool_input = dict(tool_input)
    if "file_path" in tool_input:
        tool_input["file_path"] = str(tmp_path / tool_input["file_path"])
    if tool_input.get("path") == ".":
        tool_input["path"] = str(tmp_path)

    decision = policy.decide(tool_name, tool_input, project_dir=tmp_path, lane=LANE)
    assert decision.permission == "allow", (
        f"pattern {pattern!r} (cmd={tool_input}) regressed to "
        f"{decision.permission}/{decision.category}: {decision.reason}"
    )
    # Bash allows are either bounded-by-default or via the operator override.
    if tool_name == "Bash":
        assert decision.category in ("bash-ok", "allow-override"), (
            f"unexpected allow category for {pattern!r}: {decision.category}"
        )


def test_override_is_load_bearing_for_interpreters(tmp_path: Path):
    """The interpreter patterns (pytest, uv) are NOT default-allowed: without the
    approval-rules file they DENY (bash-interpreter), and only the operator
    allow-override flips them. This proves the override actually does work — not
    that everything happens to be allowed anyway."""
    (tmp_path / ".fleet").mkdir(parents=True, exist_ok=True)  # no rules file
    for cmd in ("pytest -q", "uv run pytest"):
        d = policy.decide("Bash", {"command": cmd}, project_dir=tmp_path, lane=LANE)
        assert d.permission == "deny" and d.category == "bash-interpreter", (
            f"expected interpreter deny without rules for {cmd!r}, got "
            f"{d.permission}/{d.category}"
        )

    # With the archived rules present, the same commands flip to allow-override.
    _write_rules(tmp_path, _load_archived_patterns())
    for cmd in ("pytest -q", "uv run pytest"):
        d = policy.decide("Bash", {"command": cmd}, project_dir=tmp_path, lane=LANE)
        assert d.permission == "allow" and d.category == "allow-override", (
            f"expected allow-override with rules for {cmd!r}, got "
            f"{d.permission}/{d.category}"
        )


@pytest.mark.parametrize(
    ("cmd", "expected_category"),
    [
        ("sudo rm -rf /", "bash-privilege"),
        ("rm -rf /", "bash-root-destructive"),
        ("cat ~/.ssh/id_rsa", "secret-read"),
    ],
)
def test_floor_deny_is_not_overridable(
    cmd: str, expected_category: str, tmp_path: Path
):
    """A floor deny stays DENY even when a permissive approval-rule that WOULD
    match its head is present. The escape hatch can never lift the hard floor."""
    # Seed rules that would match each floor head if overrides applied to floors.
    _write_rules(
        tmp_path,
        ["Bash(sudo:*)", "Bash(rm:*)", "Bash(cat:*)"],
    )
    decision = policy.decide("Bash", {"command": cmd}, project_dir=tmp_path, lane=LANE)
    assert decision.permission == "deny", (
        f"floor command {cmd!r} was wrongly allowed: "
        f"{decision.category}: {decision.reason}"
    )
    assert decision.category == expected_category, (
        f"floor command {cmd!r} expected category {expected_category}, "
        f"got {decision.category}"
    )
    assert decision.category in policy._FLOOR_CATEGORIES
