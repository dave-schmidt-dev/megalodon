#!/usr/bin/env python3
"""V9 M1 — Megalodon queue applier.

Drains `queue/pending/*.json` in submitted_utc order, applies each request
to its target file under per-file `fcntl.LOCK_EX`, and archives applied /
rejected requests. Singleton (one applier per mission), enforced via
`queue/.applier.lock/` mkdir-atomic. Crash-safe via WAL `journal.log`
(S-8 §B B2): journal-before-apply with `_reconcile_indoubt` on restart.

Heartbeat refreshed every drain cycle to `.applier.lock/heartbeat.txt`
(S-8 §B B3) so workers can detect dead applier within `<poll * N>` seconds.

Strict-mode owner enforcement (S-8 §B B4): pre-v9 claim dirs without
`owner.txt` are REJECTED (not stolen). Use
`scripts/migrate_claims_to_owner_txt.py` to migrate legacy claims.

Q1 intents (S-8 §A Q1): STATUS_ROW_INSERT, TASKS_INJECT,
MISSION_EVENT_CORRECTION (additive to original 6).

Usage:
    python -m megalodon_ui.queue.applier --mission-dir PATH [--poll-seconds 2] [--debug]

Spec: docs/superpowers/specs/2026-05-16-v9-m1-queue-trio-design.md
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

from .journal import Journal
from .schemas import validate_payload
from megalodon_ui.mission_config import load_mission_config
from megalodon_ui.mission_config.schema import validate_task_id_with_config
from megalodon_ui.mission_config.regex_builder import build_lane_short_charclass

SCHEMA_VERSION = 1
DEFAULT_POLL_SECONDS = 2.0

INTENTS = {
    # Original 6 (QUEUE-DESIGN.md)
    "STATUS_UPDATE",
    "TASKS_BRACKET",
    "HISTORY_APPEND",
    "MISSION_EVENT_APPEND",
    "CLAIM_DIR_CREATE",
    "CLAIM_DIR_DONE",
    # Q1 additions (S-8 §A Q1)
    "STATUS_ROW_INSERT",
    "TASKS_INJECT",
    "MISSION_EVENT_CORRECTION",
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
    """Per-file fcntl.LOCK_EX context manager with read/write/append helpers."""

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
    def __init__(self, mission_dir: Path, poll_seconds: float = DEFAULT_POLL_SECONDS,
                 debug: bool = False):
        self.mission_dir = Path(mission_dir).resolve()
        self.queue_dir = self.mission_dir / "queue"
        self.pending_dir = self.queue_dir / "pending"
        self.applied_dir = self.queue_dir / "applied"
        self.rejected_dir = self.queue_dir / "rejected"
        self.lock_dir = self.queue_dir / ".applier.lock"
        self.journal_path = self.queue_dir / "journal.log"
        self.poll_seconds = poll_seconds
        self.debug = debug
        self._running = True
        self._applied_ids: set[str] = set()
        self._rejected_ids: set[str] = set()

        # Load mission-bound config so lane-regex is derived from the actual
        # mission rather than the v9.0 default. Falls back to default shape
        # when no MISSION.md / .mission-events is present (new or bare missions).
        _mission_cfg = load_mission_config(self.mission_dir)
        self._lane_short_charclass = build_lane_short_charclass(_mission_cfg)

        # Ensure dirs exist BEFORE the journal opens.
        self.setup_dirs()
        self.journal = Journal(self.journal_path)
        self._replay_journal_state()

    # ---- setup ----

    def setup_dirs(self) -> None:
        for d in (self.queue_dir, self.pending_dir, self.applied_dir,
                  self.rejected_dir):
            d.mkdir(parents=True, exist_ok=True)

    def _replay_journal_state(self) -> None:
        """Populate `_applied_ids` / `_rejected_ids` from WAL.

        For any rid in PENDING_INDOUBT state, reconciliation runs in
        `drain_once` when the pending request_file is processed.
        """
        terminal = self.journal.replay()
        for rid, status in terminal.items():
            if status == "APPLIED":
                self._applied_ids.add(rid)
            elif status == "REJECTED":
                self._rejected_ids.add(rid)

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
            # Stale lock — take over.
            shutil.rmtree(self.lock_dir, ignore_errors=True)
            try:
                self.lock_dir.mkdir()
            except FileExistsError:
                return False
        (self.lock_dir / "pid.txt").write_text(f"{os.getpid()} {utc_now()}\n")
        (self.lock_dir / "start_utc.txt").write_text(utc_now() + "\n")
        self._write_heartbeat()
        return True

    def release_singleton(self) -> None:
        shutil.rmtree(self.lock_dir, ignore_errors=True)

    def _write_heartbeat(self) -> None:
        """S-8 §B B3: refresh `.applier.lock/heartbeat.txt` so workers can
        detect dead applier within `<poll * N>` seconds."""
        try:
            self.lock_dir.mkdir(parents=True, exist_ok=True)
            (self.lock_dir / "heartbeat.txt").write_text(utc_now() + "\n")
        except OSError:
            # Heartbeat is best-effort; don't crash the applier over it.
            pass

    def stop(self, *_args) -> None:
        self._running = False

    # ---- drain ----

    def drain_once(self) -> int:
        self._write_heartbeat()  # S-8 §B B3

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

            # WAL-based dedup: terminal in journal → archive idempotently.
            if rid in self._applied_ids:
                self._archive(req_path, "applied", rid, idempotent=True)
                count += 1
                continue
            if rid in self._rejected_ids:
                self._archive(req_path, "rejected", rid, idempotent=True)
                continue

            # S-8 §B B2: PENDING_INDOUBT — reconcile before re-applying.
            terminal = self.journal.replay().get(rid)
            if terminal == "PENDING_INDOUBT":
                reconciled = self._reconcile_indoubt(rid, req)
                if reconciled == "APPLIED":
                    self.journal.write_applied(rid, "reconciled-already-applied")
                    self._applied_ids.add(rid)
                    self._archive(req_path, "applied", rid, idempotent=True)
                    count += 1
                    continue
                # else: fall through and re-apply.

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
                # S-8 §B B2: write PENDING to journal BEFORE applying.
                self.journal.write_pending(
                    rid, req["intent"], req["target_file"], req.get("payload", {}),
                )
                self._apply(req)
                self.journal.write_applied(rid, "ok")
                self._applied_ids.add(rid)
                self._archive(req_path, "applied", rid)
                count += 1
            except ValueError as e:
                # Apply-time validation/precondition (B4 strict-owner, etc.).
                self.journal.write_rejected(rid, str(e))
                self._rejected_ids.add(rid)
                self._reject(req_path, req, f"apply-failed: {e}")
            except Exception as e:
                # Unexpected (I/O, etc.) — record REJECTED but re-raise so
                # the operator sees the underlying failure; the target file
                # is left in whatever state the partial apply produced
                # (AtomicFile fsync discipline keeps overwrites atomic).
                self.journal.write_rejected(rid, f"unexpected: {e!r}")
                self._rejected_ids.add(rid)
                self._reject(req_path, req, f"apply-failed: {e!r}")
                raise

        return count

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)

        if not self.acquire_singleton():
            print("[applier] another applier is running; exiting", file=sys.stderr)
            sys.exit(1)

        try:
            print(f"[applier] started pid={os.getpid()} mission={self.mission_dir}")
            while self._running:
                try:
                    self.drain_once()
                except Exception as e:
                    print(f"[applier] drain error: {e!r}", file=sys.stderr)
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
        # Pydantic payload validation (Q1 intent prefix etc.).
        try:
            validate_payload(req["intent"], req["payload"])
        except Exception as e:
            return False, f"payload-schema:{e}"
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

    # ---- reconciliation (S-8 §B B2) ----

    def _reconcile_indoubt(self, rid: str, req: dict[str, Any]) -> str:
        """Decide whether a PENDING_INDOUBT rid was actually applied.

        For HISTORY_APPEND / MISSION_EVENT_APPEND / MISSION_EVENT_CORRECTION:
          scan target file for `payload["line"]`; if present → APPLIED.
        For overwrite-style intents (STATUS_UPDATE, TASKS_BRACKET,
        STATUS_ROW_INSERT, TASKS_INJECT, CLAIM_DIR_*):
          re-apply is idempotent / safe → return NOT_APPLIED.

        Returns "APPLIED" or "NOT_APPLIED".
        """
        intent = req.get("intent")
        payload = req.get("payload", {})
        target_file = req.get("target_file", "")
        target = self.mission_dir / target_file if target_file else None

        if intent in ("HISTORY_APPEND", "MISSION_EVENT_APPEND",
                      "MISSION_EVENT_CORRECTION"):
            line = (payload.get("line") or "").rstrip("\n")
            if target and target.exists() and line and line in target.read_text(encoding="utf-8"):
                return "APPLIED"
            return "NOT_APPLIED"
        return "NOT_APPLIED"

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
            pattern = rf"^\|\s*{re.escape(lane.upper())}\s*\|.*$"
            matches = re.findall(pattern, content, re.MULTILINE | re.IGNORECASE)
            if len(matches) != 1:
                raise ValueError(
                    f"status-row-not-unique:lane={lane}:matches={len(matches)}"
                )
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
            # Format: `- [bracket] [LANE-X] `task` — desc`
            pattern = rf"^(-\s+)\[[^\]]+\]\s+(\[LANE-{self._lane_short_charclass}\]\s+`{re.escape(task_id)}`.*)$"
            matches = re.findall(pattern, content, re.MULTILINE)
            if len(matches) != 1:
                raise ValueError(
                    f"task-not-unique:id={task_id}:matches={len(matches)}"
                )
            old_line = re.search(pattern, content, re.MULTILINE).group(0)
            prefix, rest = matches[0]
            new_line = f"{prefix}{new_bracket} {rest}"
            f.write(content.replace(old_line, new_line))

    def _apply_history_append(self, target: Path, payload: dict[str, Any], req: dict[str, Any]) -> None:
        line = payload["line"].rstrip("\n") + "\n"
        if not re.match(
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z \| agent-[a-f0-9]+ \| [A-Za-z]+ \| ",
            line,
        ):
            raise ValueError(f"history-line-format:{line[:80]}")
        with AtomicFile(target) as f:
            if line.strip() in f.read():
                return
            f.append(line)

    def _apply_mission_event_append(self, target: Path, payload: dict[str, Any], req: dict[str, Any]) -> None:
        line = payload["line"].rstrip("\n") + "\n"
        if not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z ", line):
            raise ValueError(f"event-line-format:{line[:80]}")
        with AtomicFile(target) as f:
            if line.strip() in f.read():
                return
            f.append(line)

    def _apply_claim_dir_create(self, target: Path, payload: dict[str, Any], req: dict[str, Any]) -> None:
        claims_dir = self.mission_dir / "claims" / payload["task_id"]
        owner_file = claims_dir / "owner.txt"
        try:
            claims_dir.mkdir(parents=True)
        except FileExistsError:
            # S-8 §B B4 strict: pre-v9 claim dir with NO owner.txt → REJECT.
            if not owner_file.exists():
                raise ValueError(
                    f"claim-exists-no-owner:{payload['task_id']} "
                    "(use scripts/migrate_claims_to_owner_txt.py)"
                )
            existing = owner_file.read_text().strip().split()
            if existing and existing[0] != payload["owner_agent"]:
                raise ValueError(f"claim-already-owned:{existing[0]}")
            # idempotent same owner: rewrite owner.txt with current utc.
        owner_file.write_text(
            f"{payload['owner_agent']} {payload.get('owner_lane', '')} "
            f"{req['submitted_utc']}\n"
        )

    def _apply_claim_dir_done(self, target: Path, payload: dict[str, Any], req: dict[str, Any]) -> None:
        claims_dir = self.mission_dir / "claims" / payload["task_id"]
        owner_file = claims_dir / "owner.txt"
        if not owner_file.exists():
            raise ValueError(f"claim-no-owner-file:{payload['task_id']}")
        owner_agent = owner_file.read_text().strip().split()[0]
        if owner_agent != payload["agent"]:
            raise ValueError(
                f"claim-owner-mismatch:owner={owner_agent}:done-by={payload['agent']}"
            )
        (claims_dir / "done").touch()

    # ---- Q1 intents ----

    def _apply_status_row_insert(self, target: Path, payload: dict[str, Any], req: dict[str, Any]) -> None:
        lane = payload["lane"]
        with AtomicFile(target) as f:
            content = f.read()
            pattern = rf"^\|\s*{re.escape(lane.upper())}\s*\|"
            if re.search(pattern, content, re.MULTILINE | re.IGNORECASE):
                raise ValueError(f"status-row-already-exists:lane={lane}")
            new_row = (
                f"| {lane.upper():<9} | {payload.get('agent', req['agent'])} "
                f"| {payload.get('initial_state', 'idle')} | {payload['initial_utc']} "
                f"| {payload.get('initial_notes', '')} |\n"
            )
            if content and not content.endswith("\n"):
                content += "\n"
            f.write(content + new_row)

    def _apply_tasks_inject(self, target: Path, payload: dict[str, Any], req: dict[str, Any]) -> None:
        task_id = payload["task_id"]
        with AtomicFile(target) as f:
            content = f.read()
            if re.search(rf"`{re.escape(task_id)}`", content):
                raise ValueError(f"task-already-exists:id={task_id}")
            lane = payload["lane"]
            bracket = payload.get("bracket", "[ ]")
            desc = payload["description"]
            new_line = f"- {bracket} [LANE-{lane}] `{task_id}` — {desc}\n"
            after = payload.get("after_task_id")
            if after:
                pattern = rf"(^.*`{re.escape(after)}`.*\n)"
                m = re.search(pattern, content, re.MULTILINE)
                if not m:
                    raise ValueError(f"after-task-not-found:id={after}")
                new_content = content[:m.end()] + new_line + content[m.end():]
                f.write(new_content)
            else:
                if content and not content.endswith("\n"):
                    content += "\n"
                f.write(content + new_line)

    def _apply_mission_event_correction(self, target: Path, payload: dict[str, Any], req: dict[str, Any]) -> None:
        line = payload["line"].rstrip("\n") + "\n"
        # Schema already enforced "CORRECTION by " prefix.
        with AtomicFile(target) as f:
            if line.strip() in f.read():
                return
            f.append(line)

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
        (self.rejected_dir / f"{rid}-reason.txt").write_text(
            f"{utc_now()}\n{reason}\n"
        )

    @staticmethod
    def _read_field(path: Path, field: str) -> str | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get(field)
        except (json.JSONDecodeError, OSError):
            return None


def main() -> int:
    p = argparse.ArgumentParser(
        prog="python -m megalodon_ui.queue.applier",
        description="Megalodon v9 queue applier daemon.",
    )
    p.add_argument("--mission-dir", required=True, type=Path)
    p.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    p.add_argument("--debug", action="store_true")
    args = p.parse_args()
    Applier(args.mission_dir, poll_seconds=args.poll_seconds,
            debug=args.debug).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
