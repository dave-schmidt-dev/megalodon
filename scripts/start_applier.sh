#!/usr/bin/env bash
# V9 M1 — operator-friendly launcher for the queue applier daemon.
#
# Usage:
#   ./scripts/start_applier.sh [MISSION_DIR] [--poll-seconds N] [--debug]
#
# Defaults MISSION_DIR to $PWD. Forwards remaining args to the applier.
set -euo pipefail

MISSION_DIR="${1:-$PWD}"
shift 2>/dev/null || true

echo "Starting applier for mission: $MISSION_DIR"

# Resolve the repo root. Bind the fallback to its own assignment: the previous
# `$(git ... || cd ... && pwd)` form parsed as `(git || cd) && pwd`, so when
# `git rev-parse` succeeded `pwd` ALSO ran, yielding a two-line PROJECT_ROOT
# that broke `uv --directory` with "os error 2".
PROJECT_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null)" \
  || PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

exec uv run --directory "$PROJECT_ROOT" \
    --with pyyaml --with pydantic \
    python -m megalodon_ui.queue.applier \
    --mission-dir "$MISSION_DIR" "$@"
