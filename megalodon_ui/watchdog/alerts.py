"""V9 A1 — watchdog alert dedup + finding write."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


_ACTION_HINTS = {
    "CRASHED": "Check lane terminal; restart worker with `read launch.md` if needed.",
    "STATUS-STALE": "Worker may be in a long tool call. Check terminal; SIGNAL the lane if stuck.",
    "HUNG": (
        "Session JSONL has stopped writing despite STATUS heartbeat — likely hung "
        "mid-tool-call. Investigate."
    ),
    "STREAM-LOG-SIZE": (
        "Per-lane stream log has grown past the warn threshold. Rotate or "
        "truncate `<mission>/.fleet/<short>.stream.log` to reclaim space."
    ),
}


# Severity mapping for the structured alert log (contract §2). All current
# watchdog detectors are TIER-1 operator-attention events; CRASHED is the most
# urgent. Kept as a table so a future detector can register a different tier.
_ALERT_SEVERITY = {
    "CRASHED": "critical",
    "STATUS-STALE": "warning",
    "HUNG": "warning",
    "STREAM-LOG-SIZE": "warning",
}

#: Structured JSONL alert log (contract §2). One line per fired alert so
#: ``GET /api/v1/alerts`` has a clean machine-readable source; the markdown
#: findings remain for back-compat / human reading.
ALERTS_JSONL_RELPATH = Path(".fleet") / "watchdog-alerts.jsonl"


class AlertManager:
    def __init__(self, mission_dir: Path):
        self.mission_dir = mission_dir
        self.findings_dir = mission_dir / "findings"
        self.state_path = mission_dir / ".scratch" / "watchdog" / "state.json"
        self.alerts_jsonl_path = mission_dir / ALERTS_JSONL_RELPATH
        self._state = self._load_state()

    def _append_jsonl(self, entry: dict) -> None:
        """Append one structured alert record to the JSONL log (best-effort).

        Contract §2 source of truth for ``GET /api/v1/alerts``. A write failure
        must not crash the watchdog pass (the markdown finding is still written),
        so any OSError is swallowed.
        """
        try:
            self.alerts_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            with self.alerts_jsonl_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except OSError:
            pass

    def _load_state(self) -> dict:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        return {"started_utc": _utc(), "last_poll_utc": None, "lanes": {}}

    def _save_state(self) -> None:
        self._state["last_poll_utc"] = _utc()
        _atomic_write(self.state_path, json.dumps(self._state, indent=2))

    def alert(
        self, lane: str, alert_type: str, *, evidence: Iterable[str]
    ) -> Path | None:
        """Write SIGNAL finding unless duplicate. Returns path or None."""
        lane_state = self._state["lanes"].get(lane, {})
        if lane_state.get("last_alert_type") == alert_type:
            return None  # Dedup

        utc = _utc()
        filename = f"watchdog-ALERT-{lane}-{utc.replace(':', '-')}.md"
        path = self.findings_dir / filename

        # Materialize evidence to a list: it is consumed twice (markdown body +
        # structured JSONL) and the caller may pass a one-shot generator.
        evidence_list = list(evidence)
        evidence_lines = (
            "\n".join(f"- {e}" for e in evidence_list) or "- (no additional evidence)"
        )
        action = _ACTION_HINTS.get(alert_type, "Operator decision required.")
        body = f"""---
signal-type: WATCHDOG-ALERT
addressed-to: operator
severity: TIER-1
lane: {lane}
alert-type: {alert_type}
utc: {utc}
agent: watchdog
expected-ack: operator decides — restart, signal worker, or dismiss
---

# Watchdog alert: {lane} lane {alert_type}

**Detected at:** {utc}

**Signal:** {lane} detector reports state `{alert_type}`.

**Suggested action:** {action}

**Evidence:**
{evidence_lines}

This is an automated notification. The watchdog will NOT auto-respawn or
take any other action. Operator decision required.
"""
        self.findings_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, body)

        # Contract §2: append a structured record so GET /api/v1/alerts has a
        # clean source. ``kind`` mirrors the markdown ``alert-type``; ``message``
        # is the human one-liner; ``evidence`` is the raw list.
        self._append_jsonl(
            {
                "ts": utc,
                "lane": lane,
                "kind": alert_type,
                "severity": _ALERT_SEVERITY.get(alert_type, "warning"),
                "evidence": evidence_list,
                "message": f"{lane} lane {alert_type}",
            }
        )

        self._state["lanes"][lane] = {
            "last_alert_type": alert_type,
            "last_alert_utc": utc,
            "status": "alerted",
        }
        self._save_state()
        return path

    def recover(self, lane: str) -> None:
        """Mark lane as recovered (clear dedup)."""
        self._state["lanes"][lane] = {
            "last_alert_type": None,
            "last_alert_utc": None,
            "status": "ok",
        }
        self._save_state()
