"""V9 M1 — tests for scripts/migrate_claims_to_owner_txt.py (CR-6).

Verifies idempotency + inference from STATUS.md/HISTORY.md + dry-run.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts import migrate_claims_to_owner_txt as mig


def test_skips_claims_with_existing_owner(tmp_path):
    mission = tmp_path / "m"
    (mission / "claims" / "P1-A").mkdir(parents=True)
    (mission / "claims" / "P1-A" / "owner.txt").write_text(
        "agent-existing 2026-01-01T00:00:00Z\n"
    )
    n = mig.migrate(mission)
    assert n == 0
    assert (mission / "claims" / "P1-A" / "owner.txt").read_text().startswith(
        "agent-existing"
    )


def test_creates_owner_for_orphan_claim(tmp_path):
    mission = tmp_path / "m"
    (mission / "claims" / "P1-A").mkdir(parents=True)
    n = mig.migrate(mission, default_owner="legacy-pre-v9")
    assert n == 1
    content = (mission / "claims" / "P1-A" / "owner.txt").read_text()
    assert "legacy-pre-v9" in content


def test_idempotent_re_run_is_noop(tmp_path):
    mission = tmp_path / "m"
    (mission / "claims" / "P1-A").mkdir(parents=True)
    mig.migrate(mission)
    n2 = mig.migrate(mission)
    assert n2 == 0


def test_dry_run_writes_nothing(tmp_path):
    mission = tmp_path / "m"
    (mission / "claims" / "P1-A").mkdir(parents=True)
    n = mig.migrate(mission, dry_run=True)
    assert n == 1  # would have written
    assert not (mission / "claims" / "P1-A" / "owner.txt").exists()


def test_infers_owner_from_status_md(tmp_path):
    mission = tmp_path / "m"
    (mission / "claims" / "P1-A").mkdir(parents=True)
    (mission / "STATUS.md").write_text(
        "| AUDIT | agent-aaaa | working: P1-A | 2026-01-01T00:00:00Z | foo |\n"
    )
    mig.migrate(mission)
    content = (mission / "claims" / "P1-A" / "owner.txt").read_text()
    assert "agent-aaaa" in content


def test_handles_missing_claims_dir(tmp_path):
    mission = tmp_path / "m"
    mission.mkdir()
    n = mig.migrate(mission)
    assert n == 0
