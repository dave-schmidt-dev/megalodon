"""V9 M1 queue_client tests — including B1 regression + Q1 helpers."""

import json
import re
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.queue import queue_client


# ---- B1 regression (S-8 §B B1) ----


def test_b1_utc_default_is_valid_iso8601_seconds(queue_mission):
    """status_update without explicit new_utc must produce full ISO-8601
    (with seconds), NOT the broken `:Z + Z` truncation."""
    rid = queue_client.status_update(
        queue_mission,
        "agent-aaaa",
        "AUDIT",
        "working: x",
        "",
    )
    req_path = queue_mission / "queue" / "pending" / f"{rid}.json"
    req = json.loads(req_path.read_text())
    iso_re = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
    assert re.match(iso_re, req["submitted_utc"]), req["submitted_utc"]
    assert re.match(iso_re, req["payload"]["new_utc"]), req["payload"]["new_utc"]


def test_b1_explicit_utc_passthrough(queue_mission):
    """Explicit new_utc bypasses the default and is used verbatim."""
    rid = queue_client.status_update(
        queue_mission,
        "agent-aaaa",
        "AUDIT",
        "working: x",
        "",
        new_utc="2026-12-31T23:59:59Z",
    )
    req = json.loads((queue_mission / "queue" / "pending" / f"{rid}.json").read_text())
    assert req["payload"]["new_utc"] == "2026-12-31T23:59:59Z"


# ---- submit envelope shape ----


def test_submit_envelope_includes_required_fields(queue_mission):
    rid = queue_client.submit(
        queue_mission,
        "agent-aaaa",
        "AUDIT",
        "STATUS.md",
        "STATUS_UPDATE",
        {
            "lane": "AUDIT",
            "agent": "agent-aaaa",
            "new_state": "x",
            "new_utc": queue_client.utc_now(),
            "new_notes": "",
        },
    )
    req = json.loads((queue_mission / "queue" / "pending" / f"{rid}.json").read_text())
    for field in (
        "schema_version",
        "request_id",
        "submitted_utc",
        "agent",
        "lane",
        "target_file",
        "intent",
        "payload",
        "idempotency_key",
        "expected_hash_before",
        "fallback",
    ):
        assert field in req, f"missing envelope field: {field}"
    assert req["schema_version"] == 1
    assert req["fallback"] == "REJECT"


def test_submit_idempotency_key_is_stable_for_same_payload(queue_mission):
    payload = {
        "lane": "AUDIT",
        "agent": "agent-aaaa",
        "new_state": "x",
        "new_utc": "2026-05-16T22:00:00Z",
        "new_notes": "fixed",
    }
    rid1 = queue_client.submit(
        queue_mission,
        "agent-aaaa",
        "AUDIT",
        "STATUS.md",
        "STATUS_UPDATE",
        payload,
    )
    rid2 = queue_client.submit(
        queue_mission,
        "agent-aaaa",
        "AUDIT",
        "STATUS.md",
        "STATUS_UPDATE",
        payload,
    )
    r1 = json.loads((queue_mission / "queue" / "pending" / f"{rid1}.json").read_text())
    r2 = json.loads((queue_mission / "queue" / "pending" / f"{rid2}.json").read_text())
    assert r1["idempotency_key"] == r2["idempotency_key"]
    # Request IDs may differ since they include timestamp + nonce.


# ---- intent helper coverage ----


def test_tasks_bracket_helper(queue_mission):
    rid = queue_client.tasks_bracket(
        queue_mission,
        "agent-aaaa",
        "AUDIT",
        "Q-X",
        "[ ]",
    )
    req = json.loads((queue_mission / "queue" / "pending" / f"{rid}.json").read_text())
    assert req["intent"] == "TASKS_BRACKET"
    assert req["payload"]["task_id"] == "Q-X"
    assert req["payload"]["new_bracket"] == "[ ]"


def test_task_claim_uses_claimed_bracket(queue_mission):
    rid = queue_client.task_claim(
        queue_mission,
        "agent-aaaa",
        "AUDIT",
        "Q-X",
    )
    req = json.loads((queue_mission / "queue" / "pending" / f"{rid}.json").read_text())
    assert req["payload"]["new_bracket"].startswith("[claimed:")


