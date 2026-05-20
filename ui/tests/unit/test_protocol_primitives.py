"""Unit tests for Megalodon protocol primitives (no server required).

Test IDs from findings/agent-9265-E-P1-test-plan-2026-05-16T15-33Z.md §2 and
findings/agent-9265-E-P2.5-test-plan-v2-2026-05-16T15-44Z.md §"Updated test
inventory".

These exercise pure functions that BACKEND lane (P3-C) must expose as
importable modules per testability requirement B.1.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import re

import pytest


# Importability check — must succeed once BACKEND publishes its module.
# Until then these tests skip cleanly so CI doesn't redbar on absent code.
try:
    from megalodon_ui import primitives  # type: ignore[import-not-found]

    BACKEND_AVAILABLE = True
except ImportError:
    primitives = None  # type: ignore[assignment]
    BACKEND_AVAILABLE = False


pytestmark = pytest.mark.unit


# ---------- RULE 1: heartbeat staleness ----------


@pytest.mark.skipif(not BACKEND_AVAILABLE, reason="awaits P3-C megalodon_ui.primitives")
def test_R1_a_stale_age_computation():
    """T-R1-a — Last UTC age computation; >15 min returns True for stale."""
    now = datetime(2026, 5, 16, 15, 30, tzinfo=timezone.utc)
    last_utc = (now - timedelta(minutes=16)).strftime("%Y-%m-%dT%H:%MZ")
    assert primitives.is_stale(last_utc, now=now) is True
    fresh = (now - timedelta(minutes=14)).strftime("%Y-%m-%dT%H:%MZ")
    assert primitives.is_stale(fresh, now=now) is False


# ---------- RULE 2: atomic mkdir claim ----------


@pytest.mark.skipif(not BACKEND_AVAILABLE, reason="awaits P3-C megalodon_ui.primitives")
def test_R2_a_mkdir_claim_uses_mkdir(tmp_path):
    """T-R2-a — UI claim action uses mkdir, not file write."""
    claims = tmp_path / "claims"
    claims.mkdir()
    assert primitives.try_claim(claims, "T1") is True
    assert (claims / "T1").is_dir()
    # second claim must fail without touching the existing dir
    assert primitives.try_claim(claims, "T1") is False


@pytest.mark.skipif(not BACKEND_AVAILABLE, reason="awaits P3-C megalodon_ui.primitives")
def test_R2_b_canonical_claim_dir_naming(tmp_path):
    """T-R2-b — task IDs with `→` are normalized to ASCII `-to-` before mkdir.

    Source: P2.5-E response to META CHALLENGE-2 (5-source BLOCKING quorum on
    encoding-mutex defect).
    """
    claims = tmp_path / "claims"
    claims.mkdir()
    primitives.try_claim(claims, "P2-E→D")  # Unicode arrow
    assert (claims / "P2-E-to-D").is_dir()
    assert not (claims / "P2-E→D").exists()


# ---------- RULE 4: SIGNALs cite evidence ----------


@pytest.mark.skipif(not BACKEND_AVAILABLE, reason="awaits P3-C megalodon_ui.primitives")
def test_R4_a_signal_validator_rejects_no_cite():
    """T-R4-a — signal payload without `path:line` evidence is invalid."""
    with pytest.raises(ValueError, match="evidence"):
        primitives.validate_signal({"text": "claim", "cite": ""})


# ---------- RULE 6: stale-row reclamation ----------


@pytest.mark.skipif(not BACKEND_AVAILABLE, reason="awaits P3-C megalodon_ui.primitives")
def test_R6_a_retroactive_recovery(tmp_path):
    """T-R6-a — finding exists but claim/done missing → recover."""
    (tmp_path / "findings" / "agent-X-A-T1.md").parent.mkdir(parents=True)
    (tmp_path / "findings" / "agent-X-A-T1.md").write_text("---\nlane: A\n---\n")
    (tmp_path / "claims" / "T1").mkdir(parents=True)
    primitives.reclaim_or_recover(tmp_path, "T1", "agent-X")
    assert (tmp_path / "claims" / "T1" / "done").exists()


@pytest.mark.skipif(not BACKEND_AVAILABLE, reason="awaits P3-C megalodon_ui.primitives")
def test_R6_b_stale_reclaim_no_finding(tmp_path):
    """T-R6-b — finding absent → STALE-RECLAIMED + rm -rf claim dir."""
    (tmp_path / "claims" / "T1").mkdir(parents=True)
    primitives.reclaim_or_recover(tmp_path, "T1", "agent-X")
    assert not (tmp_path / "claims" / "T1").exists()


# ---------- RULE 10: atomic completion four-step ----------


@pytest.mark.skipif(not BACKEND_AVAILABLE, reason="awaits P3-C megalodon_ui.primitives")
def test_R10_a_atomic_completion_block(tmp_path):
    """T-R10-a — single mark_complete call performs all 4 RULE-10 steps."""
    # setup
    (tmp_path / "findings" / "agent-X-A-T1-2026.md").parent.mkdir(parents=True)
    (tmp_path / "findings" / "agent-X-A-T1-2026.md").write_text("")
    (tmp_path / "claims" / "T1").mkdir(parents=True)
    (tmp_path / "TASKS.md").write_text("- [ ] [LANE-A] `T1` — x\n")
    (tmp_path / "HISTORY.md").write_text("# History\n")
    (tmp_path / "STATUS.md").write_text(
        "| Lane | Agent | State | Last UTC | Notes |\n"
        "|---|---|---|---|---|\n"
        "| LANE-A | agent-X | working: T1 | 2026-05-16T15:00Z | x |\n"
    )

    primitives.mark_complete(
        tmp_path,
        task_id="T1",
        agent="agent-X",
        lane="LANE-A",
        finding="findings/agent-X-A-T1-2026.md",
        severity="MINOR",
    )

    assert (tmp_path / "claims" / "T1" / "done").exists()
    assert "[done: agent-X" in (tmp_path / "TASKS.md").read_text()
    assert "| agent-X | LANE-A | T1 |" in (tmp_path / "HISTORY.md").read_text()
    assert "idle" in (tmp_path / "STATUS.md").read_text()


# ---------- RULE 11: phase-flip mkdir lock ----------


@pytest.mark.skipif(not BACKEND_AVAILABLE, reason="awaits P3-C megalodon_ui.primitives")
def test_R11_a_phase_flip_winner_full_sequence(tmp_path):
    """T-R11-a — mkdir wins, all 4 post-mkdir steps complete."""
    setup_phase_flip_test(tmp_path, phase="PHASE-PLAN")
    won = primitives.try_phase_flip(
        tmp_path, "PHASE-PLAN", "PHASE-CHALLENGE", "agent-X"
    )
    assert won is True
    last_event = (tmp_path / ".mission-events").read_text().strip().splitlines()[-1]
    assert "PHASE-PLAN->PHASE-CHALLENGE" in last_event
    assert "PHASE-CHALLENGE" in (tmp_path / "README.md").read_text()


@pytest.mark.skipif(not BACKEND_AVAILABLE, reason="awaits P3-C megalodon_ui.primitives")
def test_R11_b_phase_flip_loser_no_op(tmp_path):
    """T-R11-b — mkdir loses (peer holds lock); flip is no-op."""
    setup_phase_flip_test(tmp_path, phase="PHASE-PLAN")
    (tmp_path / ".phase-flip-locks" / "PHASE-PLAN-to-PHASE-CHALLENGE").mkdir()
    won = primitives.try_phase_flip(
        tmp_path, "PHASE-PLAN", "PHASE-CHALLENGE", "agent-X"
    )
    assert won is False
    # .mission-events untouched
    last_event = (tmp_path / ".mission-events").read_text().strip().splitlines()[-1]
    assert "INIT->PHASE-PLAN" in last_event


@pytest.mark.skipif(not BACKEND_AVAILABLE, reason="awaits P3-C megalodon_ui.primitives")
def test_R11_c_crash_during_flip_recovery(tmp_path):
    """T-R11-c (NEW) — crash between mkdir and .mission-events append; verify recovery.

    Source: P2.5-E response to META CHALLENGE-1. This test SHOULD FAIL initially
    because v7 protocol has no recovery; the failure is itself a v8-changeset
    signal to AUDIT (RULE 11 step 4a per P2.5-E).
    """
    setup_phase_flip_test(tmp_path, phase="PHASE-PLAN")
    # Simulate crash: lock exists, .mission-events still says PHASE-PLAN.
    lock_dir = tmp_path / ".phase-flip-locks" / "PHASE-PLAN-to-PHASE-CHALLENGE"
    lock_dir.mkdir()
    # Backdate lock mtime to 5 min before fictional now (was a test bug:
    # without os.utime, lock mtime = real-time, detector saw lock "in future"
    # vs fictional now and no-op'd; P3-E Stage 2b fix per agent-43d9).
    old_ts = datetime(2026, 5, 16, 15, 30, tzinfo=timezone.utc).timestamp()
    os.utime(lock_dir, (old_ts, old_ts))
    primitives.detect_and_recover_stuck_flips(
        tmp_path,
        now=datetime(2026, 5, 16, 15, 35, tzinfo=timezone.utc),
        stuck_after_seconds=60,
    )
    # After recovery, .mission-events should reflect the flip.
    last = (tmp_path / ".mission-events").read_text().strip().splitlines()[-1]
    assert "PHASE-PLAN->PHASE-CHALLENGE" in last
    assert "RECOVERY" in last or "recovered" in last.lower()


@pytest.mark.skipif(not BACKEND_AVAILABLE, reason="awaits P3-C megalodon_ui.primitives")
def test_R11_d_recovery_no_op_when_lock_holder_progressing(tmp_path):
    """T-R11-d (NEW per META P2-F-to-E CH-1) — Edit-14 false-positive guard.

    Source: live evidence at run-2 .mission-events:3-5 — Edit-14 fired after
    77s because canonical winner's mkdir+append spanned a cron-paused interval;
    no owner-id contract meant the detector could not distinguish 'lock is
    progressing' from 'lock is abandoned'.

    Setup: simulate a lock-holder mid-append (mkdir done, .mission-events not
    yet updated, owner-trace exists, holder's STATUS row is fresh). Detector
    must NOT remkdir nor append a spurious RECOVERY event.

    Requires BACKEND P3-C to ship the v8.1-candidate owner.txt contract:
    try_phase_flip writes <lock>/owner.txt at acquisition; detect_and_recover_stuck_flips
    reads owner.txt + checks holder STATUS freshness before recovering.
    """
    setup_phase_flip_test(tmp_path, phase="PHASE-PLAN")
    lock_dir = tmp_path / ".phase-flip-locks" / "PHASE-PLAN-to-PHASE-CHALLENGE"
    lock_dir.mkdir()
    # Backdate lock mtime so detector treats it as "stuck-age >60s" (otherwise
    # test passes vacuously: detector sees lock too young to recover regardless
    # of owner-trace presence). Same fix as test_R11_c.
    old_ts = datetime(2026, 5, 16, 15, 30, tzinfo=timezone.utc).timestamp()
    os.utime(lock_dir, (old_ts, old_ts))
    # v8.1-candidate owner trace: written atomically by lock acquirer.
    (lock_dir / "owner.txt").write_text("agent-X 2026-05-16T15:30:00Z\n")
    # Holder's STATUS row is fresh (Last UTC just now → not stale).
    (tmp_path / "STATUS.md").write_text(
        "| Lane | Agent | State | Last UTC | Notes |\n"
        "|---|---|---|---|---|\n"
        "| LANE-A | agent-X | working: PHASE-FLIP | 2026-05-16T15:30:00Z | acquired lock; appending event |\n"
    )
    # Lock age > stuck_after_seconds, BUT owner trace + fresh holder STATUS
    # means recovery MUST NOT fire.
    primitives.detect_and_recover_stuck_flips(
        tmp_path,
        now=datetime(2026, 5, 16, 15, 32, tzinfo=timezone.utc),
        stuck_after_seconds=60,
    )
    # Invariant: .mission-events still has only INIT entry (no spurious RECOVERY).
    events = (tmp_path / ".mission-events").read_text().strip().splitlines()
    assert len(events) == 1, f"expected 1 event, got {len(events)}: {events}"
    assert "INIT->PHASE-PLAN" in events[0]
    # Invariant: lock dir still present (not removed by false-positive recovery).
    assert lock_dir.exists()


def setup_phase_flip_test(root: Path, phase: str):
    (root / ".phase-flip-locks").mkdir()
    (root / ".mission-events").write_text(
        f"2026-05-16T15:14:00Z INIT->{phase} by orchestrator — start\n"
    )
    (root / "README.md").write_text(
        f"# Megalodon\n\n## Mission status\n\n**Current: {phase}**\n"
    )
    (root / "claims").mkdir()
    (root / "findings").mkdir()


# ---------- HISTORY format drift validator (CHALLENGE-3 response) ----------


# Note: no skipif — this test has zero `primitives` dependency (regex built
# inline, asserts on string literals). Per P2.5-E §A5 CH-5 verification.
def test_V_HIST_validator_canonical_lines():
    """T-V-HIST-validator — HISTORY.md lines match canonical regex."""
    canonical = re.compile(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z \| agent-[0-9a-f]{4} \| LANE-[A-F] \| "
        r"[A-Z][\w.→\-]+ \| .+\.md \| (BLOCKING|MAJOR|MINOR|NIT|DELTA|RECOVERY)$"
    )
    good = "2026-05-16T15:30Z | agent-1234 | LANE-A | P1-A | findings/x.md | MAJOR"
    drift1 = "2026-05-16T15:30Z | agent-1234 | F | P1-A | findings/x.md | MAJOR"  # short form
    drift2 = "2026-05-16T15:30Z | agent-1234 | FRONTEND | P1-A | findings/x.md | MAJOR"  # long form
    assert canonical.match(good)
    assert not canonical.match(drift1)
    assert not canonical.match(drift2)


# ---------- Severity quorum math (TIER 2 §"Severity escalation") ----------


@pytest.mark.skipif(not BACKEND_AVAILABLE, reason="awaits P3-C megalodon_ui.primitives")
def test_severity_escalation_minor_to_major_with_one_peer():
    """MINOR → MAJOR with 1 peer's Pass-1 independent finding (README.md:138)."""
    findings = [
        {"severity": "MINOR", "artifact": "x.py:42", "agent": "agent-1", "pass": 1},
        {"severity": "MINOR", "artifact": "x.py:42", "agent": "agent-2", "pass": 1},
    ]
    assert primitives.compute_effective_severity(findings) == "MAJOR"


@pytest.mark.skipif(not BACKEND_AVAILABLE, reason="awaits P3-C megalodon_ui.primitives")
def test_severity_escalation_major_to_blocking_with_two_independent():
    """MAJOR → BLOCKING with 2+ INDEPENDENT lanes' Pass-1 (README.md:139)."""
    findings = [
        {
            "severity": "MAJOR",
            "artifact": "x.py:42",
            "agent": "a",
            "lane": "A",
            "pass": 1,
        },
        {
            "severity": "MAJOR",
            "artifact": "x.py:42",
            "agent": "b",
            "lane": "B",
            "pass": 1,
        },
        {
            "severity": "MAJOR",
            "artifact": "x.py:42",
            "agent": "c",
            "lane": "C",
            "pass": 1,
        },
    ]
    assert primitives.compute_effective_severity(findings) == "BLOCKING"


@pytest.mark.skipif(not BACKEND_AVAILABLE, reason="awaits P3-C megalodon_ui.primitives")
def test_severity_ack_verified_does_not_count_toward_quorum():
    """ACK-VERIFIED responses do NOT count toward quorum (README.md:139)."""
    findings = [
        {
            "severity": "MAJOR",
            "artifact": "x.py:42",
            "agent": "a",
            "lane": "A",
            "pass": 1,
        },
        {
            "severity": "MAJOR",
            "artifact": "x.py:42",
            "agent": "b",
            "lane": "B",
            "pass": 2,
            "type": "ACK-VERIFIED",
        },
    ]
    assert primitives.compute_effective_severity(findings) == "MAJOR"  # not BLOCKING
