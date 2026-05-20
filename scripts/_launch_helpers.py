"""Megalodon v9.1 — launch_fleet.sh Python helper.

Handles:
- render_applescript(): AppleScript generation for iTerm pane layout.
- CLI entry point: ``python3 -m scripts._launch_helpers applescript --mission-dir ...``

CR-4: non-Claude lanes get MANUAL_TICK banner in AppleScript / dry-run output.
WR-5: checks .fleet-ledger/ for an existing session before allowing spawn.
PM-1: grid cols = ceil(sqrt(N)), rows = ceil(N / cols).

Note: the ``plan`` subcommand was consolidated into megalodon_ui.preview (CV-3, v9.2).
"""

from __future__ import annotations

import math
import pathlib
import sys
from typing import Any

MANUAL_TICK_BANNER = (
    "MANUAL TICK REQUIRED — re-prompt this tab each tick or use v9.2 wrapper"
)

# ---------------------------------------------------------------------------
# Adapter registry
# ---------------------------------------------------------------------------


def _load_adapters() -> dict[str, Any]:
    """Return CLI-name -> adapter mapping. Deferred import keeps FastAPI out."""
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


# ---------------------------------------------------------------------------
# Grid math (PM-1)
# ---------------------------------------------------------------------------


def _grid(n: int) -> tuple[int, int]:
    """Return (cols, rows) for an N-lane grid.

    cols = ceil(sqrt(N)), rows = ceil(N / cols).
    Special case: N=0 returns (0, 0).
    """
    if n <= 0:
        return (0, 0)
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    return (cols, rows)


# ---------------------------------------------------------------------------
# Internal plan builder (applescript subcommand only; plan subcommand removed CV-3)
# ---------------------------------------------------------------------------


def _build_applescript_plan(mission_dir: str | pathlib.Path) -> list[dict]:
    """Build per-lane plan dicts for render_applescript.

    plan subcommand (per-lane argv preview) was moved to megalodon_ui.preview in v9.2 (CV-3).
    This private helper remains to supply render_applescript with its required data shape.
    """
    from scripts._config_loader import load_for_scripts

    mission_path = pathlib.Path(mission_dir).resolve()
    config = load_for_scripts(mission_path)
    adapters = _load_adapters()

    plan: list[dict] = []
    for idx, lane in enumerate(config.lanes):
        cli = lane.harness.cli
        model = lane.harness.model
        adapter = adapters.get(cli)
        if adapter is None:
            raise ValueError(f"Unknown harness cli: {cli!r}")

        launch_file = mission_path / f"launch-{lane.name}.md"
        argv, env_overlay = adapter.build_argv(
            str(launch_file),
            model=model,
            cwd=mission_path,
        )

        plan.append(
            {
                "lane": lane.name,
                "cli": cli,
                "model": model,
                "argv": argv,
                "env_overlay": env_overlay,
                "applescript_pane_index": idx,
                "cwd": str(mission_path),
                "manual_tick": (cli != "claude"),
            }
        )

    return plan


# ---------------------------------------------------------------------------
# AppleScript generator (PM-1 loop)
# ---------------------------------------------------------------------------


def _as_escape(s: str) -> str:
    """Escape for embedding inside an AppleScript double-quoted string."""
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    return s


def _badge_prefix(lane: str) -> str:
    """Return a shell fragment that sets iTerm's SetBadgeFormat escape."""
    import base64

    b64 = base64.b64encode(lane.encode()).decode()
    return f"printf '\\e]1337;SetBadgeFormat=%s\\a' {b64}"


def _pane_shell_cmd(entry: dict) -> str:
    """Build the shell command string to write into an iTerm pane.

    For claude: cd <cwd> && claude --print --model <model> <launch-file>
    For others: cd <cwd> && echo 'MANUAL TICK REQUIRED ...' && <cli> (interactive)
    """
    cwd = entry["cwd"]
    lane = entry["lane"]
    argv = entry["argv"]
    badge = _badge_prefix(lane)

    # Shell-safe quoting for the cwd (double-quoted)
    cwd_q = '"' + cwd.replace("\\", "\\\\").replace('"', '\\"') + '"'

    if entry["manual_tick"]:
        # CR-4: banner + launch CLI without positional prompt (interactive)
        # argv[0] is the CLI binary name for non-Claude adapters
        cli_bin = argv[0]
        banner = MANUAL_TICK_BANNER.replace("'", "'\\''")
        return f"{badge} ; cd {cwd_q} && echo '{banner}' && {cli_bin}"
    else:
        # Claude: full argv as built by ClaudeAdapter.build_argv
        # argv = ["claude", "--print", "--model", <model>, <launch-file>]
        argv_shell = " ".join(
            '"' + a.replace("\\", "\\\\").replace('"', '\\"') + '"'
            if " " in a or '"' in a
            else a
            for a in argv
        )
        return f"{badge} ; cd {cwd_q} && {argv_shell}"


