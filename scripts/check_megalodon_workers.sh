#!/usr/bin/env bash
# Orchestrator-tick snapshot for a live Megalodon dogfood mission.
#
# Single-file invocation so the operator approves it once via
# /permissions allow Bash(scripts/check_megalodon_workers.sh:*) and the
# cron'd "check the megalodon workers" prompt stops re-prompting every 5 min.
#
# Output contract: see docs/v9/v9-3-ORCHESTRATOR-TICK.md (Lane | Agent | Task
# table appended at end). This script only emits the raw snapshot; the
# orchestrator (Claude Code) renders the table from this output.
#
# Usage: scripts/check_megalodon_workers.sh <mission-dir> [<server-port>]
# Defaults: mission-dir=/Users/dave/Documents/Projects/megalodon-fleet, port=8765

set -uo pipefail

MISSION_DIR="${1:-/Users/dave/Documents/Projects/megalodon-fleet}"
PORT="${2:-8765}"

if [[ ! -d "$MISSION_DIR" ]]; then
    echo "ERROR: mission dir not found: $MISSION_DIR" >&2
    exit 1
fi

cd "$MISSION_DIR" || exit 1

echo "MISSION: $MISSION_DIR"
echo "TIME:    $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo ""

# Done count + done list (full, not truncated — table render needs to see all).
DONE_COUNT=$(grep -cE "^\- \[done:" TASKS.md 2>/dev/null || echo 0)
echo "DONE: $DONE_COUNT"
grep -E "^\- \[done:" TASKS.md 2>/dev/null | sed 's/Output:.*//' | sed 's/^- /  /'
echo ""

# Findings + signals counts.
FINDINGS_DIR="$MISSION_DIR/findings"
SIGNALS_DIR="$MISSION_DIR/signals"
FINDINGS_COUNT=$(ls "$FINDINGS_DIR" 2>/dev/null | wc -l | tr -d ' ')
SIGNALS_COUNT=$(ls "$SIGNALS_DIR" 2>/dev/null | wc -l | tr -d ' ')
echo "FINDINGS: $FINDINGS_COUNT"
echo "SIGNALS: $SIGNALS_COUNT"
[[ "$SIGNALS_COUNT" -gt 0 ]] && ls "$SIGNALS_DIR" 2>/dev/null | sed 's/^/  /'
echo ""

