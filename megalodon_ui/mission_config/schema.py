from typing import Annotated, Any, Literal
from pydantic import BaseModel, Field, field_validator, StringConstraints

# ─── Top-level path-traversal guard (CR-3) ───────────────────────────
# Applied AFTER operator's task_id_patterns match, NOT as part of the
# operator's regex (so operator can't accidentally weaken it).
_FORBIDDEN_TASK_ID_CHARS = ("/", "\\", "..", "\x00")

def _assert_no_path_traversal(task_id: str) -> None:
    """PM-6 mitigation: clear error naming the specific char + doc link."""
    for forbidden in _FORBIDDEN_TASK_ID_CHARS:
        if forbidden in task_id:
            raise ValueError(
                f"task_id {task_id!r} contains forbidden character {forbidden!r}. "
                f"Task IDs become filesystem paths (claims/<task_id>/). "
                f"See docs/v9/v9-1-MISSION-CONFIG.md#task-id-grammar for allowed patterns."
            )


class HarnessBinding(BaseModel):
    cli: Literal["claude", "codex", "gemini", "copilot", "cursor", "vibe"]
    model: str
    extra_args: list[str] = Field(default_factory=list)
    auth_env: list[str] = Field(default_factory=list)


class LaneConfig(BaseModel):
    name: Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_-]*$", max_length=20)]
    short: Annotated[str, StringConstraints(pattern=r"^[A-Z]{1,2}$")] | None = None
    role: str = ""
    harness: HarnessBinding
    cadence_seconds: int = Field(ge=30, le=3600, default=300)
    tick_offset_seconds: int = Field(ge=0, le=600, default=0)
    # Live-REPL mode (v9.3 dogfood): spawn the harness CLI without --print so
    # it opens an interactive REPL inside its tmux pane. The initial_prompt
    # is sent post-spawn via tmux send-keys, enabling /loop autonomous
    # iteration (Claude Code-specific feature; other adapters ignore).
    live_repl: bool = False
    initial_prompt: str | None = None


class TaskIdPattern(BaseModel):
    patterns: list[str]
    description: str = ""

    @field_validator("patterns")
    @classmethod
    def patterns_compile(cls, v):
        import re
        for p in v:
            try:
                re.compile(p)
            except re.error as exc:
                raise ValueError(f"invalid regex pattern {p!r}: {exc}") from exc
        return v


class MissionInfo(BaseModel):
    """Required mission identity. Consumed by M3 deterministic agent_id + UI header."""
    id: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    utc_started: Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")]
    type: str = "software-engineering"
    description: str = ""


class MissionConfig(BaseModel):
    schema_version: int = 1
    mission: MissionInfo
    lanes: list[LaneConfig] = Field(min_length=1)
    phases: list[Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_-]*$")]] = Field(min_length=1)
    task_id_patterns: TaskIdPattern = Field(default_factory=lambda: TaskIdPattern(patterns=[r"^[A-Z][A-Za-z0-9\-\.]*$"]))
    harness_rebinding_reserved: dict[str, Any] = Field(default_factory=dict)

    orchestrator_pseudo_lane: Annotated[
        str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_-]*$", max_length=20)
    ] = "ORCHESTRATOR"

    task_sections: list[Annotated[str, StringConstraints(min_length=1, max_length=80)]] = Field(
        default_factory=lambda: ["PHASE-PLAN", "OPERATOR-ACCEPTANCE"]
    )

    @field_validator("lanes")
    @classmethod
    def lane_names_unique(cls, v):
        names = [l.name for l in v]
        if len(names) != len(set(names)):
            raise ValueError("duplicate lane names")
        return v

    @field_validator("lanes")
    @classmethod
    def short_codes_assigned_and_unique(cls, v):
        def to_short(i: int) -> str:
            if i < 26:
                return chr(ord("A") + i)
            return chr(ord("A") + (i // 26) - 1) + chr(ord("A") + (i % 26))
        seen = set()
        for i, lane in enumerate(v):
            if lane.short is None:
                lane.short = to_short(i)
            if lane.short in seen:
                raise ValueError(f"duplicate short code: {lane.short}")
            seen.add(lane.short)
        return v

    @field_validator("phases")
    @classmethod
    def phase_names_unique(cls, v):
        if len(v) != len(set(v)):
            raise ValueError("duplicate phase names")
        return v


def validate_task_id_with_config(task_id: str, config: "MissionConfig") -> None:
    """Validate a task_id against config patterns + path-traversal guard (CR-3)."""
    import re
    _assert_no_path_traversal(task_id)
    if not any(re.match(p, task_id) for p in config.task_id_patterns.patterns):
        raise ValueError(f"task_id does not match any configured pattern: {task_id!r}")
