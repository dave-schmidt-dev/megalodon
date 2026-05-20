"""V9 M6 — intent-expired detection + cross-lane reclaim eligibility.

When a worker declares intent to claim a task on the next tick (typical for
REPAIR work), they stamp their STATUS row Notes column with::

    intent-declared: <task-id> @ <utc> walltime: <Nm>

The walltime suffix is optional (default 12 minutes). Expiry threshold is
``max(12, walltime + 5)`` minutes after the declared UTC. After expiry, peers
listed in the task-assignment matrix MAY reclaim without RULE-6 ceremony.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_INTENT_RE = re.compile(
    r"intent-declared:\s*(?P<task>[A-Z0-9_-]+)\s*@\s*(?P<utc>\S+)"
    r"(?:\s*walltime:\s*(?P<walltime>\d+)m)?"
)


def parse_intent(notes: str) -> dict | None:
    """Parse an ``intent-declared:`` directive from a STATUS Notes string.

    Returns ``None`` if no directive is present. Otherwise returns a dict
    with ``task_id`` (str), ``declared_utc`` (str, ISO-8601), and
    ``walltime_minutes`` (int, default 12).
    """
    if not notes:
        return None
    m = _INTENT_RE.search(notes)
    if not m:
        return None
    return {
        "task_id": m["task"],
        "declared_utc": m["utc"],
        "walltime_minutes": int(m["walltime"]) if m["walltime"] else 12,
    }


def is_expired(intent: dict, now: datetime | None = None) -> bool:
    """Return True iff the intent has passed its expiry threshold.

    Threshold = ``max(12, walltime_minutes + 5)`` minutes after
    ``declared_utc``. Heartbeat-ACK absence (detected upstream) may also
    trigger reclaim, but is not modeled here.
    """
    declared = datetime.strptime(intent["declared_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    threshold = timedelta(minutes=max(12, intent["walltime_minutes"] + 5))
    now = now or datetime.now(timezone.utc)
    return (now - declared) > threshold
