"""megalodon_ui.preflight.interview — interactive REPL state machine.

Spawns Claude (or a mock) to propose a MissionConfig, then iteratively refines
it based on operator input until approved or abandoned.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Callable

import yaml
from pydantic import ValidationError

from megalodon_ui.mission_config.schema import MissionConfig
from megalodon_ui.preflight.proposer import build_initial_prompt, build_refine_prompt


def _default_claude_runner(argv: list[str], env_overlay: dict[str, str]) -> str:
    """Default runner: invokes claude subprocess and returns stdout."""
    import os

    env = {**os.environ, **env_overlay}
    result = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"claude subprocess exited {result.returncode}:\n{result.stderr}"
        )
    return result.stdout


def _invoke_claude(prompt: str, claude_runner: Callable) -> str:
    """Build argv via ClaudeAdapter and call claude_runner."""
    from megalodon_ui.harnesses.claude import ClaudeAdapter
    from pathlib import Path

    adapter = ClaudeAdapter()
    argv, env_overlay = adapter.build_argv(
        prompt,
        model=adapter.default_model,
        cwd=Path("."),
        output_format="text",
    )
    return claude_runner(argv, env_overlay)


def _parse_and_validate(yaml_text: str) -> tuple[MissionConfig | None, str | None]:
    """Try to parse YAML text as MissionConfig. Returns (config, None) or (None, error_msg)."""
    # Strip markdown fences if Claude ignored our strict output rules
    stripped = yaml_text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        # Remove first line (```yaml or ```) and last ``` line
        inner_lines = []
        in_block = False
        for line in lines:
            if not in_block:
                if line.startswith("```"):
                    in_block = True
                continue
            if line.strip() == "```":
                break
            inner_lines.append(line)
        stripped = "\n".join(inner_lines)

    try:
        raw = yaml.safe_load(stripped)
    except yaml.YAMLError as exc:
        return None, f"YAML parse error: {exc}"

    if not isinstance(raw, dict):
        return None, f"Expected YAML mapping, got {type(raw).__name__}"

    try:
        config = MissionConfig.model_validate(raw)
    except ValidationError as exc:
        return None, f"Validation error:\n{exc}"

    return config, None


def _serialize_config(config: MissionConfig) -> str:
    """Serialize MissionConfig to YAML string."""
    return yaml.safe_dump(
        config.model_dump(mode="json"),
        sort_keys=False,
        default_flow_style=False,
    )


def run_interview(
    goal: str,
    preamble: str,
    max_refine: int,
    claude_runner: Callable | None = None,
) -> tuple[MissionConfig | None, str | None]:
    """Run the interactive REPL.

    Returns (approved_config, None) on approval; (None, last_draft_yaml) on abandon.
    `claude_runner` is a callable for dependency injection — defaults to a real
    subprocess.run of `claude --print ...`. Tests pass a mock.

    claude_runner signature: (argv: list[str], env_overlay: dict) -> str
    """
    if claude_runner is None:
        claude_runner = _default_claude_runner

    refine_count = 0
    current_yaml: str | None = None
    current_config: MissionConfig | None = None

    # Step 1: get initial proposal
    prompt = build_initial_prompt(goal, preamble)

    while True:
        # Invoke Claude
        try:
            raw_output = _invoke_claude(prompt, claude_runner)
        except RuntimeError as exc:
            print(f"\nError invoking Claude: {exc}", file=sys.stderr)
            return None, current_yaml

        # Parse + validate
        config, error = _parse_and_validate(raw_output)
        if config is None:
            # Validation failed — incorporate error into next refinement
            print(f"\nClaude returned invalid YAML: {error}", file=sys.stderr)
            if refine_count >= max_refine:
                print(
                    "\nMax refinement iterations reached and config is still invalid. Abandoning.",
                    file=sys.stderr,
                )
                return None, current_yaml
            refine_count += 1
            feedback = (
                f"The YAML you produced had the following error:\n{error}\n"
                "Please fix it and output a valid MissionConfig YAML."
            )
            prior = current_yaml if current_yaml else raw_output
            prompt = build_refine_prompt(prior, feedback)
            continue

        current_config = config
        current_yaml = _serialize_config(config)

        # Show the draft to the operator
        print("\n--- Current .mission-config.yaml draft ---")
        print(current_yaml)
        print("------------------------------------------")

        # Check if we're at the cap before accepting revision input
        at_cap = refine_count >= max_refine
        if at_cap:
            print(
                f"\nMax refinements ({max_refine}) reached. "
                "You must approve or abandon this draft."
            )
            # Inner loop: only accept approve/abandon — do NOT invoke Claude again
            while True:
                try:
                    operator_input = input("Type 'approve' to accept or 'abandon' to exit: ").strip()
                except EOFError:
                    return None, current_yaml
                if operator_input.lower() == "approve":
                    return current_config, None
                if operator_input.lower() == "abandon":
                    return None, current_yaml
                print("Please type 'approve' or 'abandon'.")
            # (unreachable — loop exits via return)

        # Prompt operator for normal revision / approve / abandon
        try:
            operator_input = input("approve / abandon / <revision request>: ").strip()
        except EOFError:
            # Non-interactive — treat as abandon
            return None, current_yaml

        if operator_input.lower() == "approve":
            return current_config, None

        if operator_input.lower() == "abandon":
            return None, current_yaml

        # Revision request
        refine_count += 1
        prompt = build_refine_prompt(current_yaml, operator_input)
