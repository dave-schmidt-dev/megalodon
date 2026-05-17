#!/usr/bin/env bash
# scripts/run_e2e.sh — canonical playwright invocation for v9 workers.
#
# Resolves project root from this script's location; uses `uv run --directory`
# instead of `cd /abs && uv run` (Codex CR-5 hygiene). Forwards all args to
# `playwright test`.
#
# Operator allowlist: `./scripts/run_e2e.sh *`
# Spec: docs/superpowers/specs/2026-05-16-v9-m3-helper-scripts-design.md §8

set -euo pipefail

SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$( cd -- "$SCRIPT_DIR/.." &> /dev/null && pwd )"

exec uv run --directory "$PROJECT_ROOT" \
    --with fastapi --with "uvicorn[standard]" --with sse-starlette --with pyyaml \
    npx playwright test \
    --config ui/tests/e2e/playwright.config.ts \
    "$@"
