"""V9 A9 — parse Claude Code session JSONL for tokens + cost.

JSONL format per Claude Code: each line is a JSON envelope with optional
``message.usage.{input_tokens, output_tokens, cache_*_tokens}`` fields when
the line represents an assistant turn. Lines without a usage block are
ignored. Malformed (non-JSON) lines are also ignored so a single corrupt
line does not abort the whole parse.

Pricing is documented per million tokens; cost is reported as "estimated"
(not authoritative). Operator can update the ``PRICING`` dict.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Anthropic published pricing as of 2026-05 (per-million tokens)
PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-7": {"in": 15.0, "out": 75.0},
    "claude-sonnet-4-6": {"in": 3.0, "out": 15.0},
    "claude-haiku-4-5-20251001": {"in": 0.80, "out": 4.0},
}


def parse(jsonl_path: Path) -> dict:
    """Parse one Claude Code session JSONL file, returning token + cost totals."""
    jsonl_path = Path(jsonl_path)
    total_in = 0
    total_out = 0
    total_cache_create = 0
    total_cache_read = 0
    model_seen: str | None = None
    n_turns = 0
    started_utc: str | None = None
    last_utc: str | None = None
    with open(jsonl_path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            try:
                env = json.loads(line)
            except json.JSONDecodeError:
                # Skip malformed lines so one bad line doesn't abort parse.
                continue
            if not isinstance(env, dict):
                continue
            ts = env.get("timestamp") or env.get("ts")
            if ts:
                if started_utc is None:
                    started_utc = ts
                last_utc = ts
            msg = env.get("message", env)
            usage = msg.get("usage") if isinstance(msg, dict) else None
            if not usage:
                continue
            total_in += usage.get("input_tokens", 0) or 0
            total_out += usage.get("output_tokens", 0) or 0
            total_cache_create += usage.get("cache_creation_input_tokens", 0) or 0
            total_cache_read += usage.get("cache_read_input_tokens", 0) or 0
            if isinstance(msg, dict):
                model_seen = msg.get("model") or model_seen
            n_turns += 1

    pricing = PRICING.get(model_seen or "", {"in": 0.0, "out": 0.0})
    cost = (total_in + total_cache_create) * pricing[
        "in"
    ] / 1_000_000 + total_out * pricing["out"] / 1_000_000
    return {
        "jsonl_path": str(jsonl_path),
        "model": model_seen,
        "turns": n_turns,
        "tokens": {
            "input": total_in,
            "output": total_out,
            "cache_creation": total_cache_create,
            "cache_read": total_cache_read,
        },
        "estimated_cost_usd": round(cost, 4),
        "started_utc": started_utc,
        "last_utc": last_utc,
    }


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    p.add_argument("jsonl_path", type=Path, nargs="?")
    p.add_argument(
        "--project-glob",
        help="e.g., ~/.claude/projects/*/*.jsonl",
    )
    args = p.parse_args(argv)
    if args.jsonl_path:
        print(json.dumps(parse(args.jsonl_path), indent=2))
        return 0
    if args.project_glob:
        import glob
        import os

        results = []
        for path in glob.glob(os.path.expanduser(args.project_glob)):
            try:
                results.append(parse(Path(path)))
            except Exception as e:  # noqa: BLE001 — diagnostic for operator
                results.append({"jsonl_path": path, "error": str(e)})
        print(json.dumps(results, indent=2))
        return 0
    p.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
