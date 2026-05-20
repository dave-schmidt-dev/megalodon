"""CV-4 semantic regex equivalence corpus.

Returns three lists of task-ID strings used to prove v9.1's regex_builder
output matches v9.0's hardcoded TASK_ID_RE on identical inputs.
"""

from __future__ import annotations
from pathlib import Path
import re


def archive_task_ids(repo_root: Path) -> list[str]:
    """Extract every distinct task-ID-like string from .archive/**/HISTORY.md
    and .archive/**/TASKS.md.

    Patterns to extract: anything matching the v9.0 shape ^[A-Z][\\w\\-\\.]*$
    found inside backticks in HISTORY/TASKS markdown. De-duplicated, sorted.

    Returns empty list if .archive/ doesn't exist or has no qualifying files
    (CI / fresh-clone scenario). The test that consumes this should still
    pass with an empty list — it only asserts that the new and v9.0 regexes
    agree on the corpus, not that the corpus is non-empty."""
    archive_dir = repo_root / ".archive"
    if not archive_dir.exists():
        return []

    # Broad shape: starts with uppercase letter, rest are word chars, hyphens, dots
    broad_re = re.compile(r"^[A-Z][\w\-\.]*$")
    seen: set[str] = set()

    for md_file in archive_dir.rglob("*.md"):
        if md_file.name not in ("HISTORY.md", "TASKS.md"):
            continue
        try:
            text = md_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Extract tokens inside backticks
        for token in re.findall(r"`([^`\n]+)`", text):
            token = token.strip()
            if broad_re.match(token) and token not in seen:
                seen.add(token)

    return sorted(seen)


def positive_corpus() -> list[str]:
    """30 hand-curated positive task IDs covering every v9.0 prefix family.

    Returns IDs that MUST match the v9.0 TASK_ID_RE (and therefore should
    also match the v9.1 builder output)."""
    return [
        # P\d+(\.\d+)?(-[A-F](-to-[A-F])?)? family (8 cases)
        "P1",
        "P2.5",
        "P3-A",
        "P3-A-to-F",
        "P10",
        "P0.1",
        "P0-B",
        "P9-F-to-A",
        # P\d+-RUN-... family (4)
        "P4-RUN-MUTATIONS-E2E-5",
        "P5-RUN-X",
        "P0-RUN-Z_Y",
        "P11-RUN-AB-CD",
        # REPAIR-... (4)
        "REPAIR-X",
        "REPAIR-CLAIM_RACE",
        "REPAIR-MIGRATE-V8",
        "REPAIR-A",
        # OPERATOR-... (4)
        "OPERATOR-NOTIFY",
        "OPERATOR-ACK",
        "OPERATOR-PHASE_FLIP",
        "OPERATOR-A",
        # S-\d+ (3)
        "S-1",
        "S-12",
        "S-999",
        # TEST-\d+ (3)
        "TEST-1",
        "TEST-7",
        "TEST-100",
        # CHALLENGE-... (4) — CR-5
        "CHALLENGE-AB1",
        "CHALLENGE-FOO-BAR",
        "CHALLENGE-X",
        "CHALLENGE-1_2_3",
    ]  # total = 30


def negative_corpus() -> list[str]:
    """30 hand-curated NEGATIVE task IDs that MUST NOT match.

    Includes path traversal, empty, malformed, lowercase, special chars."""
    return [
        # Path-traversal forbidden chars (4)
        "../etc/passwd",
        "foo/bar",
        "foo\\bar",
        "foo\x00bar",
        # Empty / whitespace (3)
        "",
        "   ",
        "\t",
        # Lowercase / wrong-case (4)
        "p1",
        "audit-X",
        "challenge-foo",
        "operator-x",
        # Missing required structure (5)
        "P",
        "P-",
        "P--",
        "RUN-X",
        "RUN-1",
        # Special chars not in v9.0 grammar (5)
        "P1!",
        "P1@home",
        "S-1#tag",
        "TEST-1$",
        "P1.5+P2",
        # Plausible-but-wrong prefixes (5)
        "AUDIT-1",
        "BACKEND-X",
        "META-2",
        "INIT-0",
        "FOO-BAR-BAZ",
        # Multi-line / overlength (4)
        "P1\nP2",
        "P" + "1" * 200,
        "X" * 256,
        "ÿ" + "P1",
    ]  # total = 30
