"""Argument validation for v9 helper scripts (Codex CR-4 regex inventory).

Lane/task-id constants are built from the default v9.0 MissionConfig shape at
import time. CWD is used only to satisfy the synthesize() signature — only
lane/phase/pattern data is consumed; mission.id and utc_started are irrelevant
to the validators.
"""

from __future__ import annotations

import re
from pathlib import Path

from megalodon_ui.mission_config.default_v9_0_shape import synthesize
from megalodon_ui.mission_config.regex_builder import build_lane_re, build_task_id_re
from megalodon_ui.mission_config.schema import MissionConfig

_default_config = synthesize(Path.cwd())

TASK_ID_RE: re.Pattern = build_task_id_re(_default_config)
LANE_RE: re.Pattern = build_lane_re(_default_config)
AGENT_RE = re.compile(r"^agent-[0-9a-f]{4}$")
SEVERITY_RE = re.compile(
    r"^(DELTA|NIT|MAJOR|BLOCKING|TIER-1|TIER-2|MEDIUM|MINOR"
    r"|TERMINAL|RECOVERY|EXEC-PASS|BLOCKED-DEGRADED)$"
)
NOTES_CHARSET_RE = re.compile(r"^[\w\s.,:/()\-_\[\]'\"=@#+*?!&]*$")
# Charset excludes shell metacharacters: backtick, dollar, semicolon, pipe, > and <.
# The forbidden-list in _check_notes_like enforces these explicitly with better
# error messages; regex provides defense-in-depth catchall for anything else.

LANE_LONG_TO_SHORT: dict[str, str] = {
    lane.name: lane.short for lane in _default_config.lanes
}

_NOTES_MAX = 2000
_SUMMARY_MAX = 200


def _check(regex: re.Pattern, value: str, name: str) -> None:
    if not isinstance(value, str) or not regex.match(value):
        raise ValueError(f"invalid {name}: {value!r}")


def validate_task_id(value: str) -> None:
    _check(TASK_ID_RE, value, "task_id")


def validate_lane(value: str) -> None:
    _check(LANE_RE, value, "lane")


def validate_agent(value: str) -> None:
    _check(AGENT_RE, value, "agent")


def validate_severity(value: str) -> None:
    _check(SEVERITY_RE, value, "severity")


def _check_notes_like(value: str, name: str, max_len: int) -> None:
    if not isinstance(value, str):
        raise ValueError(f"invalid {name}: not a string")
    if len(value) > max_len:
        raise ValueError(f"{name} too long: {len(value)} > {max_len}")
    forbidden = ("`", "$", "|", ";", ">", "<")
    for ch in forbidden:
        if ch in value:
            raise ValueError(f"{name} contains forbidden character {ch!r}: {value!r}")
    if not NOTES_CHARSET_RE.match(value):
        raise ValueError(f"{name} contains disallowed characters: {value!r}")


def validate_notes(value: str) -> None:
    _check_notes_like(value, "notes", _NOTES_MAX)


def validate_summary(value: str) -> None:
    _check_notes_like(value, "summary", _SUMMARY_MAX)


def validators_from_config(config: MissionConfig) -> dict:
    """Build validator regexes from a specific MissionConfig.

    Returns dict with keys: 'task_id_re', 'lane_re', 'lane_long_to_short'.
    Used by callers (e.g., server.py) that operate against a non-default mission.
    """
    return {
        "task_id_re": build_task_id_re(config),
        "lane_re": build_lane_re(config),
        "lane_long_to_short": {lane.name: lane.short for lane in config.lanes},
    }
