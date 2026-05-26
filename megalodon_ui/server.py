"""megalodon_ui.server — FastAPI app factory.

`make_app(*, mission_dir, config=None, port=8080)` returns an ASGI app bound
to the given mission directory. Pure factory: two calls produce two
independent apps. No module-level globals; all state in `MissionContext`
attached to `app.state.megalodon`.

This is the BACKEND P3-C deliverable per the P2.5-C plan-v2 8-step sequence.
The endpoint surface here covers the integration-test contract; the legacy
`/api/v1/*` surface in `ui/server.py` remains the live dashboard server
until the migration is complete.
"""

from __future__ import annotations

import asyncio
import collections
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import AppConfig
from . import primitives
from . import signal_parser
from .queue import queue_client as _qc
from .mission_config import load_mission_config
from .mission_config.schema import MissionConfig
from .mission_config.regex_builder import (
    build_task_line_re,
    build_status_row_re,
)
from .signal_grammar import (
    # Re-exported under their historical private names so external callers
    # (e.g. scripts/tests/test_signal_channels.py) keep importing them from
    # ``megalodon_ui.server``. server.py itself routes through
    # ``parse_signal_filename`` rather than touching the compiled patterns.
    SIGNAL_FILENAME_RE as _SIGNAL_FILENAME_RE,  # noqa: F401
    SIGNAL_FILENAME_LEGACY_RE as _SIGNAL_FILENAME_LEGACY_RE,  # noqa: F401
    parse_signal_filename,
)
from .constants import (
    API_CHALLENGE,
    API_CONFIG,
    API_EVENTS,
    API_FINDINGS,
    API_INJECT_TASK,
    API_MISSION_STATUS,
    API_PHASE_FLIP,
    API_RECLAIM,
    API_SIGNAL,
    API_STATE,
    SSE_STATUS_CHANGE,
    SSE_SYNC,
    STALE_THRESHOLD_SECONDS,
)
from ._v92_constants import (
    COOKIE_MAX_AGE_SECONDS,
    LIFESPAN_STARTUP_TIMEOUT_SECONDS,
    SOCKET_PATH_LIMIT_BYTES,
    TAIL_ON_CONNECT_BYTES,
)
from . import auth
from . import tmux
from .spawn import FleetSpawner, TooManySubscribersError
from .harnesses import get_adapter


# ---------------------------------------------------------------------------
# v9.2 auth gate — DENY-BY-DEFAULT (security inversion)
# ---------------------------------------------------------------------------
#
# Previously the gate was an *allowlist of gated paths*: anything not matched by
# ``_V92_GATED_PATH_RE`` / ``_V92_GATED_EXACT`` was served WITHOUT a cookie.
# That left the entire legacy ``/api/v1/*`` surface (``/state``, ``/config``,
# ``/findings``, all mutation POSTs, the SSE ``/events`` stream, …) wide open —
# and ``GET /api/v1/config`` even handed out the CSRF token unauthenticated,
# defeating CSRF.
#
# The gate is now deny-by-default: EVERY request requires a valid ``mui_session``
# cookie EXCEPT a small, explicit PUBLIC allowlist below. The public set is the
# minimum needed to bootstrap the SPA and exchange a token:
#   * the SPA shell + its assets (``/``, ``/index.html``, ``/static/*``,
#     ``/favicon*``),
#   * the liveness probe (``GET /healthz``),
#   * the token-exchange endpoint (``POST /api/v1/auth/exchange``) — the ONE
#     door through which a cookie is minted.
# Everything else (anything under ``/api/**``, the SSE streams, every mutation)
# is gated. FE bootstraps auth before any other fetch, so this is safe.

#: The ONE public endpoint under ``/api/**`` — the token-exchange door through
#: which a session cookie is minted. Everything else under ``/api/**`` is gated.
_V92_PUBLIC_API_EXACT: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/api/v1/auth/exchange"),
    }
)

#: Cookie name used to carry the session id after exchange.
SESSION_COOKIE_NAME = "mui_session"


def _v92_path_is_gated(method: str, path: str) -> bool:
    """Return True iff (*method*, *path*) REQUIRES a valid session cookie.

    Deny-by-default for the data/control plane: any path under ``/api/**`` is
    gated (this covers ``/state``, ``/config``, ``/findings*``, ``/events`` SSE,
    ``/tasks``, ``/status``, the legacy ``/api/*`` duplicates, AND every
    mutation POST) EXCEPT the single public token-exchange endpoint.

    Non-``/api`` paths are NOT gated: they only resolve to the SPA shell
    (``/``, the ``/{spa_path}`` catch-all that re-serves ``index.html`` for
    client-side routes like ``/tasks``), the ``/static/*`` bundle, ``/favicon*``,
    and ``GET /healthz``. None of those expose mission data — they are the
    bootstrap surface the login flow needs BEFORE a cookie exists. The SPA fetches
    everything sensitive through gated ``/api/**`` calls after auth.
    """
    if not path.startswith("/api/"):
        return False
    if (method, path) in _V92_PUBLIC_API_EXACT:
        return False
    return True


# ---------------------------------------------------------------------------
# v9.4 GET /api/v1/lanes/stale — module-level cache + test-override hook
# ---------------------------------------------------------------------------

# Per-app cache keyed by id(app). Each value: {"response": dict, "computed_at": float}.
# Module-level so concurrent requests within the TTL window get a single
# computation. Keys are cleaned up lazily — they accumulate only across
# make_app() calls within a process (negligible for production; fine for tests).
_stale_cache: dict[int, dict] = {}

#: One-shot per-lane silent_seconds override, populated ONLY by the
#: ``_test/stale_override`` endpoint. Consumed once on the next
#: ``GET /api/v1/lanes/stale`` call and then cleared. Setting
#: ``_TEST_STALE_OVERRIDES["A"] = 1200.0`` makes lane "A" appear 1200 s
#: stale in the next response.
_TEST_STALE_OVERRIDES: dict[str, float] = {}

_STALE_THRESHOLD_SECONDS: float = 900.0
_STALE_CACHE_TTL_SECONDS: float = 5.0

# Governor deny-loop detection (plan §8.3/§8.7): a fail-closed governor can
# produce a deny→retry→deny loop. Such a lane is NOT silent (so silence-based
# `stale` won't catch it), or a governor-blocked lane goes quiet and gets
# mis-read as `stale` and killed. A deny-looping lane therefore gets a DISTINCT
# `governor-blocked` status computed from the governor-log, not from silence.
_GOVERNOR_BLOCK_WINDOW_SECONDS = 60.0  # deny-loop detection window
_GOVERNOR_BLOCK_DENY_COUNT = 5  # N denies within the window → governor-blocked
_GOVERNOR_LOG_TAIL_LINES = 500  # bound governor-log read to the tail

# Regex to extract the UTC timestamp from an applier log line:
# Format: 2026-05-16T22:00:00Z | INFO | APPLIED rid=... agent=agent-xxxx lane=... ...
_APPLIER_LOG_TS_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z", re.MULTILINE
)
# Parse agent= field from an applier log line.
_APPLIER_LOG_AGENT_RE = re.compile(r"\bagent=(\S+)")
# Parse lane= (submitting_lane) field from an applier log line — matches lane=X NOT submitting_lane=.
_APPLIER_LOG_LANE_RE = re.compile(r"(?<!\w)lane=(\S+)")


def _stale_latest_applier_ts(mission_dir: Path, agent_id: str) -> datetime | None:
    """Return the most-recent applier-log timestamp for *agent_id*.

    Scans ``.fleet/queue-applier.log`` from the end (lines reversed) to find
    the latest entry where ``agent=<agent_id>`` appears. Returns a UTC-aware
    datetime, or None if the file is missing / no matching entry.
    """
    log_path = mission_dir / ".fleet" / "queue-applier.log"
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    best: datetime | None = None
    for line in text.splitlines():
        am = _APPLIER_LOG_AGENT_RE.search(line)
        if am is None or am.group(1) != agent_id:
            continue
        ts_m = _APPLIER_LOG_TS_RE.search(line)
        if ts_m is None:
            continue
        try:
            ts = datetime.fromisoformat(ts_m.group(1) + "+00:00")
        except ValueError:
            continue
        if best is None or ts > best:
            best = ts
    return best


def _compute_governor_blocked(mission_dir: Path) -> dict[str, dict]:
    """Detect lanes stuck in a governor deny-loop (plan §8.3/§8.7).

    Reads today's ``.fleet/governor-log-{YYYY-MM-DD}.jsonl`` (UTC date) and, per
    lane, counts ``permission == "deny"`` decisions whose ``ts`` falls within the
    last ``_GOVERNOR_BLOCK_WINDOW_SECONDS``. A lane with
    ``deny_count >= _GOVERNOR_BLOCK_DENY_COUNT`` is governor-blocked.

    Returns a mapping ``lane_short -> {deny_count, window_seconds, last_category,
    last_reason}`` containing only governor-blocked lanes. Robust by design:
    missing file → ``{}``; unparseable lines / bad timestamps are skipped; never
    raises.

    Only the tail matters, so at most the last ``_GOVERNOR_LOG_TAIL_LINES`` lines
    of today's file are considered (bounded; mirrors how
    ``_stale_latest_applier_ts`` reads the whole file but cannot blow up here
    because we slice the line list).
    """
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    log_path = mission_dir / ".fleet" / f"governor-log-{today}.jsonl"
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    window_start = now.timestamp() - _GOVERNOR_BLOCK_WINDOW_SECONDS
    # Per-lane: deny count in window + most-recent category/reason in window.
    counts: dict[str, int] = {}
    last_seen: dict[str, tuple[float, str | None, str | None]] = {}
    # Per-lane TRAILING consecutive-deny run (Task E / contract §3): count of
    # consecutive `deny` decisions since the lane's last `allow`, across today's
    # full (tail-bounded) log — distinct from the window deny count above. An
    # `allow` resets the run to 0; a `deny` increments it. Because we process
    # lines in document (chronological) order, the value left in this dict after
    # the loop IS the trailing run.
    consecutive: dict[str, int] = {}

    # Only the tail matters for a deny-loop; bound the work on a huge file.
    lines = text.splitlines()[-_GOVERNOR_LOG_TAIL_LINES:]
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue
        permission = entry.get("permission")
        if permission not in ("deny", "allow"):
            continue
        lane = entry.get("lane")
        if not lane:
            continue

        # Consecutive-deny tracking runs on EVERY decision line (no timestamp
        # gate): an allow anywhere resets the trailing run, a deny extends it.
        if permission == "allow":
            consecutive[lane] = 0
            continue
        consecutive[lane] = consecutive.get(lane, 0) + 1

        # --- window deny-count (existing behaviour) ---
        raw_ts = entry.get("ts")
        try:
            ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
        except (ValueError, AttributeError, TypeError):
            continue
        ts_epoch = ts.timestamp()
        if ts_epoch < window_start:
            continue
        counts[lane] = counts.get(lane, 0) + 1
        prev = last_seen.get(lane)
        if prev is None or ts_epoch >= prev[0]:
            last_seen[lane] = (
                ts_epoch,
                entry.get("category"),
                entry.get("reason"),
            )

    blocked: dict[str, dict] = {}
    for lane, deny_count in counts.items():
        if deny_count >= _GOVERNOR_BLOCK_DENY_COUNT:
            _, last_category, last_reason = last_seen[lane]
            blocked[lane] = {
                "deny_count": deny_count,
                "window_seconds": _GOVERNOR_BLOCK_WINDOW_SECONDS,
                "last_category": last_category,
                "last_reason": last_reason,
                # Trailing consecutive denies since the last allow (contract §3).
                "consecutive_denies": consecutive.get(lane, 0),
            }
    return blocked


#: Bound the alerts JSONL read so a long-running mission's log can't blow up
#: the endpoint; the operator only needs the recent tail anyway.
_ALERTS_TAIL_LINES = 500


def _read_alerts(mission_dir: Path, limit: int = _ALERTS_TAIL_LINES) -> list[dict]:
    """Read the structured watchdog alerts JSONL, newest-first (contract §2).

    Source: ``.fleet/watchdog-alerts.jsonl`` (written by ``AlertManager``). Each
    line is one ``{ts, lane, kind, severity, evidence, message}`` record. Robust
    by design: a missing file → ``[]``; unparseable / non-dict lines are skipped;
    never raises. Only the last ``limit`` lines are considered (bounded), then
    reversed so the newest alert is first.

    Args:
        mission_dir: Mission root.
        limit: Max trailing lines to parse.

    Returns:
        List of alert dicts, newest-first.
    """
    from .watchdog.alerts import ALERTS_JSONL_RELPATH

    path = mission_dir / ALERTS_JSONL_RELPATH
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: list[dict] = []
    for line in text.splitlines()[-limit:]:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(entry, dict):
            out.append(entry)
    out.reverse()  # newest-first
    return out


def _compute_stale_response(
    mission_dir: Path,
    lane_rows: list[dict[str, Any]],
    mission_config: "MissionConfig",
) -> dict:
    """Compute the full stale-lanes payload (no caching here).

    *Source priority* (all three are considered; the newest wins):
      1. status-md  — ``last_utc`` parsed from STATUS.md row.
      2. stream-log — mtime of ``.fleet/<short>.stream.log``.
      3. applier-log — newest line in ``.fleet/queue-applier.log`` matching
                       the lane's agent_id from the STATUS.md ``agent`` column.

    Tie-break: when two sources share the same second-level precision we prefer
    in order: applier-log > stream-log > status-md (i.e. last wins on a
    ``max()`` over ``(ts, source_priority)`` tuples).

    A lane detected as governor-blocked (deny-loop, see
    ``_compute_governor_blocked``) is reported in a separate ``governor_blocked``
    list and is EXCLUDED from ``stale_lanes`` — the operator must not kill a
    governor-blocked lane thinking it is merely stale (plan §8.3/§8.7).
    """
    now = datetime.now(timezone.utc)

    # parse_status returns "LANE-A" form; mission_config.lanes.short is "A".
    def _row_short(row: dict) -> str:
        lane = row.get("lane", "")
        return lane[len("LANE-") :] if lane.startswith("LANE-") else lane

    short_to_agent: dict[str, str] = {
        _row_short(r): r.get("agent", "") for r in lane_rows
    }

    governor_blocked_map = _compute_governor_blocked(mission_dir)

    stale_lanes: list[dict] = []
    for lc in mission_config.lanes:
        short = lc.short
        if not short:
            continue

        # --- source 1: STATUS.md last_utc ---
        status_ts: datetime | None = None
        row = next((r for r in lane_rows if _row_short(r) == short), None)
        if row is not None:
            raw = row.get("last_utc", "")
            try:
                status_ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        # --- source 2: stream-log mtime ---
        stream_ts: datetime | None = None
        stream_log = mission_dir / ".fleet" / f"{short}.stream.log"
        try:
            mtime = stream_log.stat().st_mtime
            stream_ts = datetime.fromtimestamp(mtime, tz=timezone.utc)
        except OSError:
            pass

        # --- source 3: applier-log latest matching entry ---
        applier_ts: datetime | None = None
        agent_id = short_to_agent.get(short)
        if agent_id:
            applier_ts = _stale_latest_applier_ts(mission_dir, agent_id)

        # --- max of sources with priority tie-break ---
        # Priority order (highest last so max picks it on equal timestamps):
        # 0=status-md, 1=stream-log, 2=applier-log
        candidates: list[tuple[datetime, int, str]] = []
        if status_ts is not None:
            candidates.append((status_ts, 0, "status-md"))
        if stream_ts is not None:
            candidates.append((stream_ts, 1, "stream-log"))
        if applier_ts is not None:
            candidates.append((applier_ts, 2, "applier-log"))

        if candidates:
            best_ts, _, best_source = max(candidates, key=lambda t: (t[0], t[1]))
            silent_seconds = (now - best_ts).total_seconds()
            last_activity_source = best_source
        else:
            silent_seconds = float("inf")
            last_activity_source = "none"

        # --- test-override hook (one-shot) ---
        # Note: caller (_compute_stale_response's caller) uses the return value
        # to detect which lanes had overrides consumed and invalidate the cache.
        if short in _TEST_STALE_OVERRIDES:
            silent_seconds = _TEST_STALE_OVERRIDES.pop(short)

        # A governor-blocked lane is reported separately, never as plain stale,
        # so the operator does not kill it thinking it is merely silent.
        is_stale = (
            silent_seconds >= _STALE_THRESHOLD_SECONDS
            and short not in governor_blocked_map
        )

        if is_stale:
            stale_lanes.append(
                {
                    "lane": short,
                    "silent_seconds": silent_seconds
                    if silent_seconds != float("inf")
                    else None,
                    "last_activity_source": last_activity_source,
                }
            )

    governor_blocked = [
        {"lane": lane, **info} for lane, info in governor_blocked_map.items()
    ]

    return {
        "stale_lanes": stale_lanes,
        "governor_blocked": governor_blocked,
        "checked_at_utc": now.isoformat(),
    }


# ---------------------------------------------------------------------------
# MissionContext — per-app state attached to app.state.megalodon
# ---------------------------------------------------------------------------


@dataclass
class MissionContext:
    """Per-`make_app` instance state.

    No module globals; multiple `make_app()` calls in one process produce
    independent contexts (required for parallel pytest workers).
    """

    mission_dir: Path
    config: AppConfig
    port: int
    csrf_token: str  # mirror of config.csrf_token for fast access
    allowed_origins: tuple[str, ...]
    mission_config: MissionConfig = field(default=None)  # type: ignore[assignment]
    status_row_re: re.Pattern = field(default=None)  # type: ignore[assignment]
    task_line_re: re.Pattern = field(default=None)  # type: ignore[assignment]
    session_store: "auth.SessionStore" = field(default=None)  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def parse_status(
    mission_dir: Path, ctx: "MissionContext | None" = None
) -> list[dict[str, Any]]:
    """Parse STATUS.md table into a list of lane dicts.

    REPAIR-MUTATIONS-E2E-5-STATUS-VIEW: each row gets `staleness_seconds`
    (float, age since last_utc) and `is_stale` (bool, RULE-1 15min threshold).
    Consumed by FE `dashboard.js:115,187` for `data-stale` attr + band class.
    """
    path = mission_dir / "STATUS.md"
    if not path.exists():
        return []
    text = path.read_text()
    now = datetime.now(timezone.utc)
    status_re = (
        ctx.status_row_re
        if ctx is not None
        else build_status_row_re(load_mission_config(mission_dir))
    )
    rows: list[dict[str, Any]] = []
    for m in status_re.finditer(text):
        lane = m.group("lane").strip()
        if lane.lower() == "lane":
            continue
        agent = m.group("agent").strip()
        if agent.startswith("---") or agent == "":
            continue
        last_utc = m.group("last_utc").strip()
        staleness_seconds: float | None = None
        is_stale = False
        try:
            ts = datetime.fromisoformat(last_utc.replace("Z", "+00:00"))
            staleness_seconds = (now - ts).total_seconds()
            is_stale = staleness_seconds > STALE_THRESHOLD_SECONDS  # RULE-1: 15 min
        except (ValueError, AttributeError):
            pass
        rows.append(
            {
                "lane": lane,
                "agent": agent,
                "state": m.group("state").strip(),
                "last_utc": last_utc,
                "notes": m.group("notes").strip(),
                "staleness_seconds": staleness_seconds,
                "is_stale": is_stale,
            }
        )
    return rows


