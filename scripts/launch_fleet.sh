#!/usr/bin/env bash
# V9 A2 — Megalodon fleet launcher.
#
# Default mode (back-compat): prints one CLI invocation per lane to stdout
# so the operator can copy each into a separate Claude session. With --spawn
# the script opens a single iTerm window with a 2x3 pane layout and launches
# the right CLI in each pane.
#
# Layout (2 rows x 3 cols):
#     +----------+----------+----------+
#     |  AUDIT   | ARCHITECT|  BACKEND |
#     +----------+----------+----------+
#     | FRONTEND |   TEST   |   META   |
#     +----------+----------+----------+
#
# Usage:
#     ./scripts/launch_fleet.sh [<mission-dir>] [flags]
#
# Flags:
#     --spawn                 Open iTerm window with 6 panes (macOS / iTerm2 only).
#     --dry-run               With --spawn: print the AppleScript instead of running it.
#     --no-launch             With --spawn: open panes that echo the command they
#                             *would* run, instead of actually launching a CLI agent.
#                             Use for layout tests without joining a real mission.
#     --skip-applier-check    Skip the queue/.applier.lock/heartbeat.txt freshness gate.
#     --cli-<lane>=<bin>      Override CLI binary for one lane. <lane> is one of
#                             audit|architect|backend|frontend|test|meta.
#                             Default: claude (per V9 A2 spec).
#     --prompt-override=<txt> Replace the default "read launch-<LANE>.md" prompt
#                             on every lane. Use for variety/smoke tests that
#                             must not join a live mission.
#     -h, --help              Show this help.
#
# Orchestrator invocation (e.g. from a Claude Code Bash tool, no TTY):
#     ./scripts/launch_fleet.sh --spawn
#
# Operator dry-run (verifies AppleScript without opening windows):
#     ./scripts/launch_fleet.sh --spawn --dry-run --skip-applier-check
#
# Operator layout test (opens window but doesn't launch agents):
#     ./scripts/launch_fleet.sh --spawn --no-launch --skip-applier-check
set -euo pipefail

# TODO(v9-patch): lane names + model/cadence per lane are hardcoded here and
# in scripts/gen_lane_launches.py. After the v9 protocol patch lands, source
# this from a single registry (likely megalodon_ui/constants.py or a YAML).
# Lane → model mapping (parallel arrays for bash 3.2 compatibility on macOS).
LANES=(AUDIT ARCHITECT BACKEND FRONTEND TEST META)
# Use claude's "latest" aliases (sonnet/opus) so we don't have to chase version bumps.
# See claude --help: --model accepts an alias or a full ID like claude-sonnet-4-6.
LANE_MODELS=(sonnet opus opus opus opus sonnet)
LANE_CLIS=(claude claude claude claude claude claude)

MISSION_DIR=""
MODE="print"          # print | spawn
DRY_RUN=false
NO_LAUNCH=false
SKIP_APPLIER_CHECK=false
PROMPT_OVERRIDE=""    # if set, replaces "read launch-<LANE>.md" in every lane prompt

usage() {
    sed -n '2,/^set -euo/p' "$0" | sed -e 's/^# \{0,1\}//' -e '$d'
}

# set_cli_for_lane <LANE_NAME> <cli_bin>
set_cli_for_lane() {
    local lane_name="$1"
    local cli="$2"
    local i=0
    for lane in "${LANES[@]}"; do
        if [[ "$lane" == "$lane_name" ]]; then
            LANE_CLIS[$i]="$cli"
            return 0
        fi
        i=$((i + 1))
    done
    echo "error: unknown lane: $lane_name" >&2
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --spawn) MODE="spawn" ;;
        --dry-run) DRY_RUN=true ;;
        --no-launch) NO_LAUNCH=true ;;
        --skip-applier-check) SKIP_APPLIER_CHECK=true ;;
        --cli-audit=*)     set_cli_for_lane AUDIT     "${1#*=}" ;;
        --cli-architect=*) set_cli_for_lane ARCHITECT "${1#*=}" ;;
        --cli-backend=*)   set_cli_for_lane BACKEND   "${1#*=}" ;;
        --cli-frontend=*)  set_cli_for_lane FRONTEND  "${1#*=}" ;;
        --cli-test=*)      set_cli_for_lane TEST      "${1#*=}" ;;
        --cli-meta=*)      set_cli_for_lane META      "${1#*=}" ;;
        --prompt-override=*) PROMPT_OVERRIDE="${1#*=}" ;;
        -h|--help) usage; exit 0 ;;
        --*) echo "error: unknown flag: $1" >&2; exit 1 ;;
        *) MISSION_DIR="$1" ;;
    esac
    shift
