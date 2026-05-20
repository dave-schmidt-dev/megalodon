#!/usr/bin/env python3
"""
Megalodon v9 queue client.

Worker-side helper for submitting write requests to the queue applier.
Replaces direct Edit-tool writes to STATUS.md / TASKS.md / HISTORY.md / .mission-events
under v9. Workers import these functions; the applier (docs/v9/queue/applier.py) drains
the queue and applies the requested mutations atomically.

Spec: docs/v9/QUEUE-DESIGN.md
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _request_id(agent: str, target: str, intent: str, utc: str) -> str:
    # Sanitize colons (UTC), dots (filenames), AND slashes (claims/<id>/...).
    safe_target = target.replace(".", "_").replace("/", "_")
    return (
        f"{utc.replace(':', '-')}-{agent}-{safe_target}-{intent}-{secrets.token_hex(2)}"
    )


def _idempotency_key(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def submit(
    mission_dir: Path | str,
    agent: str,
    lane: str,
    target_file: str,
    intent: str,
    payload: dict[str, Any],
    *,
    preconditions: dict[str, Any] | None = None,
    expected_hash_before: str | None = None,
    fallback: str = "REJECT",
) -> str:
    """Submit a write request to the queue. Returns request_id."""
    mission = Path(mission_dir)
    utc = utc_now()
    rid = _request_id(agent, target_file, intent, utc)
    request = {
        "schema_version": SCHEMA_VERSION,
        "request_id": rid,
        "submitted_utc": utc,
        "agent": agent,
        "lane": lane,
        "target_file": target_file,
        "intent": intent,
        "preconditions": preconditions or {},
        "payload": payload,
        "idempotency_key": _idempotency_key(payload),
        "expected_hash_before": expected_hash_before,
        "fallback": fallback,
    }
    pending = mission / "queue" / "pending" / f"{rid}.json"
    _atomic_write(pending, json.dumps(request, indent=2) + "\n")
    return rid


# ---- intent-specific convenience wrappers ----


def status_update(
    mission_dir: Path | str,
    agent: str,
    lane: str,
    new_state: str,
    new_notes: str,
    *,
    new_utc: str | None = None,
    required_phase: str | None = None,
) -> str:
    """Update a STATUS.md row for the given lane."""
    utc = new_utc or utc_now()  # S-8 B1 fix: full ISO-8601 UTC, no truncation
    payload = {
        "lane": lane,
        "agent": agent,
        "new_state": new_state,
        "new_utc": utc,
        "new_notes": new_notes,
    }
    preconditions = {"required_phase": required_phase} if required_phase else {}
    return submit(
        mission_dir,
        agent,
        lane,
        "STATUS.md",
        "STATUS_UPDATE",
        payload,
        preconditions=preconditions,
    )


def tasks_bracket(
    mission_dir: Path | str,
    agent: str,
    lane: str,
    task_id: str,
    new_bracket: str,
) -> str:
    """Rewrite a TASKS.md bracket prefix for the given task_id."""
    payload = {"task_id": task_id, "new_bracket": new_bracket}
    return submit(
        mission_dir,
        agent,
        lane,
        "TASKS.md",
        "TASKS_BRACKET",
        payload,
    )


def task_claim(
    mission_dir: Path | str, agent: str, lane: str, task_id: str, utc: str | None = None
) -> str:
    bracket = f"[claimed: {agent} @ {utc or utc_now()}]"
    return tasks_bracket(mission_dir, agent, lane, task_id, bracket)


def task_done(
    mission_dir: Path | str, agent: str, lane: str, task_id: str, utc: str | None = None
) -> str:
    bracket = f"[done: {agent} @ {utc or utc_now()}]"
    return tasks_bracket(mission_dir, agent, lane, task_id, bracket)


def history_append(
    mission_dir: Path | str,
    agent: str,
    lane: str,
    task_id: str,
    finding_path: str,
    severity: str,
    utc: str | None = None,
) -> str:
    """Append a completion line to HISTORY.md.

    Format: `<UTC> | <agent> | <LANE> | <task-id> | <finding-filename> | <severity>`
    """
    line = f"{utc or utc_now()} | {agent} | {lane} | {task_id} | {finding_path} | {severity}"
    payload = {"line": line}
    return submit(
        mission_dir,
        agent,
        lane,
        "HISTORY.md",
        "HISTORY_APPEND",
        payload,
    )


def mission_event(
    mission_dir: Path | str,
    agent: str,
    lane: str,
    line: str,
) -> str:
    """Append a line to .mission-events."""
    payload = {"line": line}
    return submit(
        mission_dir,
        agent,
        lane,
        ".mission-events",
        "MISSION_EVENT_APPEND",
        payload,
    )


def claim_dir_create(
    mission_dir: Path | str,
    agent: str,
    lane: str,
    task_id: str,
) -> str:
    """Create claims/<task_id>/ with owner.txt set to the calling agent."""
    payload = {"task_id": task_id, "owner_agent": agent, "owner_lane": lane}
    return submit(
        mission_dir,
        agent,
        lane,
        f"claims/{task_id}",
        "CLAIM_DIR_CREATE",
        payload,
    )


def claim_dir_done(
    mission_dir: Path | str,
    agent: str,
    lane: str,
    task_id: str,
) -> str:
    """Mark claims/<task_id>/done — only owner may do this."""
    payload = {"task_id": task_id, "agent": agent}
    return submit(
        mission_dir,
        agent,
        lane,
        f"claims/{task_id}/done",
        "CLAIM_DIR_DONE",
        payload,
    )


# ---- Q1 additions (S-8 §A Q1) ----


def status_row_insert(
    mission_dir: Path | str,
    agent: str,
    lane: str,
    *,
    initial_state: str = "idle",
    initial_utc: str | None = None,
    initial_notes: str = "",
) -> str:
    """Insert a new row into STATUS.md (for surplus observer lanes etc.)."""
    payload = {
        "lane": lane,
        "agent": agent,
        "initial_state": initial_state,
        "initial_utc": initial_utc or utc_now(),
        "initial_notes": initial_notes,
    }
    return submit(
        mission_dir,
        agent,
        lane,
        "STATUS.md",
        "STATUS_ROW_INSERT",
        payload,
    )


def tasks_inject(
    mission_dir: Path | str,
    agent: str,
    submitting_lane: str,
    *,
    task_id: str,
    lane: str,
    description: str,
    bracket: str = "[ ]",
    after_task_id: str | None = None,
) -> str:
    """Insert a new task line into TASKS.md. Pre-condition: task_id unique."""
    payload = {
        "task_id": task_id,
        "lane": lane,
        "bracket": bracket,
        "description": description,
        "after_task_id": after_task_id,
    }
    return submit(
        mission_dir,
        agent,
        submitting_lane,
        "TASKS.md",
        "TASKS_INJECT",
        payload,
    )


def mission_event_correction(
    mission_dir: Path | str,
    agent: str,
    lane: str,
    line: str,
) -> str:
    """Append a CORRECTION line to .mission-events.

    Line MUST contain 'CORRECTION by ' per schema.
    """
    payload = {"line": line}
    return submit(
        mission_dir,
        agent,
        lane,
        ".mission-events",
        "MISSION_EVENT_CORRECTION",
        payload,
    )


# ---- low-effort poll helper for the rare worker that needs to wait ----


def wait_until_applied(
    mission_dir: Path | str, request_id: str, timeout_seconds: float = 30.0
) -> str:
    """Block until request_id moves out of pending. Returns 'applied' or 'rejected'."""
    import time

    mission = Path(mission_dir)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        applied = mission / "queue" / "applied" / f"{request_id}.json"
        rejected = mission / "queue" / "rejected" / f"{request_id}.json"
        if applied.exists():
            return "applied"
        if rejected.exists():
            return "rejected"
        time.sleep(0.5)
    return "timeout"


# ---- CLI for shell-friendly invocations (handles cas_write use case directly) ----

if __name__ == "__main__":
    import argparse
    import sys

    p = argparse.ArgumentParser(description="Megalodon v9 queue client")
    p.add_argument("--mission-dir", required=True, type=Path)
    p.add_argument("--agent", required=True)
    p.add_argument("--lane", required=True)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("status")
    sp.add_argument("--state", required=True)
    sp.add_argument("--utc", default=None)
    sp.add_argument("--notes", required=True)

    sp = sub.add_parser("claim")
    sp.add_argument("--task", required=True)

    sp = sub.add_parser("done")
    sp.add_argument("--task", required=True)

    sp = sub.add_parser("history")
    sp.add_argument("--task", required=True)
    sp.add_argument("--finding", required=True)
    sp.add_argument("--severity", required=True)

    sp = sub.add_parser("event")
    sp.add_argument("--line", required=True)

    sp = sub.add_parser("claim-dir")
    sp.add_argument("--task", required=True)

    sp = sub.add_parser("claim-done")
    sp.add_argument("--task", required=True)

    args = p.parse_args()
    common = dict(mission_dir=args.mission_dir, agent=args.agent, lane=args.lane)

    if args.cmd == "status":
        rid = status_update(
            **common, new_state=args.state, new_utc=args.utc, new_notes=args.notes
        )
    elif args.cmd == "claim":
        rid = task_claim(**common, task_id=args.task)
    elif args.cmd == "done":
        rid = task_done(**common, task_id=args.task)
    elif args.cmd == "history":
        rid = history_append(
            **common,
            task_id=args.task,
            finding_path=args.finding,
            severity=args.severity,
        )
    elif args.cmd == "event":
        rid = mission_event(**common, line=args.line)
    elif args.cmd == "claim-dir":
        rid = claim_dir_create(**common, task_id=args.task)
    elif args.cmd == "claim-done":
        rid = claim_dir_done(**common, task_id=args.task)
    else:
        p.print_help()
        sys.exit(2)

    print(rid)