def test_task_done_uses_done_bracket(queue_mission):
    rid = queue_client.task_done(
        queue_mission,
        "agent-aaaa",
        "AUDIT",
        "Q-X",
    )
    req = json.loads((queue_mission / "queue" / "pending" / f"{rid}.json").read_text())
    assert req["payload"]["new_bracket"].startswith("[done:")


def test_history_append_line_format(queue_mission):
    rid = queue_client.history_append(
        queue_mission,
        "agent-aaaa",
        "AUDIT",
        "Q-X",
        "findings/x.md",
        "MAJOR",
    )
    req = json.loads((queue_mission / "queue" / "pending" / f"{rid}.json").read_text())
    line = req["payload"]["line"]
    assert "agent-aaaa" in line
    assert "AUDIT" in line
    assert "MAJOR" in line


def test_mission_event_helper(queue_mission):
    rid = queue_client.mission_event(
        queue_mission,
        "agent-aaaa",
        "AUDIT",
        "2026-05-16T22:00:00Z some line",
    )
    req = json.loads((queue_mission / "queue" / "pending" / f"{rid}.json").read_text())
    assert req["intent"] == "MISSION_EVENT_APPEND"


def test_claim_dir_create_helper(queue_mission):
    rid = queue_client.claim_dir_create(
        queue_mission,
        "agent-aaaa",
        "AUDIT",
        "Q-CLAIM-1",
    )
    req = json.loads((queue_mission / "queue" / "pending" / f"{rid}.json").read_text())
    assert req["intent"] == "CLAIM_DIR_CREATE"
    assert req["payload"]["task_id"] == "Q-CLAIM-1"
    assert req["payload"]["owner_agent"] == "agent-aaaa"


def test_claim_dir_done_helper(queue_mission):
    rid = queue_client.claim_dir_done(
        queue_mission,
        "agent-aaaa",
        "AUDIT",
        "Q-CLAIM-1",
    )
    req = json.loads((queue_mission / "queue" / "pending" / f"{rid}.json").read_text())
    assert req["intent"] == "CLAIM_DIR_DONE"
    assert req["payload"]["agent"] == "agent-aaaa"


def test_status_row_insert_helper(queue_mission):
    rid = queue_client.status_row_insert(
        queue_mission,
        "agent-x",
        "OBS-1",
        initial_state="watching",
        initial_notes="hi",
    )
    req = json.loads((queue_mission / "queue" / "pending" / f"{rid}.json").read_text())
    assert req["intent"] == "STATUS_ROW_INSERT"
    assert req["payload"]["lane"] == "OBS-1"
    assert req["payload"]["initial_state"] == "watching"


def test_tasks_inject_helper(queue_mission):
    rid = queue_client.tasks_inject(
        queue_mission,
        "agent-x",
        "META",
        task_id="CHALLENGE-99",
        lane="C",
        description="why",
    )
    req = json.loads((queue_mission / "queue" / "pending" / f"{rid}.json").read_text())
    assert req["intent"] == "TASKS_INJECT"
    assert req["payload"]["task_id"] == "CHALLENGE-99"
    assert req["payload"]["lane"] == "C"


def test_mission_event_correction_helper(queue_mission):
    rid = queue_client.mission_event_correction(
        queue_mission,
        "agent-x",
        "AUDIT",
        f"{queue_client.utc_now()} CORRECTION by agent-x -- typo fix",
    )
    req = json.loads((queue_mission / "queue" / "pending" / f"{rid}.json").read_text())
    assert req["intent"] == "MISSION_EVENT_CORRECTION"
    assert "CORRECTION by" in req["payload"]["line"]


# ---- wait_until_applied ----


def test_wait_until_applied_timeout(queue_mission):
    """No applier running → wait_until_applied returns 'timeout'."""
    result = queue_client.wait_until_applied(
        queue_mission,
        "no-such-rid",
        timeout_seconds=0.5,
    )
    assert result == "timeout"


