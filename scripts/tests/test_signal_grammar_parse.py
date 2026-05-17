"""V9 A8 — tests for SIGNAL frontmatter parser."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from megalodon_ui.signal_parser import parse_signal


def test_parses_valid_signal(tmp_path):
    p = tmp_path / "f.md"
    p.write_text(
        """---
signal-type: SIG-ORCH-001
addressed-to: all-lanes
severity: TIER-1
utc: 2026-05-17T00:00:00Z
agent: orch
idempotency-key: abc123
---

Body text.
"""
    )
    fm = parse_signal(p)
    assert fm["signal-type"] == "SIG-ORCH-001"
    assert fm["addressed-to"] == "all-lanes"


def test_rejects_non_signal(tmp_path):
    p = tmp_path / "f.md"
    p.write_text(
        """---
lane: AUDIT
severity: MAJOR
---

Just a finding.
"""
    )
    assert parse_signal(p) is None


def test_handles_malformed_frontmatter(tmp_path):
    p = tmp_path / "f.md"
    p.write_text("---\nthis: is: malformed: yaml\n---\nbody\n")
    # Either returns None or doesn't raise.
    result = parse_signal(p)
    assert result is None or isinstance(result, dict)
