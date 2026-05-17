"""V9 M1 — Pydantic payload schemas for queue intents.

Original 6 intents per QUEUE-DESIGN.md:
  STATUS_UPDATE, TASKS_BRACKET, HISTORY_APPEND, MISSION_EVENT_APPEND,
  CLAIM_DIR_CREATE, CLAIM_DIR_DONE.

Q1 additions per S-8 §A Q1:
  STATUS_ROW_INSERT, TASKS_INJECT, MISSION_EVENT_CORRECTION.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class StatusUpdatePayload(BaseModel):
    lane: str
    agent: str
    new_state: str
    new_utc: str
    new_notes: str = ""


class TasksBracketPayload(BaseModel):
    task_id: str
    new_bracket: str


class HistoryAppendPayload(BaseModel):
    line: str


class MissionEventAppendPayload(BaseModel):
    line: str


class ClaimDirCreatePayload(BaseModel):
    task_id: str
    owner_agent: str
    owner_lane: str = ""


class ClaimDirDonePayload(BaseModel):
    task_id: str
    agent: str


class StatusRowInsertPayload(BaseModel):
    lane: str
    agent: str
    initial_state: str = "idle"
    initial_utc: str
    initial_notes: str = ""


class TasksInjectPayload(BaseModel):
    task_id: str
    lane: str
    bracket: str = "[ ]"
    description: str
    after_task_id: str | None = None


class MissionEventCorrectionPayload(BaseModel):
    line: str

    @field_validator("line")
    @classmethod
    def must_have_correction_prefix(cls, v: str) -> str:
        if "CORRECTION by " not in v:
            raise ValueError("MISSION_EVENT_CORRECTION line must contain 'CORRECTION by '")
        return v


INTENT_SCHEMAS = {
    "STATUS_UPDATE": StatusUpdatePayload,
    "TASKS_BRACKET": TasksBracketPayload,
    "HISTORY_APPEND": HistoryAppendPayload,
    "MISSION_EVENT_APPEND": MissionEventAppendPayload,
    "CLAIM_DIR_CREATE": ClaimDirCreatePayload,
    "CLAIM_DIR_DONE": ClaimDirDonePayload,
    "STATUS_ROW_INSERT": StatusRowInsertPayload,
    "TASKS_INJECT": TasksInjectPayload,
    "MISSION_EVENT_CORRECTION": MissionEventCorrectionPayload,
}


def validate_payload(intent: str, payload: dict) -> None:
    """Raise ValueError if intent unknown or payload doesn't match schema."""
    if intent not in INTENT_SCHEMAS:
        raise ValueError(f"unknown intent: {intent!r}")
    INTENT_SCHEMAS[intent].model_validate(payload)