def test_wait_until_applied_detects_applied(queue_mission, tmp_path):
    """Manually drop a fake applied file; wait_until_applied returns 'applied'."""
    rid = "fake-rid-applied"
    (queue_mission / "queue" / "applied").mkdir(parents=True, exist_ok=True)
    (queue_mission / "queue" / "applied" / f"{rid}.json").write_text("{}")
    assert (
        queue_client.wait_until_applied(
            queue_mission,
            rid,
            timeout_seconds=2.0,
        )
        == "applied"
    )


def test_wait_until_applied_detects_rejected(queue_mission):
    rid = "fake-rid-rejected"
    (queue_mission / "queue" / "rejected").mkdir(parents=True, exist_ok=True)
    (queue_mission / "queue" / "rejected" / f"{rid}.json").write_text("{}")
    assert (
        queue_client.wait_until_applied(
            queue_mission,
            rid,
            timeout_seconds=2.0,
        )
        == "rejected"
    )


# ---- BUG 1: history_append must not double-submit ----


def test_backend_history_append_single_pending_intent(mission_dir):
    """BUG 1 regression: backend.history_append must enqueue exactly ONE
    HISTORY_APPEND intent (not two), so a single close yields a single row."""
    from scripts._backends import queue_client as backend

    result = backend.history_append(
        mission_dir,
        agent="agent-abcd",
        lane_short="A",
        task_id="TEST-1",
        finding_path="findings/f.md",
        severity="DELTA",
        notes="single row",
        utc="2026-05-16T22:30:00Z",
    )
    assert result["ok"] is True, result

    # Exactly one HISTORY data row landed (ignore header / format / separator
    # lines that don't begin with an ISO-8601 UTC timestamp).
    text = (mission_dir / "HISTORY.md").read_text(encoding="utf-8")
    data_rows = [
        ln
        for ln in text.splitlines()
        if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z \|", ln)
    ]
    assert len(data_rows) == 1, data_rows
    assert "single row" in data_rows[0]


# ---- BUG 3: tasks-inject CLI subcommand wiring ----


def test_main_tasks_inject_enqueues_and_applies(queue_mission):
    """BUG 3 regression: queue_client.main(['tasks-inject', ...]) must enqueue
    a TASKS_INJECT intent that the applier applies to TASKS.md."""
    from megalodon_ui.queue.applier import Applier

    rc = queue_client.main(
        [
            "--mission-dir",
            str(queue_mission),
            "--agent",
            "agent-abcd",
            "--lane",
            "META",
            "tasks-inject",
            "--task",
            "INJECTED-1",
            "--task-lane",
            "C",
            "--description",
            "newly injected task",
        ]
    )
    assert rc == 0

    pend = list((queue_mission / "queue" / "pending").glob("*.json"))
    assert len(pend) == 1, pend
    req = json.loads(pend[0].read_text())
    assert req["intent"] == "TASKS_INJECT"
    assert req["payload"]["task_id"] == "INJECTED-1"
    assert req["payload"]["lane"] == "C"

    applier = Applier(queue_mission)
    rid = req["request_id"]
    deadline = __import__("time").time() + 5.0
    while __import__("time").time() < deadline:
        applier.drain_once()
        if (queue_mission / "queue" / "applied" / f"{rid}.json").exists():
            break
        __import__("time").sleep(0.05)
    tasks = (queue_mission / "TASKS.md").read_text(encoding="utf-8")
    assert "INJECTED-1" in tasks
    assert "newly injected task" in tasks


def test_main_callable_status_forwards(tmp_path, monkeypatch):
    """queue_client.main(argv) is importable and forwards to status_update."""
    import megalodon_ui.queue.queue_client as qc

    captured = {}

    def fake_status_update(**kwargs):
        captured.update(kwargs)
        return "req-123"

    monkeypatch.setattr(qc, "status_update", fake_status_update)
    rc = qc.main(
        [
            "--mission-dir",
            str(tmp_path),
            "--agent",
            "agent-abcd",
            "--lane",
            "BACKEND",
            "status",
            "--state",
            "idle",
            "--notes",
            "hb",
        ]
    )
    assert rc == 0
    assert captured["new_state"] == "idle"
    assert captured["agent"] == "agent-abcd"
