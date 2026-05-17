"""V9 A3 — select model for a given lane.

Reads ``<mission_dir>/.scratch/fleet-matrix-override.json`` if present, falls
back to baked-in defaults documented in ``docs/v9/fleet-matrix.md``. Unknown
lanes fall back to ``opus-4.7`` (safe default for code lanes).
"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULTS = {
    "AUDIT": "sonnet-4.6",
    "ARCHITECT": "opus-4.7",
    "BACKEND": "opus-4.7",
    "FRONTEND": "opus-4.7",
    "TEST": "opus-4.7",
    "META": "sonnet-4.6",
}
_FALLBACK = "opus-4.7"


def select(lane: str, mission_dir: Path) -> str:
    """Return the model assignment for ``lane``.

    Override precedence:
        1. ``<mission_dir>/.scratch/fleet-matrix-override.json`` → ``lanes.<lane>.model``
        2. Baked-in ``DEFAULTS`` table.
        3. ``_FALLBACK`` (``opus-4.7``) for unknown lanes.
    """
    override = mission_dir / ".scratch" / "fleet-matrix-override.json"
    if override.exists():
        try:
            data = json.loads(override.read_text(encoding="utf-8"))
            model = (
                data.get("lanes", {})
                .get(lane, {})
                .get("model")
            )
            if model:
                return model
        except (json.JSONDecodeError, OSError):
            # Malformed override → fall through to defaults.
            pass
    return DEFAULTS.get(lane, _FALLBACK)
