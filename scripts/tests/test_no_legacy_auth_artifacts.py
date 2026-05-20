"""CI gate: no legacy auth artifacts in code/test trees (WR-11 + gap 2).

Plan §6.3: v9.2 auth is bearer-in-hash + cookie-only. Any of the following
in code or tests would be a regression:

- ``?t=<token>`` — bearer in query string (logged everywhere)
- ``X-Megalodon-Token`` — custom header bearer (predates the cookie design)
- ``bearer=...`` — bearer as form/body field outside the exchange payload
- ``api_key=`` / ``api-key=`` / ``jwt=`` — anti-patterns we never want

Scope per plan: ``megalodon_ui/``, ``ui/static/``, ``scripts/tests/``,
``ui/tests/``. The test file itself constructs its forbidden literals via
string concatenation so it does not match its own grep — no allowlist
needed for self-reference.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

SCAN_DIRS = (
    REPO_ROOT / "megalodon_ui",
    REPO_ROOT / "ui" / "static",
    REPO_ROOT / "scripts" / "tests",
    REPO_ROOT / "ui" / "tests",
)

# Constructed via concatenation so the test file does not match its own grep.
_QM = "?"
_FORBIDDEN_PATTERNS: tuple[str, ...] = (
    _QM + "t=",
    "X-Megalodon" + "-Token",
    "bearer" + "=",
    "api_key" + "=",
    "api-key" + "=",
    "jwt" + "=",
)

# Case-insensitive for `bearer=` (could appear capitalized in legacy code).
_FORBIDDEN_RE = re.compile(
    "|".join(re.escape(p) for p in _FORBIDDEN_PATTERNS),
    re.IGNORECASE,
)

# Files allowed to mention these patterns (e.g., plan docs, this test).
_ALLOWLIST_RELATIVE: frozenset[str] = frozenset(
    {
        # Self: constructed strings won't match anyway, but explicit is kinder
        # to future readers running the file by hand.
        "scripts/tests/test_no_legacy_auth_artifacts.py",
    }
)

# File extensions to scan; binary/asset extensions are skipped wholesale.
_SCAN_SUFFIXES: frozenset[str] = frozenset(
    {
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".html",
        ".css",
        ".md",
        ".json",
        ".yaml",
        ".yml",
    }
)

# Generated / vendored paths to skip — bundled JS, build outputs, caches.
_SKIP_PATH_PARTS: frozenset[str] = frozenset(
    {
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        "playwright-report",
        "test-results",
        "dist",
        "build",
        ".venv",
    }
)


def _iter_scanned_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_DIRS:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix not in _SCAN_SUFFIXES:
                continue
            if any(part in _SKIP_PATH_PARTS for part in p.parts):
                continue
            files.append(p)
    return files


def test_no_legacy_auth_artifacts_present():
    offenders: list[str] = []
    for path in _iter_scanned_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in _ALLOWLIST_RELATIVE:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if _FORBIDDEN_RE.search(line):
                offenders.append(f"{rel}:{i}: {line.strip()}")
    assert not offenders, (
        "Legacy auth artifacts detected — must use bearer-in-hash + cookie:\n"
        + "\n".join(offenders)
    )


def test_audit_actually_scans_files():
    """Sanity: SCAN_DIRS resolve to real trees, and at least one file is read."""
    files = _iter_scanned_files()
    assert files, f"no files matched under {SCAN_DIRS}"
