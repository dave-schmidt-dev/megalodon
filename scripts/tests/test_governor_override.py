"""Operator allow-override tests for the governor Bash engine (Task P0.9).

The P0 hardening denies a class of generic-exec commands (build/exec runners,
editors). These are deliberately NON-floor categories: an operator can lift them
per-project by dropping a rule into ``<project_dir>/.fleet/approval-rules.json``.
Floor categories (root-destructive, privilege, secret, write-out-of-scope,
anti-tamper, write-secret) are NOT liftable — a broad override rule must never
flip them.

Override file format (verified against ``policy._load_override_patterns`` and
``approval_rules.extract_pattern``): a raw JSON *list* of
``{"pattern": "Bash(<specifier>)"}`` entries, where ``<specifier>`` is the
conservative head form ``<head>:*`` (e.g. ``make:*``) or a literal command
prefix with ``*`` wildcards.

Uses a per-test ``tmp_path`` project dir because it writes into ``.fleet/``.
"""

from __future__ import annotations

import json
from pathlib import Path

from megalodon_ui.governor.policy import decide


def _write_rules(project_dir: Path, patterns: list[str]) -> None:
    """Write a raw-list approval-rules.json with the given Bash(...) patterns."""
    fleet_dir = project_dir / ".fleet"
    fleet_dir.mkdir(parents=True, exist_ok=True)
    rules = [{"pattern": p} for p in patterns]
    (fleet_dir / "approval-rules.json").write_text(json.dumps(rules), encoding="utf-8")


def test_make_denies_by_default(tmp_path: Path) -> None:
    """With no override rules, `make` denies as bash-exec-runner."""
    d = decide("Bash", {"command": "make"}, project_dir=tmp_path, lane="TEST")
    assert d.permission == "deny", f"make should deny: {d.permission}/{d.category}"
    assert d.category == "bash-exec-runner", (
        f"expected bash-exec-runner, got {d.category}"
    )


def test_make_override_lifts_deny_to_allow(tmp_path: Path) -> None:
    """An operator rule `Bash(make:*)` flips the deny to allow (allow-override)."""
    _write_rules(tmp_path, ["Bash(make:*)"])
    d = decide("Bash", {"command": "make"}, project_dir=tmp_path, lane="TEST")
    assert d.permission == "allow", (
        f"override should lift make: {d.permission}/{d.category}"
    )
    assert d.category == "allow-override", f"expected allow-override, got {d.category}"


def test_override_does_not_lift_floor(tmp_path: Path) -> None:
    """NEGATIVE control: a broad override never lifts a FLOOR category.

    `rm -rf /` is bash-root-destructive (a floor). Even with an override rule
    present for `rm` AND a catch-all wildcard, it must STILL deny — floors are
    not overridable.
    """
    _write_rules(tmp_path, ["Bash(rm:*)", "Bash(*)"])
    d = decide("Bash", {"command": "rm -rf /"}, project_dir=tmp_path, lane="TEST")
    assert d.permission == "deny", (
        f"floor must NOT be liftable by override: {d.permission}/{d.category}"
    )
    assert d.category == "bash-root-destructive", (
        f"expected bash-root-destructive floor, got {d.category}"
    )
