#!/usr/bin/env bash
# claim.sh — bounded claims/ directory mutex for Megalodon workers.
#
# Usage: scripts/claim.sh <task-id> <agent-id>
#
# Atomically claims a task by creating claims/<task-id>/ (mkdir is the mutex)
# and writing <agent-id> to claims/<task-id>/owner.txt. Run from the mission
# directory (cwd contains claims/).
#
# Exit codes:
#   0  claimed (or idempotent re-claim by the same agent)
#   2  argument / validation error (missing args, bad task-id)
#   3  already claimed by a DIFFERENT agent
#
# This is the ONLY sanctioned claims/ mutation path. It is a local filesystem
# mutex, distinct from RULE-15 queue-routed shared-DOCUMENT mutations.
set -euo pipefail

TASK_ID="${1:-}"
AGENT_ID="${2:-}"

if [[ -z "$TASK_ID" || -z "$AGENT_ID" ]]; then
  echo "usage: claim.sh <task-id> <agent-id>" >&2
  exit 2
fi

# Reject anything that isn't a flat, safe task-id (blocks path traversal).
if [[ ! "$TASK_ID" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "claim.sh: invalid task-id '$TASK_ID' (must match [A-Za-z0-9._-]+)" >&2
  exit 2
fi

CLAIM_DIR="claims/$TASK_ID"
OWNER="$CLAIM_DIR/owner.txt"

if [[ -d "$CLAIM_DIR" ]]; then
  # Already exists — idempotent only if the same agent owns it.
  if [[ -f "$OWNER" ]] && [[ "$(cat "$OWNER")" == "$AGENT_ID" ]]; then
    exit 0
  fi
  echo "claim.sh: $TASK_ID already claimed by $(cat "$OWNER" 2>/dev/null || echo '?')" >&2
  exit 3
fi

# mkdir is the atomic mutex: two racing callers — exactly one wins the create.
if mkdir "$CLAIM_DIR" 2>/dev/null; then
  printf '%s' "$AGENT_ID" > "$OWNER"
  exit 0
fi

# Lost the race between the -d check and mkdir: re-evaluate ownership.
if [[ -f "$OWNER" ]] && [[ "$(cat "$OWNER")" == "$AGENT_ID" ]]; then
  exit 0
fi
echo "claim.sh: $TASK_ID claimed concurrently by another agent" >&2
exit 3
