"""conftest.py — ensure project-root megalodon_ui/ is on sys.path for pytest.

pytest's rootdir is auto-detected as `ui/tests/` (because of `ui/tests/pytest.ini`),
so a project-root conftest.py is NOT loaded by pytest. This conftest.py lives
inside the rootdir and inserts the project root into sys.path before any test
collection runs.

Without this, `from megalodon_ui import primitives` raises ImportError under
`uv run --with pytest ...` (uv-managed environments don't auto-add CWD to
sys.path), so every `@pytest.mark.skipif(not BACKEND_AVAILABLE)` gate stays
SKIPPED even after BACKEND ships the stub.

Added per P3-E Stage 2 (TEST agent-43d9 @ 2026-05-16T19:09Z).
Cited evidence: standalone `from megalodon_ui import primitives` succeeds; same
under pytest fails with ImportError; collect-only confirms pytest's rootdir is
`ui/tests/`.

If BACKEND P3-C eventually ships `pyproject.toml` making megalodon_ui pip-
installable, this file becomes redundant but remains harmless.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