done

MISSION_DIR="${MISSION_DIR:-$PWD}"
if [[ ! -d "$MISSION_DIR" ]]; then
    echo "error: mission dir not found: $MISSION_DIR" >&2
    exit 1
fi

# Verify lane launch files exist (warning in print mode, hard error in real spawn).
missing=0
for lane in "${LANES[@]}"; do
    if [[ ! -f "$MISSION_DIR/launch-${lane}.md" ]]; then
        echo "warning: $MISSION_DIR/launch-${lane}.md missing — run python3 scripts/gen_lane_launches.py" >&2
        missing=$((missing + 1))
    fi
done
if [[ "$missing" -gt 0 && "$MODE" == "spawn" && "$NO_LAUNCH" == "false" ]]; then
    echo "error: $missing lane launch file(s) missing; cannot spawn real CLIs without them" >&2
    exit 2
fi

# Applier heartbeat freshness gate (only for real spawns).
if [[ "$MODE" == "spawn" && "$NO_LAUNCH" == "false" && "$SKIP_APPLIER_CHECK" == "false" ]]; then
    hb="$MISSION_DIR/queue/.applier.lock/heartbeat.txt"
    if [[ ! -f "$hb" ]]; then
        echo "error: applier heartbeat not found at $hb" >&2
        echo "       Start it first: $MISSION_DIR/scripts/start_applier.sh \"$MISSION_DIR\" &" >&2
        echo "       Or bypass with --skip-applier-check" >&2
        exit 3
    fi
    # `date -r FILE +%s` is portable across macOS BSD and GNU coreutils.
    hb_mtime=$(date -r "$hb" +%s)
    now=$(date +%s)
    age=$((now - hb_mtime))
    if [[ "$age" -gt 30 ]]; then
        echo "error: applier heartbeat stale (${age}s; threshold 30s)" >&2
        echo "       Restart with: pkill -f megalodon_ui.queue.applier" >&2
        echo "                     $MISSION_DIR/scripts/start_applier.sh \"$MISSION_DIR\" &" >&2
        exit 4
    fi
fi

# ---------------------------------------------------------------------------
# Print mode: emit one shell command per lane (legacy default).
# ---------------------------------------------------------------------------
if [[ "$MODE" == "print" ]]; then
    echo "# V9 A2 fleet launch — copy each line into a separate Claude session."
    echo "# Mission dir: $MISSION_DIR"
    echo ""
    i=0
    for lane in "${LANES[@]}"; do
        model="${LANE_MODELS[$i]}"
        cli="${LANE_CLIS[$i]}"
        echo "cd $MISSION_DIR && $cli --model $model \"read launch-${lane}.md\""
        i=$((i + 1))
    done
    exit 0
fi

# ---------------------------------------------------------------------------
# Spawn mode: build a single AppleScript that opens iTerm with a 2x3 layout.
# ---------------------------------------------------------------------------

# badge_prefix <LANE> → a shell snippet that sets iTerm's sticky pane badge.
# Uses iTerm's proprietary \e]1337;SetBadgeFormat=<base64>\a escape, which
# survives shell-driven session name overrides (unlike `set name` in AppleScript).
badge_prefix() {
    local lane="$1"
    local b64
    b64=$(printf '%s' "$lane" | base64)
    printf "printf '\\\\e]1337;SetBadgeFormat=%%s\\\\a' %s" "$b64"
}

