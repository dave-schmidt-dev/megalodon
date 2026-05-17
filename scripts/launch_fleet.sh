#!/usr/bin/env bash
# V9 A2 — emit per-lane invocations for the 6-lane fleet.
#
# Usage: ./scripts/launch_fleet.sh [<mission-dir>]
#
# Headless-safe: prints the invocations rather than actually opening terminals.
# Operator copy/pastes into 6 Claude sessions (or wires up osascript /
# gnome-terminal locally). The lane-bound launch files (launch-AUDIT.md, etc.)
# encode per-lane cadence, model, and tick stagger.
set -euo pipefail

MISSION_DIR="${1:-$PWD}"

if [[ ! -d "$MISSION_DIR" ]]; then
    echo "error: mission dir not found: $MISSION_DIR" >&2
    exit 1
fi

declare -A LANE_MODEL=(
    [AUDIT]="sonnet-4.6"
    [ARCHITECT]="opus-4.7"
    [BACKEND]="opus-4.7"
    [FRONTEND]="opus-4.7"
    [TEST]="opus-4.7"
    [META]="sonnet-4.6"
)

echo "# V9 A2 fleet launch — copy each line into a separate Claude session."
echo "# Mission dir: $MISSION_DIR"
echo ""
for lane in AUDIT ARCHITECT BACKEND FRONTEND TEST META; do
    model="${LANE_MODEL[$lane]}"
    launch_file="launch-${lane}.md"
    if [[ ! -f "$MISSION_DIR/$launch_file" ]]; then
        echo "warning: $launch_file missing — run python3 scripts/gen_lane_launches.py first" >&2
    fi
    echo "cd $MISSION_DIR && claude --model $model \"read $launch_file\""
done
