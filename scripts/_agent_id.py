"""V9 A4 — deterministic agent IDs from (mission, lane, launch_utc).

Replaces `secrets.token_hex(2)` so that re-launching the same lane in the same
mission at the same UTC reproduces the same agent ID. A different launch UTC
(typical re-launch case) yields a different ID via the SHA1 input.
"""
from __future__ import annotations

import hashlib


def deterministic_agent_id(mission_id: str, lane: str, launch_utc: str) -> str:
    """Return ``agent-XXXX`` where XXXX is the first 4 hex chars of SHA1.

    Args:
        mission_id: arbitrary mission identifier (e.g. directory basename).
        lane: lane label (AUDIT/ARCHITECT/BACKEND/FRONTEND/TEST/META).
        launch_utc: ISO-8601 UTC string captured at launch time.

    Returns:
        Stable 10-character ID of the form ``agent-XXXX``.
    """
    seed = f"{mission_id}|{lane}|{launch_utc}".encode("utf-8")
    return "agent-" + hashlib.sha1(seed).hexdigest()[:4]