# sh_dquote <value> → bash-safe double-quoted form of <value>.
# Use for any caller-controlled value (paths, prompts) that gets embedded
# inside a shell command we'll later send to iTerm via `write text`. Without
# this, a single-quote in $MISSION_DIR or --prompt-override would terminate
# an enclosing `echo '...'` mid-stream and produce a malformed shell command.
sh_dquote() {
    local s="$1"
    s=${s//\\/\\\\}
    s=${s//\"/\\\"}
    s=${s//\$/\\\$}
    s=${s//\`/\\\`}
    printf '"%s"' "$s"
}

# pane_cmd <LANE> <cli> <model> → the shell command to type into that pane.
pane_cmd() {
    local lane="$1"
    local cli="$2"
    local model="$3"
    local badge
    badge=$(badge_prefix "$lane")
    # Resolve the lane prompt; --prompt-override replaces "read launch-<LANE>.md"
    # for every lane (used for variety/smoke tests that must not join a mission).
    local prompt="read launch-${lane}.md"
    if [[ -n "$PROMPT_OVERRIDE" ]]; then
        prompt="$PROMPT_OVERRIDE"
    fi
    # Shell-quote caller-controlled values so prompts/paths with metachars
    # (', ", $, `, \) don't break the embedded command.
    local mdir_q prompt_q
    mdir_q=$(sh_dquote "$MISSION_DIR")
    prompt_q=$(sh_dquote "$prompt")
    if [[ "$NO_LAUNCH" == "true" ]]; then
        echo "$badge ; echo \"=== $lane (test mode; no agent launched) ===\" && echo \"cd $mdir_q && $cli --model $model $prompt_q\""
        return 0
    fi
    case "$cli" in
        claude)
            echo "$badge ; cd $mdir_q && $cli --model $model $prompt_q"
            ;;
        codex|gemini|cursor-agent|vibe|copilot)
            # These CLIs don't accept a positional prompt cleanly in interactive
            # mode. Launch the REPL; the operator types the lane prompt themselves.
            echo "$badge ; cd $mdir_q && echo \"Type: $prompt_q\" && $cli"
            ;;
        *)
            echo "error: unknown CLI '$cli' for lane $lane" >&2
            exit 5
            ;;
    esac
}

# Escape a shell command for embedding inside an AppleScript double-quoted string.
# Bash 3.2-compatible: convert each " to \"  and each \ to \\ (\ first to avoid double-escape).
as_escape() {
    local s="$1"
    s=${s//\\/\\\\}
    s=${s//\"/\\\"}
    printf '%s' "$s"
}

scpt=$(mktemp -t launch_fleet.XXXXXX)
trap 'rm -f "$scpt"' EXIT

{
    echo 'tell application "iTerm"'
    echo '    activate'
    echo '    set newWindow to (create window with default profile)'
    echo '    set sessA to current session of newWindow'
    echo ''
    echo '    tell sessA'
    echo "        set name to \"${LANES[0]}\""
    echo '        set sessB to (split vertically with default profile)'
    echo '    end tell'
    echo '    tell sessB'
    echo "        set name to \"${LANES[1]}\""
    echo '        set sessC to (split vertically with default profile)'
    echo '    end tell'
    echo '    tell sessC'
    echo "        set name to \"${LANES[2]}\""
    echo '    end tell'
    echo '    tell sessA'
    echo '        set sessD to (split horizontally with default profile)'
    echo '    end tell'
    echo '    tell sessD'
    echo "        set name to \"${LANES[3]}\""
    echo '    end tell'
    echo '    tell sessB'
    echo '        set sessE to (split horizontally with default profile)'
    echo '    end tell'
    echo '    tell sessE'
    echo "        set name to \"${LANES[4]}\""
    echo '    end tell'
    echo '    tell sessC'
    echo '        set sessF to (split horizontally with default profile)'
    echo '    end tell'
    echo '    tell sessF'
    echo "        set name to \"${LANES[5]}\""
    echo '    end tell'
    echo ''

    sess_vars=(sessA sessB sessC sessD sessE sessF)
    i=0
    for lane in "${LANES[@]}"; do
        cmd=$(pane_cmd "$lane" "${LANE_CLIS[$i]}" "${LANE_MODELS[$i]}")
        cmd_as=$(as_escape "$cmd")
        echo "    tell ${sess_vars[$i]}"
        echo "        write text \"$cmd_as\""
        echo '    end tell'
        i=$((i + 1))
    done

    echo '    return "OK:" & (id of newWindow)'
    echo 'end tell'
} > "$scpt"

if [[ "$DRY_RUN" == "true" ]]; then
    cat "$scpt"
    exit 0
fi

if ! command -v osascript >/dev/null 2>&1; then
    echo "error: osascript not found (--spawn requires macOS)" >&2
    exit 7
fi

result=$(osascript "$scpt")
echo "Spawned: $result"
i=0
for lane in "${LANES[@]}"; do
    printf "  %-10s  %-15s  model=%s\n" "$lane" "${LANE_CLIS[$i]}" "${LANE_MODELS[$i]}"
    i=$((i + 1))
done