# Active claims with age.
echo "CLAIMS:"
now=$(date +%s)
shopt -s nullglob
for d in "$MISSION_DIR"/claims/*/; do
    tid=$(basename "$d")
    [[ -z "$tid" ]] && continue
    owner="?"
    [[ -f "$d/owner.txt" ]] && owner=$(cat "$d/owner.txt" 2>/dev/null || echo "?")
    mtime=$(stat -f "%m" "$d" 2>/dev/null || echo 0)
    age=$(( (now - mtime) / 60 ))
    echo "  $tid -> $owner (${age}min)"
done
shopt -u nullglob
echo ""

# Pending permission prompts. Best-effort: only attempt if token file exists.
echo "PROMPTS:"
TOKEN_FILE="$MISSION_DIR/.fleet/ui.token"
if [[ -f "$TOKEN_FILE" ]]; then
    TOKEN=$(cat "$TOKEN_FILE")
    PORT_OPEN=$(lsof -nP -i ":$PORT" -t 2>/dev/null | head -1)
    if [[ -n "$PORT_OPEN" ]]; then
        # Inline uv run with httpx — operator already approved this script as
        # a unit, so the runtime call is in-scope of the single approval.
        uv run --quiet --with httpx python3 - <<PYEOF
import httpx, sys
try:
    with httpx.Client(timeout=5.0) as c:
        c.post("http://127.0.0.1:${PORT}/api/v1/auth/exchange", json={"token": "${TOKEN}"})
        r = c.get("http://127.0.0.1:${PORT}/api/v1/permission_prompts")
        ps = r.json().get("prompts", [])
        print(f"  {len(ps)} pending")
        for p in ps:
            cmd = p["command"][:200]
            print(f"  LANE-{p['lane']}: {cmd}")
except Exception as e:
    print(f"  (api unreachable: {type(e).__name__})", file=sys.stderr)
PYEOF
    else
        echo "  (server not listening on port $PORT)"
    fi
else
    echo "  (no ui.token — fleet not running)"
fi
echo ""

# Newest findings (5 most recent).
echo "NEWEST FINDINGS (5):"
ls -t "$FINDINGS_DIR" 2>/dev/null | head -5 | sed 's/^/  /'
echo ""

# Stale-lane detection (v9.3.4 — operator rule, 2026-05-19T19:23Z):
#   "If a lane hasn't updated in 15 minutes, peek the stream."
# We compute per-lane "last activity" as max(mtime of newest finding written by
# this agent, mtime of newest claim dir owned by this agent, last applier-log
# entry mentioning this agent). If that's > 15min ago AND the lane is not
# marked idle in the latest finding, flag it.
echo "STALE LANES (>15min silent):"
APPLIER_LOG="$MISSION_DIR/.fleet/queue-applier.log"

# Pre-fetch lanes that have a CURRENTLY-PENDING permission prompt — those
# aren't "silent", they're "blocked on operator approval" which is operator
# action, not agent failure. Treating them as silent floods the report with
# false stales (LANE-E showed 200+ min stale for hours while actually alive
# but waiting on a buried prompt).
# Bash 3 (macOS default) doesn't have associative arrays, so we store as a
# pipe-delimited string and use case-match for membership.
PENDING_LANES_LIST=""
if [[ -f "$TOKEN_FILE" && -n "${PORT_OPEN:-}" ]]; then
    PENDING_LANES_LIST=$(uv run --quiet --with httpx python3 - <<PYEOF 2>/dev/null
import httpx
try:
    with httpx.Client(timeout=5.0) as c:
        c.post("http://127.0.0.1:${PORT}/api/v1/auth/exchange", json={"token": "${TOKEN}"})
        r = c.get("http://127.0.0.1:${PORT}/api/v1/permission_prompts")
        for p in r.json().get("prompts", []):
            print(p["lane"])
except Exception:
    pass
PYEOF
)
fi
# Convert newlines to pipe-delimited for substring match below.
PENDING_LANES_PIPED="|$(echo "$PENDING_LANES_LIST" | tr '\n' '|' | sed 's/||/|/g')"

declare -a LANE_AGENT_PAIRS
# Derive (LANE-SHORT, agent-id) from the most-recent claim/finding/log per agent.
# Simple heuristic: parse newest finding filenames "agent-XXXX-LANE-..." in
# findings/ and group by agent.
while IFS= read -r fname; do
    agent=$(echo "$fname" | grep -oE '^agent-[a-z0-9]+' | head -1)
    lane=$(echo "$fname" | grep -oE 'agent-[a-z0-9]+-[A-Z]' | sed 's/.*-//')
    [[ -z "$agent" || -z "$lane" ]] && continue
    LANE_AGENT_PAIRS+=("$lane:$agent")
done < <(ls "$FINDINGS_DIR" 2>/dev/null)

# Unique by (lane:agent).
UNIQUE_PAIRS=$(printf "%s\n" "${LANE_AGENT_PAIRS[@]}" | sort -u)
any_stale=0
while IFS= read -r pair; do
    [[ -z "$pair" ]] && continue
    lane="${pair%%:*}"
    agent="${pair##*:}"
    # Find newest activity timestamp for this agent across findings + applier log.
    newest_finding_ts=$(ls -t "$FINDINGS_DIR" 2>/dev/null | grep "^$agent-" | head -1)
    if [[ -n "$newest_finding_ts" ]]; then
        f_mtime=$(stat -f "%m" "$FINDINGS_DIR/$newest_finding_ts" 2>/dev/null || echo 0)
    else
        f_mtime=0
    fi
    # Last applier log line mentioning this agent.
    if [[ -f "$APPLIER_LOG" ]]; then
        last_log_iso=$(grep "$agent" "$APPLIER_LOG" 2>/dev/null | tail -1 | grep -oE '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z' | head -1)
        if [[ -n "$last_log_iso" ]]; then
            # Parse ISO to epoch (BSD date format).
            l_mtime=$(date -j -u -f "%Y-%m-%dT%H:%M:%SZ" "$last_log_iso" +%s 2>/dev/null || echo 0)
        else
            l_mtime=0
        fi
    else
        l_mtime=0
    fi
    last_act=$(( f_mtime > l_mtime ? f_mtime : l_mtime ))
    [[ "$last_act" -eq 0 ]] && continue
    silent_min=$(( (now - last_act) / 60 ))
    if [[ "$silent_min" -ge 15 ]]; then
        # Differentiate "silent (probably wedged)" from "alive but waiting on
        # operator approval". Membership check via substring on the
        # pipe-delimited list (bash 3 has no assoc arrays on macOS default).
        if [[ "$PENDING_LANES_PIPED" == *"|$lane|"* ]]; then
            echo "  LANE-$lane ($agent): ${silent_min}min stale-display BUT pending approval — approve in dashboard"
        else
            any_stale=1
            echo "  LANE-$lane ($agent): silent ${silent_min}min — peek .fleet/$lane.stream.log"
        fi
    fi
done <<< "$UNIQUE_PAIRS"
[[ "$any_stale" -eq 0 ]] && echo "  (none — all genuinely-silent lanes within 15min)"