def render_applescript(plan: list[dict]) -> str:
    """Generate AppleScript text for an iTerm grid given the launch plan.

    Uses PM-1 grid math: cols = ceil(sqrt(N)), rows = ceil(N/cols).
    Assigns session variable names sess0, sess1, ... sessN-1.

    Split strategy:
    - Columns are created first by splitting the first row of panes vertically.
    - Then each column-head is split horizontally to fill rows.
    """
    n = len(plan)
    if n == 0:
        return 'tell application "iTerm"\nend tell\n'

    cols, rows = _grid(n)

    # sess_var[i] = variable name for pane index i.
    # Use alphabetic suffixes for the first 26 panes (sessA..sessZ) for
    # readability and back-compat with existing AppleScript assertions;
    # fall back to sess26, sess27, ... for larger fleets.
    def _sess_name(i: int) -> str:
        if i < 26:
            return "sess" + chr(ord("A") + i)
        return f"sess{i}"

    sess_vars = [_sess_name(i) for i in range(n)]

    lines: list[str] = []
    lines.append('tell application "iTerm"')
    lines.append("    activate")
    lines.append("    set newWindow to (create window with default profile)")
    lines.append(f"    set {sess_vars[0]} to current session of newWindow")
    lines.append("")

    # Build top row (row 0): split vertically to create all columns.
    # sess0 is top-left. Split sess[col-1] vertically to get sess[col].
    for col in range(1, min(cols, n)):
        prev = sess_vars[col - 1]
        curr = sess_vars[col]
        lines.append(f"    tell {prev}")
        lines.append(f"        set {curr} to (split vertically with default profile)")
        lines.append("    end tell")

    # Set names for top row
    for col in range(min(cols, n)):
        lines.append(f"    tell {sess_vars[col]}")
        lines.append(f'        set name to "{plan[col]["lane"]}"')
        lines.append("    end tell")

    # Fill remaining rows: split each column head horizontally to get row r.
    for row in range(1, rows):
        for col in range(cols):
            pane_idx = row * cols + col
            if pane_idx >= n:
                break
            # The pane in the previous row of this column
            parent_idx = (row - 1) * cols + col
            parent = sess_vars[parent_idx]
            curr = sess_vars[pane_idx]
            lines.append(f"    tell {parent}")
            lines.append(
                f"        set {curr} to (split horizontally with default profile)"
            )
            lines.append("    end tell")
            lines.append(f"    tell {curr}")
            lines.append(f'        set name to "{plan[pane_idx]["lane"]}"')
            lines.append("    end tell")

    lines.append("")

    # Write text into each pane
    for i, entry in enumerate(plan):
        cmd = _pane_shell_cmd(entry)
        cmd_escaped = _as_escape(cmd)
        lines.append(f"    tell {sess_vars[i]}")
        lines.append(f'        write text "{cmd_escaped}"')
        lines.append("    end tell")

    lines.append('    return "OK:" & (id of newWindow)')
    lines.append("end tell")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# WR-5: existing fleet check
# ---------------------------------------------------------------------------


def check_existing_fleet(mission_dir: str | pathlib.Path) -> str | None:
    """Return a session ID string if .fleet-ledger/ has an active session, else None."""
    ledger = pathlib.Path(mission_dir).resolve() / ".fleet-ledger"
    if not ledger.is_dir():
        return None
    # Look for any session marker files
    for f in sorted(ledger.iterdir()):
        if f.is_file() and f.suffix in (".json", ".txt", ""):
            return f.stem or f.name
    return None


# ---------------------------------------------------------------------------
# __main__ CLI
# ---------------------------------------------------------------------------


def _cmd_applescript(args: list[str]) -> None:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--mission-dir", required=True)
    opts = p.parse_args(args)

    existing = check_existing_fleet(opts.mission_dir)
    if existing:
        print(
            f"warning: .fleet-ledger/ has session {existing!r} — "
            "an existing fleet may already be running.",
            file=sys.stderr,
        )

    plan = _build_applescript_plan(opts.mission_dir)
    print(render_applescript(plan), end="")


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        print(
            "Usage: python3 -m scripts._launch_helpers <applescript> [opts]",
            file=sys.stderr,
        )
        sys.exit(1)
    cmd, rest = argv[0], argv[1:]
    if cmd == "applescript":
        _cmd_applescript(rest)
    else:
        print(
            f"Unknown subcommand: {cmd!r} (hint: 'plan' moved to megalodon_ui.preview)",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
