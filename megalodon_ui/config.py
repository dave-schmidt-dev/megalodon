"""AppConfig dataclass for megalodon_ui.

Per BACKEND P2.5-C plan-v2 Δ3: `poll_interval_seconds` is `float` (tests pass
0.05); `allowed_origins` derived from runtime port at make_app() time per FE C1.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _default_csrf() -> str:
    return secrets.token_hex(16)


@dataclass(frozen=True)
class AppConfig:
    """Tunables for the megalodon_ui FastAPI app.

    Most fields have sane defaults. `csrf_token` is generated per-instance via
    `secrets.token_hex(16)` unless overridden. Tests construct with
    `AppConfig(csrf_token="test-csrf", poll_interval_seconds=0.05)` for
    determinism + speed.

    `allowed_origins` is None by default; `make_app()` computes a tuple from
    the runtime `port` argument if not explicitly set.
    """

    csrf_token: str = field(default_factory=_default_csrf)
    heartbeat_interval_seconds: int = 15
    poll_interval_seconds: float = 2.0  # Δ3: float, not int
    file_watch_debounce_ms: int = 100
    stale_threshold_seconds: int = 900  # RULE 6 — 15 min
    sse_queue_capacity: int = 100
    allowed_origins: tuple[str, ...] | None = None
    static_dir: Path | None = None  # Optional override per CH-6 (fec0)
    log_level: str = "INFO"


def default_config_from_env() -> AppConfig:
    """Build AppConfig honoring MEGALODON_UI_* environment variables."""
    return AppConfig(
        csrf_token=os.environ.get("MEGALODON_UI_CSRF_TOKEN") or _default_csrf(),
        heartbeat_interval_seconds=int(os.environ.get("MEGALODON_UI_HEARTBEAT", "15")),
        poll_interval_seconds=float(os.environ.get("MEGALODON_UI_POLL", "2.0")),
        log_level=os.environ.get("MEGALODON_UI_LOG_LEVEL", "INFO"),
    )