def _parse_yaml_frontmatter(text: str) -> dict[str, Any]:
    """Minimal YAML frontmatter parser (sufficient for our finding files)."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    block = text[3:end].strip()
    out: dict[str, Any] = {}
    for line in block.splitlines():
        if ":" in line and not line.startswith(" "):
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out


def _build_task_section_re(mc: "MissionConfig | None") -> "re.Pattern | None":
    """Build the `^## <title>$` matcher for TASKS.md, honoring `task_sections`.

    The real missions use human-readable section headers (``## PHASE 1 — PLAN``)
    declared in ``config.task_sections``; older/canonical missions use
    ``## PHASE-PLAN`` declared in ``config.phases``.  This matcher accepts BOTH
    so every TASKS.md format parses (Bug fix: ``/api/v1/tasks`` previously only
    matched canonical phase tokens and so returned ``{"phases": []}`` on real
    missions).  Returns None when neither source supplies any title.
    """
    section_titles: list[str] = []
    if mc is not None and getattr(mc, "task_sections", None):
        section_titles = list(mc.task_sections)
    if mc is not None:
        for p in mc.phases:
            if p not in section_titles:
                section_titles.append(p)
    if not section_titles:
        return None
    # Length-descending so e.g. "PHASE 2.5 — Plan-v2" wins over a "PHASE 2"
    # prefix-match. Escape so / — ( ) are matched literally.
    section_titles.sort(key=len, reverse=True)
    return re.compile(
        r"^##\s+(?P<title>" + "|".join(re.escape(t) for t in section_titles) + r")\s*$",
        re.MULTILINE,
    )


def _parse_task_state_block(state_block: str) -> tuple[str, str | None, str | None]:
    """Decode a task line's ``state_block`` into (state, agent, utc).

    States: ``open`` (blank), ``done:``, ``claimed:``, ``blocked:``.  For
    done/claimed the trailing ``agent@utc`` is split out.  Anything else is
    treated as ``open``.
    """
    state_block = state_block.strip()
    if state_block in ("", " "):
        return "open", None, None
    for prefix, state in (("done:", "done"), ("claimed:", "claimed")):
        if state_block.startswith(prefix):
            rest = state_block[len(prefix) :].strip()
            agent, _, utc = rest.partition("@")
            return state, (agent.strip() or None), (utc.strip() or None)
    if state_block.startswith("blocked:"):
        return "blocked", None, None
    return "open", None, None


def parse_tasks(
    mission_dir: Path, ctx: "MissionContext | None" = None
) -> list[dict[str, Any]]:
    """Parse TASKS.md into a list of phase dicts.

    REPAIR-MUTATIONS-E2E-5-STATUS-VIEW: shape `[{name, tasks: [...]}]`.
    Each task dict has `id`, `lane`, `state`
    ("open"|"claimed"|"done"|"blocked"), `agent` (if claimed/done), `utc` (if
    claimed/done), `description`.  Consumed by FE `tasks.js:400-419` via
    ``GET /api/v1/tasks``.

    Sections are matched from ``config.task_sections`` (human headers like
    ``## PHASE 1 — PLAN``) with a fallback to ``config.phases`` (canonical
    ``## PHASE-PLAN``), so BOTH the real-mission and v9.0 TASKS.md formats
    populate the kanban (previously only canonical tokens matched, leaving the
    real-format kanban empty).
    """
    path = mission_dir / "TASKS.md"
    if not path.exists():
        return []
    text = path.read_text()
    if ctx is not None:
        task_line_re = ctx.task_line_re
        mc = ctx.mission_config
    else:
        mc = load_mission_config(mission_dir)
        task_line_re = build_task_line_re(mc)

    section_re = _build_task_section_re(mc)
    if section_re is None:
        return []
    section_hdrs = list(section_re.finditer(text))

    # Build a short-code → long-name map from the mission config so task.lane
    # matches what the FE kanban buckets by (config.lanes[i].name, e.g. "AUDIT").
    short_to_name: dict[str, str] = {}
    if mc is not None:
        for lane_cfg in mc.lanes:
            if lane_cfg.short:
                short_to_name[lane_cfg.short] = lane_cfg.name

    # Catch-all `^## ` so an unrecognized ## header (not in task_sections/phases)
    # bounds a known section instead of letting it run to EOF and vacuum up
    # unrelated task lines.
    any_h2_re = re.compile(r"^##\s+", re.MULTILINE)

    phases: list[dict[str, Any]] = []
    for i, hdr in enumerate(section_hdrs):
        start = hdr.end()
        end = section_hdrs[i + 1].start() if i + 1 < len(section_hdrs) else len(text)
        next_h2 = any_h2_re.search(text, start + 1, end)
        if next_h2:
            end = next_h2.start()
        section = text[start:end]
        tasks: list[dict[str, Any]] = []
        for m in task_line_re.finditer(section):
            state, agent, utc = _parse_task_state_block(m.group("state_block"))
            short = m.group("lane")
            tasks.append(
                {
                    "id": m.group("task_id").strip(),
                    "lane": short_to_name.get(short, f"LANE-{short}"),
                    "state": state,
                    "agent": agent,
                    "utc": utc,
                    "description": (m.group("description") or "").strip(),
                }
            )
        phases.append({"name": hdr.group("title").strip(), "tasks": tasks})
    return phases


def parse_findings(
    mission_dir: Path, *, include_scratch: bool = False
) -> list[dict[str, Any]]:
    """Parse findings/ directory; return list of dicts with YAML metadata."""
    findings_dir = mission_dir / "findings"
    out = []
    if not findings_dir.is_dir():
        return out
    for p in sorted(findings_dir.iterdir()):
        if not p.is_file():
            continue
        if not p.name.endswith(".md"):
            continue
        is_scratch = ".scratch" in p.name
        if is_scratch and not include_scratch:
            continue
        meta = _parse_yaml_frontmatter(p.read_text())
        meta["filename"] = p.name
        meta["scratch"] = is_scratch
        # Normalize severity field name
        if "severity" not in meta and "Severity" in meta:
            meta["severity"] = meta["Severity"]
        out.append(meta)
    return out


# ---------------------------------------------------------------------------
# Mission diagnostics surfaced via /api/v1/state.mission
# ---------------------------------------------------------------------------


_LANE_CANONICAL_RE = re.compile(r"^LANE-[A-Z]$")
_PHASE_FLIP_LOCK_DIRNAME_RE = re.compile(
    r"^(?P<from>[A-Z][A-Z0-9-]*)-to-(?P<to>[A-Z][A-Z0-9-]*)$"
)


def _detect_stuck_flip_lock(mission_dir: Path) -> dict[str, Any] | None:
    """Surface the oldest phase-flip lock directory (if any) for the FE.

    Megalodon's distributed phase-flip protocol creates a lock dir under
    `.phase-flip-locks/<FROM>-to-<TO>/` while a worker performs the flip. The
    dir is removed when the flip lands. If the worker crashes mid-flip the
    dir lingers; the operator-console renders a warning panel from this hint
    so they can manually complete or roll back.

    Returns {"from_phase", "to_phase", "lock_age_seconds"} or None.
    Multi-lock case: return the oldest lock (most concerning).
    """
    locks_dir = mission_dir / ".phase-flip-locks"
    if not locks_dir.is_dir():
        return None
    candidates: list[tuple[float, str, str]] = []
    for p in locks_dir.iterdir():
        if not p.is_dir():
            continue
        m = _PHASE_FLIP_LOCK_DIRNAME_RE.match(p.name)
        if not m:
            continue
        try:
            age = max(0.0, time.time() - p.stat().st_mtime)
        except OSError:
            continue
        candidates.append((age, m.group("from"), m.group("to")))
    if not candidates:
        return None
    candidates.sort(reverse=True)  # oldest first
    age, from_phase, to_phase = candidates[0]
    return {
        "from_phase": from_phase,
        "to_phase": to_phase,
        "lock_age_seconds": round(age, 1),
    }


def _list_claim_dirs(mission_dir: Path) -> list[dict[str, Any]]:
    """List immediate subdirs of `claims/` for the FE non-canonical panel.

    Each entry: {dirname, has_done, mtime, owner}. `dirname` is the raw on-disk
    name (preserving Unicode like `P2-C→B`). The FE's tasks.js cross-references
    these against TASKS task ids and surfaces any dir without a matching id
    as a "non-canonical claim" — T-FX-FAILMODE-b asserts on this.

    `owner` is the stripped contents of an ``owner.txt`` inside the claim dir
    when present (forward-compat for owner-stamped claims), else ``None``.
    Today's claim dirs carry no owner.txt, so ``owner`` is expected to be null.
    """
    claims_dir = mission_dir / "claims"
    if not claims_dir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for p in claims_dir.iterdir():
        if not p.is_dir():
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0.0
        owner: str | None = None
        owner_path = p / "owner.txt"
        try:
            if owner_path.is_file():
                owner = owner_path.read_text(errors="replace").strip() or None
        except OSError:
            owner = None
        out.append(
            {
                "dirname": p.name,
                "has_done": (p / "done").is_file(),
                "mtime": mtime,
                "owner": owner,
            }
        )
    return out


def _parse_history_entries(mission_dir: Path) -> list[dict[str, Any]]:
    """Parse HISTORY.md into structured entries with a per-row `drift` flag.

    HISTORY format (per RULE 10 step 3):
      `<utc> | <agent> | <lane> | <task_id> | <finding> | <severity>`

    An entry is `drift: True` when EITHER:
      - the lane field doesn't match the canonical `LANE-[A-Z]` form
        (a worker wrote `F` or `FRONTEND` instead of `LANE-F`/`LANE-D`), OR
      - the task_id starts with `DRIFT-` (an explicit drift marker injected
        by `_gen.py` for the fix-medium-failure-modes fixture).

    Used by FE `renderHistoryTail` (mission.js) to flag drifted rows with a
    warning glyph + `data-drift="true"` attribute. T-FX-FAILMODE-c asserts
    exactly 3 drift rows in fix-medium-failure-modes.
    """
    history_path = mission_dir / "HISTORY.md"
    if not history_path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for raw in history_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [c.strip() for c in line.split("|")]
        if len(parts) < 4:
            continue
        utc = parts[0]
        agent = parts[1]
        lane = parts[2]
        task_id = parts[3]
        finding = parts[4] if len(parts) > 4 else ""
        severity = parts[5] if len(parts) > 5 else ""
        drift = not _LANE_CANONICAL_RE.match(lane) or task_id.startswith("DRIFT-")
        out.append(
            {
                "utc": utc,
                "agent": agent,
                "lane": lane,
                "task_id": task_id,
                "finding": finding,
                "severity": severity,
                "drift": drift,
            }
        )
    return out


# ---------------------------------------------------------------------------
# v9.3 dashboard payload helpers — /api/v1/state shape consumed by
# ui/static/pages/tasks.js + signals.js + mission.js.
# ---------------------------------------------------------------------------


# Canonical phase id mapping for TASKS.md section headers like
# "## PHASE 1 — PLAN". Maps the trailing word(s) (PLAN / BUILD / VERIFY /
# CHALLENGE / RUN / HEAL) onto the canonical "PHASE-<WORD>" id the FE keys
# off in store.tasks.phases. Sections that don't match (e.g.
# "CROSS-LANE / SECONDARY TASK POOL", "OPERATOR-ACCEPTANCE TASKS",
# "OPERATOR-INJECTED (live)") fall through to tasks.cross — see
# tasks.js:475 which reads (store.get("tasks.cross") || []).
_PHASE_SECTION_TAIL_RE = re.compile(
    r"PHASE\s+\d+(?:\.\d+)?\s*[—\-]\s*(?P<name>[A-Z][A-Z0-9_-]*)",
    re.IGNORECASE,
)


def _section_title_to_phase_id(title: str) -> str | None:
    """Map a TASKS.md section title to a canonical PHASE-* id, or None for cross.

    Examples:
      "PHASE 1 — PLAN"      -> "PHASE-PLAN"
      "PHASE 2 — BUILD"     -> "PHASE-BUILD"
      "PHASE 2 — CHALLENGE" -> "PHASE-CHALLENGE"
      "PHASE 3 — VERIFY"    -> "PHASE-VERIFY"
      "PHASE 3 — BUILD"     -> "PHASE-BUILD"  (v9.0 5-phase shape)
      "PHASE 5 — RUN"       -> "PHASE-RUN"
      "PHASE-PLAN"          -> "PHASE-PLAN"   (already canonical)
      "OPERATOR-ACCEPTANCE TASKS"       -> None (-> cross)
      "CROSS-LANE / SECONDARY TASK POOL"-> None (-> cross)
      "OPERATOR-INJECTED (live)"        -> None (-> cross)
    """
    t = title.strip()
    if not t:
        return None
    # Already canonical form: "PHASE-FOO".
    if t.startswith("PHASE-") and " " not in t and "/" not in t:
        return t
    m = _PHASE_SECTION_TAIL_RE.search(t)
    if m:
        return "PHASE-" + m.group("name").upper()
    return None


def parse_tasks_fe_shape(
    mission_dir: Path,
    ctx: "MissionContext | None" = None,
) -> dict[str, Any]:
    """Parse TASKS.md into the FE-facing shape `{phases: {...}, cross: [...]}`.

    Differs from `parse_tasks` (which feeds `/api/v1/tasks` as an ordered list
    of `{name, tasks}`) in two ways:

      1. Uses `ctx.mission_config.task_sections` as the section header source —
         so it picks up human-readable headers like ``## PHASE 1 — PLAN`` that
         the canonical-phase regex (``PHASE-PLAN``) misses.
      2. Returns per-task fields named per the v9.3 FE contract
         (tasks.js:21,156,440-477): ``task_id``, ``claim_state``,
         ``claim_agent``, ``claim_utc`` — *plus* legacy aliases
         (``id``, ``state``, ``agent``, ``utc``) so old consumers keep working.

    Sections whose title doesn't map to a ``PHASE-*`` id (e.g. cross-lane pool,
    operator-acceptance, operator-injected) drop into ``cross``.
    """
    path = mission_dir / "TASKS.md"
    if not path.is_file():
        return {"phases": {}, "cross": []}
    text = path.read_text()

    if ctx is not None:
        task_line_re = ctx.task_line_re
        mc = ctx.mission_config
    else:
        mc = load_mission_config(mission_dir)
        task_line_re = build_task_line_re(mc)

    # Short-code -> long lane name (e.g. "A" -> "AUDIT") so the FE kanban
    # buckets by config-declared name. Matches parse_tasks() behavior.
    short_to_name: dict[str, str] = {}
    if mc is not None:
        for lane_cfg in mc.lanes:
            if lane_cfg.short:
                short_to_name[lane_cfg.short] = lane_cfg.name

    # Section-header regex from config.task_sections with a canonical PHASE-*
    # fallback (shared with parse_tasks so both endpoints match identically).
    section_re = _build_task_section_re(mc)
    if section_re is None:
        return {"phases": {}, "cross": []}
    section_hdrs = list(section_re.finditer(text))

    phases: dict[str, list[dict[str, Any]]] = {}
    cross: list[dict[str, Any]] = []

    # Catch-all `^## ` to bound a known section when an unknown ## header
    # (e.g. ``## OPERATOR-INJECTED (live)`` not present in task_sections)
    # would otherwise let our last known section run to EOF and accidentally
    # vacuum up unrelated task lines.
    _any_h2_re = re.compile(r"^##\s+", re.MULTILINE)

    for i, hdr in enumerate(section_hdrs):
        start = hdr.end()
        if i + 1 < len(section_hdrs):
            end = section_hdrs[i + 1].start()
        else:
            end = len(text)
        # Trim to the next ## header (if any) that we don't recognize, so
        # tasks from unconfigured sections aren't merged into ours.
        next_h2 = _any_h2_re.search(text, start + 1, end)
        if next_h2:
            end = next_h2.start()
        section = text[start:end]
        title = hdr.group("title").strip()
        phase_id = _section_title_to_phase_id(title)

        for m in task_line_re.finditer(section):
            claim_state, agent, utc = _parse_task_state_block(m.group("state_block"))
            short = m.group("lane")
            task_id = m.group("task_id").strip()
            description = (m.group("description") or "").strip()
            lane_name = short_to_name.get(short, f"LANE-{short}")
            rec: dict[str, Any] = {
                # FE v9.3 contract (tasks.js:21,440-477)
                "task_id": task_id,
                "lane": lane_name,
                "description": description,
                "claim_state": claim_state,
                "phase": phase_id,
                # Legacy aliases — parse_tasks() shape, retained so any older
                # consumer still works.
                "id": task_id,
                "state": claim_state,
            }
            if agent is not None:
                rec["claim_agent"] = agent
                rec["agent"] = agent
            if utc is not None:
                rec["claim_utc"] = utc
                rec["utc"] = utc

            if phase_id:
                phases.setdefault(phase_id, []).append(rec)
            else:
                cross.append(rec)

    return {"phases": phases, "cross": cross}


# Signals — `<mission>/signals/LANE-<FROM>-to-LANE-<TO>-<topic>-<UTC>.md`.
# The canonical + legacy filename grammar (FROZEN WIRE CONTRACT §A) lives in the
# leaf module ``signal_grammar`` so server.py and activity_wall.py share ONE
# copy (no drift, no import cycle). ``_SIGNAL_FILENAME_RE`` /
# ``_SIGNAL_FILENAME_LEGACY_RE`` are imported above; ``parse_signal_filename``
# is the convenience helper.

# `[SIG from=X to=Y text="..." cite=...]` token embedded in STATUS.md notes.
_SIG_TOKEN_RE = re.compile(
    r'\[SIG\s+from=(\S+)\s+to=(\S+)\s+text="([^"]*)"\s*(?:cite=(\S+))?\s*\]'
)


def _defang_sig_text(text: str) -> str:
    """Neutralize chars that could forge a second `[SIG ...]` token.

    The signal endpoints interpolate request-supplied ``text``/``claim`` into a
    ``[SIG from=X to=Y text="..." cite=...]`` token written into STATUS.md
    notes, which ``_parse_status_note_signals`` later reads back. Without
    defanging, a payload containing ``"`` plus ``]``/``[`` could close the
    token early and inject a SECOND token with an attacker-chosen ``from=``
    sender label (stored injection). We therefore:

      * replace ``"`` → ``'`` (can't close the quoted text field),
      * drop ``[`` and ``]`` (can't open/close a token),
      * collapse CR/LF and runs of whitespace to single spaces (can't break the
        STATUS.md table row).

    Non-breaking by design — ordinary quoted prose survives (as single-quoted)
    so ``validate_signal`` and existing callers are unaffected.
    """
    cleaned = str(text).replace('"', "'").replace("[", "").replace("]", "")
    return re.sub(r"\s+", " ", cleaned).strip()


def _slugify(text: str, default: str = "note") -> str:
    """Return a lowercase ``[a-z0-9-]+`` slug, or *default* if empty.

    Collapses runs of non-alphanumerics to single dashes and trims leading /
    trailing dashes. Always returns a non-empty slug.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return slug or default


def _normalize_lane_label(raw: str, default: str) -> str:
    """Normalize a lane label to the canonical ``LANE-<X>`` form.

    Used to keep the ``source:"finding"`` and ``source:"status-note"`` channels
    visually consistent with ``source:"file"`` signals (whose grammar always
    produces ``LANE-<X>`` from/to). Rules:

      * empty / whitespace → ``default`` (e.g. ``LANE-UNKNOWN`` / ``LANE-ALL``),
      * already ``LANE-`` prefixed → kept verbatim (uppercased),
      * ``ORCHESTRATOR`` → ``ORCH`` (the canonical orchestrator short),
      * a bare lane short matching ``[A-Z0-9]+`` → prefixed with ``LANE-``.

    Tolerant by design: any value that would NOT yield a canonical
    ``LANE-[A-Z0-9]+`` token (e.g. ``all-lanes``, multi-word labels) is returned
    uppercased-as-is so unrecognizable frontmatter is never mangled.
    """
    short = (raw or "").strip().upper()
    if not short:
        return default
    if short.startswith("LANE-"):
        return short
    if short == "ORCHESTRATOR":
        short = "ORCH"
    if re.fullmatch(r"[A-Z0-9]+", short):
        return f"LANE-{short}"
    return short


def _mtime_iso(path: Path) -> str:
    """Best-effort ISO-8601 UTC mtime of *path*; "" on failure."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return ""
    return (
        datetime.fromtimestamp(mtime, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _signal_sort_key(rec: dict[str, Any]) -> str:
    """Sort key for newest-first ordering: best-available timestamp string.

    Prefers ``utc`` when present; otherwise falls back to the mtime ISO stamp
    stashed under ``_mtime`` by ``parse_signals``. Both are ISO-8601-ish and
    sort lexicographically in chronological order. Empty sorts oldest.
    """
    return rec.get("utc") or rec.get("_mtime") or ""


def _parse_file_signals(signals_dir: Path) -> list[dict[str, Any]]:
    """Parse ``signals/*.md`` files into signal dicts (``source:"file"``)."""
    out: list[dict[str, Any]] = []
    try:
        entries = sorted(signals_dir.iterdir())
    except OSError:
        return out
    for p in entries:
        try:
            if not p.is_file() or not p.name.endswith(".md"):
                continue
        except OSError:
            continue
        parsed = parse_signal_filename(p.name)
        if parsed is None:
            # Not a signal file (README / stray) — skip silently.
            continue
        from_lane = parsed["from_lane"]
        to_lane = parsed["to_lane"]
        topic = parsed["topic"]
        utc = parsed["utc"]
        rec: dict[str, Any] = {
            "filename": p.name,
            "from_lane": from_lane,
            "to_lane": to_lane,
            "to": to_lane,  # signals.js reads sig.to
            "topic": topic,
            "utc": utc,
            "kind": "SIGNAL",
            "source": "file",
            "_mtime": _mtime_iso(p),
        }
        try:
            rec["body"] = p.read_text(errors="replace")[:4096]
        except OSError:
            rec["body"] = ""
        out.append(rec)
    return out


# A STATUS.md table row: ``| <lane> | <agent> | <state> | ... | <notes> |``.
# We need only (a) the OWNING lane (first cell) and (b) the full row text so we
# can find which `[SIG ...]` tokens physically sit in that row. The lane cell is
# the v9.0 ``[A-Z][A-Z\- ]*?`` shape (long or short lane label, e.g. ``LANE-C``
# or ``CORE-AUDIT``). Line-anchored, so each match is exactly one table row.
_STATUS_ROW_LANE_RE = re.compile(
    r"^\|\s*(?P<lane>[A-Z][A-Z0-9\- ]*?)\s*\|(?P<rest>.*)\|\s*$",
    re.MULTILINE,
)

#: Sender labels that are SERVER-GENERATED (orchestrator origin). The POST
#: signal endpoints write ``[SIG from=orchestrator ...]`` into the TARGET lane's
#: row, so an orchestrator-origin token legitimately disagrees with the owning
#: row's lane. These are trusted (server-written) and keep their claimed sender.
_ORCH_SENDER_LABELS = frozenset({"ORCHESTRATOR", "ORCH", "LANE-ORCH"})


def _parse_status_note_signals(mission_dir: Path) -> list[dict[str, Any]]:
    """Parse `[SIG ...]` tokens out of STATUS.md notes (``source:"status-note"``).

    SECURITY (anti-spoof): the ``from=`` field inside a ``[SIG ...]`` token is
    attacker-controllable — an agent editing its OWN STATUS row can write
    ``[SIG from=LANE-A ...]`` and forge a message (e.g. a fake approval) that
    renders as if it came from LANE-A. We therefore bind the sender to the lane
    whose STATUS row the token physically sits in:

      * The authoritative ``from_lane`` is the OWNING ROW'S lane.
      * EXCEPTION: an orchestrator-origin token (``from=orchestrator``/``ORCH``)
        is server-written (the POST signal endpoints route it into the *target*
        lane's row), so it is trusted and keeps ``LANE-ORCH``.
      * If a non-orchestrator claimed ``from=`` DISAGREES with the owning lane,
        we override ``from_lane`` to the true owning lane, stash the forged value
        in ``claimed_from``, and set ``from_unverified: true``.
      * A token that is NOT inside any recognizable STATUS row (loose text) can't
        be bound, so it is flagged ``from_unverified: true`` with the claimed
        sender preserved (best-effort, never trusted).
    """
    status_path = mission_dir / "STATUS.md"
    out: list[dict[str, Any]] = []
    try:
        if not status_path.is_file():
            return out
        text = status_path.read_text(errors="replace")
    except OSError:
        return out
    mtime = _mtime_iso(status_path)

    # Map each `[SIG ...]` token's character offset → the owning row's lane, by
    # scanning table rows and recording the span each row's text covers.
    owning_lane_by_span: list[tuple[int, int, str]] = []  # (start, end, lane)
    for rm in _STATUS_ROW_LANE_RE.finditer(text):
        lane_label = (rm.group("lane") or "").strip()
        if lane_label.lower() == "lane":  # header row
            continue
        owning_lane_by_span.append((rm.start(), rm.end(), lane_label))

    def _owning_lane_for(pos: int) -> str | None:
        for start, end, lane in owning_lane_by_span:
            if start <= pos < end:
                return lane
        return None

    for idx, m in enumerate(_SIG_TOKEN_RE.finditer(text)):
        from_raw = (m.group(1) or "").strip()
        to_raw = (m.group(2) or "").strip()
        sig_text = (m.group(3) or "").strip()
        cite = (m.group(4) or "").strip()

        claimed_from = _normalize_lane_label(from_raw, "LANE-UNKNOWN")
        to_lane = _normalize_lane_label(to_raw, "LANE-ALL")

        owning_label = _owning_lane_for(m.start())
        owning_lane = (
            _normalize_lane_label(owning_label, "LANE-UNKNOWN")
            if owning_label is not None
            else None
        )

        from_unverified = False
        if from_raw.upper() in _ORCH_SENDER_LABELS:
            # Server-written orchestrator token (POST endpoints write from=ORCH
            # into the target's row). Trusted — keep the orchestrator sender.
            from_lane = "LANE-ORCH"
        elif owning_lane is None:
            # Loose token not inside a recognizable row — cannot bind; flag it.
            from_lane = claimed_from
            from_unverified = True
        else:
            # Authoritative: the sender IS the owning row's lane. If the claimed
            # value disagrees, the token is forged — override + flag.
            from_lane = owning_lane
            if claimed_from != owning_lane:
                from_unverified = True

        topic = _slugify(" ".join(sig_text.split()[:5]))
        body = sig_text + (f" (cite: {cite})" if cite else "")
        rec: dict[str, Any] = {
            "filename": f"status-note-{idx}",
            "from_lane": from_lane,
            "claimed_from": claimed_from,
            "from_unverified": from_unverified,
            "to_lane": to_lane,
            "to": to_lane,
            "topic": topic,
            "utc": "",
            "kind": "SIGNAL",
            "source": "status-note",
            "body": body[:4096],
            "_mtime": mtime,
        }
        out.append(rec)
    return out


def _parse_finding_signals(mission_dir: Path) -> list[dict[str, Any]]:
    """Scan ``findings/*.md`` for SIGNAL-class findings (``source:"finding"``)."""
    findings_dir = mission_dir / "findings"
    out: list[dict[str, Any]] = []
    try:
        if not findings_dir.is_dir():
            return out
        entries = sorted(findings_dir.iterdir())
    except OSError:
        return out
    for p in entries:
        try:
            if not p.is_file() or not p.name.endswith(".md"):
                continue
        except OSError:
            continue
        try:
            fm = signal_parser.parse_signal(p)
        except Exception:
            fm = None
        if not fm:
            continue
        # from/to from frontmatter if present, else file lane / "ALL".
        # Normalize to the canonical LANE-<X> form so finding-source signals
        # render consistently with file/status-note signals (NIT, Wave 2
        # review). Tolerant: unrecognizable labels (e.g. "all-lanes") pass
        # through uppercased-as-is.
        from_raw = str(
            fm.get("from-lane")
            or fm.get("from_lane")
            or fm.get("agent")
            or fm.get("lane")
            or ""
        ).strip()
        to_raw = str(
            fm.get("to-lane") or fm.get("to_lane") or fm.get("addressed-to") or ""
        ).strip()
        from_lane = _normalize_lane_label(from_raw, "LANE-UNKNOWN")
        to_lane = _normalize_lane_label(to_raw, "LANE-ALL")
        topic = _slugify(str(fm.get("signal-type", "note")))
        body = ""
        try:
            raw = p.read_text(errors="replace")
            # Body is everything after the closing frontmatter `---`.
            if raw.startswith("---"):
                end = raw.find("\n---", 3)
                if end >= 0:
                    raw = raw[end + 4 :].lstrip("\n")
            body = raw[:4096]
        except OSError:
            pass
        out.append(
            {
                "filename": p.name,
                "from_lane": from_lane,
                "to_lane": to_lane,
                "to": to_lane,
                "topic": topic,
                "utc": str(fm.get("utc", "")).strip(),
                "kind": "SIGNAL",
                "source": "finding",
                "body": body,
                "_mtime": _mtime_iso(p),
            }
        )
    return out


def parse_signals(mission_dir: Path) -> list[dict[str, Any]]:
    """Return signal dicts unified from all THREE comms channels, newest-first.

    The three channels (the "schism" this fixes):
      * ``source:"file"`` — ``signals/*.md`` files (canonical grammar §A, with a
        legacy fallback where topic=remainder and utc="").
      * ``source:"status-note"`` — ``[SIG from=X to=Y text="..." cite=...]``
        tokens embedded in STATUS.md notes cells.
      * ``source:"finding"`` — SIGNAL-class findings (``signal-type`` in YAML
        frontmatter), via ``signal_parser.parse_signal``.

    Each dict has: ``filename``, ``from_lane``, ``to_lane``, ``to`` (alias),
    ``topic``, ``utc``, ``kind`` (always "SIGNAL"), ``body`` (≤4 KB), and
    ``source``. Ordered newest-first by best-available timestamp (``utc`` if
    present, else file mtime).

    Tolerant by design: a missing dir, malformed file, or unreadable token
    never raises — the offending item is skipped so the timeline still renders.
    """
    signals_dir = mission_dir / "signals"
    combined: list[dict[str, Any]] = []
    if signals_dir.is_dir():
        combined.extend(_parse_file_signals(signals_dir))
    combined.extend(_parse_status_note_signals(mission_dir))
    combined.extend(_parse_finding_signals(mission_dir))
    # Newest-first by best-available timestamp.
    combined.sort(key=_signal_sort_key, reverse=True)
    # Drop the internal sort-helper key before returning to callers/FE.
    for rec in combined:
        rec.pop("_mtime", None)
    return combined


def _write_signal_file(
    mission_dir: Path,
    from_lane: str,
    to_lane: str,
    topic: str,
    body_text: str,
) -> Path:
    """Write a canonical ``signals/*.md`` file and return its path.

    Composes ``signals/LANE-<FROM>-to-LANE-<TO>-<topic>-<UTC>.md`` per the
    canonical grammar (§A), creating ``signals/`` if needed. This is the key
    fix: the channel the UI actually reads now receives writes, and the
    activity-wall ``signals/`` watcher emits a live event for free.

    ``from_lane`` / ``to_lane`` may be passed with or without the ``LANE-``
    prefix and are normalized to ``LANE-<SHORT>`` (uppercased short). ``topic``
    is slugified; empty topic defaults to ``"note"``.
    """

    def _norm(lane: str) -> str:
        short = str(lane).strip().upper()
        if short.startswith("LANE-"):
            short = short[len("LANE-") :]
        short = re.sub(r"[^A-Z0-9]", "", short) or "UNKNOWN"
        return f"LANE-{short}"

    from_norm = _norm(from_lane)
    to_norm = _norm(to_lane)
    topic_slug = _slugify(topic)
    utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%MZ")
    filename = f"{from_norm}-to-{to_norm}-{topic_slug}-{utc}.md"
    signals_dir = mission_dir / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)
    path = signals_dir / filename
    path.write_text(body_text or "", encoding="utf-8")
    return path


def _read_mission_md_fields(mission_dir: Path) -> dict[str, Any]:
    """Extract `id` and `status` from MISSION.md frontmatter-ish lines.

    Looks for ``**Mission ID:** `<id>``` and ``**Status:** <STATUS>`` near the
    top of the file (whole-file scan; live MISSION.md uses these literal
    labels per v9.3-dogfood mission). Returns {} if missing.

    Tolerant: missing MISSION.md returns {}; partial matches return only the
    found keys (e.g. status without id).
    """
    path = mission_dir / "MISSION.md"
    if not path.is_file():
        return {}
    try:
        text = path.read_text()
    except OSError:
        return {}
    out: dict[str, Any] = {}
    id_match = re.search(
        r"\*\*Mission ID:\*\*\s*`?(?P<id>[^`\n]+?)`?\s*$",
        text,
        re.MULTILINE,
    )
    if id_match:
        out["id"] = id_match.group("id").strip()
    status_match = re.search(r"\*\*Status:\*\*\s+(?P<status>\S+)", text)
    if status_match:
        out["status"] = status_match.group("status").strip()
    return out


def _read_mission_events_tail(
    mission_dir: Path, limit: int = 50
) -> list[dict[str, Any]]:
    """Return last `limit` entries from `<mission>/.mission-events`, newest-first.

    Format-tolerant: each line is parsed as JSON when possible (per the v9.3
    spec); free-form text lines fall back to ``{"raw": "<line>"}`` so the
    live v9.2/v9.3 file (which today writes text like
    ``2026-05-19T21:24Z INIT->PHASE-PLAN by orchestrator -- ...``) still
    surfaces in the dashboard. Missing file -> [].
    """
    events_path = mission_dir / ".mission-events"
    if not events_path.is_file():
        return []
    try:
        raw = events_path.read_text(errors="replace")
    except OSError:
        return []
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    tail = lines[-limit:]
    out: list[dict[str, Any]] = []
    for ln in tail:
        try:
            obj = json.loads(ln)
            if isinstance(obj, dict):
                out.append(obj)
                continue
        except (ValueError, TypeError):
            pass
        out.append({"raw": ln})
    out.reverse()  # newest-first
    return out


# ---------------------------------------------------------------------------
# Lifespan helpers
# ---------------------------------------------------------------------------


async def _df_watchdog(mission_dir: Path) -> None:
    """Background task: exit 12 if disk free < 50 MB at mission_dir.

    Runs every 60 seconds. Designed to be run as an asyncio task inside the
    lifespan context manager; cancelled on server shutdown.
    """
    while True:
        await asyncio.sleep(60)
        stat = shutil.disk_usage(mission_dir)
        if stat.free < 50 * 1024 * 1024:  # 50 MB
            print(
                f"disk free < 50MB at {mission_dir}: {stat.free} bytes",
                file=sys.stderr,
            )
            sys.exit(12)


#: Lane-health watchdog poll interval (seconds). Module-level so tests can
#: monkey-patch it to a small value without waiting a full minute.
_LANE_WATCHDOG_INTERVAL_SECONDS: float = 60.0


async def _lane_health_watchdog(mission_dir: Path) -> None:
    """Background task: run the watchdog S1–S4 detectors on an interval (Task C).

    The standalone ``watchdog.daemon.run()`` is a SYNC signal-driven loop that
    installs SIGTERM/SIGINT handlers, so it cannot be called from the server
    lifespan. Instead we drive the extracted ``check_lanes_once`` off-loop via
    ``asyncio.to_thread`` every ``_LANE_WATCHDOG_INTERVAL_SECONDS`` — mirroring
    the ``_df_watchdog`` pattern. Each pass writes findings + the structured
    JSONL alert log (see ``AlertManager``) so a dead/stale/hung lane is visible
    via ``GET /api/v1/alerts`` within one interval rather than ~15 min.

    Robust by design: an exception in one pass is logged and the loop continues
    (a transient FS error must not take the health watchdog permanently dark).
    Cancelled on server shutdown.
    """
    from .watchdog.alerts import AlertManager
    from .watchdog.daemon import check_lanes_once

    alerts = AlertManager(mission_dir)
    while True:
        await asyncio.sleep(_LANE_WATCHDOG_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(check_lanes_once, mission_dir, alerts)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            print(f"lane-health watchdog pass error: {e!r}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Auto-recovery supervisor (Task F / contract §4) — DEFAULT OFF.
# ---------------------------------------------------------------------------

#: Env flag that ARMS the supervisor. Unset / anything-but-"1" ⇒ disabled.
#: Auto-recovery silently restarts agents (an OUTWARD action), so it is fail-
#: closed: the operator must opt in explicitly.
AUTORECOVER_ENV = "MEGALODON_AUTORECOVER"

#: A lane must be continuously unhealthy for at least this many seconds before
#: the FIRST restart fires (debounce — a momentary blip must not trigger a
#: restart). Module-level so tests can shrink it.
_AUTORECOVER_GRACE_SECONDS: float = 60.0

#: Trailing consecutive governor denies that mark a lane unhealthy (alongside
#: liveness == "dead"). Distinct from the window deny-count threshold.
_AUTORECOVER_DENY_THRESHOLD: int = 5

#: Hard cap on restart attempts per lane per process. Once hit, the supervisor
#: stops acting on that lane (no infinite restart storm) until the lane recovers.
_AUTORECOVER_MAX_ATTEMPTS: int = 5

#: Backoff base + cap (seconds): the n-th restart for a lane is gated by
#: ``min(base * 2**(n-1), cap)`` since the previous attempt — never faster.
_AUTORECOVER_BACKOFF_BASE_SECONDS: float = 30.0
_AUTORECOVER_BACKOFF_CAP_SECONDS: float = 600.0

#: Supervisor poll interval. Module-level so tests can drive it fast.
_AUTORECOVER_INTERVAL_SECONDS: float = 30.0


def autorecover_enabled() -> bool:
    """Return True iff the operator armed auto-recovery via the env flag.

    Fail-closed: only the exact string ``"1"`` arms it.
    """
    return os.environ.get(AUTORECOVER_ENV) == "1"


async def _perform_restart_loop(mission_dir: Path, spawner: Any, short: str) -> bool:
    """Execute the restart-loop action for ``short`` (shared by route + supervisor).

    Re-issues the lane's recorded ``initial_prompt`` via tmux send-keys (skipped
    for the fake spawner) and appends a ``source="restart-loop"`` line to today's
    ``.fleet/inject-log-YYYY-MM-DD.jsonl`` — which the activity wall tails into a
    ``restart-loop`` event. This is the SAME path the operator's manual
    ``POST /api/v1/lane/{short}/restart-loop`` uses, so auto-recovery and manual
    recovery are observationally identical on the wall.

    Args:
        mission_dir: Mission root.
        spawner: The live (or fake) spawner; must have ``sessions[short]``.
        short: Lane short code.

    Returns:
        True if the restart action completed; False on a missing lane / absent
        initial_prompt / tmux send-keys failure (the caller decides how to log).
    """
    session = spawner.sessions.get(short) if spawner is not None else None
    if session is None or not getattr(session, "initial_prompt", None):
        return False

    text = session.initial_prompt
    text_bytes = text.encode("utf-8")

    # Fake-spawner short-circuit (no real tmux socket); the fake_emit attribute
    # marks the fake spawner.
    if not hasattr(spawner, "fake_emit"):
        rc = await tmux.send_keys(spawner.socket, session.name, text, enter=True)
        if rc != 0:
            return False

    fleet_dir = mission_dir / ".fleet"
    fleet_dir.mkdir(parents=True, exist_ok=True)
    ts_now = datetime.now(timezone.utc)
    log_path = fleet_dir / f"inject-log-{ts_now.strftime('%Y-%m-%d')}.jsonl"
    entry = {
        "ts": ts_now.isoformat(),
        "lane": short,
        "text_sha256": hashlib.sha256(text_bytes).hexdigest(),
        "byte_count": len(text_bytes),
        "enter": True,
        "source": "restart-loop",
    }
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    return True


@dataclass
class _LaneRecoverState:
    """Per-lane bookkeeping for the supervisor (in-memory, process-lived)."""

    unhealthy_since: float | None = None  # monotonic ts of first unhealthy sighting
    attempts: int = 0  # restarts fired this process
    next_allowed_mono: float = 0.0  # earliest monotonic ts the next restart may fire


class AutoRecoverSupervisor:
    """Bounded, logged, idempotent auto-restart of dead / deny-looping lanes.

    SAFETY (contract §4): default OFF (the lifespan only constructs+runs this
    when ``autorecover_enabled()``). A lane is *unhealthy* when its liveness is
    strictly ``"dead"`` OR its trailing ``consecutive_denies`` reaches the
    threshold. A restart fires only after the lane has been continuously
    unhealthy for ``grace_seconds`` AND the per-lane backoff has elapsed AND the
    per-lane attempt cap is not yet hit. A healthy lane resets its state, so the
    supervisor is idempotent and never touches a healthy lane.

    All restart actions are logged to ``.fleet/autorecover.log`` and (because the
    injected ``restart_fn`` is the restart-loop path, which appends to the
    inject-log JSONL with ``source="restart-loop"``) also surface on the activity
    wall for free.

    The ``restart_fn`` / ``get_liveness`` / ``compute_consecutive_denies`` seams
    are injected so tests can drive the supervisor without a real tmux fleet.
    """

    def __init__(
        self,
        mission_dir: Path,
        *,
        get_liveness: "Callable[[], dict[str, str]]",
        get_consecutive_denies: "Callable[[], dict[str, int]]",
        restart_fn: "Callable[[str], Any]",
        grace_seconds: float | None = None,
        deny_threshold: int | None = None,
        max_attempts: int | None = None,
        backoff_base_seconds: float | None = None,
        backoff_cap_seconds: float | None = None,
        clock: "Callable[[], float] | None" = None,
    ) -> None:
        self.mission_dir = mission_dir
        self._get_liveness = get_liveness
        self._get_consecutive_denies = get_consecutive_denies
        self._restart_fn = restart_fn
        self.grace_seconds = (
            grace_seconds if grace_seconds is not None else _AUTORECOVER_GRACE_SECONDS
        )
        self.deny_threshold = (
            deny_threshold
            if deny_threshold is not None
            else _AUTORECOVER_DENY_THRESHOLD
        )
        self.max_attempts = (
            max_attempts if max_attempts is not None else _AUTORECOVER_MAX_ATTEMPTS
        )
        self.backoff_base_seconds = (
            backoff_base_seconds
            if backoff_base_seconds is not None
            else _AUTORECOVER_BACKOFF_BASE_SECONDS
        )
        self.backoff_cap_seconds = (
            backoff_cap_seconds
            if backoff_cap_seconds is not None
            else _AUTORECOVER_BACKOFF_CAP_SECONDS
        )
        self._clock = clock or time.monotonic
        self._state: dict[str, _LaneRecoverState] = {}
        self.log_path = mission_dir / ".fleet" / "autorecover.log"

    def _log(self, message: str) -> None:
        """Append a UTC-stamped line to ``.fleet/autorecover.log`` (best-effort)."""
        line = f"{datetime.now(timezone.utc).isoformat()} | {message}\n"
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            pass

    def _is_unhealthy(
        self, lane: str, liveness: dict[str, str], denies: dict[str, int]
    ) -> bool:
        """A lane is unhealthy iff strictly dead OR at/over the deny threshold."""
        if liveness.get(lane) == "dead":
            return True
        return denies.get(lane, 0) >= self.deny_threshold

    async def tick(self) -> list[str]:
        """Evaluate all lanes once; restart those eligible. Returns lanes restarted.

        Idempotent and bounded: a healthy lane's state is cleared; an unhealthy
        lane is restarted at most once per tick, only when grace + backoff +
        attempt-cap all permit.
        """
        liveness = self._get_liveness()
        denies = self._get_consecutive_denies()
        now = self._clock()
        # Union of lanes seen in either source so a lane that vanished from one
        # map still gets its state evaluated/cleared.
        lanes = set(liveness) | set(denies) | set(self._state)
        restarted: list[str] = []

        for lane in sorted(lanes):
            st = self._state.setdefault(lane, _LaneRecoverState())
            if not self._is_unhealthy(lane, liveness, denies):
                # Healthy ⇒ reset everything. Never act on a healthy lane.
                if st.unhealthy_since is not None or st.attempts:
                    self._log(f"lane={lane} recovered — clearing recovery state")
                self._state[lane] = _LaneRecoverState()
                continue

            # Unhealthy. Start (or keep) the continuous-unhealthy clock.
            if st.unhealthy_since is None:
                st.unhealthy_since = now
                continue  # debounce: never restart on the very first sighting

            if (now - st.unhealthy_since) < self.grace_seconds:
                continue  # not unhealthy long enough yet
            if st.attempts >= self.max_attempts:
                continue  # hard cap reached — stop acting on this lane
            if now < st.next_allowed_mono:
                continue  # backoff not elapsed — never restart faster than backoff

            # Fire the bounded restart.
            reason = (
                "dead"
                if liveness.get(lane) == "dead"
                else f"deny-loop({denies.get(lane, 0)})"
            )
            try:
                result = self._restart_fn(lane)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:  # noqa: BLE001
                self._log(f"lane={lane} restart FAILED ({reason}): {e!r}")
                # Still advance backoff/attempt so a persistently-failing restart
                # cannot busy-loop.
            st.attempts += 1
            backoff = min(
                self.backoff_base_seconds * (2 ** (st.attempts - 1)),
                self.backoff_cap_seconds,
            )
            st.next_allowed_mono = now + backoff
            self._log(
                f"lane={lane} restart #{st.attempts}/{self.max_attempts} "
                f"reason={reason} next_backoff={backoff:.0f}s"
            )
            restarted.append(lane)

        return restarted

    async def run(self, interval_seconds: float | None = None) -> None:
        """Run the supervisor loop until cancelled (lifespan task).

        Robust by design: an exception in one tick is logged and the loop
        continues — auto-recovery going dark must not crash the server.
        """
        interval = (
            interval_seconds
            if interval_seconds is not None
            else _AUTORECOVER_INTERVAL_SECONDS
        )
        self._log("auto-recovery supervisor ARMED")
        while True:
            await asyncio.sleep(interval)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self._log(f"tick error: {e!r}")


# ---------------------------------------------------------------------------
# V9 M2 — contract validation
# ---------------------------------------------------------------------------


def _validate_contract(app: FastAPI, contract_path: Path) -> None:
    """V9 M2 — assert declared routes match registered routes.

    Raises RuntimeError if a contract-declared route isn't registered.
    Warns (non-fatal) if a registered route isn't declared. The introspect
    endpoint is excluded from both sides.
    """
    import warnings

    from .contract_loader import load_contract

    if not contract_path.exists():
        warnings.warn(
            f"api-contract.md not found at {contract_path} — skipping validation"
        )
        return

    contract = load_contract(contract_path)
    registered: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if path and methods and path.startswith("/api/v1/"):
            for method in methods:
                # HEAD is auto-added for GET; ignore.
                if method == "HEAD":
                    continue
                registered.add((method, path))

    declared = {(e["method"], e["path"]) for e in contract["endpoints"]}
    registered_filtered = {
        r for r in registered if not r[1].endswith("__contract_introspect__")
    }

    missing = declared - registered_filtered
    if missing:
        raise RuntimeError(
            f"BE contract violation: declared routes not registered: {missing}"
        )
    extras = registered_filtered - declared
    if extras:
        warnings.warn(f"Routes registered but not in contract: {extras}")


# ---------------------------------------------------------------------------
# SSE event generator (Task 4.2)
# ---------------------------------------------------------------------------


async def generate_lane_pane_stream_events(
    spawner: "FleetSpawner",
    lane: str,
    stream_log: Path,
    q: "asyncio.Queue[bytes]",
):
    """Yield SSE event dicts for ``GET /api/v1/lane/{lane}/pane-stream``.

    First event is base64(``\\x1bc``) (terminal-clear); second (if present)
    is base64 of the last ``TAIL_ON_CONNECT_BYTES`` of the stream log; then
    one base64-encoded event per live chunk delivered through ``q``.

    The caller (route handler) is responsible for ``spawner.subscribe`` to
    obtain ``q`` *before* constructing this generator — that way a
    ``TooManySubscribersError`` surfaces as HTTP 503, not as an SSE event
    inside an already-200 response. The ``finally`` clause unsubscribes so
    a 11th-subscriber bounce releases a slot on disconnect.
    """
    import base64

    try:
        # 1. Terminal-clear sentinel.
        yield {"data": base64.b64encode(b"\x1bc").decode("ascii")}

        # 2. Replay last TAIL_ON_CONNECT_BYTES from stream log.
        try:
            with stream_log.open("rb") as f:
                f.seek(0, 2)
                size = f.tell()
                start = max(0, size - TAIL_ON_CONNECT_BYTES)
                f.seek(start)
                replay = f.read()
        except FileNotFoundError:
            replay = b""
        if replay:
            yield {"data": base64.b64encode(replay).decode("ascii")}

        # 3. Live tail loop. Cancellation propagates out of ``q.get`` when
        # sse-starlette tears the stream down on client disconnect.
        while True:
            chunk = await q.get()
            yield {"data": base64.b64encode(chunk).decode("ascii")}
    finally:
        await spawner.unsubscribe(lane, q)


# ---------------------------------------------------------------------------
# make_app factory
# ---------------------------------------------------------------------------


def _parse_narrator_interval_env() -> float | None:
    """Parse MEGALODON_NARRATOR_INTERVAL_S to a float, or None if unset/unparseable.

    Returned value is fed to clamp_interval_s (None -> default 30s).
    """
    raw = os.environ.get("MEGALODON_NARRATOR_INTERVAL_S")
    if not raw:
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


def make_app(
    *,
    mission_dir: Path,
    config: AppConfig | None = None,
    port: int = 8080,
) -> FastAPI:
    """Build a Megalodon UI FastAPI app bound to `mission_dir`.

    Args:
        mission_dir: Absolute path to mission directory; must exist.
        config: Optional AppConfig overrides; defaults to AppConfig().
        port: Bind port (default 8080). Used to compute allowed_origins
            unless config.allowed_origins is set.

    Returns:
        FastAPI app with the integration-test endpoint surface registered.

    Raises:
        FileNotFoundError if mission_dir does not exist.
        NotADirectoryError if mission_dir is not a directory.
    """
    mission_dir = Path(mission_dir).resolve()
    if not mission_dir.exists():
        raise FileNotFoundError(f"mission_dir does not exist: {mission_dir}")
    if not mission_dir.is_dir():
        raise NotADirectoryError(f"mission_dir is not a directory: {mission_dir}")

    cfg = config or AppConfig()
    # Δ4: port-derived allowed_origins per FE P2-D-to-C C1.
    if cfg.allowed_origins is not None:
        origins = cfg.allowed_origins
    else:
        origins = (
            f"http://127.0.0.1:{port}",
            f"http://localhost:{port}",
        )

    mc = load_mission_config(mission_dir)

    ctx = MissionContext(
        mission_dir=mission_dir,
        config=cfg,
        port=port,
        csrf_token=cfg.csrf_token,
        allowed_origins=origins,
        mission_config=mc,
        status_row_re=build_status_row_re(mc),
        task_line_re=build_task_line_re(mc),
        session_store=auth.SessionStore(),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN001
        """Start the tmux fleet and watchdog; shut down on exit.

        Exit codes:
          10 — socket path too long (fatal; bypass uvicorn signal handling).
          11 — start_all timed out (fatal).
          12 — disk free < 50 MB (fatal, from watchdog task).

        Test overrides via env vars (read inside lifespan so tests can inject
        via monkeypatch.setenv before the context manager runs):
          MEGALODON_LIFESPAN_TIMEOUT_S  — float override for startup timeout.
          MEGALODON_LIFESPAN_SLEEP_S    — if set, sleep this many seconds before
                                          start_all; lets tests trigger the timeout
                                          deterministically.
        """
        # Test mode: skip fleet spawn entirely. Used by the v9.1 integration
        # tests that exercise request handlers without needing a real tmux
        # fleet. The flag also relaxes the socket-path length guard since
        # pytest tmp_path on macOS routinely exceeds 100 bytes.
        test_mode = os.environ.get("MEGALODON_LIFESPAN_TEST_MODE") == "1"
        fake_spawner = os.environ.get("MEGALODON_FAKE_SPAWNER") == "1"

        # 1. Socket path length guard.
        socket = mission_dir / ".fleet" / "tmux.sock"
        if (
            not test_mode
            and not fake_spawner
            and len(str(socket).encode()) > SOCKET_PATH_LIMIT_BYTES
        ):
            print(f"socket path too long: {socket}", file=sys.stderr)
            sys.exit(10)

        if fake_spawner:
            from .spawn_fake import FakeFleetSpawner

            # Fake/demo session persistence (Wave 4 A2 seam). The fake branch
            # now persists sessions to disk by DEFAULT — like the live branch —
            # so an operator restarting a demo is not bricked with a dead
            # cookie (the prior in-memory-only default invalidated the session
            # on every restart). The default path mirrors live:
            # <mission>/.fleet/sessions.json. MEGALODON_FAKE_SESSIONS_PATH still
            # overrides the location (the restart-reconnect e2e PW-3 uses it to
            # point at a tmp file). This NEVER affects the live branch or
            # test-mode (MEGALODON_LIFESPAN_TEST_MODE=1), which never reach here.
            #
            # Pollution note: fake-spawner test runs use a tmp_path / copied
            # tmpdir mission (see ui/tests/integration/conftest.py and the e2e
            # playwright config's prepareFixture), so writing sessions.json
            # under that mission's .fleet/ does not touch tracked fixtures.
            _fake_sessions_path = os.environ.get("MEGALODON_FAKE_SESSIONS_PATH")
            _sessions_path = (
                Path(_fake_sessions_path)
                if _fake_sessions_path
                else mission_dir / ".fleet" / "sessions.json"
            )
            ctx.session_store = auth.SessionStore(path=_sessions_path)

            fake_spawner_obj = FakeFleetSpawner(
                mission_dir,
                ctx.mission_config,
                get_adapter,
                socket,
            )
            app.state.spawner = fake_spawner_obj
            app.state.startup_complete = True
            from .activity_wall import ActivityWall

            _aw_fake = ActivityWall(mission_dir)
            await _aw_fake.start()
            app.state.activity_wall = _aw_fake
            from .narrator.hub import NarrativeHub

            app.state.narrative_hub = NarrativeHub()
            app.state.narrative_cache = {}

            # Narrator scheduler in fake/demo mode (Bug fix: the fake branch
            # previously created an empty cache and returned, so the demo board
            # was permanently blank — run_narrator_scheduler only ran in the
            # live branch). Reuse the SAME scheduler/build_rows path the live
            # branch uses (no forked logic) against the fake spawner's sessions,
            # and seed the cache with one deterministic tick at startup so the
            # board is populated even before any SSE subscriber connects.
            from .narrator.board_state import build_lane_rows
            from .narrator.runtime import NarratorRuntime
            from .narrator.scheduler import (
                clamp_interval_s,
                narrator_tick,
                run_narrator_scheduler,
            )

            _fake_narrator_runtime = NarratorRuntime.from_env()
            await _fake_narrator_runtime.start()

            async def _fake_narrator_build_rows():
                tasks_fe = parse_tasks_fe_shape(mission_dir, ctx)
                status_rows = parse_status(mission_dir, ctx)
                return await build_lane_rows(
                    mission_dir,
                    tasks_fe,
                    fake_spawner_obj.sessions,
                    fake_spawner_obj.adapter_resolver,
                    ctx.mission_config.lanes,
                    status_rows=status_rows,
                )

            # Deterministic one-shot tick: populate Goal/Now/Last/state from
            # build_lane_rows immediately so a fresh demo boot is never blank.
            try:
                await narrator_tick(
                    hub=app.state.narrative_hub,
                    runtime=_fake_narrator_runtime,
                    cache=app.state.narrative_cache,
                    build_rows=_fake_narrator_build_rows,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[narrator-fake] startup tick failed: {exc!r}", file=sys.stderr)

            _fake_narrator_interval_s = clamp_interval_s(_parse_narrator_interval_env())
            _fake_narrator_stop = asyncio.Event()
            _fake_narrator_task = asyncio.create_task(
                run_narrator_scheduler(
                    hub=app.state.narrative_hub,
                    runtime=_fake_narrator_runtime,
                    cache=app.state.narrative_cache,
                    build_rows=_fake_narrator_build_rows,
                    interval_s=_fake_narrator_interval_s,
                    stop_event=_fake_narrator_stop,
                )
            )
            app.state.narrator_runtime = _fake_narrator_runtime
            app.state.narrator_scheduler_task = _fake_narrator_task
            # Lane-health watchdog (Task C) also runs under the fake fleet so the
            # safety backbone is exercised end-to-end: a stale STATUS row produces
            # an alert reachable via GET /api/v1/alerts without a real tmux fleet.
            _fake_lane_watchdog_task = asyncio.create_task(
                _lane_health_watchdog(mission_dir)
            )
            try:
                yield
            finally:
                _fake_narrator_stop.set()
                _fake_narrator_task.cancel()
                try:
                    await _fake_narrator_task
                except (asyncio.CancelledError, Exception):
                    pass
                _fake_lane_watchdog_task.cancel()
                try:
                    await _fake_lane_watchdog_task
                except (asyncio.CancelledError, Exception):
                    pass
                try:
                    await _fake_narrator_runtime.stop()
                except Exception:
                    pass
                await _aw_fake.stop()
            return

        if test_mode:
            app.state.spawner = None
            app.state.startup_complete = True
            applier_task: asyncio.Task | None = None
            if os.environ.get("MEGALODON_INPROCESS_APPLIER") == "1":
                # Drive the queue applier inline so v9.0 e2e specs that POST
                # mutations (challenge / reclaim / inject-task / phase-flip)
                # see them propagate to TASKS.md / STATUS.md without a separate
                # daemon process.
                from .queue.applier import Applier

                applier = Applier(mission_dir, poll_seconds=0.2)

                async def _drain_loop() -> None:
                    while True:
                        try:
                            applier.drain_once()
                        except Exception:
                            pass
                        await asyncio.sleep(0.2)

                applier_task = asyncio.create_task(_drain_loop())
            # Activity wall (test mode)
            from .activity_wall import ActivityWall

            _aw_test = ActivityWall(mission_dir)
            await _aw_test.start()
            app.state.activity_wall = _aw_test
            from .narrator.hub import NarrativeHub

            app.state.narrative_hub = NarrativeHub()
            app.state.narrative_cache = {}
            try:
                yield
            finally:
                await _aw_test.stop()
                if applier_task is not None:
                    applier_task.cancel()
                    try:
                        await applier_task
                    except (asyncio.CancelledError, Exception):
                        pass
            return

        # 2. Construct FleetSpawner and start_all under a timeout.
        spawner = FleetSpawner(mission_dir, ctx.mission_config, get_adapter, socket)
        app.state.spawner = spawner
        app.state.startup_complete = False

        timeout = float(
            os.environ.get(
                "MEGALODON_LIFESPAN_TIMEOUT_S", LIFESPAN_STARTUP_TIMEOUT_SECONDS
            )
        )
        sleep_s_raw = os.environ.get("MEGALODON_LIFESPAN_SLEEP_S")

        async def _start_with_optional_sleep() -> None:
            if sleep_s_raw is not None:
                await asyncio.sleep(float(sleep_s_raw))
            await spawner.start_all()

        try:
            await asyncio.wait_for(_start_with_optional_sleep(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                await spawner.stop_all()
            except Exception:
                pass
            print(
                f"lifespan startup timeout > {timeout}s",
                file=sys.stderr,
            )
            sys.exit(11)

        app.state.startup_complete = True

        # 3. Start df-check background task (every 60 s; exit 12 if < 50 MB free).
        df_task = asyncio.create_task(_df_watchdog(mission_dir))

        # 3b. Start lane-health watchdog (Task C): drives the extracted
        # check_lanes_once off-loop on an interval so a dead/stale/hung lane is
        # surfaced via findings + the alerts JSONL within one interval instead of
        # going invisible for ~15 min. Cancelled on shutdown alongside df_task.
        lane_watchdog_task = asyncio.create_task(_lane_health_watchdog(mission_dir))

        # 3c. Auto-recovery supervisor (Task F) — DEFAULT OFF. Armed only by
        # MEGALODON_AUTORECOVER=1 (silently restarting agents is an outward
        # action; fail-closed). Reads liveness from the live sessions + trailing
        # consecutive denies from the governor-log, and restarts via the shared
        # restart-loop path (bounded backoff + attempt cap, all logged).
        autorecover_task: asyncio.Task | None = None
        if autorecover_enabled():
            from .narrator.board_state import _derive_liveness as _dl

            def _live_liveness() -> dict[str, str]:
                return {short: _dl(sess) for short, sess in spawner.sessions.items()}

            def _live_consecutive_denies() -> dict[str, int]:
                # NOTE: the deny-loop recovery trigger inherits
                # _compute_governor_blocked's 5-denies-in-60s window gate — only
                # lanes already flagged window-blocked surface a consecutive_denies
                # count here, so a SLOW deny-loop (denies spread beyond the 60s
                # window) won't trip recovery. Liveness-"dead" recovery is on a
                # separate path (_live_liveness) and is unaffected.
                blocked = _compute_governor_blocked(mission_dir)
                return {
                    lane: info.get("consecutive_denies", 0)
                    for lane, info in blocked.items()
                }

            async def _live_restart(lane: str) -> None:
                await _perform_restart_loop(mission_dir, spawner, lane)

            _autorecover = AutoRecoverSupervisor(
                mission_dir,
                get_liveness=_live_liveness,
                get_consecutive_denies=_live_consecutive_denies,
                restart_fn=_live_restart,
            )
            app.state.autorecover = _autorecover
            autorecover_task = asyncio.create_task(_autorecover.run())

        # 4a. Start activity wall: fan-in from 6 sources into ring buffer.
        from .activity_wall import ActivityWall

        activity_wall = ActivityWall(mission_dir)
        await activity_wall.start()
        app.state.activity_wall = activity_wall

        # 4b. Narrative hub + cache (passive plumbing for summary board, Task 2.2).
        from .narrator.hub import NarrativeHub

        app.state.narrative_hub = NarrativeHub()
        app.state.narrative_cache = {}

        # 4b-ii. Persistent session store (Task D2 / WR-3): reassign to the
        # disk-backed store now that mission_dir/.fleet is confirmed available.
        # The hot validate() path reads only the in-memory dict (no disk IO), so
        # synchronous persist calls on create/revoke are negligible for this
        # single-operator localhost tool.  Test/fake branches never reach here,
        # so ctx.session_store keeps path=None (writes nothing) in those modes.
        ctx.session_store = auth.SessionStore(
            path=mission_dir / ".fleet" / "sessions.json"
        )

        # 4c. Narrator runtime + scheduler (Task 4.1).
        from .narrator.runtime import NarratorRuntime
        from .narrator.scheduler import clamp_interval_s, run_narrator_scheduler
        from .narrator.board_state import build_lane_rows

        narrator_runtime = NarratorRuntime.from_env()
        await narrator_runtime.start()

        async def _narrator_build_rows():
            tasks_fe = parse_tasks_fe_shape(mission_dir, ctx)
            # STATUS.md fallback: lets the board reflect live lane activity
            # (working:/initialized) when no TASKS.md row backs the lane yet.
            status_rows = parse_status(mission_dir, ctx)
            return await build_lane_rows(
                mission_dir,
                tasks_fe,
                spawner.sessions,
                spawner.adapter_resolver,
                ctx.mission_config.lanes,
                status_rows=status_rows,
            )

        narrator_interval_s = clamp_interval_s(_parse_narrator_interval_env())

        narrator_stop_event = asyncio.Event()
        narrator_scheduler_task = asyncio.create_task(
            run_narrator_scheduler(
                hub=app.state.narrative_hub,
                runtime=narrator_runtime,
                cache=app.state.narrative_cache,
                build_rows=_narrator_build_rows,
                interval_s=narrator_interval_s,
                stop_event=narrator_stop_event,
            )
        )
        app.state.narrator_runtime = narrator_runtime
        app.state.narrator_scheduler_task = narrator_scheduler_task

        # 4b-iii. Observed dashboard auto-open (Task D4). Replaces the old
        # unconditional pre-uvicorn browser-open: open a tab only if no live
        # tab reconnects within the grace window, so restarts don't pile up
        # duplicate tabs while a genuinely fresh launch still opens one.
        # The handoff values are set on app.state by __main__.main() on a real
        # launch; read defensively (getattr) since this branch is live-only and
        # those attrs are absent under any other entrypoint.
        from .dashboard_open import (
            auto_open_watch,
            open_dashboard_nonfatal,
            parse_open_grace_env,
        )

        _open_url = getattr(app.state, "dashboard_open_url", None)
        _open_enabled = getattr(app.state, "dashboard_open_enabled", False)
        _open_force = getattr(app.state, "dashboard_force_open", False)
        auto_open_task: asyncio.Task | None = None
        if _open_url:
            auto_open_task = asyncio.create_task(
                auto_open_watch(
                    enabled=bool(_open_enabled),
                    force_open=bool(_open_force),
                    url=_open_url,
                    get_subscriber_count=(
                        lambda: (
                            app.state.narrative_hub.subscriber_count
                            + app.state.activity_wall.subscriber_count
                        )
                    ),
                    open_fn=open_dashboard_nonfatal,
                    grace_s=parse_open_grace_env(),
                    poll_s=0.5,
                ),
                name="dashboard-auto-open",
            )

        # 5. Start in-process queue applier (v9.3): drains pending intents from
        #    agents' POST /api/v1/{task/claim,task/done,status/update,...} so the
        #    requests resolve to applied/rejected quickly. Without this the agents
        #    poll /api/v1/queue/<rid> forever — each retry is a curl that, in a
        #    for-loop, becomes a compound-bash prompt the operator has to approve.
        from .queue.applier import Applier

        _live_applier = Applier(mission_dir, poll_seconds=1.0)
        _have_applier_lock = _live_applier.acquire_singleton()
        if not _have_applier_lock:
            print(
                "[applier] another applier already holds the lock; in-process drain disabled",
                file=sys.stderr,
            )

        async def _live_drain_loop() -> None:
            while True:
                try:
                    _live_applier.drain_once()
                except Exception as e:  # noqa: BLE001
                    print(f"[applier] drain error: {e!r}", file=sys.stderr)
                await asyncio.sleep(1.0)

        applier_task = (
            asyncio.create_task(_live_drain_loop()) if _have_applier_lock else None
        )

        try:
            yield
        finally:
            if auto_open_task is not None:
                auto_open_task.cancel()
                try:
                    await auto_open_task
                except (asyncio.CancelledError, Exception):
                    pass
            narrator_stop_event.set()
            narrator_scheduler_task.cancel()
            try:
                await narrator_scheduler_task
            except (asyncio.CancelledError, Exception):
                pass
            try:
                await narrator_runtime.stop()
            except Exception:
                pass
            if applier_task is not None:
                applier_task.cancel()
                try:
                    await applier_task
                except (asyncio.CancelledError, Exception):
                    pass
            if _have_applier_lock:
                try:
                    _live_applier.release_singleton()
                except Exception:
                    pass
            await activity_wall.stop()
            df_task.cancel()
            lane_watchdog_task.cancel()
            try:
                await lane_watchdog_task
            except (asyncio.CancelledError, Exception):
                pass
            if autorecover_task is not None:
                autorecover_task.cancel()
                try:
                    await autorecover_task
                except (asyncio.CancelledError, Exception):
                    pass
            try:
                await spawner.stop_all()
            except Exception:
                pass

    app = FastAPI(title="Megalodon UI", version="2.0.0", lifespan=lifespan)
    app.state.megalodon = ctx  # accessible via dependency injection

    @app.middleware("http")
    async def v92_auth_gate(request: Request, call_next):  # noqa: ANN001
        """Deny-by-default auth gate: every ``/api/**`` path requires a cookie.

        Security inversion (was: allowlist of gated paths → wide-open legacy
        surface). Now any request under ``/api/**`` is rejected with 401 unless
        it carries a valid ``mui_session`` cookie, EXCEPT the single public
        token-exchange endpoint. Non-``/api`` paths (SPA shell, static assets,
        favicon, healthz) are served pre-auth so the login bootstrap can render.
        """
        path = request.url.path
        method = request.method
        if _v92_path_is_gated(method, path):
            cookie = request.cookies.get(SESSION_COOKIE_NAME)
            if not ctx.session_store.validate(cookie):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "authentication required"},
                )
        return await call_next(request)

    # v9.3.5 — disable browser caching for /static/ and / (index.html).
    # The dogfood loop iterates FE JS rapidly; without no-cache headers the
    # operator's Safari serves stale app.js for hours and the symptom looks
    # like "my fix didn't work" when actually the browser never re-fetched.
    # Tradeoff: extra network round-trips per page load. Acceptable for
    # dev/dogfood; not for prod.
    @app.middleware("http")
    async def no_cache_dev_assets(request: Request, call_next):  # noqa: ANN001
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.startswith("/static/"):
            response.headers["Cache-Control"] = (
                "no-store, no-cache, must-revalidate, max-age=0"
            )
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    # REPAIR-MUTATIONS-E2E-1-SSE: serve UI assets so index.html's
    # `/static/js/{store,sse,app}.js` and `/static/css/base.css` resolve.
    static_dir = ctx.config.static_dir or (
        Path(__file__).resolve().parent.parent / "ui" / "static"
    )
    if static_dir.exists():
        app.mount(
            "/static",
            StaticFiles(directory=str(static_dir), html=True),
            name="static",
        )

    _register_routes(app, ctx)

    # V9 M2 — contract validation. Opt-in via env var until contract.md is
    # fully cross-checked across all factory callers; flip to default-on once
    # we're confident no surprise drift exists.
    if os.environ.get("M9_VALIDATE_CONTRACT") == "1":
        contract_path = (
            Path(__file__).resolve().parents[1] / "docs" / "v9" / "api-contract.md"
        )
        _validate_contract(app, contract_path)

    return app


# ---------------------------------------------------------------------------
# Request body schemas
# ---------------------------------------------------------------------------


class InjectBody(BaseModel):
    """Body schema for POST /api/v1/lane/{short}/inject."""

    text: str
    enter: bool = True


class ApprovalRuleBody(BaseModel):
    """Body schema for POST /api/v1/approval-rules."""

    pattern: str
    added_by_session: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _register_routes(app: FastAPI, ctx: MissionContext) -> None:

    @app.post("/api/v1/auth/exchange")
    async def post_auth_exchange(req: Request) -> Response:
        """Validate bearer token against ``.fleet/ui.token``; mint a session cookie.

        Plan §6.3: 200 on success with ``mui_session`` HttpOnly+SameSite=Strict
        cookie; 401 on any other outcome (invalid token, missing token, missing
        file). No body discrimination — same 401 shape for all failure paths
        so an attacker can't probe token-file presence.
        """
        try:
            body = await req.json()
        except Exception:
            body = {}
        supplied = str(body.get("token", "")) if isinstance(body, dict) else ""
        stored = auth.read_token(ctx.mission_dir / ".fleet" / "ui.token")
        if not auth.compare_token(supplied, stored):
            return JSONResponse(status_code=401, content={"detail": "invalid token"})
        sid = ctx.session_store.create()
        resp = JSONResponse(status_code=200, content={"ok": True})
        resp.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=sid,
            max_age=COOKIE_MAX_AGE_SECONDS,
            httponly=True,
            samesite="strict",
            path="/",
            secure=False,  # localhost is plain HTTP
        )
        return resp

    @app.get("/api/v1/lane/{lane}/pane-stream")
    async def lane_pane_stream(lane: str, request: Request):  # noqa: ANN201
        """SSE stream of base64-encoded bytes from a lane's tmux pane (Task 4.2).

        Per plan §6.4 / P4:
          * First event: ``base64(b"\\x1bc")`` — terminal-clear sentinel so
            xterm.js starts from a known state.
          * Second event: ``base64(<last TAIL_ON_CONNECT_BYTES of stream_log>)``
            so a late connector sees recent context.
          * Subsequent events: each live byte chunk from the tail producer,
            base64-encoded.

        Rejection paths:
          * 401 — handled by ``v92_auth_gate`` middleware (this handler is
            only reached with a valid ``mui_session`` cookie).
          * 404 — unknown lane or spawner not yet initialized.
          * 503 — lane already has ``SSE_MAX_SUBSCRIBERS_PER_LANE`` subscribers;
            ``Retry-After: 5`` header set so the browser modal can back off.

        The event-yielding logic lives in
        ``generate_lane_pane_stream_events`` so unit tests can iterate it
        directly without an in-process HTTP transport (httpx ASGITransport
        and Starlette TestClient both buffer SSE bodies until the generator
        completes — fine for finite responses, deadlock for infinite tails).
        End-to-end SSE behaviour is covered by Playwright in Phase 5 against
        a real uvicorn process.
        """
        from sse_starlette.sse import EventSourceResponse

        spawner = getattr(app.state, "spawner", None)
        if spawner is None or lane not in spawner.sessions:
            return JSONResponse(
                status_code=404, content={"detail": f"unknown lane {lane}"}
            )

        try:
            q = await spawner.subscribe(lane)
        except TooManySubscribersError:
            return JSONResponse(
                status_code=503,
                content={"detail": "too many subscribers"},
                headers={"Retry-After": "5"},
            )

        session = spawner.get(lane)
        return EventSourceResponse(
            generate_lane_pane_stream_events(spawner, lane, session.stream_log, q)
        )

    @app.get("/api/v1/lane/{lane}/state")
    async def lane_state(lane: str):  # noqa: ANN201
        """Return runtime state for a lane (Task 6.4, CV-8).

        ``{running, exited_rc, started_utc, last_bytes_offset}``.

        The handler queries ``tmux display-message -p -F
        '#{pane_dead}|#{pane_dead_status}'`` on demand, with a 1 s TTL cache
        on ``LaneSession.pane_dead_checked_at``. No background polling —
        the cost is bounded by request rate (CV-8).
        """
        import time
        from megalodon_ui import tmux as _tmux

        spawner = getattr(app.state, "spawner", None)
        if spawner is None or lane not in spawner.sessions:
            return JSONResponse(
                status_code=404, content={"detail": f"unknown lane {lane}"}
            )

        session = spawner.get(lane)

        # Fake-spawner short-circuit: trust in-memory state, skip real tmux query.
        if hasattr(spawner, "set_pane_dead"):
            pass
        else:
            # 1 s TTL cache for the pane-dead probe.
            now = time.monotonic()
            if now - session.pane_dead_checked_at >= 1.0:
                dead, status = await _tmux.display_message_pane_dead(
                    spawner.socket, session.name
                )
                session.pane_dead_checked_at = now
                if dead:
                    session.exited_rc = status
                    session.running = False

        # Best-effort byte count — stream log may not exist in degenerate tests.
        try:
            last_bytes_offset = session.stream_log.stat().st_size
        except (OSError, FileNotFoundError):
            last_bytes_offset = 0

        return JSONResponse(
            content={
                "running": bool(session.running) and session.exited_rc is None,
                "exited_rc": session.exited_rc,
                "started_utc": getattr(session, "started_utc", None),
                "last_bytes_offset": last_bytes_offset,
            }
        )

    @app.post("/api/v1/lane/{lane}/followup")
    async def lane_followup(lane: str, request: Request):  # noqa: ANN201
        """Respawn a lane's tmux pane under a new follow-up prompt (Task 6.2).

        Per plan §6.4: body `{prompt: str, model?: str}` → resolves the lane's
        adapter, calls `adapter.build_followup_argv(prompt, prior_session_id=...,
        model=..., cwd=...)`, then calls `spawner.respawn(lane, argv, env)`.
        Returns 202 immediately; the new session id is discovered
        asynchronously by `spawner.respawn` (P6.3) and persisted to
        ``<mission>/.fleet/<short>.session.txt`` (CV-5).

        Rejection paths:
          * 401 — middleware (handled before this body runs).
          * 404 — unknown lane or spawner not initialized.
          * 422 — missing or whitespace-only prompt.
        """
        spawner = getattr(app.state, "spawner", None)
        if spawner is None or lane not in spawner.sessions:
            return JSONResponse(
                status_code=404, content={"detail": f"unknown lane {lane}"}
            )

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                status_code=422, content={"detail": "invalid JSON body"}
            )
        prompt_raw = body.get("prompt") if isinstance(body, dict) else None
        if not isinstance(prompt_raw, str) or not prompt_raw.strip():
            return JSONResponse(
                status_code=422,
                content={"detail": "prompt is required and must be non-empty"},
            )
        prompt = prompt_raw

        lane_cfg = None
        for lc in ctx.mission_config.lanes:
            if lc.short == lane:
                lane_cfg = lc
                break
        if lane_cfg is None:
            return JSONResponse(
                status_code=404, content={"detail": f"no lane config for {lane}"}
            )

        model_override = body.get("model") if isinstance(body, dict) else None
        model = (
            model_override
            if isinstance(model_override, str) and model_override.strip()
            else lane_cfg.harness.model
        )

        adapter = spawner.adapter_resolver(lane_cfg.harness.cli)
        session = spawner.get(lane)
        # Governor --settings (Task 2.2): single-source gate. No preflight here —
        # the fleet is already running — so the path is re-derived (settings_path
        # left None). Helper applies the enabled + claude-cli check in one place.
        from megalodon_ui.governor.wiring import governor_kwargs

        _gov_kw = governor_kwargs(ctx.mission_config, lane_cfg)
        argv, env = adapter.build_followup_argv(
            prompt,
            prior_session_id=session.session_id,
            model=model,
            cwd=spawner.mission_dir,
            **_gov_kw,
        )

        await spawner.respawn(lane, argv, env)
        return JSONResponse(
            status_code=202,
            content={"lane": lane, "status": "respawned"},
        )

    # Per-lane rate-limit state for /inject. Keyed by lane short-code; each
    # value is a deque of UTC epoch floats for calls within the last 60 s.
    _inject_rl: dict[str, collections.deque] = {}
    _INJECT_RL_WINDOW = 60.0
    _INJECT_RL_MAX = 10
    _INJECT_TEXT_LIMIT = 16384  # bytes

    @app.post("/api/v1/lane/{short}/inject")
    async def lane_inject(short: str, body: InjectBody, request: Request):  # noqa: ANN201
        """Inject keystrokes into a lane's tmux pane.

        Body: ``{text: str, enter: bool}``
        Required header: ``X-CSRF-Token`` — must match ``ctx.csrf_token``.

        Rejection paths:
          * 401 — middleware (cookie gate, handled before this handler runs).
          * 403 — missing or mismatched X-CSRF-Token header.
          * 404 — unknown lane or spawner not initialized.
          * 413 — text exceeds 16384 bytes (UTF-8 encoded).
          * 429 — rate limit exceeded (10 calls / 60 s per lane).
          * 500 — tmux send-keys failed.

        On success: calls ``tmux.send_keys`` and appends a JSON audit-log line
        to ``.fleet/inject-log-YYYY-MM-DD.jsonl`` (UTC date, SHA-256 of text).
        Returns 202 Accepted with ``{ok: true}``.
        """
        # CSRF verification (timing-safe compare per QA T1.3 follow-up)
        csrf = request.headers.get("X-CSRF-Token", "")
        if not csrf or not secrets.compare_digest(csrf, ctx.csrf_token):
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF token missing or invalid"},
            )

        # Lane / spawner resolution
        spawner = getattr(app.state, "spawner", None)
        if spawner is None or short not in spawner.sessions:
            return JSONResponse(
                status_code=404, content={"detail": f"unknown lane {short}"}
            )

        # Text size guard
        text_bytes = body.text.encode("utf-8")
        if len(text_bytes) > _INJECT_TEXT_LIMIT:
            return JSONResponse(
                status_code=413,
                content={"detail": f"text exceeds {_INJECT_TEXT_LIMIT} bytes"},
            )

        # Per-lane rate limit: 10 calls per 60 s
        now = time.time()
        if short not in _inject_rl:
            _inject_rl[short] = collections.deque()
        dq = _inject_rl[short]
        cutoff = now - _INJECT_RL_WINDOW
        while dq and dq[0] <= cutoff:
            dq.popleft()
        if len(dq) >= _INJECT_RL_MAX:
            return JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded (10 calls/60 s per lane)"},
            )
        dq.append(now)

        # Tmux keystroke injection.
        # Fake-spawner short-circuit: FakeFleetSpawner has no real tmux socket;
        # skip send_keys and fall through to the audit log.  The fake_emit
        # attribute is the canonical marker that distinguishes FakeFleetSpawner
        # from the real FleetSpawner.
        if not hasattr(spawner, "fake_emit"):
            session_name = spawner.sessions[short].name
            rc = await tmux.send_keys(
                spawner.socket, session_name, body.text, enter=body.enter
            )
            if rc != 0:
                return JSONResponse(
                    status_code=500,
                    content={"detail": f"tmux send-keys failed (rc={rc})"},
                )

        # Audit log: append JSON line to .fleet/inject-log-YYYY-MM-DD.jsonl
        fleet_dir = ctx.mission_dir / ".fleet"
        fleet_dir.mkdir(parents=True, exist_ok=True)
        ts_now = datetime.now(timezone.utc)
        date_str = ts_now.strftime("%Y-%m-%d")
        log_path = fleet_dir / f"inject-log-{date_str}.jsonl"
        entry = {
            "ts": ts_now.isoformat(),
            "lane": short,
            "text_sha256": hashlib.sha256(text_bytes).hexdigest(),
            "byte_count": len(text_bytes),
            "enter": body.enter,
        }
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

        return JSONResponse(status_code=202, content={"ok": True})

    @app.post("/api/v1/lane/{short}/restart-loop")
    async def lane_restart_loop(short: str, request: Request):  # noqa: ANN201
        """Restart a lane's /loop cycle using its initial_prompt.

        Body: empty ``{}``
        Required header: ``X-CSRF-Token`` — must match ``ctx.csrf_token``.

        Rejection paths:
          * 401 — middleware (cookie gate, handled before this handler runs).
          * 403 — missing or mismatched X-CSRF-Token header.
          * 404 — unknown lane or spawner not initialized.
          * 409 — no initial_prompt recorded for this lane.
          * 500 — tmux send-keys failed.

        On success: calls ``tmux.send_keys`` with the lane's initial_prompt
        and appends a JSON audit-log line to ``.fleet/inject-log-YYYY-MM-DD.jsonl``
        (UTC date, SHA-256 of initial_prompt, source="restart-loop").
        Returns 202 Accepted with ``{ok: true}``.
        """
        # CSRF verification (timing-safe compare per QA T1.3 follow-up)
        csrf = request.headers.get("X-CSRF-Token", "")
        if not csrf or not secrets.compare_digest(csrf, ctx.csrf_token):
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF token missing or invalid"},
            )

        # Lane / spawner resolution
        spawner = getattr(app.state, "spawner", None)
        if spawner is None or short not in spawner.sessions:
            return JSONResponse(
                status_code=404, content={"detail": f"unknown lane {short}"}
            )

        # Check initial_prompt exists
        session = spawner.sessions[short]
        if not session.initial_prompt:
            return JSONResponse(
                status_code=409,
                content={"detail": "no initial_prompt recorded"},
            )

        text = session.initial_prompt
        text_bytes = text.encode("utf-8")

        # Tmux keystroke injection.
        # Fake-spawner short-circuit: FakeFleetSpawner has no real tmux socket;
        # skip send_keys and fall through to the audit log.  The fake_emit
        # attribute is the canonical marker that distinguishes FakeFleetSpawner
        # from the real FleetSpawner.
        if not hasattr(spawner, "fake_emit"):
            session_name = session.name
            rc = await tmux.send_keys(spawner.socket, session_name, text, enter=True)
            if rc != 0:
                return JSONResponse(
                    status_code=500,
                    content={"detail": f"tmux send-keys failed (rc={rc})"},
                )

        # Audit log: append JSON line to .fleet/inject-log-YYYY-MM-DD.jsonl
        # Reuse the same file as inject; add source="restart-loop" to distinguish
        fleet_dir = ctx.mission_dir / ".fleet"
        fleet_dir.mkdir(parents=True, exist_ok=True)
        ts_now = datetime.now(timezone.utc)
        date_str = ts_now.strftime("%Y-%m-%d")
        log_path = fleet_dir / f"inject-log-{date_str}.jsonl"
        entry = {
            "ts": ts_now.isoformat(),
            "lane": short,
            "text_sha256": hashlib.sha256(text_bytes).hexdigest(),
            "byte_count": len(text_bytes),
            "enter": True,
            "source": "restart-loop",
        }
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

        return JSONResponse(status_code=202, content={"ok": True})

    @app.delete("/api/v1/fleet")
    async def delete_fleet(request: Request):  # noqa: ANN201
        """Destructive teardown — kill the tmux server + unlink bootstrap files (Task 7.1).

        Cookie-gated (deny-by-default; under ``/api/**``). Best-effort throughout:

        * ``tmux.kill_server(socket)`` — non-zero rc is tolerated (server may
          already be gone if the operator killed it manually).
        * Unlinks ``ui.token``, ``tmux.sock``, ``dashboard.url``,
          ``approval-rules.json`` from ``<mission>/.fleet/``; removes all
          ``inject-log-*.jsonl`` files (daily-rotated); ``missing_ok=True``
          keeps the call idempotent.

        After the response is sent, the surrounding lifespan sees
        ``app.state.shutdown_requested = True`` and the uvicorn process exits 0.
        """
        fleet_dir = ctx.mission_dir / ".fleet"
        socket = fleet_dir / "tmux.sock"
        try:
            await tmux.kill_server(socket)
        except FileNotFoundError:
            pass
        for name in ("ui.token", "tmux.sock", "dashboard.url", "approval-rules.json"):
            (fleet_dir / name).unlink(missing_ok=True)
        # Clean daily-rotated inject log files (glob pattern)
        for p in fleet_dir.glob("inject-log-*.jsonl"):
            p.unlink(missing_ok=True)
        request.app.state.shutdown_requested = True
        return JSONResponse(status_code=200, content={"status": "shutdown"})

    if os.environ.get("MEGALODON_FAKE_SPAWNER") == "1":

        @app.post("/api/v1/__fake__/emit")
        async def fake_emit_route(request: Request):  # noqa: ANN201
            """Test-only — fan out a byte chunk into a lane's subscriber queues.

            Registered only when ``MEGALODON_FAKE_SPAWNER=1``. Cookie-gated
            (deny-by-default; under ``/api/**``). Body: ``{lane, data_b64}``.
            """
            import base64

            spawner = getattr(app.state, "spawner", None)
            if spawner is None or not hasattr(spawner, "fake_emit"):
                return JSONResponse(
                    status_code=404, content={"detail": "no fake spawner"}
                )
            body = await request.json()
            lane = body.get("lane")
            data_b64 = body.get("data_b64", "")
            try:
                data = base64.b64decode(data_b64)
            except Exception:
                return JSONResponse(status_code=422, content={"detail": "bad base64"})
            if lane not in spawner.sessions:
                return JSONResponse(
                    status_code=404, content={"detail": f"unknown lane {lane}"}
                )
            await spawner.fake_emit(lane, data)
            return JSONResponse(status_code=200, content={"emitted": len(data)})

        @app.post("/api/v1/__fake__/set_state")
        async def fake_set_state_route(request: Request):  # noqa: ANN201
            """Test-only — flip a lane to running=False+exited_rc=<rc>, or alive."""
            spawner = getattr(app.state, "spawner", None)
            if spawner is None or not hasattr(spawner, "set_pane_dead"):
                return JSONResponse(
                    status_code=404, content={"detail": "no fake spawner"}
                )
            body = await request.json()
            lane = body.get("lane")
            if lane not in spawner.sessions:
                return JSONResponse(
                    status_code=404, content={"detail": f"unknown lane {lane}"}
                )
            if body.get("running") is False:
                spawner.set_pane_dead(lane, int(body.get("rc", 0)))
            else:
                spawner.set_pane_alive(lane)
            return JSONResponse(status_code=200, content={"lane": lane})

        @app.post("/api/v1/__fake__/narrative")
        async def fake_narrative_inject_route(request: Request):  # noqa: ANN201
            """Test-only — seed narrative_cache and publish to narrative_hub.

            Registered only when ``MEGALODON_FAKE_SPAWNER=1``. Cookie-gated
            (deny-by-default; under ``/api/**``). Body: ``{"lanes": {<short>: <row_payload>, ...}}``.

            Merges the supplied lanes into ``app.state.narrative_cache`` (i.e.
            ``cache[short] = row_payload`` for each), then publishes the full
            updated cache as ``{"lanes": dict(app.state.narrative_cache)}`` to
            ``app.state.narrative_hub`` — the same frame shape the real scheduler
            emits, so the board's stream→render path is exercised unchanged.

            Returns ``{"ok": true, "lanes": [<short>, ...]}``.
            """
            body = await request.json()
            incoming = body.get("lanes", {})
            if not isinstance(incoming, dict):
                return JSONResponse(
                    status_code=422, content={"detail": "lanes must be an object"}
                )
            cache = app.state.narrative_cache
            for short, row_payload in incoming.items():
                cache[short] = row_payload
            app.state.narrative_hub.publish({"lanes": dict(cache)})
            return JSONResponse(
                status_code=200,
                content={"ok": True, "lanes": list(incoming.keys())},
            )

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        """Liveness + readiness probe.

        Returns 200 {"status": "ok"} once the lifespan startup completes
        (i.e., FleetSpawner.start_all() returned without error).
        Returns 503 {"status": "starting"} during startup (before the lifespan
        has set app.state.startup_complete = True).
        """
        if getattr(app.state, "startup_complete", False):
            return JSONResponse(content={"status": "ok"}, status_code=200)
        return JSONResponse(content={"status": "starting"}, status_code=503)

    @app.get("/api/status")
    async def get_status() -> JSONResponse:
        rows = parse_status(ctx.mission_dir, ctx)
        return JSONResponse(content=rows)

    @app.get("/api/findings")
    async def get_findings(severity: str | None = None, scratch: str | None = None):
        include_scratch = str(scratch).lower() in ("true", "1", "yes")
        findings = parse_findings(ctx.mission_dir, include_scratch=include_scratch)
        if severity:
            # Support CSV list of severities (e.g., "MAJOR,BLOCKING").
            wanted = {s.strip().upper() for s in severity.split(",")}
            findings = [
                f
                for f in findings
                if (str(f.get("severity", "")).strip().upper() in wanted)
            ]
        return JSONResponse(content=findings)

    @app.get(API_CONFIG)
    async def get_config():
        # FE C5: documented response shape.
        # P5.2: `v92_dashboard` is a server-runtime flag (env var
        # `MEGALODON_V92_DASHBOARD`), not a MissionConfig declaration —
        # this lets v9.0 fixtures stay v9.0 without YAML edits.
        v92_raw = os.environ.get("MEGALODON_V92_DASHBOARD", "").strip().lower()
        v92_dashboard = v92_raw in ("1", "true", "yes", "on")
        return {
            "csrf_token": ctx.csrf_token,
            "heartbeat_interval_seconds": ctx.config.heartbeat_interval_seconds,
            "poll_interval_seconds": ctx.config.poll_interval_seconds,
            "stale_threshold_seconds": ctx.config.stale_threshold_seconds,
            "allowed_origins": list(ctx.allowed_origins),
            "lanes": [lane.model_dump() for lane in ctx.mission_config.lanes],
            "phases": ctx.mission_config.phases,
            "task_id_patterns": ctx.mission_config.task_id_patterns.patterns,
            "harnesses": list({lane.harness.cli for lane in ctx.mission_config.lanes}),
            "task_sections": ctx.mission_config.task_sections,
            "v92_dashboard": v92_dashboard,
        }

    @app.post("/api/tasks")
    async def post_task(req: Request):
        body = await req.json()
        kind = body.get("kind", "").upper()
        target = body.get("target_finding", "")
        if not kind:
            raise HTTPException(status_code=422, detail="kind required")

        # Construct task entry. CHALLENGE form: `[ ] [CHALLENGE-<short>] ...`
        short_target = Path(target).stem if target else "manual"
        task_line = f"\n- [ ] [CHALLENGE-{short_target}] CHALLENGE on {target}\n"

        tasks_path = ctx.mission_dir / "TASKS.md"
        if not tasks_path.exists():
            tasks_path.write_text("# Tasks\n")
        # Append to CHALLENGE section if present, else end of file.
        text = tasks_path.read_text()
        if "## CHALLENGE TASKS" in text:
            text = text.replace(
                "## CHALLENGE TASKS",
                f"## CHALLENGE TASKS{task_line}",
                1,
            )
        else:
            text = text.rstrip("\n") + "\n" + task_line
        tasks_path.write_text(text)
        return JSONResponse(
            content={"ok": True, "task_line": task_line.strip()}, status_code=201
        )

    @app.post("/api/lanes/{lane}/reclaim")
    async def post_reclaim(lane: str):
        # Find target lane's working task from STATUS, attempt reclaim.
        rows = parse_status(ctx.mission_dir, ctx)
        target = next((r for r in rows if r["lane"].upper() == lane.upper()), None)
        if not target:
            raise HTTPException(status_code=404, detail=f"lane {lane!r} not found")
        state = target.get("state", "")
        # Parse "working: <task_id>" if present.
        m = re.match(r"working:\s*(\S+)", state)
        if not m:
            # Nothing to reclaim — already idle.
            return Response(status_code=204)
        task_id = m.group(1)
        primitives.reclaim_or_recover(ctx.mission_dir, task_id, "orchestrator")
        return JSONResponse(content={"ok": True, "task_id": task_id})

    @app.post("/api/lanes/{lane}/signal")
    async def post_signal(lane: str, req: Request):
        body = await req.json()
        try:
            primitives.validate_signal(body)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        text = str(body.get("text", "")).strip()
        cite = str(body.get("cite") or body.get("evidence") or "").strip()
        topic = str(body.get("topic") or "").strip() or _slugify(text)
        # Defang token-breaking chars so a crafted `text` can't forge a second
        # `[SIG ...]` token with an attacker-chosen sender (stored injection).
        safe_text = _defang_sig_text(text)
        safe_cite = _defang_sig_text(cite)

        # Append to STATUS.md row's Notes column (CAS-naive minimal impl).
        status_path = ctx.mission_dir / "STATUS.md"
        if not status_path.exists():
            raise HTTPException(status_code=500, detail="STATUS.md missing")
        status_text = status_path.read_text()

        # Find the target lane's row line; append a SIG token to its Notes cell.
        sig_token = (
            f' [SIG from=orchestrator to={lane} text="{safe_text}" cite={safe_cite}]'
        )
        # Simplest: append the signal text + cite to the Notes column (last cell).
        lines = status_text.splitlines(keepends=True)
        new_lines = []
        appended = False
        lane_upper = lane.upper()
        for line in lines:
            if (
                not appended
                and line.lstrip().startswith("|")
                and lane_upper in line.upper()
            ):
                # Skip header/separator rows (they don't contain agent IDs).
                if "Agent" in line or "---" in line:
                    new_lines.append(line)
                    continue
                # Insert before trailing pipe (and any whitespace/newline).
                stripped = line.rstrip("\n")
                trailing = line[len(stripped) :]
                # Find last "|" in the row to insert before it
                if stripped.endswith("|"):
                    new_line = stripped[:-1] + sig_token + " |" + trailing
                else:
                    new_line = stripped + sig_token + trailing
                new_lines.append(new_line)
                appended = True
            else:
                new_lines.append(line)
        if not appended:
            raise HTTPException(status_code=404, detail=f"lane {lane!r} row not found")
        status_path.write_text("".join(new_lines))

        # ALSO write a canonical signals/*.md file so the channel the UI reads
        # actually receives the write (and the activity-wall watcher fires).
        # from_lane = "ORCH" per the FROZEN WIRE CONTRACT §C. Best-effort: a
        # signals-write failure must not fail the (already-committed) STATUS write.
        body_md = safe_text + (f"\n\ncite: {safe_cite}\n" if safe_cite else "\n")
        try:
            _write_signal_file(ctx.mission_dir, "ORCH", lane, topic, body_md)
        except OSError:
            import logging as _logging

            _logging.getLogger(__name__).warning(
                "post_signal: failed to write signals/ file", exc_info=True
            )
        return JSONResponse(content={"ok": True}, status_code=201)

    @app.post("/api/mission/flip")
    async def post_flip(req: Request):
        body = await req.json()
        from_phase = str(body.get("from", "")).strip()
        to_phase = str(body.get("to", "")).strip()
        if not from_phase or not to_phase:
            raise HTTPException(status_code=422, detail="from and to required")
        won = primitives.try_phase_flip(
            ctx.mission_dir, from_phase, to_phase, "orchestrator"
        )
        if not won:
            raise HTTPException(
                status_code=409, detail="phase-flip lock held by another worker"
            )
        return {"ok": True, "from": from_phase, "to": to_phase}

    # Helper to call other handlers from /api/v1/* aliases.
    class _FakeReq:
        def __init__(self, body):
            self._body = body

        async def json(self):
            return self._body

    def _make_req_with_body(_original, body):
        return _FakeReq(body)

    # ----- canonical /api/v1/* surface per ui/api-contract.md -----
    # TEST P3-E is aligning the integration tests to use these per the
    # canonical contract. Bodies use the contract's field names.

    @app.get("/api/v1/status")
    async def get_v1_status():
        return {"lanes": parse_status(ctx.mission_dir, ctx)}

    @app.get("/api/v1/tasks")
    async def get_v1_tasks():
        # REPAIR-MUTATIONS-E2E-5-STATUS-VIEW (b): TASKS.md parsed into
        # phase/task tree consumed by FE `tasks.js:417,452`.
        return {"phases": parse_tasks(ctx.mission_dir, ctx)}

    @app.get(API_STATE)
    async def get_v1_state():
        # REPAIR-MUTATIONS-E2E-11-STATE-ENDPOINT: aggregate bootstrap
        # consumed by FE `sse.js:67 hydrateInitialState()` →
        # `store.js:193-217 hydrate()`. Top-level keys: status, tasks,
        # findings, signals, mission, config.
        #
        # v9.3 dogfood payload completion: state.tasks now uses
        # parse_tasks_fe_shape() which honors `task_sections` from
        # MissionConfig (so headers like ``## PHASE 1 — PLAN`` parse, not
        # only canonical ``## PHASE-PLAN``) and emits the cross-lane bucket
        # the FE expects in tasks.cross. state.signals reads signals/ dir,
        # and state.mission includes id+status (MISSION.md) + last-50
        # events (.mission-events) so mission.js can render the run header.
        mission_phase = "INIT"
        events_path = ctx.mission_dir / ".mission-events"
        if events_path.exists():
            try:
                last_line = events_path.read_text().strip().splitlines()[-1]
                # Format: "<utc> <FROM-PHASE>-><TO-PHASE> by <agent> -- ..."
                if "->" in last_line:
                    after_arrow = last_line.split("->", 1)[1]
                    mission_phase = after_arrow.split(" ", 1)[0].strip()
            except (IndexError, ValueError):
                pass

        tasks_payload = parse_tasks_fe_shape(ctx.mission_dir, ctx)
        mission_md = _read_mission_md_fields(ctx.mission_dir)
        mission_payload: dict[str, Any] = {
            "phase": mission_phase,
            "stuckFlipLock": _detect_stuck_flip_lock(ctx.mission_dir),
            "history": _parse_history_entries(ctx.mission_dir),
            "events": _read_mission_events_tail(ctx.mission_dir, limit=50),
            # lanes_online: omitted here — would require a tmux subprocess
            # against ctx's tmux socket; the FE renders the lane card grid
            # from status.lanes (parse_status) which is already accurate.
        }
        if "id" in mission_md:
            mission_payload["id"] = mission_md["id"]
        if "status" in mission_md:
            mission_payload["status"] = mission_md["status"]

        return {
            "status": {"lanes": parse_status(ctx.mission_dir, ctx)},
            "tasks": tasks_payload,
            # Include scratch findings — the FE's `filter-scratch` chip
            # toggles visibility client-side, but it can only reveal what
            # the store already has. (REPAIR-MUTATIONS-E2E-5-STATUS-VIEW
            # contract.)
            "findings": {"list": parse_findings(ctx.mission_dir, include_scratch=True)},
            "signals": {"list": parse_signals(ctx.mission_dir)},
            "claims": {"list": _list_claim_dirs(ctx.mission_dir)},
            "mission": mission_payload,
            "config": {
                "csrf_token": ctx.csrf_token,
                "poll_interval_seconds": ctx.config.poll_interval_seconds,
            },
        }

    @app.get(API_FINDINGS)
    async def get_v1_findings(
        lane: str | None = None,
        severity: str | None = None,
        task: str | None = None,
        scratch: str | None = None,
    ):
        include_scratch = str(scratch).lower() in ("true", "1", "yes")
        findings = parse_findings(ctx.mission_dir, include_scratch=include_scratch)
        if severity:
            wanted = {s.strip().upper() for s in severity.split(",")}
            findings = [
                f
                for f in findings
                if str(f.get("severity", "")).strip().upper() in wanted
            ]
        if lane:
            findings = [
                f
                for f in findings
                if str(f.get("lane", "")).strip().upper() == lane.upper()
            ]
        if task:
            findings = [
                f
                for f in findings
                if task in str(f.get("task", "")) or task in str(f.get("task-id", ""))
            ]
        return {"findings": findings}

    @app.get(API_FINDINGS + "/{filename}")
    async def get_v1_finding_detail(filename: str):
        """V9 M2 — fetch single finding body + frontmatter by filename.

        FE consumer: ui/static/pages/findings.js:528. Lazily loads body for
        the findings drawer; cached client-side under
        `findings.byFilename.<filename>`.
        """
        # Sanitize: reject path traversal.
        if "/" in filename or "\\" in filename or ".." in filename:
            raise HTTPException(status_code=400, detail="invalid filename")
        path = ctx.mission_dir / "findings" / filename
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="finding not found")
        text = path.read_text()
        frontmatter = _parse_yaml_frontmatter(text)
        # Body is everything after the closing `---` line; fall back to whole
        # text if there's no frontmatter.
        body = text
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end >= 0:
                body = text[end + 4 :].lstrip("\n")
        return {"filename": filename, "body": body, "frontmatter": frontmatter}

    @app.post(API_SIGNAL)
    async def post_v1_signal(req: Request):
        """V9 M1.5: now 202-async via queue.

        Routes the signal into the target lane's STATUS row notes via
        STATUS_UPDATE intent. FE may poll /api/v1/queue/{rid}.
        """
        body = await req.json()
        # api-contract.md: {to_lane, claim, evidence}
        to_lane = str(body.get("to_lane", "")).strip()
        claim = str(body.get("claim", "")).strip()
        evidence = str(body.get("evidence", "")).strip()
        topic = str(body.get("topic") or "").strip() or _slugify(claim)
        # Defang token-breaking chars so a crafted `claim` can't forge a second
        # `[SIG ...]` token with an attacker-chosen sender (stored injection).
        safe_claim = _defang_sig_text(claim)
        safe_evidence = _defang_sig_text(evidence)
        if not to_lane:
            raise HTTPException(status_code=422, detail="to_lane required")
        try:
            primitives.validate_signal({"evidence": evidence, "cite": evidence})
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

        # Read current row for this lane to preserve agent/state.
        rows = parse_status(ctx.mission_dir, ctx)
        target = next((r for r in rows if r["lane"].upper() == to_lane.upper()), None)
        if not target:
            raise HTTPException(status_code=404, detail=f"lane {to_lane!r} not found")

        sig_token = (
            f"[SIG from=orchestrator to={to_lane} "
            f'text="{safe_claim}" cite={safe_evidence}]'
        )
        new_notes = f"{target['notes']} {sig_token}".strip()
        rid = _qc.status_update(
            ctx.mission_dir,
            agent=target["agent"],
            lane=to_lane.upper(),
            new_state=target["state"],
            new_notes=new_notes,
        )

        # ALSO write a canonical signals/*.md file so the channel the UI reads
        # receives the write directly (the STATUS note above is queued/async;
        # this gives the operator an immediate, durable signal record and fires
        # the activity-wall watcher). from_lane="ORCH" per FROZEN WIRE §C.
        # Best-effort: a signals-write failure must not change the 202 contract.
        body_md = safe_claim + (
            f"\n\ncite: {safe_evidence}\n" if safe_evidence else "\n"
        )
        try:
            _write_signal_file(ctx.mission_dir, "ORCH", to_lane, topic, body_md)
        except OSError:
            import logging as _logging

            _logging.getLogger(__name__).warning(
                "post_v1_signal: failed to write signals/ file", exc_info=True
            )
        return JSONResponse(
            status_code=202,
            content={"request_id": rid, "intent": "STATUS_UPDATE", "status": "pending"},
            headers={"Location": f"/api/v1/queue/{rid}"},
        )

    @app.get("/api/v1/coordination")
    async def get_v1_coordination(request: Request):  # noqa: ANN201
        """Coordination / handoff / contention view (cookie-gated via §F regex).

        Joins three authoritative sources so the operator can SEE who is doing
        what, what is claimed, and the most recent cross-lane signals:

        * ``lanes`` — from ``parse_status``; each row mapped to
          ``{lane, agent, state, working_task, blocked, notes_excerpt}``.
          ``working_task`` is parsed from a ``working: <id>`` state; ``blocked``
          is True when the state mentions "blocked".
        * ``claims`` — from ``_list_claim_dirs``; each enriched with
          ``task_id`` (canonical dirname), ``working_lane`` (the lane whose
          working_task matches this claim, else null), and ``contested`` (True
          when no lane is working on it AND it has no ``done`` marker — an
          orphaned/contended claim).
        * ``signals_recent`` — top 20 from the unified ``parse_signals``.

        Grounded entirely in STATUS / on-disk state — no invented data.
        """
        status_rows = parse_status(ctx.mission_dir, ctx)
        lanes: list[dict[str, Any]] = []
        # Map canonical task_id -> lane short currently working it.
        working_by_task: dict[str, str] = {}
        for row in status_rows:
            state = row.get("state", "") or ""
            wm = re.search(r"working:\s*(\S+)", state)
            working_task = wm.group(1) if wm else None
            blocked = "blocked" in state.lower()
            if working_task:
                working_by_task[primitives.canonicalize_task_id(working_task)] = (
                    row.get("lane", "")
                )
            lanes.append(
                {
                    "lane": row.get("lane", ""),
                    "agent": row.get("agent", ""),
                    "state": state,
                    "working_task": working_task,
                    "blocked": blocked,
                    "notes_excerpt": (row.get("notes", "") or "")[:160],
                }
            )

        claims: list[dict[str, Any]] = []
        for c in _list_claim_dirs(ctx.mission_dir):
            dirname = c.get("dirname", "")
            task_id = primitives.canonicalize_task_id(dirname)
            has_done = bool(c.get("has_done"))
            working_lane = working_by_task.get(task_id)
            # Contested: nobody is actively working it and it isn't marked done.
            contested = working_lane is None and not has_done
            mtime = c.get("mtime", 0.0)
            claims.append(
                {
                    "task_id": task_id,
                    "dirname": dirname,
                    "has_done": has_done,
                    "mtime": mtime,
                    "owner": c.get("owner"),
                    "working_lane": working_lane,
                    "contested": contested,
                }
            )

        signals_recent = parse_signals(ctx.mission_dir)[:20]
        return JSONResponse(
            content={
                "lanes": lanes,
                "claims": claims,
                "signals_recent": signals_recent,
            }
        )

    @app.post(API_RECLAIM)
    async def post_v1_reclaim(req: Request):
        """V9 M1.5: now 202-async via queue when there's a task to reclaim.

        If lane is already idle (no `working: <task>`), returns 204 as
        before — nothing to do.
        """
        body = await req.json()
        lane = str(body.get("lane", "")).strip()
        if not lane:
            raise HTTPException(status_code=422, detail="lane required")

        rows = parse_status(ctx.mission_dir, ctx)
        target = next((r for r in rows if r["lane"].upper() == lane.upper()), None)
        if not target:
            raise HTTPException(status_code=404, detail=f"lane {lane!r} not found")
        m = re.match(r"working:\s*(\S+)", target.get("state", ""))
        if not m:
            return Response(status_code=204)
        # RULE 6 dispatch (per primitives.reclaim_or_recover docstring):
        #   - matching finding exists → retroactive recovery (state=idle)
        #   - no finding                → STALE-RECLAIMED + rm -rf claim dir
        # E2E spec T-A-RC-e2e asserts the row text contains "STALE-RECLAIMED"
        # after a no-finding reclaim, matching the canonical fix-large fixture.
        task_id = m.group(1)
        has_finding = (
            primitives._finding_exists_for_task(ctx.mission_dir, task_id) is not None
        )
        if has_finding:
            new_state = "idle"
            new_notes = f"retroactive recovery for {task_id} by orchestrator"
        else:
            new_state = "STALE-RECLAIMED"
            new_notes = f"reclaimed by orchestrator (no finding for {task_id})"
        # Apply the primitive side-effects synchronously (rm -rf claim dir or
        # touch done marker) so the on-disk state matches the new STATUS row.
        primitives.reclaim_or_recover(ctx.mission_dir, task_id, "orchestrator")
        rid = _qc.status_update(
            ctx.mission_dir,
            agent=target["agent"],
            lane=lane.upper(),
            new_state=new_state,
            new_notes=new_notes,
        )
        return JSONResponse(
            status_code=202,
            content={"request_id": rid, "intent": "STATUS_UPDATE", "status": "pending"},
            headers={"Location": f"/api/v1/queue/{rid}"},
        )

    @app.post(API_CHALLENGE)
    async def post_v1_challenge(req: Request):
        """V9 M1.5: now 202-async via queue (TASKS_INJECT)."""
        body = await req.json()
        finding = str(body.get("finding_filename", "")).strip()
        description = str(body.get("description", "")).strip()
        if not finding:
            raise HTTPException(status_code=422, detail="finding_filename required")
        short_target = Path(finding).stem
        task_id = f"CHALLENGE-{short_target}"
        rid = _qc.tasks_inject(
            ctx.mission_dir,
            agent="orchestrator",
            submitting_lane=ctx.mission_config.orchestrator_pseudo_lane,
            task_id=task_id,
            lane="A",
            description=description or f"CHALLENGE on {finding}",
        )
        return JSONResponse(
            status_code=202,
            content={"request_id": rid, "intent": "TASKS_INJECT", "status": "pending"},
            headers={"Location": f"/api/v1/queue/{rid}"},
        )

    @app.post(API_PHASE_FLIP)
    async def post_v1_phase_flip(req: Request):
        body = await req.json()
        return await post_flip(_make_req_with_body(req, body))

    @app.post(API_MISSION_STATUS)
    async def post_v1_mission_status(req: Request):
        body = await req.json()
        status = str(body.get("status", "")).strip().upper()
        if status not in ("IDLE", "ACTIVE", "DRAINING", "COMPLETE"):
            raise HTTPException(status_code=422, detail="invalid status")
        # Best-effort: update README Mission status section.
        readme = ctx.mission_dir / "README.md"
        if readme.exists():
            text = readme.read_text()
            new_text = re.sub(
                r"\*\*Current:\s*[^*]+\*\*",
                f"**Current: {status}**",
                text,
                count=1,
            )
            readme.write_text(new_text)
        return {"ok": True, "status": status}

    @app.post(API_INJECT_TASK)
    async def post_v1_inject_task(req: Request):
        """V9 M1.5: now 202-async via queue (TASKS_INJECT).

        Body: {task_text, section?}. We parse a canonical
        ``- [bracket] [LANE-X] `task-id` — description`` line; if it
        parses, route through queue. Free-form text is rejected (FE
        should use the canonical shape).
        """
        body = await req.json()
        task_text = str(body.get("task_text", "")).strip()
        if not task_text:
            raise HTTPException(status_code=422, detail="task_text required")
        m = re.match(
            r"^-?\s*(\[[^\]]+\])\s*\[LANE-([A-Z])\]\s*`([^`]+)`\s*(?:[—-]\s*(.*))?$",
            task_text,
        )
        if not m:
            raise HTTPException(
                status_code=422,
                detail="task_text must match `- [bracket] [LANE-X] `id` — desc`",
            )
        bracket, lane, task_id, desc = (
            m.group(1),
            m.group(2),
            m.group(3),
            (m.group(4) or ""),
        )
        rid = _qc.tasks_inject(
            ctx.mission_dir,
            agent="orchestrator",
            submitting_lane=ctx.mission_config.orchestrator_pseudo_lane,
            task_id=task_id,
            lane=lane,
            description=desc,
            bracket=bracket,
        )
        return JSONResponse(
            status_code=202,
            content={"request_id": rid, "intent": "TASKS_INJECT", "status": "pending"},
            headers={"Location": f"/api/v1/queue/{rid}"},
        )

    # V9 M1.5 — queue request introspection endpoint.
    @app.get("/api/v1/queue/{request_id}")
    async def get_v1_queue_status(request_id: str):
        """Return current state of a queue request submitted via M1.5
        202-async endpoints.

        Response shape: `{request_id, status, rejection_reason}` where
        status ∈ {pending, applied, rejected}.
        """
        mission = ctx.mission_dir
        if (mission / "queue" / "applied" / f"{request_id}.json").exists():
            return {
                "request_id": request_id,
                "status": "applied",
                "rejection_reason": None,
            }
        rejected = mission / "queue" / "rejected" / f"{request_id}.json"
        if rejected.exists():
            reason_file = mission / "queue" / "rejected" / f"{request_id}-reason.txt"
            reason = reason_file.read_text() if reason_file.exists() else None
            return {
                "request_id": request_id,
                "status": "rejected",
                "rejection_reason": reason,
            }
        if (mission / "queue" / "pending" / f"{request_id}.json").exists():
            return {
                "request_id": request_id,
                "status": "pending",
                "rejection_reason": None,
            }
        raise HTTPException(404, "request_id not found")

    # ----- V9 M2: introspection endpoint for contract scan -----

    # ----- v9.4 stale-lane detection ----------------------------------------

    @app.get("/api/v1/lanes/stale")
    async def get_stale_lanes():  # noqa: ANN201
        """Return silent lanes and governor-blocked (deny-loop) lanes.

        A lane is *stale* when it has been silent for ≥ 900 s. A lane is
        *governor-blocked* when the governor-log shows a deny-loop (plan
        §8.3/§8.7); such a lane is reported separately and EXCLUDED from
        ``stale_lanes`` so the operator does not kill it thinking it is merely
        silent.

        Cookie-gated (deny-by-default; under ``/api/**``). Cached for 5 s (serves
        concurrent operator polls without recomputing).

        Response shape::

            {
              "stale_lanes": [
                {"lane": "A", "silent_seconds": 1234.5,
                 "last_activity_source": "stream-log"},
                ...
              ],
              "governor_blocked": [
                {"lane": "B", "deny_count": 6, "window_seconds": 60.0,
                 "last_category": "write", "last_reason": "outside-mission"},
                ...
              ],
              "checked_at_utc": "2026-05-20T15:00:00+00:00"
            }

        ``last_activity_source`` is one of ``"status-md"``, ``"stream-log"``,
        ``"applier-log"``, or ``"none"`` (when all data sources are missing for
        the lane).

        ``silent_seconds`` is null when no source provided any timestamp (the
        lane is treated as infinitely stale).
        """
        now_mono = time.monotonic()
        app_key = id(app)
        cached = _stale_cache.get(app_key)
        # Use cache if it exists, is fresh, and there are no pending test overrides.
        # Pending overrides trigger fresh computation.
        override_count_before = len(_TEST_STALE_OVERRIDES)
        if (
            cached is not None
            and (now_mono - cached["computed_mono"]) < _STALE_CACHE_TTL_SECONDS
            and override_count_before == 0
        ):
            return JSONResponse(content=cached["response"])

        lane_rows = parse_status(ctx.mission_dir, ctx)
        response = _compute_stale_response(
            ctx.mission_dir,
            lane_rows,
            ctx.mission_config,
        )
        override_count_after = len(_TEST_STALE_OVERRIDES)
        # Cache only if no overrides were consumed in this computation.
        # If any were consumed (override_count_after < override_count_before),
        # don't cache so the next call recomputes with the override gone.
        if override_count_after >= override_count_before:
            _stale_cache[app_key] = {"response": response, "computed_mono": now_mono}
        else:
            # Clear cache so next call recomputes.
            _stale_cache.pop(app_key, None)
        return JSONResponse(content=response)

    @app.get("/api/v1/alerts")
    async def get_alerts():  # noqa: ANN201
        """Return recent watchdog alerts, newest-first (contract §2).

        Cookie-gated (deny-by-default; under ``/api/**``). Source: the structured JSONL the
        watchdog ``AlertManager`` appends to ``.fleet/watchdog-alerts.jsonl``.

        Response shape::

            {"alerts": [
              {"ts": "...", "lane": "A", "kind": "CRASHED",
               "severity": "critical", "evidence": ["pid 4242 not alive"],
               "message": "A lane CRASHED"},
              ...
            ]}

        The list is empty when no alert has ever fired (file absent). Bounded to
        the last ``_ALERTS_TAIL_LINES`` records.
        """
        return JSONResponse(content={"alerts": _read_alerts(ctx.mission_dir)})

    if os.environ.get("MEGALODON_FAKE_SPAWNER") == "1":

        @app.post("/api/v1/_test/stale_override")
        async def post_stale_override(request: Request):  # noqa: ANN201
            """Test-only — populate _TEST_STALE_OVERRIDES for the next stale check.

            Registered ONLY when ``MEGALODON_FAKE_SPAWNER=1``. Cookie-gated
            (deny-by-default; under ``/api/**``). Query params: ``lane`` (str), ``seconds``
            (float). Body: empty or `{}`. CSRF-protected via ``X-CSRF-Token``.

            On success: sets ``_TEST_STALE_OVERRIDES[lane] = seconds`` and
            returns 200 with ``{ok: true, lane, seconds}``. The next call to
            ``GET /api/v1/lanes/stale`` will pop this override and use it as
            the ``silent_seconds`` for the lane (one-shot).

            Rejection paths:
              * 401 — middleware (cookie gate, handled before this handler runs).
              * 403 — missing or mismatched X-CSRF-Token header.
              * 422 — missing lane, missing seconds, or seconds not a valid float.
            """
            # CSRF verification (timing-safe compare per T1.3)
            csrf = request.headers.get("X-CSRF-Token", "")
            if not csrf or not secrets.compare_digest(csrf, ctx.csrf_token):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF token missing or invalid"},
                )

            # Parse query params
            lane = request.query_params.get("lane", "").strip()
            seconds_str = request.query_params.get("seconds", "").strip()

            if not lane:
                return JSONResponse(
                    status_code=422,
                    content={"detail": "lane query param is required"},
                )
            if not seconds_str:
                return JSONResponse(
                    status_code=422,
                    content={"detail": "seconds query param is required"},
                )

            try:
                seconds = float(seconds_str)
            except ValueError:
                return JSONResponse(
                    status_code=422,
                    content={
                        "detail": f"seconds must be a valid float, got {seconds_str!r}"
                    },
                )

            # Set the override (one-shot, consumed by next GET /api/v1/lanes/stale).
            # Clear the stale-lanes cache so the next GET request recomputes
            # with the new override applied.
            _TEST_STALE_OVERRIDES[lane] = seconds
            _stale_cache.pop(id(app), None)

            return JSONResponse(
                status_code=200,
                content={"ok": True, "lane": lane, "seconds": seconds},
            )

    # ----- v9.3 agent-side queue endpoints (all shared-doc mutations) ------
    #
    # The v9 protocol previously had agents direct-edit TASKS.md / STATUS.md /
    # HISTORY.md via the Edit tool and direct mkdir/rm. That created a race
    # surface against operator-side queued mutations. v9.3 closes the loop:
    # every shared-doc mutation — agent OR operator — routes through these
    # endpoints, which call queue_client and hit the same in-process applier.
    # No more direct file edits to group docs.
    #
    # Default mode is asynchronous (202 + Location header to /api/v1/queue/<rid>).
    # Pass ``?wait=true`` to block until the applier resolves the intent (or
    # ~5s elapses) — this collapses the agent's "POST then poll-in-a-for-loop"
    # pattern into a single curl that returns the final {status, ...}. Without
    # wait=true the agent has to write compound-bash for the poll, which trips
    # the static allowlist matcher and prompts the operator. wait=true is the
    # only reason any agent calling these endpoints from a /loop tick should
    # ever need more than one curl per intent.

    async def _wait_for_resolution(
        request_id: str,
        *,
        timeout_s: float = 5.0,
        poll_s: float = 0.15,
    ) -> dict:
        """Block up to ``timeout_s`` for ``request_id`` to land in applied/
        rejected. Returns the same shape as GET /api/v1/queue/{rid}.
        Falls back to ``{status: pending}`` if the applier hasn't resolved
        in time — the agent can then issue a single follow-up GET if it cares.
        """
        deadline = asyncio.get_event_loop().time() + max(0.0, timeout_s)
        mission = ctx.mission_dir
        applied = mission / "queue" / "applied" / f"{request_id}.json"
        rejected = mission / "queue" / "rejected" / f"{request_id}.json"
        reason_file = mission / "queue" / "rejected" / f"{request_id}-reason.txt"
        while True:
            if applied.exists():
                return {
                    "request_id": request_id,
                    "status": "applied",
                    "rejection_reason": None,
                }
            if rejected.exists():
                reason = reason_file.read_text() if reason_file.exists() else None
                return {
                    "request_id": request_id,
                    "status": "rejected",
                    "rejection_reason": reason,
                }
            if asyncio.get_event_loop().time() >= deadline:
                return {
                    "request_id": request_id,
                    "status": "pending",
                    "rejection_reason": None,
                }
            await asyncio.sleep(poll_s)

    def _wait_param(req: Request) -> bool:
        return str(req.query_params.get("wait", "")).lower() in ("1", "true", "yes")

    def _queue_response(rid: str, intent: str, wait: bool):
        """Build the response for a queue endpoint. If ``wait`` is True, block
        for resolution and return 200 with the final status; otherwise 202.

        The async (202) shape is preserved bit-for-bit for existing callers
        (FE poll loop, integration tests, the legacy v9 protocol). Only when
        the caller opts in with ``?wait=true`` does it switch to synchronous.
        """

        async def _build():
            if not wait:
                return JSONResponse(
                    status_code=202,
                    content={"request_id": rid, "intent": intent, "status": "pending"},
                    headers={"Location": f"/api/v1/queue/{rid}"},
                )
            result = await _wait_for_resolution(rid)
            status = result.get("status", "pending")
            http_code = (
                200 if status == "applied" else (409 if status == "rejected" else 202)
            )
            return JSONResponse(
                status_code=http_code,
                content={
                    "request_id": rid,
                    "intent": intent,
                    "status": status,
                    "rejection_reason": result.get("rejection_reason"),
                },
                headers={"Location": f"/api/v1/queue/{rid}"},
            )

        return _build()

    @app.post("/api/v1/task/claim")
    async def post_v1_task_claim(req: Request):  # noqa: ANN201
        """Claim a task atomically via the queue.

        Body: ``{"lane": "A", "task_id": "P1-A", "agent": "agent-xxxx"}``.
        Queues a TASKS_BRACKET intent that rewrites the task's bracket to
        ``[claimed: <agent> @ <UTC>]``. The applier checks for prior claim
        and rejects if another agent already claimed.

        Default: 202 + Location: /api/v1/queue/<rid> (async, poll for outcome).
        ``?wait=true``: block up to ~5s for resolution and return the final
        status directly (200 applied / 409 rejected / 202 still-pending).
        """
        body = await req.json()
        lane = str(body.get("lane", "")).strip()
        task_id = str(body.get("task_id", "")).strip()
        agent = str(body.get("agent", "")).strip()
        if not (lane and task_id and agent):
            raise HTTPException(status_code=422, detail="lane, task_id, agent required")
        rid = _qc.task_claim(ctx.mission_dir, agent=agent, lane=lane, task_id=task_id)
        return await _queue_response(rid, "TASKS_BRACKET", _wait_param(req))

    @app.post("/api/v1/task/done")
    async def post_v1_task_done(req: Request):  # noqa: ANN201
        """Mark a task done via the queue. ``?wait=true`` for sync."""
        body = await req.json()
        lane = str(body.get("lane", "")).strip()
        task_id = str(body.get("task_id", "")).strip()
        agent = str(body.get("agent", "")).strip()
        if not (lane and task_id and agent):
            raise HTTPException(status_code=422, detail="lane, task_id, agent required")
        rid = _qc.task_done(ctx.mission_dir, agent=agent, lane=lane, task_id=task_id)
        return await _queue_response(rid, "TASKS_BRACKET", _wait_param(req))

    @app.post("/api/v1/status/update")
    async def post_v1_status_update(req: Request):  # noqa: ANN201
        """Update a lane row in STATUS.md via the queue. ``?wait=true`` for sync."""
        body = await req.json()
        lane = str(body.get("lane", "")).strip()
        agent = str(body.get("agent", "")).strip()
        if not (lane and agent):
            raise HTTPException(status_code=422, detail="lane and agent required")
        rid = _qc.status_update(
            ctx.mission_dir,
            agent=agent,
            lane=lane,
            new_state=body.get("new_state"),
            new_utc=body.get("new_utc"),
            new_notes=body.get("new_notes"),
        )
        return await _queue_response(rid, "STATUS_UPDATE", _wait_param(req))

    @app.post("/api/v1/history/append")
    async def post_v1_history_append(req: Request):  # noqa: ANN201
        """Append a completion entry to HISTORY.md via the queue. ``?wait=true`` for sync."""
        body = await req.json()
        lane = str(body.get("lane", "")).strip()
        agent = str(body.get("agent", "")).strip()
        task_id = str(body.get("task_id", "")).strip()
        finding_path = str(body.get("finding_path", "")).strip()
        severity = str(body.get("severity", "INFO")).strip().upper()
        if not (lane and agent and task_id and finding_path):
            raise HTTPException(
                status_code=422,
                detail="lane, agent, task_id, finding_path required",
            )
        rid = _qc.history_append(
            ctx.mission_dir,
            agent=agent,
            lane=lane,
            task_id=task_id,
            finding_path=finding_path,
            severity=severity,
        )
        return await _queue_response(rid, "HISTORY_APPEND", _wait_param(req))

    @app.post("/api/v1/mission-event")
    async def post_v1_mission_event(req: Request):  # noqa: ANN201
        """Append an event to .mission-events via the queue. ``?wait=true`` for sync."""
        body = await req.json()
        lane = str(body.get("lane", "")).strip()
        agent = str(body.get("agent", "")).strip()
        event_text = str(body.get("event_text", "")).strip()
        if not (lane and agent and event_text):
            raise HTTPException(
                status_code=422,
                detail="lane, agent, event_text required",
            )
        rid = _qc.mission_event(
            ctx.mission_dir,
            agent=agent,
            lane=lane,
            line=event_text,
        )
        return await _queue_response(rid, "MISSION_EVENT_APPEND", _wait_param(req))

    # ----- v9.3 operator feedback queue (file-backed) ----------------------

    @app.post("/api/v1/lane/{lane}/feedback")
    async def post_lane_feedback(lane: str, request: Request):  # noqa: ANN201
        """Append an operator message to ``feedback/<LANE_NAME>.md``.

        Cookie-gated. Body: ``{"message": str}``. The launch-file template
        instructs each agent to read ``feedback/<LANE>.md`` at the start of
        every /loop iteration and act on any unprocessed entries. Format is
        Markdown with a timestamped H2 per message so the agent can diff
        what's new since the last iteration.

        Returns 202 on append, 404 for unknown lane, 422 for empty message.
        """
        spawner = getattr(app.state, "spawner", None)
        if spawner is None or lane not in spawner.sessions:
            return JSONResponse(
                status_code=404, content={"detail": f"unknown lane {lane}"}
            )
        lane_cfg = None
        for lc in ctx.mission_config.lanes:
            if lc.short == lane:
                lane_cfg = lc
                break
        if lane_cfg is None:
            return JSONResponse(
                status_code=404, content={"detail": f"no config for {lane}"}
            )
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                status_code=422, content={"detail": "invalid JSON body"}
            )
        msg = body.get("message") if isinstance(body, dict) else None
        if not isinstance(msg, str) or not msg.strip():
            return JSONResponse(
                status_code=422,
                content={"detail": "message required (non-empty string)"},
            )
        feedback_dir = ctx.mission_dir / "feedback"
        feedback_dir.mkdir(parents=True, exist_ok=True)
        path = feedback_dir / f"{lane_cfg.name}.md"
        from datetime import datetime, timezone

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n## {ts} — operator\n\n{msg.strip()}\n")
        return JSONResponse(
            status_code=202,
            content={"lane": lane, "lane_name": lane_cfg.name, "appended_at": ts},
        )

    @app.get("/api/v1/__contract_introspect__")
    async def contract_introspect():
        """V9 M2 — list registered routes for contract scan cross-check.

        Returns only /api/v1/* routes. Not part of public contract (declared
        with leading double-underscore by convention; contract_scan.py
        special-cases it).
        """
        seen: set[tuple[str, str]] = set()
        for r in app.routes:
            path = getattr(r, "path", None)
            methods = getattr(r, "methods", None)
            if not path or not methods:
                continue
            if not path.startswith("/api/v1/"):
                continue
            if path.endswith("__contract_introspect__"):
                continue
            for method in methods:
                if method == "HEAD":
                    continue
                seen.add((method, path))
        return {"registered": sorted([[m, p] for (m, p) in seen])}

    # ----- activity wall endpoints --------------------------------------------

    _AW_LIMIT_MAX = 500
    _AW_LIMIT_MIN = 1
    _AW_LIMIT_DEFAULT = 100

    @app.get("/api/v1/activity-wall/snapshot")
    async def activity_wall_snapshot(request: Request):  # noqa: ANN201
        """Return recent activity-wall events as JSON, newest-first.

        Cookie-gated (deny-by-default; under ``/api/**``).

        Query params
        ------------
        limit : int, default 100
            Number of events to return.  Silently clipped to [1, 500];
            callers asking for > 500 receive 500 events (no 400 error).

        Response shape::

            {
              "events": [
                {
                  "type": "finding"|"signal"|"history"|"queue"
                         |"inject"|"restart-loop"|"approval",
                  "lane": "A" | null,
                  "ts": "2026-05-20T15:00:00Z",
                  "summary": "...",
                  "payload": { ... source-specific fields ... }
                },
                ...
              ]
            }

        Events are newest-first. The list may be empty if no events have been
        ingested since server startup.
        """
        try:
            raw = request.query_params.get("limit", str(_AW_LIMIT_DEFAULT))
            limit = int(raw)
        except (ValueError, TypeError):
            return JSONResponse(
                status_code=400,
                content={"detail": "limit must be an integer"},
            )
        # Silently clip to valid range.
        limit = max(_AW_LIMIT_MIN, min(_AW_LIMIT_MAX, limit))

        wall = getattr(app.state, "activity_wall", None)
        if wall is None:
            return JSONResponse(content={"events": []})
        return JSONResponse(content={"events": wall.snapshot(limit)})

    @app.get("/api/v1/activity-wall")
    async def activity_wall_sse(request: Request):  # noqa: ANN201
        """SSE stream of NEW activity-wall events as they arrive.

        Cookie-gated (deny-by-default; under ``/api/**``). Emits no backlog — the client
        must hydrate history via ``GET /api/v1/activity-wall/snapshot`` first.

        Each SSE data payload is a JSON-encoded event dict::

            data: {"type": ..., "lane": ..., "ts": ..., "summary": ..., "payload": ...}

        On client disconnect the per-connection asyncio.Queue is removed from
        the subscriber list and eligible for GC.
        """
        from sse_starlette.sse import EventSourceResponse

        wall = getattr(app.state, "activity_wall", None)
        if wall is None:
            return JSONResponse(
                status_code=503, content={"detail": "activity wall not initialized"}
            )

        q = wall.subscribe()

        async def _event_generator():
            try:
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        event = await asyncio.wait_for(q.get(), timeout=15.0)
                        yield {"data": json.dumps(event)}
                    except asyncio.TimeoutError:
                        # Keep-alive: send a comment so the connection stays open.
                        yield {"comment": "ka"}
            finally:
                wall.unsubscribe(q)

        return EventSourceResponse(_event_generator())

    # ----- summary board narrative endpoints (Task 2.4) -----------------------

    @app.get("/api/v1/narrative")
    async def narrative_snapshot(request: Request):  # noqa: ANN201
        """Return the current per-lane narrative cache.

        Cookie-gated (deny-by-default; under ``/api/**``). Returns the full cache map
        keyed by lane short-name.  ``now.phrase`` may be null when no narrator
        response has arrived yet; all other deterministic fields (last, now,
        goal, state) are always present for each cached lane.

        Response shape::

            {"lanes": {"A": {"last": ..., "now": {...}, "goal": ..., "state": ...}, ...}}
        """
        return JSONResponse(content={"lanes": dict(app.state.narrative_cache)})

    @app.get("/api/v1/narrative-stream")
    async def narrative_stream(request: Request):  # noqa: ANN201
        """SSE stream of narrative payload updates as they are published.

        Cookie-gated (deny-by-default; under ``/api/**``).

        On connect:
        1. Subscribe to the NarrativeHub fan-out queue.
        2. Immediately emit the current cache snapshot as the first SSE frame
           so the client has a baseline without a separate REST round-trip.
        3. Drain the queue, emitting each published payload as an SSE event.
        4. On disconnect (or generator exit): unsubscribe the queue.

        Each SSE data payload is a JSON-encoded dict.  The initial frame has
        the shape ``{"lanes": <cache snapshot>}``; subsequent frames are
        whatever the scheduler publishes (per-lane payload dicts).

        On client disconnect the per-connection asyncio.Queue is removed from
        the hub's subscriber list and eligible for GC.
        """
        from sse_starlette.sse import EventSourceResponse

        hub = getattr(app.state, "narrative_hub", None)
        if hub is None:
            return JSONResponse(
                status_code=503, content={"detail": "narrative hub not initialized"}
            )

        q = hub.subscribe()
        # Snapshot the cache at subscribe time (before any concurrent publish).
        initial = dict(app.state.narrative_cache)

        async def _event_generator():
            try:
                # Initial frame: current cache so client can hydrate immediately.
                yield {"data": json.dumps({"lanes": initial})}
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        payload = await asyncio.wait_for(q.get(), timeout=15.0)
                        yield {"data": json.dumps(payload)}
                    except asyncio.TimeoutError:
                        # Keep-alive: send a comment so the connection stays open.
                        yield {"comment": "ka"}
            finally:
                hub.unsubscribe(q)

        return EventSourceResponse(_event_generator())

    # ----- v9.4 approval-rules (T3.1) ----------------------------------------
    #
    # Persistence: .fleet/approval-rules.json — a flat JSON list.
    # No schema version field; §2 non-goals explicitly exclude schema versioning.
    # Each entry: {pattern: str, added_at_utc: ISO8601, added_by_session: str}
    #
    # Dedup policy: POST with an already-existing pattern returns 200 with the
    # existing entry (not 409) — idempotent for operator retry safety.
    #
    # Corrupt-file policy: GET returns {rules: []} with a WARNING log line rather
    # than 500 — a corrupt file should not hard-block the dashboard.
    #
    # File writes are atomic: write to a .tmp sibling then os.replace() to avoid
    # partial writes on crash.

    _APPROVAL_RULES_FILE = ".fleet/approval-rules.json"

    def _approval_rules_path() -> Path:
        return ctx.mission_dir / _APPROVAL_RULES_FILE

    def _read_approval_rules() -> list[dict]:
        """Read the approval-rules file; return [] if missing or corrupt."""
        import logging as _logging

        path = _approval_rules_path()
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            _logging.getLogger(__name__).warning(
                "approval-rules: corrupt file %s (%s) — returning empty list",
                path,
                exc,
            )
            return []

    def _write_approval_rules(rules: list[dict]) -> None:
        """Atomically write rules list to .fleet/approval-rules.json."""
        path = _approval_rules_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rules, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    @app.post("/api/v1/approval-rules")
    async def post_approval_rule(body: ApprovalRuleBody, request: Request):  # noqa: ANN201
        """Add an approval-rule pattern.

        Body: ``{pattern: str, added_by_session: str}``
        Required header: ``X-CSRF-Token`` — must match ``ctx.csrf_token``.

        Rejection paths:
          * 401 — middleware (cookie gate).
          * 403 — missing or mismatched X-CSRF-Token header.

        Dedup: if an entry with the same ``pattern`` already exists, return
        200 with the existing entry (idempotent — operator retry safe).
        Otherwise append and return 201 with the new entry.
        """
        # CSRF verification (timing-safe compare per T1.3 pattern)
        csrf = request.headers.get("X-CSRF-Token", "")
        if not csrf or not secrets.compare_digest(csrf, ctx.csrf_token):
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF token missing or invalid"},
            )

        rules = _read_approval_rules()
        for existing in rules:
            if existing.get("pattern") == body.pattern:
                # Dedup hit — return 200 with existing entry
                return JSONResponse(status_code=200, content=existing)

        entry: dict = {
            "pattern": body.pattern,
            "added_at_utc": datetime.now(timezone.utc).isoformat(),
            "added_by_session": body.added_by_session,
        }
        rules.append(entry)
        _write_approval_rules(rules)
        return JSONResponse(status_code=201, content=entry)

    @app.get("/api/v1/approval-rules")
    async def get_approval_rules():  # noqa: ANN201
        """Return all approval-rule patterns.

        Cookie-gated (deny-by-default; under ``/api/**``).

        Response shape: ``{rules: [{pattern, added_at_utc, added_by_session}, ...]}``

        Returns ``{rules: []}`` if the file is missing or corrupt (see
        corrupt-file policy comment above).
        """
        return JSONResponse(content={"rules": _read_approval_rules()})

    @app.delete("/api/v1/approval-rules")
    async def delete_approval_rule(request: Request):  # noqa: ANN201
        """Remove an approval-rule pattern.

        Query param: ``pattern`` (exact match, required).
        Required header: ``X-CSRF-Token`` — must match ``ctx.csrf_token``.

        Rejection paths:
          * 401 — middleware (cookie gate).
          * 403 — missing or mismatched X-CSRF-Token header.
          * 404 — pattern not found in the rules list.

        On success: removes the entry, writes the file atomically, returns 204.
        """
        # CSRF verification (timing-safe compare per T1.3 pattern)
        csrf = request.headers.get("X-CSRF-Token", "")
        if not csrf or not secrets.compare_digest(csrf, ctx.csrf_token):
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF token missing or invalid"},
            )

        pattern = request.query_params.get("pattern")
        if not pattern:
            return JSONResponse(
                status_code=400,
                content={"detail": "query param 'pattern' is required"},
            )

        rules = _read_approval_rules()
        new_rules = [r for r in rules if r.get("pattern") != pattern]
        if len(new_rules) == len(rules):
            return JSONResponse(
                status_code=404,
                content={"detail": f"pattern not found: {pattern}"},
            )
        _write_approval_rules(new_rules)
        return Response(status_code=204)

    @app.get("/api/v1/approval-rules/extract")
    async def get_approval_rules_extract(request: Request):  # noqa: ANN201
        """Extract an --allowedTools pattern from a raw command string.

        Query param: ``command`` (URL-encoded shell command string).
        Cookie-gated (deny-by-default; under ``/api/**``) (``approval-rules`` prefix).
        No CSRF required — safe GET method.

        Response: ``{pattern: str | null}``
        ``null`` is returned for compound commands, redirects, empty input, etc.
        """
        from .approval_rules import extract_pattern as _extract_pattern

        command = request.query_params.get("command", "")
        pattern = _extract_pattern(command) if command else None
        return JSONResponse(content={"pattern": pattern})

    # ----- SSE stream (MISSION exit-criterion #4 / TEST signal @19:41Z) -----

    @app.get(API_EVENTS)
    async def sse_events(request: Request):
        """Server-Sent Events stream via sse-starlette EventSourceResponse.

        Emits `sync` on connect; polls STATUS.md mtime on a 0.25s clock and
        emits `status-change` events when the file changes. Per api-contract.md
        §SSE, the canonical event types include sync, status-change,
        task-change, phase-flip, etc; this minimal-viable impl ships sync +
        status-change for MISSION exit-criterion #4.

        REPAIR-MUTATIONS-E2E-1-SSE: switched from raw StreamingResponse →
        EventSourceResponse to get per-event flush. Raw StreamingResponse
        buffered yields, breaking sub-second propagation for the file-touch
        live-update e2e test.
        """
        from sse_starlette.sse import EventSourceResponse

        status_path = ctx.mission_dir / "STATUS.md"

        def _now_iso() -> str:
            return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        async def event_generator():
            sync_payload = json.dumps(
                {
                    "utc": _now_iso(),
                    "mission_dir": str(ctx.mission_dir),
                }
            )
            yield {"event": SSE_SYNC, "data": sync_payload}

            try:
                last_mtime = status_path.stat().st_mtime
            except FileNotFoundError:
                last_mtime = 0.0

            # Bounded loop: 30s max, 0.25s tick. Guarantees termination even
            # if upstream client disconnect-signal is delayed (ASGI test
            # harness quirks observed during integration testing).
            check_interval = 0.25
            max_iterations = int(30.0 / check_interval)
            for _ in range(max_iterations):
                if await request.is_disconnected():
                    return
                await asyncio.sleep(check_interval)
                try:
                    current_mtime = status_path.stat().st_mtime
                except FileNotFoundError:
                    continue
                if current_mtime != last_mtime:
                    last_mtime = current_mtime
                    payload = json.dumps(
                        {
                            "utc": _now_iso(),
                            "lanes": parse_status(ctx.mission_dir, ctx),
                        }
                    )
                    yield {"event": SSE_STATUS_CHANGE, "data": payload}

        return EventSourceResponse(event_generator())

    @app.get("/", response_class=HTMLResponse)
    async def index():
        # FE C2 Approach A: index.html templating.
        static_dir = ctx.config.static_dir or (
            Path(__file__).resolve().parent.parent / "ui" / "static"
        )
        index_path = static_dir / "index.html"
        if not index_path.exists():
            return HTMLResponse(
                content=f"<html><body><h1>Megalodon UI</h1><p>Mission: {ctx.mission_dir}</p></body></html>"
            )
        html = index_path.read_text()
        # Single-token substitution per Δ4.3.
        html = html.replace("__CSRF_TOKEN__", ctx.csrf_token)
        return HTMLResponse(content=html)

    # REPAIR-MUTATIONS-E2E-2-SPA-CATCHALL: serve index.html shell for SPA
    # routes (/tasks, /findings, /mission, /signals) so client-side router
    # can take over. Declared LAST so api/* and static/* (declared earlier)
    # match first. Anchors SPEC-v2 §3-ter (agent-fec0).
    @app.get("/{spa_path:path}", response_class=HTMLResponse)
    async def spa_fallback(spa_path: str):
        if spa_path.startswith("api/") or spa_path.startswith("static/"):
            raise HTTPException(status_code=404)
        return await index()
