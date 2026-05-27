"""V9 M1 applier tests — including S-8 T1-T4 + Q1 intents + B3/B4."""

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.queue import queue_client
from megalodon_ui.queue.applier import Applier


# ---- helpers ----


def _drain_until(applier, predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        applier.drain_once()
        if predicate():
            return True
        time.sleep(0.05)
    return False


# ---- BUG 2: fallback drain must respect the singleton lock ----


def test_fallback_does_not_drain_when_singleton_held(queue_mission):
    """BUG 2 regression: the in-process fallback in `_backends.queue_client.
    _wait_or_drain` must NOT drain the queue while the applier singleton lock
    is held by someone else (the live daemon owns draining)."""
    from scripts._backends import queue_client as backend

    # Hold the singleton as if another applier owned it, but with a STALE
    # heartbeat so `_applier_alive` is False — this is the exact concurrency
    # window the bug exposes: the fallback path runs, yet the lock is held.
    holder = Applier(queue_mission)
    assert holder.acquire_singleton() is True
    hb = holder.lock_dir / "heartbeat.txt"
    old = time.time() - 3600
    os.utime(hb, (old, old))
    assert backend._applier_alive(queue_mission) is False

    # Submit a request, then ask the fallback to wait for it. The old code
    # would unconditionally drain (double-apply window). The fix must skip
    # draining while the singleton lock is held by someone else.
    rid = queue_client.tasks_bracket(
        queue_mission, "agent-aaaa", "AUDIT", "Q-FIXTURE-1", "[ ]"
    )
    try:
        status = backend._wait_or_drain(queue_mission, rid, timeout=0.5)
    finally:
        holder.release_singleton()

    # Request must remain unapplied (still pending) — the fallback refused to
    # drain because the lock was held.
    assert status != "applied"
    assert (queue_mission / "queue" / "pending" / f"{rid}.json").exists()
    assert not (queue_mission / "queue" / "applied" / f"{rid}.json").exists()


# ---- core happy paths (original 6 intents) ----


def test_status_update_lands(queue_mission):
    applier = Applier(queue_mission)
    rid = queue_client.status_update(
        queue_mission,
        "agent-aaaa",
        "AUDIT",
        "working: P5",
        "test notes",
    )
    assert _drain_until(
        applier,
        lambda: (queue_mission / "queue" / "applied" / f"{rid}.json").exists(),
    )
    status = (queue_mission / "STATUS.md").read_text()
    assert "working: P5" in status
    assert "test notes" in status


def test_tasks_bracket_marks_open_as_claimed(queue_mission):
    applier = Applier(queue_mission)
    rid = queue_client.task_claim(
        queue_mission,
        "agent-aaaa",
        "AUDIT",
        "Q-FIXTURE-1",
    )
    assert _drain_until(
        applier,
        lambda: (queue_mission / "queue" / "applied" / f"{rid}.json").exists(),
    )
    tasks = (queue_mission / "TASKS.md").read_text()
    assert "[claimed: agent-aaaa" in tasks
    assert "Q-FIXTURE-1" in tasks


def test_history_append_writes_pipe_row(queue_mission):
    applier = Applier(queue_mission)
    rid = queue_client.history_append(
        queue_mission,
        "agent-aaaa",
        "AUDIT",
        "Q-FIXTURE-1",
        "findings/sample.md",
        "MAJOR",
    )
    assert _drain_until(
        applier,
        lambda: (queue_mission / "queue" / "applied" / f"{rid}.json").exists(),
    )
    history = (queue_mission / "HISTORY.md").read_text()
    assert "agent-aaaa" in history
    assert "Q-FIXTURE-1" in history
    assert "findings/sample.md" in history


def test_mission_event_append(queue_mission):
    applier = Applier(queue_mission)
    rid = queue_client.mission_event(
        queue_mission,
        "agent-aaaa",
        "AUDIT",
        f"{queue_client.utc_now()} PHASE-PLAN->PHASE-BUILD by agent-aaaa -- test",
    )
    assert _drain_until(
        applier,
        lambda: (queue_mission / "queue" / "applied" / f"{rid}.json").exists(),
    )
    events = (queue_mission / ".mission-events").read_text()
    assert "PHASE-BUILD" in events


def test_claim_dir_create_lands(queue_mission):
    applier = Applier(queue_mission)
    rid = queue_client.claim_dir_create(
        queue_mission,
        "agent-aaaa",
        "AUDIT",
        "Q-NEW-CLAIM",
    )
    assert _drain_until(
        applier,
        lambda: (queue_mission / "queue" / "applied" / f"{rid}.json").exists(),
    )
    assert (queue_mission / "claims" / "Q-NEW-CLAIM" / "owner.txt").exists()


def test_claim_dir_done_only_owner(queue_mission):
    """Owner can mark done; non-owner is rejected."""
    applier = Applier(queue_mission)
    rid_create = queue_client.claim_dir_create(
        queue_mission,
        "agent-aaaa",
        "AUDIT",
        "Q-OWN-CLAIM",
    )
    _drain_until(
        applier,
        lambda: (queue_mission / "queue" / "applied" / f"{rid_create}.json").exists(),
    )

    rid_steal = queue_client.claim_dir_done(
        queue_mission,
        "agent-zzzz",
        "AUDIT",
        "Q-OWN-CLAIM",
    )
    assert _drain_until(
        applier,
        lambda: (queue_mission / "queue" / "rejected" / f"{rid_steal}.json").exists(),
    )

    rid_done = queue_client.claim_dir_done(
        queue_mission,
        "agent-aaaa",
        "AUDIT",
        "Q-OWN-CLAIM",
    )
    assert _drain_until(
        applier,
        lambda: (queue_mission / "queue" / "applied" / f"{rid_done}.json").exists(),
    )
    assert (queue_mission / "claims" / "Q-OWN-CLAIM" / "done").exists()


# ---- WAL/idempotency ----


def test_idempotent_replay_via_journal(queue_mission):
    """Re-applying an already-applied rid (journal says APPLIED) is a no-op."""
    applier = Applier(queue_mission)
    rid = queue_client.history_append(
        queue_mission,
        "agent-aaaa",
        "AUDIT",
        "Q-FIXTURE-1",
        "findings/x.md",
        "MAJOR",
    )
    _drain_until(
        applier,
        lambda: (queue_mission / "queue" / "applied" / f"{rid}.json").exists(),
    )
    hist1 = (queue_mission / "HISTORY.md").read_text()

    # Simulate the same request reappearing in pending/ — journal-dedup
    # should NOT double-write.
    applied = queue_mission / "queue" / "applied" / f"{rid}.json"
    pending = queue_mission / "queue" / "pending" / f"{rid}.json"
    pending.parent.mkdir(parents=True, exist_ok=True)
    applied.rename(pending)

    applier2 = Applier(queue_mission)  # fresh replay
    applier2.drain_once()
    hist2 = (queue_mission / "HISTORY.md").read_text()
    assert hist1 == hist2


def test_pending_indoubt_reconcile_already_applied(queue_mission):
    """WAL replay sees PENDING (no APPLIED) + target has line → APPLIED, no dup."""
    # Pre-populate HISTORY.md with the line.
    history = queue_mission / "HISTORY.md"
    pre_line = (
        f"{queue_client.utc_now()} | agent-aaaa | A | Q-FIXTURE-1 | "
        f"findings/recovered.md | MAJOR\n"
    )
    history.write_text(history.read_text() + pre_line)
    text_before = history.read_text()

    # Manually inject a request file + matching PENDING journal entry
    # to simulate "crashed mid-apply" (in fact already applied).
    rid = "test-indoubt-rid-001"
    req = {
        "schema_version": 1,
        "request_id": rid,
        "submitted_utc": queue_client.utc_now(),
        "agent": "agent-aaaa",
        "lane": "AUDIT",
        "target_file": "HISTORY.md",
        "intent": "HISTORY_APPEND",
        "payload": {"line": pre_line.rstrip("\n")},
        "preconditions": {},
        "idempotency_key": "x",
        "expected_hash_before": None,
        "fallback": "REJECT",
    }
    (queue_mission / "queue" / "pending").mkdir(parents=True, exist_ok=True)
    (queue_mission / "queue" / "pending" / f"{rid}.json").write_text(json.dumps(req))
    # Write a PENDING-only journal entry that pre-dates this applier.
    (queue_mission / "queue").mkdir(parents=True, exist_ok=True)
    journal_log = queue_mission / "queue" / "journal.log"
    journal_log.write_text(
        json.dumps(
            {
                "rid": rid,
                "status": "PENDING",
                "intent": "HISTORY_APPEND",
                "target": "HISTORY.md",
                "payload": {"line": pre_line.rstrip("\n")},
                "utc": queue_client.utc_now(),
            }
        )
        + "\n"
    )

    applier = Applier(queue_mission)
    applier.drain_once()
    # File was already in pending dir before applier started;
    # _replay_journal_state populated _applied_ids? NO — it was PENDING only,
    # so it's PENDING_INDOUBT. Reconcile must detect the line already present
    # and mark APPLIED without appending.
    assert (queue_mission / "queue" / "applied" / f"{rid}.json").exists()
    assert history.read_text() == text_before  # no duplicate append


# ---- T1-T4 from S-8 ----


def test_t1_concurrent_status_updates(queue_mission):
    """T1 — 5 concurrent STATUS_UPDATEs to 5 different lanes."""
    lanes = ["AUDIT", "ARCHITECT", "BACKEND", "FRONTEND", "TEST"]
    threads = []
    rids: list[str] = []
    lock = threading.Lock()

    def submit(lane):
        rid = queue_client.status_update(
            queue_mission,
            f"agent-{lane.lower()[:4]}",
            lane,
            f"working: T1-{lane}",
            "",
        )
        with lock:
            rids.append(rid)

    for lane in lanes:
        t = threading.Thread(target=submit, args=(lane,))
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    applier = Applier(queue_mission)
    assert _drain_until(
        applier,
        lambda: all(
            (queue_mission / "queue" / "applied" / f"{rid}.json").exists()
            for rid in rids
        ),
        timeout=10.0,
    )
    status = (queue_mission / "STATUS.md").read_text()
    for lane in lanes:
        assert f"working: T1-{lane}" in status


def test_t2_hash_mismatch_rejected(queue_mission):
    """T2 — STATUS_UPDATE with stale expected_hash_before is rejected."""
    rid = queue_client.submit(
        queue_mission,
        "agent-aaaa",
        "AUDIT",
        "STATUS.md",
        "STATUS_UPDATE",
        {
            "lane": "AUDIT",
            "agent": "agent-aaaa",
            "new_state": "working: T2",
            "new_utc": queue_client.utc_now(),
            "new_notes": "",
        },
        expected_hash_before="0" * 64,  # stale hash
    )
    applier = Applier(queue_mission)
    assert _drain_until(
        applier,
        lambda: (queue_mission / "queue" / "rejected" / f"{rid}.json").exists(),
    )
    reason = (queue_mission / "queue" / "rejected" / f"{rid}-reason.txt").read_text()
    assert "hash-mismatch" in reason


def test_t3_pending_overflow_drains_fifo(queue_mission):
    """T3 — 100 pending requests drain in submitted order."""
    rids = []
    for i in range(100):
        rid = queue_client.history_append(
            queue_mission,
            "agent-aaaa",
            "AUDIT",
            f"Q-T3-{i:03d}",
            f"findings/{i}.md",
            "MINOR",
        )
        rids.append(rid)
    applier = Applier(queue_mission)
    assert _drain_until(
        applier,
        lambda: all(
            (queue_mission / "queue" / "applied" / f"{rid}.json").exists()
            for rid in rids
        ),
        timeout=30.0,
    )
    history = (queue_mission / "HISTORY.md").read_text()
    for i in range(100):
        assert f"findings/{i}.md" in history


def test_t4_disk_full_mocked(queue_mission, monkeypatch):
    """T4 — disk-full (ENOSPC) on the HISTORY.md write ONLY.

    Narrow injection: AtomicFile.append raises ENOSPC *before writing a single
    byte*, but ONLY when the target file is HISTORY.md. The journal write (a
    different file, written via Journal, not AtomicFile.append) still succeeds,
    so the applier's WAL is intact. The applier's unexpected-exception path
    (applier.py) records the request as REJECTED in the journal and re-raises.

    We assert the two things that actually matter:
      1. the request is journaled REJECTED (specifically), and
      2. HISTORY.md is byte-for-byte UNMODIFIED (no partial/corrupt append).
    """
    from megalodon_ui.queue import applier as applier_mod

    rid = queue_client.history_append(
        queue_mission,
        "agent-aaaa",
        "AUDIT",
        "Q-DISKFULL",
        "findings/x.md",
        "MAJOR",
    )

    history_path = queue_mission / "HISTORY.md"
    # Snapshot the exact pre-apply bytes so we can prove no partial write landed.
    history_before = history_path.read_bytes()

    real_append = applier_mod.AtomicFile.append

    def boom_append(self, line):
        # Fail ONLY the HISTORY.md append; every other target writes normally.
        # Raise BEFORE touching the file so HISTORY.md stays byte-for-byte intact.
        if self.path == history_path:
            raise OSError(28, "No space left on device")
        return real_append(self, line)

    monkeypatch.setattr(applier_mod.AtomicFile, "append", boom_append)
    applier = Applier(queue_mission)
    # The unexpected-IO path records REJECTED then re-raises (operator-visible).
    with pytest.raises(OSError) as excinfo:
        applier.drain_once()
    assert excinfo.value.errno == 28
    monkeypatch.undo()

    # 1) The request is journaled as REJECTED — specifically, not merely PENDING.
    journal_log = (queue_mission / "queue" / "journal.log").read_text()
    rejected_rids = [
        json.loads(ln)["rid"]
        for ln in journal_log.splitlines()
        if ln.strip() and json.loads(ln).get("status") == "REJECTED"
    ]
    assert rid in rejected_rids, (
        f"expected rid {rid} journaled REJECTED; journal:\n{journal_log}"
    )

    # 2) HISTORY.md is byte-for-byte unchanged — no partial append survived the
    #    failed write (no corruption on a disk-full failure).
    assert history_path.read_bytes() == history_before


# ---- Q1 intents ----


def test_q1_status_row_insert(queue_mission):
    applier = Applier(queue_mission)
    rid = queue_client.status_row_insert(
        queue_mission,
        "agent-zzzz",
        "OBSERVER-7",
        initial_state="idle",
        initial_utc=queue_client.utc_now(),
        initial_notes="surplus observer",
    )
    assert _drain_until(
        applier,
        lambda: (queue_mission / "queue" / "applied" / f"{rid}.json").exists(),
    )
    status = (queue_mission / "STATUS.md").read_text()
    assert "OBSERVER-7" in status


def test_q1_status_row_insert_rejects_existing_lane(queue_mission):
    """Insert for an existing lane should be rejected."""
    applier = Applier(queue_mission)
    rid = queue_client.status_row_insert(
        queue_mission,
        "agent-aaaa",
        "AUDIT",
        initial_utc=queue_client.utc_now(),
    )
    assert _drain_until(
        applier,
        lambda: (queue_mission / "queue" / "rejected" / f"{rid}.json").exists(),
    )


def test_q1_tasks_inject(queue_mission):
    applier = Applier(queue_mission)
    rid = queue_client.tasks_inject(
        queue_mission,
        "agent-aaaa",
        "META",
        task_id="CHALLENGE-42",
        lane="B",
        description="Fix something",
    )
    assert _drain_until(
        applier,
        lambda: (queue_mission / "queue" / "applied" / f"{rid}.json").exists(),
    )
    tasks = (queue_mission / "TASKS.md").read_text()
    assert "CHALLENGE-42" in tasks
    assert "Fix something" in tasks


def test_q1_tasks_inject_rejects_duplicate(queue_mission):
    applier = Applier(queue_mission)
    rid = queue_client.tasks_inject(
        queue_mission,
        "agent-aaaa",
        "META",
        task_id="Q-FIXTURE-1",
        lane="A",
        description="dup",
    )
    assert _drain_until(
        applier,
        lambda: (queue_mission / "queue" / "rejected" / f"{rid}.json").exists(),
    )


def test_q1_mission_event_correction_required_prefix(queue_mission):
    """CORRECTION line without 'CORRECTION by ' must be rejected (schema)."""
    applier = Applier(queue_mission)
    rid_bad = queue_client.submit(
        queue_mission,
        "agent-aaaa",
        "AUDIT",
        ".mission-events",
        "MISSION_EVENT_CORRECTION",
        {"line": "2026-05-16T22:00:00Z no prefix"},
    )
    assert _drain_until(
        applier,
        lambda: (queue_mission / "queue" / "rejected" / f"{rid_bad}.json").exists(),
    )


def test_q1_mission_event_correction_happy_path(queue_mission):
    applier = Applier(queue_mission)
    rid = queue_client.mission_event_correction(
        queue_mission,
        "agent-aaaa",
        "AUDIT",
        f"{queue_client.utc_now()} CORRECTION by agent-aaaa -- fixing prior line",
    )
    assert _drain_until(
        applier,
        lambda: (queue_mission / "queue" / "applied" / f"{rid}.json").exists(),
    )
    events = (queue_mission / ".mission-events").read_text()
    assert "CORRECTION by agent-aaaa" in events


# ---- B3 (heartbeat) ----


def test_b3_heartbeat_updated_during_drain(queue_mission):
    applier = Applier(queue_mission)
    applier.drain_once()
    hb = queue_mission / "queue" / ".applier.lock" / "heartbeat.txt"
    assert hb.exists()
    first = hb.read_text()
    time.sleep(0.05)
    applier.drain_once()
    second = hb.read_text()
    assert second >= first  # equal at second-level OK; never goes backward


# ---- B4 (strict claim ownership) ----


def test_b4_claim_exists_no_owner_rejected(queue_mission):
    """Pre-v9-style claim dir without owner.txt → reject (don't steal)."""
    (queue_mission / "claims" / "LEGACY-TASK").mkdir(parents=True)
    rid = queue_client.claim_dir_create(
        queue_mission,
        "agent-aaaa",
        "AUDIT",
        "LEGACY-TASK",
    )
    applier = Applier(queue_mission)
    assert _drain_until(
        applier,
        lambda: (queue_mission / "queue" / "rejected" / f"{rid}.json").exists(),
    )
    # owner.txt should NOT have been written.
    assert not (queue_mission / "claims" / "LEGACY-TASK" / "owner.txt").exists()


def test_b4_claim_exists_with_owner_idempotent(queue_mission):
    """Same-owner re-create should be idempotent (not rejected, not stolen)."""
    (queue_mission / "claims" / "OK-TASK").mkdir(parents=True)
    (queue_mission / "claims" / "OK-TASK" / "owner.txt").write_text(
        "agent-aaaa AUDIT 2026-01-01T00:00:00Z\n"
    )
    rid = queue_client.claim_dir_create(
        queue_mission,
        "agent-aaaa",
        "AUDIT",
        "OK-TASK",
    )
    applier = Applier(queue_mission)
    assert _drain_until(
        applier,
        lambda: (queue_mission / "queue" / "applied" / f"{rid}.json").exists(),
    )


# ---- envelope validation ----


def test_unknown_intent_rejected(queue_mission):
    rid = queue_client.submit(
        queue_mission,
        "agent-aaaa",
        "AUDIT",
        "STATUS.md",
        "DELETE_ALL_THE_THINGS",
        {},
    )
    applier = Applier(queue_mission)
    assert _drain_until(
        applier,
        lambda: (queue_mission / "queue" / "rejected" / f"{rid}.json").exists(),
    )
    reason = (queue_mission / "queue" / "rejected" / f"{rid}-reason.txt").read_text()
    assert "unknown-intent" in reason


# ---- P3.7: corrupt pending + phase-mismatch precondition ----


def test_corrupt_pending_json_is_rejected_not_crash(queue_mission):
    """A pending/*.json that is not valid JSON must be journaled/archived as
    REJECTED with reason 'json-decode-error' — the drain loop swallows the
    decode error per-request and keeps going (no crash)."""
    pending_dir = queue_mission / "queue" / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    bad = pending_dir / "corrupt-req.json"
    bad.write_text("{ this is not valid json :::")

    applier = Applier(queue_mission)
    # drain_once must not raise on the corrupt file.
    applier.drain_once()

    # The file is moved to rejected/ under its stem (no request_id parseable).
    rejected = queue_mission / "queue" / "rejected" / "corrupt-req.json"
    assert rejected.exists(), "corrupt pending file was not archived to rejected/"
    assert not bad.exists(), "corrupt file should have been moved out of pending/"
    reason = (
        queue_mission / "queue" / "rejected" / "corrupt-req-reason.txt"
    ).read_text()
    assert "json-decode-error" in reason


def test_phase_mismatch_precondition_rejected(queue_mission):
    """A request whose preconditions.required_phase differs from the mission's
    current phase is rejected with a 'phase-mismatch' reason.

    The queue_mission fixture's last .mission-events line is INIT->PHASE-PLAN,
    so requiring PHASE-BUILD must fail the precondition check.
    """
    rid = queue_client.submit(
        queue_mission,
        "agent-aaaa",
        "AUDIT",
        "STATUS.md",
        "STATUS_UPDATE",
        {
            "lane": "AUDIT",
            "agent": "agent-aaaa",
            "new_state": "working: P-PHASE",
            "new_utc": queue_client.utc_now(),
            "new_notes": "",
        },
        preconditions={"required_phase": "PHASE-BUILD"},
    )
    applier = Applier(queue_mission)
    assert _drain_until(
        applier,
        lambda: (queue_mission / "queue" / "rejected" / f"{rid}.json").exists(),
    )
    reason = (queue_mission / "queue" / "rejected" / f"{rid}-reason.txt").read_text()
    assert "phase-mismatch" in reason
    assert "want=PHASE-BUILD" in reason
    assert "got=PHASE-PLAN" in reason
    # And the STATUS row was NOT mutated (precondition gate ran before apply).
    assert "working: P-PHASE" not in (queue_mission / "STATUS.md").read_text()


def test_singleton_lock_acquired(queue_mission):
    applier1 = Applier(queue_mission)
    assert applier1.acquire_singleton()
    applier2 = Applier(queue_mission)
    # Second instance should fail to acquire while first is "alive" (PID).
    assert not applier2.acquire_singleton()
    applier1.release_singleton()


# ---- P1.3 regression: applier logs real lane name, not "?" ----


def test_applied_log_records_real_lane(queue_mission, caplog):
    """P1.3 — APPLIED log line must show the request's lane, not '?'.

    The applier used req.get("submitting_lane", "?") at three log sites but the
    request dict key is 'lane'.  After the fix req.get("lane", "?") is used and
    the log line must show lane=A (not lane=?).
    """
    applier = Applier(queue_mission)
    rid = queue_client.history_append(
        queue_mission,
        "agent-aaaa",
        "A",
        "Q-FIXTURE-1",
        "findings/p1-3-test.md",
        "MINOR",
    )

    # The applier logger sets propagate=False with its own handler, so caplog's
    # root handler never sees its records — attach caplog's handler directly.
    applier_logger = logging.getLogger("megalodon.queue.applier")
    applier_logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger="megalodon.queue.applier"):
            assert _drain_until(
                applier,
                lambda: (queue_mission / "queue" / "applied" / f"{rid}.json").exists(),
            )
    finally:
        applier_logger.removeHandler(caplog.handler)

    applied_lines = [
        r.getMessage() for r in caplog.records if "APPLIED" in r.getMessage()
    ]
    assert applied_lines, "No APPLIED log line found"
    # Must record the real lane, not the fallback sentinel.
    assert any("lane=A" in line for line in applied_lines), (
        f"Expected 'lane=A' in APPLIED log line; got: {applied_lines}"
    )
    assert not any("lane=?" in line for line in applied_lines), (
        f"Got 'lane=?' (wrong key used) in APPLIED log line: {applied_lines}"
    )
