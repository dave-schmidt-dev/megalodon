"""INV-5 gate test — the local e2e gate must run EVERY chromium Playwright project.

This is the local-gate re-expression of the retired INV-3. The original concern:
a project-gap in the blocking gate hid real regressions — chromium-board-only
gating let the CSRF-routes break and the mission-status SSOT split ship twice
because the un-run projects never failed. CI is gone; the guardrail now lives on
the local `make` gate.

Contract: every `chromium-*` project defined in `ui/tests/e2e/playwright.config.ts`
MUST appear in the Makefile's `E2E_CHROMIUM` project list (what `make test-e2e` /
`gate-full` runs). webkit-* projects are INTENTIONALLY excluded from the local gate
(they run in manual CI-parity sweeps per the README), so they are not required here
— only the gate's chosen browser (chromium) must be gap-free.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYWRIGHT_CONFIG = REPO_ROOT / "ui" / "tests" / "e2e" / "playwright.config.ts"
MAKEFILE = REPO_ROOT / "Makefile"

_PROJECT_NAME_RE = re.compile(r"""name:\s*['"](chromium-[a-z0-9-]+)['"]""")
_MAKE_PROJECT_RE = re.compile(r"--project=(chromium-[a-z0-9-]+)")


def _config_chromium_projects() -> set[str]:
    return set(_PROJECT_NAME_RE.findall(PLAYWRIGHT_CONFIG.read_text(encoding="utf-8")))


def _gate_chromium_projects() -> set[str]:
    return set(_MAKE_PROJECT_RE.findall(MAKEFILE.read_text(encoding="utf-8")))


def test_files_exist():
    assert PLAYWRIGHT_CONFIG.exists(), f"missing {PLAYWRIGHT_CONFIG}"
    assert MAKEFILE.exists(), f"missing {MAKEFILE}"


def test_gate_runs_every_chromium_project():
    """No chromium project may be silently omitted from the local e2e gate."""
    defined = _config_chromium_projects()
    gated = _gate_chromium_projects()
    assert defined, (
        "no chromium-* projects parsed from playwright.config.ts (regex drift?)"
    )
    missing = defined - gated
    assert not missing, (
        "INV-5 violated: these chromium projects are defined in playwright.config.ts "
        f"but NOT run by the Makefile gate (E2E_CHROMIUM): {sorted(missing)}. "
        "Add them to E2E_CHROMIUM or a project-gap can hide regressions (the bug INV-3 caught)."
    )


def test_gate_has_no_phantom_projects():
    """The gate must not reference chromium projects that no longer exist in the config."""
    defined = _config_chromium_projects()
    gated = _gate_chromium_projects()
    phantom = gated - defined
    assert not phantom, (
        f"Makefile E2E_CHROMIUM references chromium projects absent from "
        f"playwright.config.ts: {sorted(phantom)} (rename/removal drift)."
    )
