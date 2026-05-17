"""Argument validation for v9 helper scripts (Codex CR-4 regex inventory)."""

import re

TASK_ID_RE = re.compile(
    r"^(P\d+(\.\d+)?(-[A-F](-to-[A-F])?)?"
    r"|P\d+-RUN-[A-Z0-9_-]+"
    r"|REPAIR-[A-Z0-9_-]+"
    r"|OPERATOR-[A-Z_-]+"
    r"|S-\d+"
    r"|TEST-\d+)$"
)
LANE_RE = re.compile(r"^(AUDIT|ARCHITECT|BACKEND|FRONTEND|TEST|META)$")
AGENT_RE = re.compile(r"^agent-[0-9a-f]{4}$")
SEVERITY_RE = re.compile(
    r"^(DELTA|NIT|MAJOR|BLOCKING|TIER-1|TIER-2|MEDIUM|MINOR"
    r"|TERMINAL|RECOVERY|EXEC-PASS|BLOCKED-DEGRADED)$"
)
NOTES_CHARSET_RE = re.compile(r"^[\w\s.,:/()\-_\[\]'\"=@#+*?!&]*$")
# Charset excludes shell metacharacters: backtick, dollar, semicolon, pipe, > and <.
# The forbidden-list in _check_notes_like enforces these explicitly with better
# error messages; regex provides defense-in-depth catchall for anything else.

LANE_LONG_TO_SHORT = {
    "AUDIT":     "A",
    "ARCHITECT": "B",
    "BACKEND":   "C",
    "FRONTEND":  "D",
    "TEST":      "E",
    "META":      "F",
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
