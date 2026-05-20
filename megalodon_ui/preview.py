"""Megalodon v9.2 — per-lane argv preview CLI (CR-8 + CV-3)."""

from __future__ import annotations

import argparse
import pathlib
import shlex
import sys
from typing import Any


def _load_adapters() -> dict[str, Any]:
    """Return cli-name -> adapter mapping. Deferred to keep FastAPI out."""
    from megalodon_ui.harnesses.claude import ClaudeAdapter
    from megalodon_ui.harnesses.codex import CodexAdapter
    from megalodon_ui.harnesses.gemini import GeminiAdapter
    from megalodon_ui.harnesses.copilot import CopilotAdapter
    from megalodon_ui.harnesses.cursor import CursorAdapter
    from megalodon_ui.harnesses.vibe import VibeAdapter

    return {
        "claude": ClaudeAdapter(),
        "codex": CodexAdapter(),
        "gemini": GeminiAdapter(),
        "copilot": CopilotAdapter(),
        "cursor": CursorAdapter(),
        "vibe": VibeAdapter(),
    }


def _tmux_lines(
    mission_dir: pathlib.Path,
    lane_short: str,
    argv: list[str],
    cols: int,
    rows: int,
) -> list[str]:
    """Return the three tmux command lines matching tmux.new_session shape."""
    socket = mission_dir / ".fleet" / "tmux.sock"
    name = f"lane-{lane_short}"
    joined_argv = shlex.join(argv)

    line1 = (
        f"tmux -S {shlex.quote(str(socket))}"
        f" new-session -d -s {shlex.quote(name)}"
        f" -x {cols} -y {rows}"
        f" -c {shlex.quote(str(mission_dir))}"
        f" {joined_argv}"
    )
    line2 = (
        f"tmux -S {shlex.quote(str(socket))}"
        f" set-option -t {shlex.quote(name)}"
        f" remain-on-exit on"
    )
    line3 = (
        f"tmux -S {shlex.quote(str(socket))}"
        f" set-environment -t {shlex.quote(name)}"
        f" MEGALODON_FLEET_OWNED 1"
    )
    return [line1, line2, line3]


def preview(mission_dir: pathlib.Path, include_tmux_argv: bool = False) -> int:
    """Print per-lane argv for the mission at mission_dir.

    Returns 0 on success, 1 on config load failure.
    """
    from megalodon_ui._v92_constants import INITIAL_PANE_COLS, INITIAL_PANE_ROWS
    from megalodon_ui.mission_config import load_mission_config

    try:
        mission_config = load_mission_config(mission_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"error: failed to load mission config: {exc}", file=sys.stderr)
        return 1

    adapters = _load_adapters()

    for lane in mission_config.lanes:
        cli = lane.harness.cli
        model = lane.harness.model
        short = lane.short or lane.name

        adapter = adapters.get(cli)
        if adapter is None:
            print(f"error: unknown harness cli {cli!r} for lane {lane.name}", file=sys.stderr)
            return 1

        launch_file = mission_dir / f"launch-{lane.name}.md"
        argv, _env_overlay = adapter.build_argv(
            str(launch_file),
            model=model,
            cwd=mission_dir,
            **({"live_repl": True} if lane.live_repl else {}),
        )

        parts = [
            f"lane={short}",
            f"cli={cli}",
            f"model={model}",
        ]
        if lane.live_repl:
            parts.append("mode=live-repl")
        parts.append(f"argv={shlex.join(argv)}")
        print("  ".join(parts))

        if lane.live_repl and lane.initial_prompt:
            preview_prompt = lane.initial_prompt
            if len(preview_prompt) > 120:
                preview_prompt = preview_prompt[:117] + "..."
            print(f"  initial_prompt (sent post-spawn via tmux send-keys): {preview_prompt}")

        if include_tmux_argv:
            for tmux_line in _tmux_lines(mission_dir, short, argv, INITIAL_PANE_COLS, INITIAL_PANE_ROWS):
                print(f"  {tmux_line}")

    return 0


def main() -> None:
    """Entry point for ``python -m megalodon_ui.preview``."""
    parser = argparse.ArgumentParser(
        prog="python -m megalodon_ui.preview",
        description="Print per-lane CLI argv for a configured Megalodon mission.",
    )
    parser.add_argument("--mission-dir", required=True, help="Path to the mission directory.")
    parser.add_argument(
        "--include-tmux-argv",
        action="store_true",
        default=False,
        help="Also print the planned tmux invocation lines per lane.",
    )
    args = parser.parse_args()

    mission_dir = pathlib.Path(args.mission_dir).resolve()
    if not mission_dir.exists():
        print(f"error: mission dir does not exist: {mission_dir}", file=sys.stderr)
        sys.exit(1)

    rc = preview(mission_dir, include_tmux_argv=args.include_tmux_argv)
    sys.exit(rc)


if __name__ == "__main__":
    main()
