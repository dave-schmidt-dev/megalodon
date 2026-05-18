"""megalodon_ui.preflight — pre-flight CLI for operator mission config interviews.

Public surface:
    from megalodon_ui.preflight.proposer import build_initial_prompt, build_refine_prompt
    from megalodon_ui.preflight.interview import run_interview
    from megalodon_ui.preflight.writer import write_atomic, write_aborted_snapshot
"""

from megalodon_ui.preflight.proposer import build_initial_prompt, build_refine_prompt
from megalodon_ui.preflight.interview import run_interview
from megalodon_ui.preflight.writer import write_atomic, write_aborted_snapshot

__all__ = [
    "build_initial_prompt",
    "build_refine_prompt",
    "run_interview",
    "write_atomic",
    "write_aborted_snapshot",
]
