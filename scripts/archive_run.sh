#!/usr/bin/env bash
# Archive a run dir: move runs/<UTC>--<slug>/ -> .archive/<UTC>--<slug>/, then
# append one INDEX.md row. .archive/ is gitignored (local cold storage), so we
# move on disk with `mv` and untrack the run from git with `git rm --cached`
# (a `git mv` into an ignored path would force-track the archive against
# convention). Durability during a run comes from runs/ being git-tracked.
# Best-effort integrity check: same-filesystem mv is atomic; the file-count
# check catches a truncated/concurrent move. Idempotent: re-running after a
# crash ensures the INDEX row exists.
#
# Usage: scripts/archive_run.sh <run-dir> [--force]
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/run_lib.sh"
REPO_ROOT="${RUN_LIB_REPO_ROOT:-$REPO_ROOT}"

RUN_DIR=""; FORCE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    *)       RUN_DIR="$1"; shift ;;
  esac
done
[[ -n "$RUN_DIR" && -d "$RUN_DIR" ]] || { echo "usage: archive_run.sh <run-dir>" >&2; exit 2; }
RUN_DIR="$(cd "$RUN_DIR" && pwd)"
assert_under_runs_or_archive "$RUN_DIR"
[[ -d "$REPO_ROOT/.archive" ]] || { echo "ABORT: $REPO_ROOT/.archive/ not found; create it first." >&2; exit 1; }

NAME="$(basename "$RUN_DIR")"            # <UTC>--<slug>
DEST="$REPO_ROOT/.archive/$NAME"

# Append one INDEX row for NAME if not already present (dedup by run ID).
# Built from templates/run/INDEX-entry.tmpl so the template is the single
# source of truth for the row shape.
register_index() {
  local index="$REPO_ROOT/.archive/INDEX.md"
  grep -q "\`$NAME\`" "$index" && return 0
  local utc slug title row tmp
  utc="${NAME%%--*}"          # <UTC> portion of <UTC>--<slug>
  slug="${NAME#*--}"          # <slug> portion
  title="$(grep -m1 '^# Mission' "$DEST/MISSION.md" 2>/dev/null | sed 's/^# Mission — //')"
  [[ -n "$title" ]] || title="$NAME"
  tmp="$(mktemp)"
  cp "$REPO_ROOT/templates/run/INDEX-entry.tmpl" "$tmp"
  subst_file "$tmp" \
    "UTC=$utc" "SLUG=$slug" "MISSION_TITLE=$title" "DATE=$utc" \
    "COMPLETED=$(run_utc_iso)" "WALL_CLOCK=n/a" "OUTPUTS=see $DEST/README.md"
  # Append the rendered row (single line; strip any trailing newline noise).
  cat "$tmp" >> "$index"
  rm -f "$tmp"
}

# Idempotent: already archived? Still ensure the INDEX row exists (a crash
# between git mv and INDEX append leaves DEST present but unregistered).
if [[ -d "$DEST" ]]; then
  register_index
  echo "Already archived: $DEST (ensured INDEX row, no move)"; exit 0
fi

# Refuse a live run.
if [[ $FORCE -eq 0 ]] && uv run python3 "$HERE/_run_liveness.py" "$RUN_DIR/.mission-events"; then
  echo "REFUSING: $RUN_DIR is still live (last event non-terminal). Pass --force to override." >&2
  exit 1
fi

SRC_COUNT="$(find "$RUN_DIR" -type f | wc -l | tr -d ' ')"
assert_under_runs_or_archive "$DEST"
mv "$RUN_DIR" "$DEST"
DEST_COUNT="$(find "$DEST" -type f | wc -l | tr -d ' ')"
[[ "$SRC_COUNT" == "$DEST_COUNT" ]] || { echo "ABORT: file count mismatch ($SRC_COUNT != $DEST_COUNT)" >&2; exit 1; }
# Untrack the run from git (it lived under the tracked runs/ tree). No-op if it
# was never committed. Operator commits the staged deletion.
git -C "$REPO_ROOT" rm -r --cached --quiet "runs/$NAME" 2>/dev/null || true
touch "$DEST/.archived"

register_index

echo "Archived: $DEST"
echo "Registered in .archive/INDEX.md"
