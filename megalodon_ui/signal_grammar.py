"""Shared signal-filename grammar (single source of truth).

The canonical signal filename grammar (FROZEN WIRE CONTRACT §A) is consumed by
BOTH ``server.py`` (``_parse_file_signals``) and ``activity_wall.py``
(``_build_signal_event``). It used to be duplicated byte-for-byte in those two
modules with "keep in sync" comments, which is a drift hazard. This leaf module
holds the one true copy.

This module intentionally imports nothing from ``server`` or ``activity_wall``
(server imports ActivityWall, so a back-import would create a cycle). It depends
only on the standard library, so both consumers can import it at module load.

Grammar
-------
Canonical::

    LANE-<FROM>-to-LANE-<TO>-<topic>-<UTC>.md

where ``<UTC>`` is a filesystem-safe dash-form stamp anchored at the end:
``YYYY-MM-DDTHH-MM[-SS]Z``. The body is free-form markdown. The FE consumer
``ui/static/pages/signals.js`` reads ``sig.from_lane``, ``sig.to``, ``sig.utc``,
``sig.topic``, ``sig.kind``.

Legacy fallback (no separate trailing UTC segment): ``topic`` = remainder,
``utc`` = "".
"""

from __future__ import annotations

import re

# Canonical filename grammar (FROZEN WIRE CONTRACT §A): from-lane, to-lane,
# topic (slug), and a filesystem-safe dash-form UTC anchored at the end.
SIGNAL_FILENAME_RE = re.compile(
    r"^(?P<from_lane>LANE-[A-Z0-9]+)-to-(?P<to_lane>LANE-[A-Z0-9]+)-"
    r"(?P<topic>.+)-(?P<utc>\d{4}-\d{2}-\d{2}T\d{2}-\d{2}(?:-\d{2})?Z)$"
)
# Legacy fallback (no separate utc segment): topic = remainder, utc = "".
SIGNAL_FILENAME_LEGACY_RE = re.compile(
    r"^(?P<from_lane>LANE-[A-Z0-9]+)-to-(?P<to_lane>LANE-[A-Z0-9]+)-(?P<rest>.+)$"
)


def parse_signal_filename(name: str) -> dict[str, str] | None:
    """Parse a signal filename *stem* into its grammar fields.

    Accepts either a bare stem or a name ending in ``.md`` (the suffix is
    stripped). Returns a dict with ``from_lane``, ``to_lane``, ``topic`` and
    ``utc`` keys, or ``None`` when *name* matches neither the canonical nor the
    legacy grammar (i.e. it is not a signal file).

    The canonical match yields a non-empty ``utc``; the legacy fallback yields
    ``utc == ""`` and ``topic`` = the remainder after the ``-to-`` segment.
    """
    stem = name[:-3] if name.endswith(".md") else name
    m = SIGNAL_FILENAME_RE.match(stem)
    if m:
        return {
            "from_lane": m.group("from_lane"),
            "to_lane": m.group("to_lane"),
            "topic": m.group("topic"),
            "utc": m.group("utc"),
        }
    legacy = SIGNAL_FILENAME_LEGACY_RE.match(stem)
    if legacy:
        return {
            "from_lane": legacy.group("from_lane"),
            "to_lane": legacy.group("to_lane"),
            "topic": legacy.group("rest"),
            "utc": "",
        }
    return None
