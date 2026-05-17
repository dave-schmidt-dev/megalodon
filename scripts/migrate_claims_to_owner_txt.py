"""V9 M1 — backfill owner.txt for pre-v9 claim directories (CR-6).

Walks `claims/*/` under mission_dir. For each claim that lacks
`owner.txt`:
  1. Try to infer owner from STATUS.md (look for the lane row whose
     `working: <task_id>` matches the claim directory name).
  2. Try to infer from HISTORY.md (look for an attribution line with the
     task_id).
  3. Fall back to `default_owner` (defaults to "legacy-pre-v9") with
     the current UTC.
  4. Write `owner.txt` atomically.

Idempotent: skips claims that already have owner.txt.

Run once during v8→v9 cutover to satisfy the applier's strict-mode
B4 check (`scripts/_backends/queue_client.py` rejects claim dirs without
an owner.txt).

Usage:
    python3 scripts/migrate_claims_to_owner_txt.py --mission-dir PATH [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _infer_owner(mission_dir: Path, task_id: str) -> str | None:
    """Best-effort owner inference from STATUS.md / HISTORY.md.

    Returns the agent string (e.g. 'agent-aaaa') or None.
    """
    status = mission_dir / "STATUS.md"
    if status.exists():
        m = re.search(
            rf"^\|\s*\w+\s*\|\s*(agent-[0-9a-f]+)\s*\|.*?{re.escape(task_id)}",
            status.read_text(), re.MULTILINE,
        )
        if m:
            return m.group(1)
    history = mission_dir / "HISTORY.md"
    if history.exists():
        m = re.search(
            rf"\|\s*(agent-[0-9a-f]+)\s*\|\s*\w+\s*\|\s*{re.escape(task_id)}",
            history.read_text(),
        )
        if m:
            return m.group(1)
    return None


def migrate(
    mission_dir: Path,
    *,
    dry_run: bool = False,
    default_owner: str = "legacy-pre-v9",
) -> int:
    """Backfill missing owner.txt files. Returns count of claims migrated."""
    claims_dir = mission_dir / "claims"
    if not claims_dir.is_dir():
        return 0
    n = 0
    for child in sorted(claims_dir.iterdir()):
        if not child.is_dir():
            continue
        owner_file = child / "owner.txt"
        if owner_file.exists():
            continue
        task_id = child.name
        owner = _infer_owner(mission_dir, task_id) or default_owner
        content = f"{owner} {_utc_now()}\n"
        if not dry_run:
            _atomic_write(owner_file, content)
        n += 1
        prefix = "[dry-run] " if dry_run else ""
        print(f"{prefix}migrated claims/{task_id}: {owner}")
    return n


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="migrate_claims_to_owner_txt")
    p.add_argument("--mission-dir", required=True, type=Path)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--default-owner", default="legacy-pre-v9")
    args = p.parse_args(argv)
    n = migrate(
        args.mission_dir.resolve(),
        dry_run=args.dry_run,
        default_owner=args.default_owner,
    )
    prefix = "[dry-run] " if args.dry_run else ""
    print(f"\n{prefix}{n} claim(s) migrated.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
