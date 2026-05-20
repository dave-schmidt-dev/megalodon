"""V9 M4 — shared constants registry.

CANONICAL source of truth for FE+BE shared identifiers. Run
`python3 scripts/gen_js_constants.py` after editing to regenerate
`ui/static/js/constants.js`. Pre-commit hook enforces this.

Do not put module-private constants here. Only FE/BE-shared values.
"""

from __future__ import annotations

# ─── localStorage keys (FE) ─────────────────────────────────────
CONTROL_MODE_KEY = "controlMode"

# ─── Time thresholds (FE + BE) ──────────────────────────────────
STALE_THRESHOLD_SECONDS = 900  # RULE-1, 15 min

# ─── SSE event names (FE + BE) ──────────────────────────────────
SSE_STATUS_CHANGE = "status-change"
SSE_TASK_CHANGE = "task-change"
SSE_PHASE_FLIP = "phase-flip"
SSE_FINDING_NEW = "finding-new"
SSE_HISTORY_APPEND = "history-append"
SSE_CLAIM_CREATE = "claim-create"
SSE_CLAIM_DONE = "claim-done"
SSE_SIGNAL_NEW = "signal-new"
SSE_LAGGING = "lagging"
SSE_HEARTBEAT = "heartbeat"
SSE_MISSION_STATUS = "mission-status"
SSE_SYNC = "sync"

SSE_EVENT_TYPES = (
    SSE_STATUS_CHANGE,
    SSE_TASK_CHANGE,
    SSE_PHASE_FLIP,
    SSE_FINDING_NEW,
    SSE_HISTORY_APPEND,
    SSE_CLAIM_CREATE,
    SSE_CLAIM_DONE,
    SSE_SIGNAL_NEW,
    SSE_LAGGING,
    SSE_HEARTBEAT,
    SSE_MISSION_STATUS,
    SSE_SYNC,
)

# ─── API paths (FE + BE) ────────────────────────────────────────
API_STATE = "/api/v1/state"
API_CONFIG = "/api/v1/config"
API_EVENTS = "/api/v1/events"
API_RECLAIM = "/api/v1/reclaim"
API_FINDINGS = "/api/v1/findings"
API_CHALLENGE = "/api/v1/challenge"
API_SIGNAL = "/api/v1/signal"
API_PHASE_FLIP = "/api/v1/phase-flip"
API_MISSION_STATUS = "/api/v1/mission-status"
API_INJECT_TASK = "/api/v1/inject-task"

# ─── Defaults ───────────────────────────────────────────────────
DEFAULT_PORT = 8080
