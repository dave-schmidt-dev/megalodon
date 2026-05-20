"""V9 M1 WAL journal — write-ahead log for crash-safe apply.

Per S-8 §B B2 (MAJOR): journal entries written BEFORE apply; replay marks
PENDING-without-APPLIED as PENDING_INDOUBT so the applier can reconcile
on restart (by scanning the target file for the payload contents — for
append intents — or just re-applying overwrite-style intents idempotently).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Journal:
    """Append-only WAL.

    Per-entry shape (one JSON object per line):
        {"rid": ..., "status": "PENDING|APPLIED|REJECTED", ...}

    Replay collapses to a terminal state per request_id; rids that have
    a PENDING with no terminal follow-up are reported as PENDING_INDOUBT.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    # ---- writer ----

    def _append(self, entry: dict[str, Any]) -> None:
        line = json.dumps(entry, sort_keys=True) + "\n"
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    def write_pending(self, rid: str, intent: str, target: str, payload: dict) -> None:
        self._append(
            {
                "rid": rid,
                "status": "PENDING",
                "intent": intent,
                "target": target,
                "payload": payload,
                "utc": _utc(),
            }
        )

    def write_applied(self, rid: str, summary: str) -> None:
        self._append(
            {
                "rid": rid,
                "status": "APPLIED",
                "summary": summary,
                "utc": _utc(),
            }
        )

    def write_rejected(self, rid: str, reason: str) -> None:
        self._append(
            {
                "rid": rid,
                "status": "REJECTED",
                "reason": reason,
                "utc": _utc(),
            }
        )

    # ---- replay ----

    def replay(self) -> dict[str, str]:
        """Returns {rid: terminal_status}.

        terminal_status ∈ {APPLIED, REJECTED, PENDING_INDOUBT}.
        Malformed lines are skipped without aborting replay.
        """
        states: dict[str, str] = {}
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rid = entry.get("rid")
                status = entry.get("status")
                if not rid or status not in {"PENDING", "APPLIED", "REJECTED"}:
                    continue
                if status == "PENDING":
                    # Only set indoubt if we haven't seen a terminal yet.
                    if rid not in states:
                        states[rid] = "PENDING_INDOUBT"
                else:
                    # APPLIED or REJECTED — terminal, wins over any PENDING.
                    states[rid] = status
        return states

    def get_payload(self, rid: str) -> dict | None:
        """Replay-scan for the PENDING entry of rid; return its payload dict.

        Used by `_reconcile_indoubt` to know what to check on the target file.
        Returns None if rid not found.
        """
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("rid") == rid and entry.get("status") == "PENDING":
                    return entry
        return None
