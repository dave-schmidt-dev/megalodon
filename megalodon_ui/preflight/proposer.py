"""megalodon_ui.preflight.proposer — prompt construction for pre-flight REPL.

Builds the system+user prompts passed to ClaudeAdapter for initial config
proposal and iterative refinement.
"""

from __future__ import annotations

# Field cheat-sheet embedded in prompts so Claude doesn't have to guess the schema.
_SCHEMA_CHEATSHEET = """\
MissionConfig fields (YAML keys, all required unless noted):
  schema_version: 1                          # integer, always 1
  mission:
    id: "my-project"                         # str, 1-80 chars
    utc_started: "2026-01-01T00:00:00Z"      # ISO 8601 UTC
    type: "software-engineering"             # str (optional, default shown)
    description: "..."                       # str (optional)
  lanes:                                     # list, at least 1 lane
    - name: "BACKEND"                        # UPPERCASE, max 20 chars, pattern ^[A-Z][A-Z0-9_-]*$
      short: "A"                             # 1-2 UPPERCASE letters (optional, auto-assigned if omitted)
      role: "Backend development lane"       # str (optional)
      harness:
        cli: "claude"                        # one of: claude|codex|gemini|copilot|cursor|vibe
        model: "claude-opus-4-7"             # model id string
        extra_args: []                       # list[str] (optional)
        auth_env: []                         # list[str] (optional)
      cadence_seconds: 300                   # int 30-3600 (optional, default 300)
      tick_offset_seconds: 0                 # int 0-600 (optional, default 0)
  phases:                                    # list of phase name strings, at least 1
    - "INIT"                                 # pattern ^[A-Z][A-Z0-9_-]*$
    - "PHASE-BUILD"
    - "COMPLETE"
  task_id_patterns:                          # optional
    patterns:
      - "^[A-Z][A-Za-z0-9\\-\\.]*$"
    description: ""
  orchestrator_pseudo_lane: "ORCHESTRATOR"  # str (optional, default shown)
  task_sections:                             # list[str] (optional)
    - "PHASE-PLAN"
    - "OPERATOR-ACCEPTANCE"
"""


def build_initial_prompt(goal: str, preamble: str) -> str:
    """Build the system+user prompt for Claude's initial config proposal.

    Returns a single string suitable for passing to ClaudeAdapter.build_argv
    as prompt_or_launch_path.
    """
    parts: list[str] = []

    if preamble.strip():
        parts.append("=== Prior mission context ===")
        parts.append(preamble.strip())
        parts.append("=== End prior mission context ===")
        parts.append("")

    parts.append("=== Operator mission goal ===")
    parts.append(goal.strip())
    parts.append("=== End operator mission goal ===")
    parts.append("")
    parts.append("=== MissionConfig schema cheat-sheet ===")
    parts.append(_SCHEMA_CHEATSHEET.strip())
    parts.append("=== End schema cheat-sheet ===")
    parts.append("")
    parts.append(
        "Based on the mission goal and any prior context above, propose a complete "
        ".mission-config.yaml for this project.\n"
        "\n"
        "STRICT OUTPUT RULES:\n"
        "- Output ONLY valid YAML text. No prose, no explanations, no markdown fences.\n"
        "- Do not wrap the YAML in ```yaml ... ``` or any other delimiters.\n"
        "- The YAML must conform exactly to the MissionConfig schema shown above.\n"
        "- Include all required fields. Use sensible defaults for optional fields.\n"
        "- Lane names must match pattern ^[A-Z][A-Z0-9_-]*$ (uppercase only).\n"
        "- Phase names must match pattern ^[A-Z][A-Z0-9_-]*$ (uppercase only).\n"
        "- Orchestrator lane must always use cli: claude (PW-5 requirement).\n"
        "- Output the YAML now:"
    )

    return "\n".join(parts)


def build_refine_prompt(prior_yaml: str, operator_feedback: str) -> str:
    """Construct the next refinement prompt: prior YAML + operator's revision.

    Returns a single string suitable for passing to ClaudeAdapter.build_argv.
    """
    parts: list[str] = []

    parts.append("=== Current .mission-config.yaml draft ===")
    parts.append(prior_yaml.strip())
    parts.append("=== End current draft ===")
    parts.append("")
    parts.append("=== Operator revision request ===")
    parts.append(operator_feedback.strip())
    parts.append("=== End revision request ===")
    parts.append("")
    parts.append("=== MissionConfig schema cheat-sheet ===")
    parts.append(_SCHEMA_CHEATSHEET.strip())
    parts.append("=== End schema cheat-sheet ===")
    parts.append("")
    parts.append(
        "Apply the operator's revision request to the current draft and output the "
        "revised .mission-config.yaml.\n"
        "\n"
        "STRICT OUTPUT RULES:\n"
        "- Output ONLY valid YAML text. No prose, no explanations, no markdown fences.\n"
        "- Do not wrap the YAML in ```yaml ... ``` or any other delimiters.\n"
        "- The YAML must conform exactly to the MissionConfig schema shown above.\n"
        "- Preserve all existing fields unless the operator's request changes them.\n"
        "- Output the revised YAML now:"
    )

    return "\n".join(parts)
