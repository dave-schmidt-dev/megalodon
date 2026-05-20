#!/usr/bin/env bash
# V9.2 — Megalodon fleet launcher.
#
# Three modes:
#   print (default)  Delegate to megalodon_ui.preview for a per-lane argv summary.
#   --dry-run        Same as print but passes --include-tmux-argv to the preview
#                    module so each lane also shows planned tmux invocations.
#   --spawn / --exec Hand off process control via exec uv run python -m megalodon_ui
#                    (WR-10). The bash process is replaced; SIGTERM propagates.
#
# Usage:
#     ./scripts/launch_fleet.sh [--mission-dir <dir>] [flags]
#
# Flags:
#     --mission-dir <dir>     Mission directory (required).
#     --dry-run               Print mode + tmux argv lines (no spawn).
#     --spawn | --exec        Exec the megalodon_ui server (replaces this process).
#     --host <addr>           Bind host for --spawn mode (default: 127.0.0.1).
#     --port <port>           Bind port for --spawn mode (default: 8000).
#     --skip-applier-check    Skip the queue/.applier.lock/heartbeat.txt gate.
#     --cli-<lane>=<bin>      Override CLI binary env var for spawn mode.
#                             e.g. --cli-AUDIT=codex  ->  MEGALODON_CLI_AUDIT=codex
#     --prompt-override=<txt> Set MEGALODON_PROMPT_OVERRIDE env var for spawn mode.
#     --no-launch             Removed in v9.2 (CV-4). Use default print mode instead.
#
# Removed in v9.2 (CV-4):
#     --no-launch             Hard error; default 'print' mode is the equivalent.
set -euo pipefail

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
MODE="print"
MISSION_DIR=""
SKIP_APPLIER_CHECK=0
PROMPT_OVERRIDE=""
HOST="127.0.0.1"
PORT="8000"
declare -a CLI_OVERRIDES

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            MODE="dry-run"
            shift
            ;;
        --spawn|--exec)
            MODE="spawn"
            shift
            ;;
        --mission-dir)
            MISSION_DIR="$2"
            shift 2
            ;;
        --mission-dir=*)
            MISSION_DIR="${1#*=}"
            shift
            ;;
        --host)
            HOST="$2"
            shift 2
            ;;
        --host=*)
            HOST="${1#*=}"
            shift
            ;;
        --port)
            PORT="$2"
            shift 2
            ;;
        --port=*)
            PORT="${1#*=}"
            shift
            ;;
        --skip-applier-check)
            SKIP_APPLIER_CHECK=1
            shift
            ;;
        --prompt-override=*)
            PROMPT_OVERRIDE="${1#*=}"
            shift
            ;;
        --cli-*=*)
            CLI_OVERRIDES+=("$1")
            shift
            ;;
        --no-launch)
            echo "error: --no-launch was removed in v9.2 (CV-4). Default 'print' mode is equivalent." >&2
            exit 2
            ;;
        -h|--help)
            sed -n '2,/^set -euo/p' "$0" | sed -e 's/^# \{0,1\}//' -e '$d'
            exit 0
            ;;
        *)
            echo "error: unknown flag $1" >&2
            exit 2
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Pre-flight: tmux must be on PATH for spawn mode (preview-only modes don't
# require tmux — they just print the planned invocation). Skip the probe under
# MEGALODON_LAUNCH_DRY_EXEC=1 since the dry-exec path doesn't actually run tmux.
# ---------------------------------------------------------------------------
if [[ "$MODE" == "spawn" && "${MEGALODON_LAUNCH_DRY_EXEC:-0}" != "1" ]]; then
    if ! command -v tmux >/dev/null 2>&1; then
        echo "error: tmux not installed (required for v9.2 spawn mode)" >&2
        exit 6
    fi
fi

# ---------------------------------------------------------------------------
# Resolve mission dir
# ---------------------------------------------------------------------------
MISSION_DIR="${MISSION_DIR:-$PWD}"
if [[ ! -d "$MISSION_DIR" ]]; then
    echo "error: mission dir not found: $MISSION_DIR" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Lane launch-file warnings (print and dry-run modes only).
# spawn mode does not need the markdown launch files.
# ---------------------------------------------------------------------------
if [[ "$MODE" == "print" || "$MODE" == "dry-run" ]]; then
    # Derive lane names from mission config if present; fall back to v9.1 defaults.
    LANES=(AUDIT ARCHITECT BACKEND FRONTEND TEST META)
    missing=0
    for lane in "${LANES[@]}"; do
        if [[ ! -f "$MISSION_DIR/launch-${lane}.md" ]]; then
            echo "warning: $MISSION_DIR/launch-${lane}.md missing — run python3 scripts/gen_lane_launches.py" >&2
            missing=$((missing + 1))
        fi
    done
    if [[ "$missing" -gt 0 ]]; then
        echo "warning: $missing lane launch file(s) missing; preview output may be partial" >&2
    fi
fi

# ---------------------------------------------------------------------------
# Resolve project root for uv run
# ---------------------------------------------------------------------------
_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(git -C "$_SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || echo "$_SCRIPT_DIR/..")"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" && pwd)"

# ---------------------------------------------------------------------------
# Mode dispatch
# ---------------------------------------------------------------------------

if [[ "$MODE" == "print" ]]; then
    # Default: delegate to preview module (no tmux argv).
    # Use exec so this bash process is replaced; rc propagates cleanly.
    exec uv run --directory "$PROJECT_ROOT" \
    --with fastapi --with "uvicorn[standard]" --with sse-starlette --with pyyaml --with pydantic --with starlette \
    python -m megalodon_ui.preview \
        --mission-dir "$MISSION_DIR"
fi

if [[ "$MODE" == "dry-run" ]]; then
    # Dry-run: delegate to preview module with tmux argv included.
    exec uv run --directory "$PROJECT_ROOT" \
    --with fastapi --with "uvicorn[standard]" --with sse-starlette --with pyyaml --with pydantic --with starlette \
    python -m megalodon_ui.preview \
        --mission-dir "$MISSION_DIR" \
        --include-tmux-argv
fi

# MODE == spawn
# ---------------------------------------------------------------------------
# Build env overlay from --cli-<LANE>=<bin> flags.
# --cli-AUDIT=codex  ->  export MEGALODON_CLI_AUDIT=codex
#
# TODO(P2+): MEGALODON_CLI_<LANE> and MEGALODON_PROMPT_OVERRIDE are exported
# here as a forward-hook for later phases; the lifespan does NOT consume them
# yet. Wire them through to FleetSpawner.start_all(prompt_override=...) and
# adapter resolution in P2/P3 (auth + stream-tap) so operators can override
# the CLI binary per lane without editing config.
# ---------------------------------------------------------------------------
for override in "${CLI_OVERRIDES[@]+"${CLI_OVERRIDES[@]}"}"; do
    # Strip leading "--cli-" prefix -> "AUDIT=codex"
    kv="${override#--cli-}"
    lane_key="${kv%%=*}"
    bin_val="${kv#*=}"
    # Uppercase via tr (portable to bash 3.2 on macOS — no ${var^^}).
    lane_upper="$(printf "%s" "$lane_key" | tr '[:lower:]' '[:upper:]')"
    export "MEGALODON_CLI_${lane_upper}=${bin_val}"
done

if [[ -n "$PROMPT_OVERRIDE" ]]; then
    export MEGALODON_PROMPT_OVERRIDE="$PROMPT_OVERRIDE"
fi

# Support test-harness dry-exec: if MEGALODON_LAUNCH_DRY_EXEC=1, print the
# exec command instead of running it (lets tests capture the intended argv
# without actually starting uvicorn).
if [[ "${MEGALODON_LAUNCH_DRY_EXEC:-0}" == "1" ]]; then
    echo "exec uv run python -m megalodon_ui --mission-dir $MISSION_DIR --host $HOST --port $PORT"
    exit 0
fi

# Hand off to the megalodon_ui server; exec replaces this bash process so
# SIGTERM sent to the PID propagates directly to Python (WR-10).
exec uv run --directory "$PROJECT_ROOT" \
    --with fastapi --with "uvicorn[standard]" --with sse-starlette --with pyyaml --with pydantic --with starlette \
    python -m megalodon_ui \
    --mission-dir "$MISSION_DIR" \
    --host "$HOST" \
    --port "$PORT"
