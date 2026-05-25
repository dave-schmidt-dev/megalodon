#!/usr/bin/env python3
"""governor_hook — PreToolUse hook entry shim (Task 2.1).

Resolves the repo root via its OWN real path (following any run-dir symlink),
then imports the governor hook module STANDALONE and delegates to main().

Hook command (in .claude/governor-settings.json):

    "$CLAUDE_PROJECT_DIR"/scripts/governor_hook.py

Lanes spawn with cwd = run dir; new_run.sh drops a relative symlink
``../../scripts`` → ``<run_dir>/scripts``, so ``$CLAUDE_PROJECT_DIR/scripts/``
resolves through that symlink back to the repo's real ``scripts/``.  Calling
``Path(__file__).resolve()`` here follows the symlink to the repo-canonical
path.

CRITICAL — bare-interpreter safety:
  Claude Code may run this hook under the bare system ``python3`` (no venv).
  The heavy ``megalodon_ui/__init__`` imports ``yaml`` (venv-only) at module
  scope, so a package-style ``from megalodon_ui.governor.hook import main``
  would ``ModuleNotFoundError`` under bare python and fail-closed-deny EVERY
  tool call — stalling the lane on call #1.  To stay stdlib-only we import the
  governor ``hook`` module STANDALONE: insert ``<repo>/megalodon_ui/governor``
  onto ``sys.path[0]`` and ``import hook`` as a TOP-LEVEL module, so the parent
  package ``__init__`` is never executed.  ``hook.py`` and ``policy.py`` are
  themselves stdlib-only; ``hook.py`` falls back to ``from policy import ...``
  when loaded this way.

Design constraints:
  * Import-light: only stdlib before the path bootstrap.
  * Executable: chmod +x (matching all scripts/*.py house style).
  * Shebang: ``#!/usr/bin/env python3`` (matching scripts/poll.py,
    scripts/queue_submit.py).

Spec: docs/superpowers/specs/ governor-hook wiring (Task 2.1).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Resolve OWN real path so this survives being invoked through the run-dir
# symlink (../../scripts → repo/scripts/).  parents[1] is the true repo root
# regardless of cwd or symlink depth.
_REPO_ROOT = Path(__file__).resolve().parents[1]

# Put the governor package DIRECTORY (not the repo root) on sys.path so `hook`
# and `policy` load as top-level modules WITHOUT executing megalodon_ui/__init__
# (which imports yaml). This keeps the hook runnable under bare system python3.
sys.path.insert(0, str(_REPO_ROOT / "megalodon_ui" / "governor"))

import hook  # noqa: E402

if __name__ == "__main__":
    hook.main()
