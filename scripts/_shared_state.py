"""M3/M1 abstraction boundary for shared-state writes.

execute_close() runs the 4 RULE-10 steps in order. As of V9 M1, the
backend routes through the queue applier (`_backends.queue_client`)
instead of doing direct fcntl writes. The legacy direct backend
remains importable for callers that need bypass (and is exercised by
its own test module).

On any step failure, writes a PARTIAL journal entry under
mission/.scripts-journal/<request-id>.json and returns with resume_hint.

resume_close() reads a PARTIAL journal and continues from the first failed step.
Each step is independently idempotent, so resume is safe to invoke repeatedly.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# V9 M1 backend swap (spec D5): single-line change. Old:
#   from ._backends import direct_fcntl as _backend
from ._backends import queue_client as _backend
from ._validation import LANE_LONG_TO_SHORT

SCHEMA_VERSION = 1
JOURNAL_DIR_NAME = ".scripts-journal"

_STEPS_IN_ORDER = ["CLAIM_DIR_DONE", "TASKS_BRACKET", "HISTORY_APPEND", "STATUS_UPDATE"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _journal_path(mission: Path, request_id: str) -> Path:
    return mission / JOURNAL_DIR_NAME / f"{request_id}.json"


def _write_journal(mission: Path, request_id: str, payload: dict[str, Any]) -> None:
    path = _journal_path(mission, request_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**payload, "last_updated_utc": _utc_now()}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _read_journal(mission: Path, request_id: str) -> dict[str, Any]:
    path = _journal_path(mission, request_id)
    if not path.exists():
        raise FileNotFoundError(f"journal not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _run_step(
    step: str,
    mission: Path,
    *,
    task_id: str,
    lane: str,
    agent: str,
    utc: str,
    finding_path: str,
    severity: str,
    notes: str,
    summary: str,
) -> dict[str, Any]:
    if step == "CLAIM_DIR_DONE":
        return _backend.claim_dir_done(mission, task_id, agent, utc)
    if step == "TASKS_BRACKET":
        return _backend.tasks_bracket(mission, task_id, agent, utc)
    if step == "HISTORY_APPEND":
        return _backend.history_append(
            mission,
            agent=agent,
            lane_short=LANE_LONG_TO_SHORT[lane],
            task_id=task_id,
            finding_path=finding_path,
            severity=severity,
            notes=notes,
            utc=utc,
        )
    if step == "STATUS_UPDATE":
        return _backend.status_update(
            mission,
            lane=lane,
            agent=agent,
            task_id=task_id,
            summary=summary,
            utc=utc,
        )
    raise ValueError(f"unknown step: {step}")


def execute_close(
    mission_dir: Path,
    *,
    request_id: str,
    task_id: str,
    lane: str,
    agent: str,
    utc: str,
    finding_path: str,
    severity: str,
    notes: str,
    summary: str,
) -> dict[str, Any]:
    mission = Path(mission_dir)
    started = _utc_now()
    args = {
        "finding": finding_path,
        "severity": severity,
        "notes": notes,
        "summary": summary,
    }
    journal_payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id,
        "started_utc": started,
        "status": "PENDING",
        "task_id": task_id,
        "lane": lane,
        "agent": agent,
        "args": args,
        "steps": [],
    }
    # Write PENDING journal before any steps — crash-visible from the start.
    _write_journal(mission, request_id, journal_payload)

    completed: list[str] = []
    step_results: list[dict[str, Any]] = []
    failed_step: str | None = None

    for step in _STEPS_IN_ORDER:
        result = _run_step(
            step,
            mission,
            task_id=task_id,
            lane=lane,
            agent=agent,
            utc=utc,
            finding_path=finding_path,
            severity=severity,
            notes=notes,
            summary=summary,
        )
        step_results.append({**result, "completed_utc": _utc_now()})
        if result["ok"]:
            completed.append(step)
        else:
            failed_step = step
            break

    journal_payload["steps"] = step_results
    if failed_step is None:
        journal_payload["status"] = "COMPLETE"
        _write_journal(mission, request_id, journal_payload)
        return {
            "request_id": request_id,
            "ok": True,
            "completed": completed,
            "failed_step": None,
            "steps": step_results,
            "resume_hint": None,
        }
    journal_payload["status"] = "PARTIAL"
    _write_journal(mission, request_id, journal_payload)
    return {
        "request_id": request_id,
        "ok": False,
        "completed": completed,
        "failed_step": failed_step,
        "steps": step_results,
        "resume_hint": f"python3 scripts/atomic_close.py --resume {request_id}",
    }


def resume_close(mission_dir: Path, request_id: str) -> dict[str, Any]:
    mission = Path(mission_dir)
    journal = _read_journal(mission, request_id)
    if journal["status"] in ("COMPLETE", "RESUMED-COMPLETE"):
        return {
            "request_id": request_id,
            "ok": True,
            "completed": [s["step"] for s in journal["steps"] if s["ok"]],
            "failed_step": None,
            "steps": journal["steps"],
            "resume_hint": None,
        }

    completed = [s["step"] for s in journal["steps"] if s["ok"]]
    remaining = [s for s in _STEPS_IN_ORDER if s not in completed]
    args = journal["args"]
    new_results: list[dict[str, Any]] = list(journal["steps"])
    failed_step: str | None = None

    for step in remaining:
        result = _run_step(
            step,
            mission,
            task_id=journal["task_id"],
            lane=journal["lane"],
            agent=journal["agent"],
            utc=_utc_now(),
            finding_path=args["finding"],
            severity=args["severity"],
            notes=args["notes"],
            summary=args["summary"],
        )
        new_results.append({**result, "completed_utc": _utc_now()})
        if result["ok"]:
            completed.append(step)
        else:
            failed_step = step
            break

    journal["steps"] = new_results
    journal["status"] = "RESUMED-COMPLETE" if failed_step is None else "PARTIAL"
    _write_journal(mission, request_id, journal)
    return {
        "request_id": request_id,
        "ok": failed_step is None,
        "completed": completed,
        "failed_step": failed_step,
        "steps": new_results,
        "resume_hint": None
        if failed_step is None
        else f"python3 scripts/atomic_close.py --resume {request_id}",
    }
