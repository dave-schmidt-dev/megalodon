#!/usr/bin/env python3
"""
Megalodon v9 queue applier.

Drains queue/pending/*.json in timestamp order, applies each request to its
target file under per-file lock, and archives applied/rejected requests.

Singleton: holds queue/.applier.lock via mkdir-atomic. Survives crash via journal.

Usage:
    python3 applier.py --mission-dir /path/to/mission [--poll-seconds 2]

Spec: docs/v9/QUEUE-DESIGN.md
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
DEFAULT_POLL_SECONDS = 2.0

INTENTS = {
    "STATUS_UPDATE",
    "TASKS_BRACKET",
    "HISTORY_APPEND",
    "MISSION_EVENT_APPEND",
    "CLAIM_DIR_CREATE",
    "CLAIM_DIR_DONE",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_file(path: Path) -> str:
    if not path.exists():
        return sha256("")
    return sha256(path.read_text(encoding="utf-8"))


class AtomicFile:
    """Per-file fcntl.LOCK_EX context manager with tmpfile+rename semantics."""

    def __init__(self, path: Path):
        self.path = path
        self._fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()
        self._fh = open(self.path, "r+", encoding="utf-8")
        fcntl.flock(self._fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._fh:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None

    def read(self) -> str:
        self._fh.seek(0)
        return self._fh.read()

    def write(self, content: str) -> None:
        self._fh.seek(0)
        self._fh.write(content)
        self._fh.truncate()
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def append(self, line: str) -> None:
        self._fh.seek(0, os.SEEK_END)
        self._fh.write(line)
        self._fh.flush()
        os.fsync(self._fh.fileno())


class Applier:
    def __init__(self, mission_dir: Path, poll_seconds: float = DEFAULT_POLL_SECONDS):
        self.mission_dir = mission_dir.resolve()
        self.queue_dir = self.mission_dir / "queue"
        self.pending_dir = self.queue_dir / "pending"
        self.applied_dir = self.queue_dir / "applied"
        self.rejected_dir = self.queue_dir / "rejected"
        self.lock_dir = self.queue_dir / ".applier.lock"
        self.journal_path = self.queue_dir / "journal.log"
        self.poll_seconds = poll_seconds
        self._running = True
        self._applied_ids: set[str] = set()

    # ---- lifecycle ----

    def setup_dirs(self) -> None:
        for d in (self.queue_dir, self.pending_dir, self.applied_dir, self.rejected_dir):
            d.mkdir(parents=True, exist_ok=True)

    def acquire_singleton(self) -> bool:
        try:
            self.lock_dir.mkdir()
        except FileExistsError:
            pid_file = self.lock_dir / "pid.txt"
            if pid_file.exists():
                try:
                    pid = int(pid_file.read_text().strip().split()[0])
                    os.kill(pid, 0)
                    return False
                except (ValueError, ProcessLookupError, PermissionError):
                    pass
            shutil.rmtree(self.lock_dir, ignore_errors=True)
            try:
                self.lock_dir.mkdir()
            except FileExistsError:
                return False
        (self.lock_dir / "pid.txt").write_text(f"{os.getpid()} {utc_now()}\n")
        return True

    def release_singleton(self) -> None:
        shutil.rmtree(self.lock_dir, ignore_errors=True)

    def replay_journal(self) -> None:
        if not self.journal_path.exists():
            return
        for line in self.journal_path.read_text().splitlines():
            parts = line.strip().split("|", 2)
            if len(parts) >= 2 and parts[1].strip() == "APPLIED":
                rid = parts[0].strip()
                self._applied_ids.add(rid)

    def stop(self, *_args) -> None:
        self._running = False

    # ---- drain ----

    def drain_once(self) -> int:
        pending = sorted(
            self.pending_dir.glob("*.json"),
            key=lambda p: (self._read_field(p, "submitted_utc") or "", p.name),
        )
        count = 0
        for req_path in pending:
            try:
                req = json.loads(req_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._reject(req_path, None, "json-decode-error")
                continue

            rid = req.get("request_id", "")
            if rid in self._applied_ids:
                self._archive(req_path, "applied", rid, idempotent=True)
                count += 1
                continue

            ok, reason = self._validate(req)
            if not ok:
                self._reject(req_path, req, reason)
                continue

            ok, reason = self._check_preconditions(req)
            if not ok:
                self._reject(req_path, req, reason)
                continue

            ok, reason = self._check_hash(req)
            if not ok and req.get("fallback") == "REJECT":
                self._reject(req_path, req, reason)
                continue

            try:
                self._apply(req)
                self._journal(rid, "APPLIED", req.get("intent", ""))
                self._applied_ids.add(rid)
                self._archive(req_path, "applied", rid)
                count += 1
            except Exception as e:
                self._journal(rid, "ERROR", repr(e)[:200])
                self._reject(req_path, req, f"apply-failed: {e!r}")

        return count

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)

        if not self.acquire_singleton():
            print("[applier] another applier is running; exiting", file=sys.stderr)
            sys.exit(1)

        try:
            self.setup_dirs()
            self.replay_journal()
            print(f"[applier] started pid={os.getpid()} mission={self.mission_dir}")
            while self._running:
                self.drain_once()
                time.sleep(self.poll_seconds)
        finally:
            self.release_singleton()
            print("[applier] stopped")

    # ---- validation ----

    def _validate(self, req: dict[str, Any]) -> tuple[bool, str]:
        required = (
            "schema_version",
            "request_id",
            "submitted_utc",
            "agent",
            "lane",
            "target_file",
            "intent",
            "payload",
        )
        for field in required:
            if field not in req:
                return False, f"missing-field:{field}"
        if req["schema_version"] != SCHEMA_VERSION:
            return False, f"schema-version:{req['schema_version']}"
        if req["intent"] not in INTENTS:
            return False, f"unknown-intent:{req['intent']}"
        if not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", req["submitted_utc"]):
            return False, "submitted_utc-format"
        return True, ""

    def _check_preconditions(self, req: dict[str, Any]) -> tuple[bool, str]:
        preconds = req.get("preconditions") or {}
        if not preconds:
            return True, ""

        required_phase = preconds.get("required_phase")
        if required_phase:
            events_path = self.mission_dir / ".mission-events"
            current_phase = self._current_phase(events_path)
            if current_phase != required_phase:
                return False, f"phase-mismatch:want={required_phase}:got={current_phase}"

        return True, ""

    def _check_hash(self, req: dict[str, Any]) -> tuple[bool, str]:
        expected = req.get("expected_hash_before")
        if not expected:
            return True, ""
        target = self.mission_dir / req["target_file"]
        actual = hash_file(target)
        if actual != expected:
            return False, f"hash-mismatch:expected={expected[:8]}:actual={actual[:8]}"
        return True, ""

    def _current_phase(self, events_path: Path) -> str | None:
        if not events_path.exists():
            return None
        for line in reversed(events_path.read_text().splitlines()):
            m = re.search(r"->\s*(PHASE-[A-Z-]+)", line)
            if m:
                return m.group(1)
        return None

    # ---- apply ----

    def _apply(self, req: dict[str, Any]) -> None:
        intent = req["intent"]
        target = self.mission_dir / req["target_file"]
        payload = req["payload"]
        handler = getattr(self, f"_apply_{intent.lower()}")
        handler(target, payload, req)

    def _apply_status_update(self, target: Path, payload: dict[str, Any], req: dict[str, Any]) -> None:
        lane = payload["lane"]
        with AtomicFile(target) as f:
            content = f.read()
            pattern = rf"^\| {re.escape(lane.upper())}\s*\|.*$"
            matches = re.findall(pattern, content, re.MULTILINE | re.IGNORECASE)
            if len(matches) != 1:
                raise ValueError(f"status-row-not-unique:lane={lane}:matches={len(matches)}")
            new_row = (
                f"| {lane.upper():<9} | {payload.get('agent', req['agent'])} "
                f"| {payload['new_state']} | {payload['new_utc']} | {payload['new_notes']} |"
            )
            f.write(content.replace(matches[0], new_row))

    def _apply_tasks_bracket(self, target: Path, payload: dict[str, Any], req: dict[str, Any]) -> None:
        task_id = payload["task_id"]
        new_bracket = payload["new_bracket"]
        with AtomicFile(target) as f:
            content = f.read()
            pattern = rf"^\[[^\]]+\]\s+(\[LANE-[A-Z]\]\s+`{re.escape(task_id)}`.*)$"
            matches = re.findall(pattern, content, re.MULTILINE)
            if len(matches) != 1:
                raise ValueError(f"task-not-unique:id={task_id}:matches={len(matches)}")
            old_line = re.search(pattern, content, re.MULTILINE).group(0)
            new_line = f"{new_bracket} {matches[0]}"
            f.write(content.replace(old_line, new_line))

    def _apply_history_append(self, target: Path, payload: dict[str, Any], req: dict[str, Any]) -> None:
        line = payload["line"].rstrip("\n") + "\n"
        if not re.match(
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z \| agent-[a-f0-9]+ \| [A-F] \| ",
            line,
        ):
            raise ValueError(f"history-line-format:{line[:80]}")
        with AtomicFile(target) as f:
            f.append(line)

    def _apply_mission_event_append(self, target: Path, payload: dict[str, Any], req: dict[str, Any]) -> None:
        line = payload["line"].rstrip("\n") + "\n"
        if not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z ", line):
            raise ValueError(f"event-line-format:{line[:80]}")
        with AtomicFile(target) as f:
            f.append(line)

    def _apply_claim_dir_create(self, target: Path, payload: dict[str, Any], req: dict[str, Any]) -> None:
        # target is conventionally claims/ but we use payload.task_id
        claims_dir = self.mission_dir / "claims" / payload["task_id"]
        try:
            claims_dir.mkdir(parents=True)
        except FileExistsError:
            owner_file = claims_dir / "owner.txt"
            if owner_file.exists():
                existing = owner_file.read_text().strip().split()
                if existing and existing[0] != payload["owner_agent"]:
                    raise ValueError(f"claim-already-owned:{existing[0]}")
            # idempotent: same owner re-creating is fine
        owner_file = claims_dir / "owner.txt"
        owner_file.write_text(f"{payload['owner_agent']} {payload.get('owner_lane', '')} {req['submitted_utc']}\n")

    def _apply_claim_dir_done(self, target: Path, payload: dict[str, Any], req: dict[str, Any]) -> None:
        claims_dir = self.mission_dir / "claims" / payload["task_id"]
        owner_file = claims_dir / "owner.txt"
        if not owner_file.exists():
            raise ValueError(f"claim-no-owner-file:{payload['task_id']}")
        owner_agent = owner_file.read_text().strip().split()[0]
        if owner_agent != payload["agent"]:
            raise ValueError(f"claim-owner-mismatch:owner={owner_agent}:done-by={payload['agent']}")
        (claims_dir / "done").touch()

    # ---- archive ----

    def _archive(self, req_path: Path, kind: str, rid: str, idempotent: bool = False) -> None:
        dest_dir = self.applied_dir if kind == "applied" else self.rejected_dir
        dest = dest_dir / f"{rid or req_path.stem}.json"
        try:
            req_path.rename(dest)
        except OSError:
            shutil.move(str(req_path), str(dest))
        if idempotent:
            (dest_dir / f"{rid}.idempotent").touch()

    def _reject(self, req_path: Path, req: dict[str, Any] | None, reason: str) -> None:
        rid = (req or {}).get("request_id", req_path.stem)
        self._archive(req_path, "rejected", rid)
        (self.rejected_dir / f"{rid}-reason.txt").write_text(f"{utc_now()}\n{reason}\n")
        self._journal(rid, "REJECTED", reason)

    def _journal(self, rid: str, status: str, detail: str) -> None:
        line = f"{rid} | {status} | {detail}\n"
        with AtomicFile(self.journal_path) as f:
            f.append(line)

    @staticmethod
    def _read_field(path: Path, field: str) -> str | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get(field)
        except (json.JSONDecodeError, OSError):
            return None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mission-dir", required=True, type=Path)
    p.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    args = p.parse_args()
    Applier(args.mission_dir, poll_seconds=args.poll_seconds).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
