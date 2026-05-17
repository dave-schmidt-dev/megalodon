#!/usr/bin/env bash
# V9 A1 — operator-friendly launcher for the watchdog daemon.
#
# Usage:
#   ./scripts/start_watchdog.sh [MISSION_DIR] [--poll-seconds N] [--cadence-seconds N] [--debug]
#
# Defaults MISSION_DIR to $PWD. Forwards remaining args to the watchdog.
# Watchdog writes SIGNAL findings on crash/silent/hung detection; never auto-respawns.
set -euo pipefail

MISSION_DIR="${1:-$PWD}"
shift 2>/dev/null || true

echo "Starting watchdog for mission: $MISSION_DIR"

PROJECT_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null || cd "$(dirname "$0")/.." && pwd)"

exec uv run --directory "$PROJECT_ROOT" \
    --with pyyaml --with pydantic \
    python -m megalodon_ui.watchdog \
    --mission-dir "$MISSION_DIR" "$@"
