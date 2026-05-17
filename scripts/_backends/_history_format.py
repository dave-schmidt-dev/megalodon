"""Canonical HISTORY.md line format for v9.1.

Single source of truth for the pipe-delimited history row written by both
direct_fcntl and queue_client backends.
"""

from __future__ import annotations


def format_history_line(
    *,
    utc: str,
    lane: str,
    agent: str,
    task_id: str,
    finding_path: str,
    severity: str,
    notes: str,
) -> str:
    """Canonical HISTORY.md line format for v9.1.

    Matches what was previously formatted by direct_fcntl. queue_client's
    HISTORY append now delegates here.

    Returns the line WITHOUT a trailing newline so callers control
    line-ending behaviour (direct_fcntl appends \\n; queue_client passes
    the bare line to the queue applier).

    Args:
        utc: ISO-8601 timestamp string, e.g. ``2026-05-16T22:30:00Z``.
        lane: Lane short code (single letter, e.g. ``A``).
        agent: Agent identifier string.
        task_id: Task identifier string.
        finding_path: Relative path to the finding file.
        severity: Severity label, e.g. ``DELTA``.
        notes: Free-form notes; only the first line is included.
    """
    notes_first = notes.split("\n", 1)[0]
    return (
        f"{utc} | {agent} | {lane} | {task_id} | "
        f"{finding_path} | {severity} ({notes_first})"
    )
