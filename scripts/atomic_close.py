#!/usr/bin/env python3
"""Atomic RULE-10 close — workers' canonical completion script.

Usage:
    python3 scripts/atomic_close.py \\
        --task <TASK-ID> --lane <LANE> --agent <AGENT-ID> \\
        --finding <PATH> --severity <SEV> \\
        --notes <TEXT> --summary <TEXT> \\
        [--mission-dir <PATH>] [--dry-run] [--resume <REQUEST-ID>] [--debug]

Exit codes:
    0 success | 1 unexpected | 2 arg validation | 3 partial close (resume available)
    | 4 precondition (task already done, claim missing) | 5 lock timeout

Spec: docs/superpowers/specs/2026-05-16-v9-m3-helper-scripts-design.md
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

# Allow `python3 scripts/atomic_close.py` from project root without install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import _validation
from scripts._logging import get_logger
from scripts._shared_state import execute_close, resume_close


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_request_id(agent: str) -> str:
    stamp = _utc_now().replace(":", "-")
    return f"{stamp}-{agent}-rule10-CLOSE-{secrets.token_hex(2)}"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="atomic_close",
        description="Atomic RULE-10 close (v9 M3 helper)",
    )
    p.add_argument("--task")
    p.add_argument("--lane")
    p.add_argument("--agent")
    p.add_argument("--finding")
    p.add_argument("--severity")
    p.add_argument("--notes")
    p.add_argument("--summary")
    p.add_argument("--mission-dir", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--resume", dest="resume_id", default=None)
    p.add_argument("--debug", action="store_true")
    return p.parse_args(argv)


def _resolve_mission(arg: str | None) -> Path:
    candidate = Path(arg) if arg else Path.cwd()
    if not (candidate / "STATUS.md").exists() or not (candidate / "TASKS.md").exists():
        raise FileNotFoundError(
            f"mission dir invalid (no STATUS.md/TASKS.md): {candidate}"
        )
    return candidate.resolve()


def _validate_or_die(args: argparse.Namespace) -> None:
    if args.resume_id:
        return
    required = ["task", "lane", "agent", "finding", "severity", "notes", "summary"]
    missing = [r for r in required if not getattr(args, r)]
    if missing:
        raise ValueError(f"missing required args: {missing}")
    _validation.validate_task_id(args.task)
    _validation.validate_lane(args.lane)
    _validation.validate_agent(args.agent)
    _validation.validate_severity(args.severity)
    _validation.validate_notes(args.notes)
    _validation.validate_summary(args.summary)


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    log = get_logger("atomic_close", debug=args.debug)
    try:
        _validate_or_die(args)
    except ValueError as e:
        log.warning("arg validation failed: %s", e)
        sys.stderr.write(f"arg validation failed: {e}\n")
        return 2
    try:
        mission = _resolve_mission(args.mission_dir)
    except FileNotFoundError as e:
        sys.stderr.write(f"{e}\n")
        return 4

    try:
        if args.resume_id:
            result = resume_close(mission, args.resume_id)
        else:
            request_id = _build_request_id(args.agent)
            if args.dry_run:
                _emit(
                    {
                        "ok": True,
                        "dry_run": True,
                        "request_id": request_id,
                        "would_run": [
                            "CLAIM_DIR_DONE",
                            "TASKS_BRACKET",
                            "HISTORY_APPEND",
                            "STATUS_UPDATE",
                        ],
                        "utc": _utc_now(),
                    }
                )
                return 0
            result = execute_close(
                mission,
                request_id=request_id,
                task_id=args.task,
                lane=args.lane,
                agent=args.agent,
                utc=_utc_now(),
                finding_path=args.finding,
                severity=args.severity,
                notes=args.notes,
                summary=args.summary,
            )
    except Exception as exc:  # noqa: BLE001
        log.error("unexpected exception: %s\n%s", exc, traceback.format_exc())
        sys.stderr.write(f"unexpected: {exc}\n")
        return 1

    _emit(result)
    if not result["ok"]:
        # Distinguish failure modes by inspecting step errors.
        for step in result["steps"]:
            if step.get("error") and "owner mismatch" in step["error"]:
                return 4
            if step.get("error") and "missing" in step["error"]:
                return 3
            if step.get("error") and "lock" in step["error"].lower():
                return 5
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
