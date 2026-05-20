"""SR-2 CI gate — no unauthorized legacy default-config call sites.

Audits the codebase for any .py file containing one of the four banned symbols:

    _DEFAULT_CONFIG | _synthesize_default | LANE_LONG_TO_SHORT | _LANE_SHORT_CHARCLASS

Every match must appear in ALLOW_LIST below, keyed by relative path from the
repo root.  A future PR that introduces a new match MUST extend ALLOW_LIST with
a one-sentence justification; that edit forces a code-review of every new holdout.

History: Task 1.4 (SR-2 grep audit, v9.2).
  Class A migrations: applier.py (module-level globals removed, instance attr added),
                      primitives.py (mark_complete gains mission_config param).
  Class B allow-listed: all entries below.
"""

import subprocess
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Allow-list — relative_path (from repo root) -> one-sentence justification
# ---------------------------------------------------------------------------

ALLOW_LIST: dict[str, str] = {
    "megalodon_ui/legacy_history.py": (
        "Comment-only reference; no executable synthesis — the string "
        "LANE_LONG_TO_SHORT appears solely as documentation in a dataclass docstring."
    ),
    "megalodon_ui/primitives.py": (
        "Module-level _DEFAULT_CONFIG / LANE_LONG_TO_SHORT are the soft-fallback "
        "defaults for mark_complete's mission_config=None path and for pure-stdlib "
        "test / legacy callers that have no MissionContext; mark_complete accepts "
        "a real MissionConfig on the request-handling path."
    ),
    "scripts/_validation.py": (
        "Pure CLI argument-validator module; LANE_LONG_TO_SHORT is built from the "
        "v9.0 default at import time and is only used to validate agent-invoked CLI "
        "arguments, never from a request handler."
    ),
    "scripts/_shared_state.py": (
        "CLI helper script that imports LANE_LONG_TO_SHORT from _validation; "
        "no request context exists — this script is invoked directly by tmux agents."
    ),
    "scripts/_state_read.py": (
        "CLI helper script that imports LANE_LONG_TO_SHORT from _validation; "
        "no request context exists — this script is invoked directly by tmux agents."
    ),
    "scripts/tests/test_validation.py": (
        "Test file that exercises the CLI validator's LANE_LONG_TO_SHORT constant; "
        "test files are not reachable from request handlers."
    ),
}

# ---------------------------------------------------------------------------
# Patterns and scope
# ---------------------------------------------------------------------------

PATTERNS = [
    "_DEFAULT_CONFIG",
    "_synthesize_default",
    "LANE_LONG_TO_SHORT",
    "_LANE_SHORT_CHARCLASS",
]

# Directories to scan (relative to repo root).
SCAN_DIRS = ["megalodon_ui", "scripts"]

# Files to always exclude from audit (this file itself; non-.py files are
# filtered before classification).
_THIS_FILE_REL = "scripts/tests/test_no_legacy_default_config_callers.py"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _run_grep(root: Path) -> list[str]:
    """Return raw grep output lines (relative_path:lineno:text)."""
    pattern = "|".join(PATTERNS)
    cmd = [
        "grep",
        "-rnE",
        pattern,
        *SCAN_DIRS,
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(root),
    )
    # grep exits 1 when no matches found — that is success here.
    if result.returncode > 1:
        pytest.fail(f"grep failed with rc={result.returncode}: {result.stderr}")
    return result.stdout.splitlines()


def _parse_matches(lines: list[str]) -> list[tuple[str, int, str]]:
    """Parse grep output into (relative_path, lineno, matched_text) tuples.

    Only .py files are included; non-Python files are skipped (they may
    contain references in comments or JSON configs).
    """
    matches = []
    for line in lines:
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        rel_path, lineno_str, text = parts[0], parts[1], parts[2]
        if not rel_path.endswith(".py"):
            continue
        try:
            lineno = int(lineno_str)
        except ValueError:
            continue
        matches.append((rel_path, lineno, text))
    return matches


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


def test_no_unauthorized_legacy_default_config_callers():
    """Every .py match of the banned symbols must be in ALLOW_LIST."""
    grep_path = subprocess.run(["which", "grep"], capture_output=True, text=True)
    if grep_path.returncode != 0:
        pytest.skip("grep not available on PATH")

    root = _repo_root()
    raw_lines = _run_grep(root)
    matches = _parse_matches(raw_lines)

    offenders: list[str] = []
    for rel_path, lineno, text in matches:
        # Exclude this file itself.
        if rel_path == _THIS_FILE_REL:
            continue
        # Exclude __pycache__ and .pyc files.
        if "__pycache__" in rel_path:
            continue
        if rel_path not in ALLOW_LIST:
            offenders.append(f"  {rel_path}:{lineno}: {text.strip()}")

    if offenders:
        offender_list = "\n".join(offenders)
        pytest.fail(
            f"SR-2 gate: {len(offenders)} unauthorized legacy default-config "
            f"call site(s) found.\n"
            f"Each must be classified as Class A (migrate) or Class B (add to "
            f"ALLOW_LIST in {_THIS_FILE_REL} with a one-sentence justification).\n\n"
            f"Offenders:\n{offender_list}"
        )
