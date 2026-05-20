"""V9 A8 — parse SIGNAL frontmatter from finding files.

Per ``docs/v9/SIGNAL-GRAMMAR.md``, a SIGNAL is a finding-class file whose YAML
frontmatter contains a ``signal-type`` key (e.g., ``SIG-ORCH-001``,
``WATCHDOG-ALERT``, ``OPERATOR-DIRECTIVE``). Non-signal findings are
filtered out by returning ``None``.

Malformed YAML never raises — the parser swallows ``yaml.YAMLError`` and
returns ``None`` so callers can iterate over the findings directory safely.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_signal(path: Path) -> dict[str, Any] | None:
    """Return parsed frontmatter dict if file is a SIGNAL, else ``None``.

    A file is considered a SIGNAL iff:
      * It starts with a YAML frontmatter block delimited by ``---``.
      * The frontmatter parses as a dict.
      * The dict has a ``signal-type`` key.

    Args:
        path: Path to a candidate finding file.

    Returns:
        Parsed frontmatter dict for SIGNALs; ``None`` otherwise (missing
        frontmatter, malformed YAML, non-dict, or no ``signal-type`` key).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = _FM_RE.match(text)
    if not m:
        return None
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None
    if not isinstance(fm, dict):
        return None
    if "signal-type" not in fm:
        return None
    return fm
