"""megalodon_ui — Megalodon orchestrator-console package.

Public API:
    from megalodon_ui import primitives              # protocol pure functions
    from megalodon_ui.server import make_app         # FastAPI app factory
    from megalodon_ui.config import AppConfig        # tunables dataclass

The package uses lazy __getattr__ (PEP 562) so that
`from megalodon_ui import primitives` does NOT eagerly import FastAPI.
This preserves the stdlib-only invariant for unit tests of primitives.
"""

__version__ = "2.0.0"
__all__ = ["primitives", "make_app", "AppConfig"]

# Eagerly export primitives (stdlib-only, safe).
from . import primitives  # noqa: E402,F401


def __getattr__(name: str):
    """Lazy import for FastAPI-dependent attributes.

    Per P2.5-C plan-v2 Δ1.1 (closes BACKEND P2-C-to-B CH-1 against ARCH P1-B):
    importing `make_app` or `AppConfig` only loads FastAPI on demand, so
    `from megalodon_ui import primitives` stays in a stdlib-only world.
    """
    if name == "make_app":
        from .server import make_app

        return make_app
    if name == "AppConfig":
        from .config import AppConfig

        return AppConfig
    raise AttributeError(f"module 'megalodon_ui' has no attribute {name!r}")
