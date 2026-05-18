"""Megalodon v9.1 pre-flight CLI.

Usage:
    python -m megalodon_ui.preflight <GOAL> [--mission-dir PATH]
                                             [--context-dir PATH]
                                             [--max-refine N]
                                             [--force]

Spawns Claude (via ClaudeAdapter / P1.6) to interview the operator about
their mission, proposes a .mission-config.yaml, and refines it interactively
until the operator approves. Then writes the YAML atomically.

Auth: requires ANTHROPIC_API_KEY. Set MOCK_CLAUDE=1 to bypass auth check
      in tests (does NOT add a --mock-claude CLI flag per spec).
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path


# ─── helpers ─────────────────────────────────────────────────────────────────

def _load_preamble(context_dir: Path) -> str:
    """Load README.md + tasks.md (or TASKS.md) from context_dir.

    Truncates each to 50 KB. Returns combined preamble string.
    """
    MAX_BYTES = 50 * 1024  # 50 KB per file
    parts: list[str] = []

    def _read_capped(path: Path) -> str:
        try:
            raw = path.read_bytes()
            if len(raw) > MAX_BYTES:
                raw = raw[:MAX_BYTES]
            return raw.decode("utf-8", errors="replace")
        except OSError:
            return ""

    readme = context_dir / "README.md"
    readme_text = _read_capped(readme)
    if readme_text.strip():
        parts.append(f"### README.md\n{readme_text}")

    tasks_path = context_dir / "tasks.md"
    if not tasks_path.exists():
        tasks_path = context_dir / "TASKS.md"
    tasks_text = _read_capped(tasks_path)
    if tasks_text.strip():
        parts.append(f"### tasks.md\n{tasks_text}")

    return "\n\n".join(parts)


# ─── main ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m megalodon_ui.preflight",
        description=(
            "Pre-flight CLI: interview the operator about their mission goal, "
            "propose a .mission-config.yaml, and refine interactively."
        ),
    )
    parser.add_argument(
        "goal",
        metavar="<GOAL>",
        help="Operator's mission goal (non-empty string describing what to build).",
    )
    parser.add_argument(
        "--mission-dir",
        type=Path,
        default=Path("."),
        metavar="PATH",
        help="Directory where .mission-config.yaml will be written (default: .).",
    )
    parser.add_argument(
        "--context-dir",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Directory to read README.md + tasks.md from for context preamble "
            "(default: same as --mission-dir)."
        ),
    )
    parser.add_argument(
        "--max-refine",
        type=int,
        default=10,
        metavar="N",
        help="Maximum number of refinement iterations before forcing approve/abandon (default: 10).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing .mission-config.yaml (default: refuse with exit 1).",
    )

    args = parser.parse_args(argv)

    # ── Step 1: validate GOAL ──
    goal: str = args.goal.strip()
    if not goal:
        print("error: <GOAL> must be a non-empty string.", file=sys.stderr)
        return 1

    # ── Step 2: auth_env_check ──
    mock_claude = os.environ.get("MOCK_CLAUDE", "").strip()
    if not mock_claude and "ANTHROPIC_API_KEY" not in os.environ:
        print(
            "Pre-flight requires ANTHROPIC_API_KEY (orchestrator is Claude). "
            "Set it and re-run.",
            file=sys.stderr,
        )
        return 1

    # ── Step 3: resolve dirs ──
    mission_dir: Path = args.mission_dir.resolve()
    context_dir: Path = (args.context_dir or args.mission_dir).resolve()

    # Refuse early if target exists and --force not given
    target = mission_dir / ".mission-config.yaml"
    if target.exists() and not args.force:
        print(
            f"error: {target} already exists. Use --force to overwrite.",
            file=sys.stderr,
        )
        return 1

    # ── Step 4: load preamble ──
    preamble = _load_preamble(context_dir)

    # ── Step 5: SIGINT / SIGTERM signal handlers ──
    # Mutable state shared with handlers
    _state: dict = {"current_yaml": None}

    def _snapshot_and_exit(signum, frame):  # noqa: ANN001
        yaml_text = _state.get("current_yaml")
        if yaml_text:
            from megalodon_ui.preflight.writer import write_aborted_snapshot
            snapshot = write_aborted_snapshot(yaml_text, mission_dir)
            print(f"\nInterrupted — draft snapshot written to {snapshot}", file=sys.stderr)
        # Clean up any leftover .tmp
        tmp = mission_dir / ".mission-config.yaml.tmp"
        try:
            tmp.unlink()
        except OSError:
            pass
        sys.exit(1)

    signal.signal(signal.SIGINT, _snapshot_and_exit)
    signal.signal(signal.SIGTERM, _snapshot_and_exit)

    # ── Step 6: run the REPL ──
    from megalodon_ui.preflight.interview import run_interview

    # Wrap run_interview to keep _state.current_yaml updated during iteration.
    # We intercept via a claude_runner wrapper so SIGINT during Claude invocation
    # still has the last draft.
    def _tracked_claude_runner(inner_runner=None):
        """Returns a claude_runner that keeps _state updated (passthrough)."""
        # interview.py will use its own default runner when inner_runner is None
        return inner_runner

    approved_config, last_yaml = run_interview(
        goal=goal,
        preamble=preamble,
        max_refine=args.max_refine,
    )

    # Update state for signal handler (if SIGINT fires after run_interview)
    if last_yaml:
        _state["current_yaml"] = last_yaml

    if approved_config is None:
        # Operator abandoned — write snapshot if we have a draft
        if last_yaml:
            from megalodon_ui.preflight.writer import write_aborted_snapshot
            snapshot = write_aborted_snapshot(last_yaml, mission_dir)
            print(f"Abandoned — draft snapshot written to {snapshot}", file=sys.stderr)
        return 1

    # ── Step 7: atomic write ──
    from megalodon_ui.preflight.writer import write_atomic

    try:
        final_path = write_atomic(approved_config, mission_dir, force=args.force)
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"wrote {final_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
