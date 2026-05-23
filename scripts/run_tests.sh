#!/usr/bin/env bash
# run_tests.sh — bounded pytest runner for Megalodon workers.
#
# Resolves project root from this script's location and uses `uv run --directory`
# (mirrors scripts/run_e2e.sh house style — CV-8). The `test` extra carries
# freezegun/pytest-asyncio/pytest-forked. `uv run` is NOT allowlisted for agents;
# this wrapper IS (Bash(scripts/run_tests.sh:*)), giving the TEST lane and
# self-verifying lanes a bounded path to the full suite.
#
# Usage: scripts/run_tests.sh [pytest args...]
set -euo pipefail

SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$( cd -- "$SCRIPT_DIR/.." &> /dev/null && pwd )"

exec uv run --directory "$PROJECT_ROOT" --extra test pytest "$@"
