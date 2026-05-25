#!/usr/bin/env bash
# Shared helpers for the v9.4 run lifecycle (new_run.sh / archive_run.sh).
# Source this file; do not execute directly.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# UTC stamp for run dir names: 2026-05-22T16-30Z  (filesystem-safe).
run_utc()      { date -u +%Y-%m-%dT%H-%MZ; }
# ISO stamp for inside config files: 2026-05-22T16:30:00Z
run_utc_iso()  { date -u +%Y-%m-%dT%H:%M:%SZ; }

# Substitute {{KEY}} placeholders in a file, in place.
# Usage: subst_file <file> KEY=VALUE [KEY=VALUE ...]
subst_file() {
  local f="$1"; shift
  local kv key val
  for kv in "$@"; do
    key="${kv%%=*}"; val="${kv#*=}"
    # Use python for safe replacement (handles slashes/newlines in val).
    python3 - "$f" "$key" "$val" <<'PY'
import sys, pathlib
f, key, val = sys.argv[1], sys.argv[2], sys.argv[3]
p = pathlib.Path(f)
p.write_text(p.read_text().replace("{{%s}}" % key, val))
PY
  done
}

# Guard: refuse to operate on a path outside runs/ or .archive/.
# Usage: assert_under_runs_or_archive <abs-path>
assert_under_runs_or_archive() {
  local p; p="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
  case "$p" in
    "$REPO_ROOT"/runs/*|"$REPO_ROOT"/.archive/*) return 0 ;;
    *) echo "REFUSING: $p is outside runs/ or .archive/" >&2; return 1 ;;
  esac
}

# Liveness check via the python parser. Returns 0 if live.
run_is_live() {
  uv run python3 "$REPO_ROOT/scripts/_run_liveness.py" "$1/.mission-events"
}
