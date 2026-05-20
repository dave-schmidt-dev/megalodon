"""V9 M2 — parse api-contract.md into structured dict.

Spec: docs/superpowers/specs/2026-05-16-v9-m2-contract-scan-design.md §6.

The contract doc is plain Markdown with strict structural conventions:
each endpoint is a heading followed by a fenced ```yaml block. We extract
the YAML blocks deterministically and ignore everything else.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_YAML_BLOCK_RE = re.compile(r"^```yaml\s*\n(.*?)\n```\s*$", re.MULTILINE | re.DOTALL)


class ContractParseError(ValueError):
    """Raised when api-contract.md contains malformed YAML."""


def load_contract(path: Path) -> dict[str, Any]:
    """Parse api-contract.md → {"endpoints": [...]}.

    Each endpoint dict has at least `method` and `path`; other keys
    (`response_model`, `status`, `content_type`, `fe_consumers`,
    `sse_events`, `description`) are optional and passed through verbatim
    from the YAML block.

    Raises:
        ContractParseError: if any YAML block fails to parse.
    """
    text = Path(path).read_text(encoding="utf-8")
    endpoints: list[dict[str, Any]] = []
    for match in _YAML_BLOCK_RE.finditer(text):
        try:
            block = yaml.safe_load(match.group(1))
        except yaml.YAMLError as e:
            raise ContractParseError(f"YAML parse error in {path}: {e}") from e
        if not isinstance(block, dict):
            continue
        if "method" not in block or "path" not in block:
            continue  # Not an endpoint block; skip
        endpoints.append(block)
    return {"endpoints": endpoints}
