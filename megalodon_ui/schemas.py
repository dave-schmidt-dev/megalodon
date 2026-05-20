"""V9 M2 — Pydantic response models for contract enforcement.

Top-level response shapes only; inner dicts (tasks/history/findings) stay
loose for v9 — tighten in v10. See spec D6 in
docs/superpowers/specs/2026-05-16-v9-m2-contract-scan-design.md.

Import-time drift assert ensures `SSEEventName` Literal stays in sync with
`megalodon_ui.constants.SSE_EVENT_TYPES`. M4 dependency.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from .constants import SSE_EVENT_TYPES


SSEEventName = Literal[
    "status-change",
    "task-change",
    "phase-flip",
    "finding-new",
    "history-append",
    "claim-create",
    "claim-done",
    "signal-new",
    "lagging",
    "heartbeat",
    "mission-status",
    "sync",
]
# Import-time drift assert: schemas.py SSEEventName must match constants.SSE_EVENT_TYPES.
_declared = frozenset(SSEEventName.__args__)
_canonical = frozenset(SSE_EVENT_TYPES)
assert _declared == _canonical, (
    f"schemas.py SSEEventName drifted from constants.SSE_EVENT_TYPES: "
    f"missing={_canonical - _declared} extra={_declared - _canonical}"
)


class LaneStatus(BaseModel):
    lane: str
    agent: str | None = None
    state: str
    last_utc: str
    staleness_seconds: float | None = None
    is_stale: bool = False
    notes: str = ""


class StatusBlock(BaseModel):
    lanes: list[dict[str, Any]] = []


class StateResponse(BaseModel):
    status: dict[str, Any]
    tasks: dict[str, Any]
    findings: dict[str, Any]
    signals: dict[str, Any]
    mission: dict[str, Any]
    config: dict[str, Any]


class ConfigResponse(BaseModel):
    csrf_token: str
    heartbeat_interval_seconds: int
    poll_interval_seconds: int
    stale_threshold_seconds: int
    allowed_origins: list[str]


class FindingSummary(BaseModel):
    filename: str
    lane: str | None = None
    severity: str | None = None
    task_id: str | None = None
    mtime_utc: str | None = None


class FindingsListResponse(BaseModel):
    findings: list[dict[str, Any]]


class FindingDetailResponse(BaseModel):
    filename: str
    body: str
    frontmatter: dict[str, Any] = {}


class ActionResponse(BaseModel):
    """Generic POST-action acknowledgement (legacy v9 pre-M1.5)."""

    ok: bool
    message: str = ""


class QueueAcceptResponse(BaseModel):
    """V9 M1.5 — 202 Accepted body for queue-routed mutation endpoints.

    Returned by POST /api/v1/{reclaim,signal,challenge,inject-task}.
    Header `Location: /api/v1/queue/{request_id}` provided for polling.
    """

    request_id: str
    intent: str  # e.g. "STATUS_UPDATE", "TASKS_INJECT"
    status: str  # "pending"


class QueueStatusResponse(BaseModel):
    """V9 M1.5 — GET /api/v1/queue/{request_id} response."""

    request_id: str
    status: str  # "pending" | "applied" | "rejected"
    rejection_reason: str | None = None
