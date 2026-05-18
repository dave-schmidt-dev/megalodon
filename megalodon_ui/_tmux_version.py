"""Parse tmux version and probe for minimum version at startup."""

import re
import subprocess
import sys


def parse_tmux_version(output: str) -> tuple[int, int]:
    """Parse tmux version string into (major, minor) tuple."""
    match = re.search(r"(\d+)\.(\d+)", output.strip())
    if not match:
        raise ValueError(f"Could not parse tmux version from: {output.strip()}")
    return (int(match.group(1)), int(match.group(2)))


def probe_or_exit_6(min_version: tuple[int, int] = (2, 6)) -> None:
    """Check tmux version; exit 6 if not found or too old."""
    try:
        result = subprocess.run(
            ["tmux", "-V"], capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        sys.stderr.write(
            "tmux is required but was not found on PATH. "
            "Install via brew install tmux (macOS) or apt-get install tmux (Linux).\n"
        )
        sys.exit(6)

    if result.returncode != 0:
        sys.stderr.write(
            f"tmux -V failed with rc {result.returncode}. "
            f"Install via brew install tmux (macOS) or apt-get install tmux (Linux).\n"
        )
        sys.exit(6)

    try:
        version = parse_tmux_version(result.stdout)
    except ValueError as e:
        sys.stderr.write(
            f"Could not parse tmux version from output: {result.stdout.strip()}\n"
            f"Error: {e}\n"
        )
        sys.exit(6)

    if version < min_version:
        maj, min_ver = version
        sys.stderr.write(
            f"tmux {min_version[0]}.{min_version[1]} or newer required; "
            f"found {maj}.{min_ver}. "
            f"Install via brew install tmux (macOS) or apt-get install tmux (Linux).\n"
        )
        sys.exit(6)
