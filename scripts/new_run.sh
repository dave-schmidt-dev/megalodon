#!/usr/bin/env bash
# Scaffold a self-contained run dir under runs/<UTC>--<slug>/ from templates/run/.
#
# Usage: scripts/new_run.sh <slug> [--title T] [--summary S] [--force]
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/run_lib.sh"
# Allow tests to override the repo root.
REPO_ROOT="${RUN_LIB_REPO_ROOT:-$REPO_ROOT}"

SLUG=""; TITLE=""; SUMMARY=""; FORCE=0
EXIT_CRITERIA="See docs/superpowers/specs/2026-05-22-v94-dogfood-and-run-lifecycle-design.md"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --title)         TITLE="$2"; shift 2 ;;
    --summary)       SUMMARY="$2"; shift 2 ;;
    --exit-criteria) EXIT_CRITERIA="$2"; shift 2 ;;
    --force)         FORCE=1; shift ;;
    -*)              echo "unknown flag: $1" >&2; exit 2 ;;
    *)               SLUG="$1"; shift ;;
  esac
done
[[ -n "$SLUG" ]] || { echo "usage: new_run.sh <slug> [--title T] [--summary S] [--exit-criteria TEXT] [--force]" >&2; exit 2; }

# Refuse if any existing run under runs/ is still live.
if [[ -d "$REPO_ROOT/runs" && $FORCE -eq 0 ]]; then
  for ev in "$REPO_ROOT"/runs/*/.mission-events; do
    [[ -e "$ev" ]] || continue
    if uv run python3 "$HERE/_run_liveness.py" "$ev"; then
      echo "REFUSING: live run at $(dirname "$ev"). Archive it first (scripts/archive_run.sh) or pass --force." >&2
      exit 1
    fi
  done
fi

UTC="$(run_utc)"; UTC_ISO="$(run_utc_iso)"; DATE="$(date -u +%Y-%m-%d)"
RUN_DIR="$REPO_ROOT/runs/${UTC}--${SLUG}"
[[ -e "$RUN_DIR" && $FORCE -eq 0 ]] && { echo "REFUSING: $RUN_DIR exists (use --force)" >&2; exit 1; }
# Pre-create parent so assert_under_runs_or_archive can resolve it.
mkdir -p "$REPO_ROOT/runs"
assert_under_runs_or_archive "$RUN_DIR"

LANES="AUDIT, ARCHITECT, BACKEND, FRONTEND, TEST, META"
mkdir -p "$RUN_DIR"/{findings,claims,signals,queue/pending,queue/applied,queue/rejected,.fleet}
for d in findings claims signals queue .fleet; do touch "$RUN_DIR/$d/.gitkeep"; done

# Bounded-tool access (Finding A, tsgate gate 2026-05-24): agents spawn with
# cwd = run dir (spawn.py: cwd=self.mission_dir) and invoke bounded tools as the
# allowlisted relative path `scripts/<tool>` (the allowlist pattern
# Bash(scripts/queue_submit.py:*) is a literal-string match). Expose the project's
# scripts/ inside the run dir via a relative symlink so that path resolves from
# the run-dir cwd — without it the first bounded-tool call file-not-founds and the
# only resolving form (an absolute repo path) misses the allowlist and prompts.
# Relative target (../../scripts) survives an archive move to .archive/<name>/.
ln -sfn ../../scripts "$RUN_DIR/scripts"

# Copy + substitute doc templates.
for t in MISSION STATUS TASKS HISTORY README; do
  cp "$REPO_ROOT/templates/run/${t}.md.tmpl" "$RUN_DIR/${t}.md"
  subst_file "$RUN_DIR/${t}.md" \
    "SLUG=$SLUG" "UTC=$UTC" "DATE=$DATE" "LANES=$LANES" \
    "MISSION_TITLE=$TITLE" "MISSION_SUMMARY=$SUMMARY" \
    "EXIT_CRITERIA=$EXIT_CRITERIA"
done
cp "$REPO_ROOT/templates/run/.mission-config.yaml.tmpl" "$RUN_DIR/.mission-config.yaml"
subst_file "$RUN_DIR/.mission-config.yaml" "SLUG=$SLUG" "UTC_ISO=$UTC_ISO" "MISSION_TITLE=$TITLE"

# Seed structured RUN-START event.
echo "RUN-START $UTC slug=$SLUG" > "$RUN_DIR/.mission-events"

# Generate per-lane launch files. gen_lane_launches imports megalodon_ui
# (-> pyyaml), which is not in the base env, so make pyyaml available
# explicitly rather than relying on an optional extra being active.
uv run --with pyyaml python3 "$HERE/gen_lane_launches.py" --mission-dir "$RUN_DIR" --out-dir "$RUN_DIR" \
  || echo "WARN: gen_lane_launches.py failed; generate launch files manually" >&2

echo "Scaffolded run: $RUN_DIR"
echo
echo "Launch:"
echo "  ./scripts/start_applier.sh $RUN_DIR &"
echo "  ./scripts/launch_fleet.sh --mission-dir $RUN_DIR --spawn --port 8765"
echo "  open http://localhost:8765/  (token in $RUN_DIR/.fleet/ui.token)"
